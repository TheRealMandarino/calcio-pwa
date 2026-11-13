import json
import logging
from collections import defaultdict
from pathlib import Path

from .updater import default_data_directory

logger = logging.getLogger(__name__)


def compute_driver_race_score(features_dict):
    """
    Calcola un punteggio pre-gara (senza lookahead bias) basato su:
    - Posizione in griglia di partenza / qualifica (fondamentale in F1)
    - Punti stagionali pre-gara
    - Forma recente del pilota (media piazzamenti ultime 3 gare)
    - Forza della monoposto (media piazzamenti team ultime 3 gare)
    """
    grid = features_dict.get("grid_position")
    if grid is None or grid <= 0:
        grid = features_dict.get("qualifying_position") or 20
    grid = min(grid, 22)

    avg_finish = features_dict.get("driver_avg_finish_last_3")
    if avg_finish is None or avg_finish <= 0:
        avg_finish = 15.0

    team_avg_finish = features_dict.get("constructor_avg_finish_last_3")
    if team_avg_finish is None or team_avg_finish <= 0:
        team_avg_finish = 15.0

    driver_pts_before = float(features_dict.get("driver_points_before") or 0.0)
    team_pts_before = float(features_dict.get("constructor_points_before") or 0.0)

    # In F1 partire davanti in griglia ha un impatto dominante
    grid_component = max(0.0, 22.0 - grid) ** 1.3
    # Forma recente del pilota
    form_component = max(0.0, 22.0 - avg_finish)
    # Forza del costruttore / affidabilità
    car_component = max(0.0, 22.0 - team_avg_finish)
    # Punti pre-gara
    pts_component = (driver_pts_before * 0.1) + (team_pts_before * 0.05)

    score = (grid_component * 4.0) + (form_component * 2.2) + (car_component * 2.0) + pts_component
    return max(score, 1.0)


def run_formula1_backtest(data_directory=None, start_year=2016, end_year=2026):
    """
    Esegue il backtest walk-forward per tutte le gare archiviate.
    Valuta:
    - Quante volte il favorito (Top 1) ha vinto la gara
    - Quante volte il vincitore era nella Top 2 e Top 3 prevista
    - Quante volte il favorito è salito a podio
    - Quanti dei 3 piloti pronosticati sul podio sono saliti sul podio reale
    """
    base_dir = Path(data_directory or default_data_directory())
    features_dir = base_dir / "features"

    if not features_dir.is_dir():
        raise FileNotFoundError(f"Directory features non trovata in {features_dir}")

    total_races = 0
    winner_top1_hits = 0
    winner_top2_hits = 0
    winner_top3_hits = 0
    favorite_on_podium_hits = 0
    podium_overlap_counts = defaultdict(int)
    total_podium_slots_matched = 0

    seasons_stats = {}

    for year in range(start_year, end_year + 1):
        feature_file = features_dir / f"{year}.json"
        if not feature_file.is_file():
            continue

        with feature_file.open(encoding="utf-8") as f:
            data = json.load(f)

        rows = data.get("rows", [])
        if not rows:
            continue

        races_by_round = defaultdict(list)
        for row in rows:
            races_by_round[row["round"]].append(row)

        s_races = 0
        s_p1_hits = 0
        s_top2_hits = 0
        s_top3_hits = 0
        s_fav_podium = 0
        s_podium_slots = 0
        s_podium_overlap = defaultdict(int)

        for round_num in sorted(races_by_round.keys()):
            drivers = races_by_round[round_num]
            if not drivers:
                continue

            # Ordina i piloti in base al rating pre-gara
            scored_drivers = []
            for d in drivers:
                feat = d.get("features", {})
                score = compute_driver_race_score(feat)
                scored_drivers.append((score, d))

            scored_drivers.sort(key=lambda x: x[0], reverse=True)
            ranked_drivers = [d for _, d in scored_drivers]

            top1_driver = ranked_drivers[0]
            top2_driver_ids = [d["driver_id"] for d in ranked_drivers[:2]]
            top3_driver_ids = [d["driver_id"] for d in ranked_drivers[:3]]

            # Esito reale della gara
            actual_p1 = next((d for d in drivers if d.get("target", {}).get("finish_position") == 1), None)
            actual_podium_drivers = [
                d for d in drivers
                if d.get("target", {}).get("finish_position") in (1, 2, 3)
            ]
            actual_podium_ids = {d["driver_id"] for d in actual_podium_drivers}

            if actual_p1 is None:
                continue

            s_races += 1
            actual_winner_id = actual_p1["driver_id"]

            # 1. Vincitore
            if top1_driver["driver_id"] == actual_winner_id:
                s_p1_hits += 1
            if actual_winner_id in top2_driver_ids:
                s_top2_hits += 1
            if actual_winner_id in top3_driver_ids:
                s_top3_hits += 1

            # 2. Favorito sul podio
            if top1_driver["driver_id"] in actual_podium_ids:
                s_fav_podium += 1

            # 3. Intersezione podio (quanti dei 3 pronosticati sono finiti sul podio reale)
            matched = len(set(top3_driver_ids).intersection(actual_podium_ids))
            s_podium_slots += matched
            s_podium_overlap[matched] += 1

        if s_races > 0:
            total_races += s_races
            winner_top1_hits += s_p1_hits
            winner_top2_hits += s_top2_hits
            winner_top3_hits += s_top3_hits
            favorite_on_podium_hits += s_fav_podium
            total_podium_slots_matched += s_podium_slots
            for k, count in s_podium_overlap.items():
                podium_overlap_counts[k] += count

            seasons_stats[year] = {
                "races": s_races,
                "winner_top1_hits": s_p1_hits,
                "winner_top1_pct": round((s_p1_hits / s_races) * 100, 1),
                "winner_top2_hits": s_top2_hits,
                "winner_top2_pct": round((s_top2_hits / s_races) * 100, 1),
                "winner_top3_hits": s_top3_hits,
                "winner_top3_pct": round((s_top3_hits / s_races) * 100, 1),
                "favorite_on_podium_hits": s_fav_podium,
                "favorite_on_podium_pct": round((s_fav_podium / s_races) * 100, 1),
                "avg_podium_matched": round(s_podium_slots / s_races, 2),
                "podium_distribution": {k: s_podium_overlap.get(k, 0) for k in range(4)},
            }

    return {
        "total_races": total_races,
        "winner_top1_hits": winner_top1_hits,
        "winner_top1_pct": round((winner_top1_hits / total_races) * 100, 1) if total_races else 0,
        "winner_top2_hits": winner_top2_hits,
        "winner_top2_pct": round((winner_top2_hits / total_races) * 100, 1) if total_races else 0,
        "winner_top3_hits": winner_top3_hits,
        "winner_top3_pct": round((winner_top3_hits / total_races) * 100, 1) if total_races else 0,
        "favorite_on_podium_hits": favorite_on_podium_hits,
        "favorite_on_podium_pct": round((favorite_on_podium_hits / total_races) * 100, 1) if total_races else 0,
        "avg_podium_matched": round(total_podium_slots_matched / total_races, 2) if total_races else 0,
        "podium_distribution": {k: podium_overlap_counts[k] for k in range(4)},
        "seasons": seasons_stats,
    }


if __name__ == "__main__":
    res = run_formula1_backtest()
    print("=" * 65)
    print("           REPORT BACKTEST FORMULA 1 (2016 - 2026)")
    print("=" * 65)
    print(f"Totale Gran Premi analizzati: {res['total_races']}")
    print(f"Vincitore esatto pronosticato (Top 1):  {res['winner_top1_hits']}/{res['total_races']} ({res['winner_top1_pct']}%)")
    print(f"Vincitore nei primi 2 pronosticati:    {res['winner_top2_hits']}/{res['total_races']} ({res['winner_top2_pct']}%)")
    print(f"Vincitore nei primi 3 pronosticati:    {res['winner_top3_hits']}/{res['total_races']} ({res['winner_top3_pct']}%)")
    print(f"Favorito (Top 1) a podio:              {res['favorite_on_podium_hits']}/{res['total_races']} ({res['favorite_on_podium_pct']}%)")
    print(f"Media piloti del podio indovinati:    {res['avg_podium_matched']} su 3 a gara")
    print("-" * 65)
    print("Distribuzione podio (quanti dei 3 piloti del podio indovinati per GP):")
    for k in range(4):
        c = res["podium_distribution"][k]
        pct = round(c / res["total_races"] * 100, 1)
        print(f"  • {k}/3 piloti azzeccati: {c} gare ({pct}%)")
    print("-" * 65)
    print("Dettaglio stagione per stagione:")
    for yr, s in res["seasons"].items():
        print(
            f"  • {yr}: {s['winner_top1_hits']}/{s['races']} vincitori ({s['winner_top1_pct']}%) | "
            f"Vincitore in Top 3: {s['winner_top3_pct']}% | "
            f"Favorito a podio: {s['favorite_on_podium_hits']}/{s['races']} ({s['favorite_on_podium_pct']}%) | "
            f"Media podio: {s['avg_podium_matched']}/3"
        )
    print("=" * 65)
