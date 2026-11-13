import json
import logging
from pathlib import Path

from .updater import default_data_directory

logger = logging.getLogger(__name__)


def build_formula1_view_payload(data_directory=None):
    """
    Costruisce il payload con:
    - Prossimo Gran Premio e probabilità/quote di vittoria per ogni pilota (logica quote calcio)
    - Classifica piloti e costruttori
    - Calendario completo con esiti e prossime tappe
    - Metadati generali di stagione
    """
    base_dir = Path(data_directory or default_data_directory())
    manifest_path = base_dir / "manifest.json"

    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest Formula 1 non trovato in {manifest_path}")

    with manifest_path.open(encoding="utf-8") as f:
        manifest = json.load(f)

    end_year = manifest.get("end_year")
    season_path = base_dir / "seasons" / f"{end_year}.json"
    if not season_path.is_file():
        raise FileNotFoundError(f"Dati stagione {end_year} non trovati in {season_path}")

    with season_path.open(encoding="utf-8") as f:
        season_data = json.load(f)

    schedule = season_data.get("schedule", [])
    results = season_data.get("results", [])
    standings = season_data.get("standings", {})

    # Mappa risultati per round
    results_by_round = {}
    for r in results:
        try:
            round_num = int(r.get("round", 0))
            results_by_round[round_num] = r
        except (ValueError, TypeError):
            continue

    completed_rounds = set(results_by_round.keys())

    # Trova il prossimo Gran Premio
    next_race_item = None
    for race in schedule:
        try:
            r_num = int(race.get("round", 0))
        except (ValueError, TypeError):
            continue
        if r_num not in completed_rounds:
            next_race_item = race
            break

    # Classifiche
    driver_standings_raw = []
    if standings.get("drivers") and isinstance(standings["drivers"], list):
        driver_standings_raw = standings["drivers"][-1].get("DriverStandings", [])

    constructor_standings_raw = []
    if standings.get("constructors") and isinstance(standings["constructors"], list):
        constructor_standings_raw = standings["constructors"][-1].get("ConstructorStandings", [])

    # Mappa punti costruttori
    team_points_map = {}
    constructor_standings = []
    for c in constructor_standings_raw:
        c_obj = c.get("Constructor", {})
        cid = c_obj.get("constructorId", "")
        pts = float(c.get("points", 0))
        team_points_map[cid] = pts
        constructor_standings.append({
            "position": int(c.get("position", len(constructor_standings) + 1)),
            "team_id": cid,
            "team": c_obj.get("name", "Team sconosciuto"),
            "nationality": c_obj.get("nationality", ""),
            "points": pts,
            "wins": int(c.get("wins", 0)),
        })

    # Classifica piloti normalizzata
    driver_standings = []
    driver_calc_items = []
    for d in driver_standings_raw:
        d_obj = d.get("Driver", {})
        c_list = d.get("Constructors", [])
        c_obj = c_list[0] if c_list else {}
        cid = c_obj.get("constructorId", "")
        pts = float(d.get("points", 0))
        wins = int(d.get("wins", 0))
        pos = int(d.get("position", len(driver_standings) + 1))
        first_name = d_obj.get("givenName", "")
        last_name = d_obj.get("familyName", "")
        full_name = f"{first_name} {last_name}".strip() or "Pilota"
        code = d_obj.get("code") or last_name[:3].upper()
        number = d_obj.get("permanentNumber") or ""

        driver_item = {
            "position": pos,
            "driver_id": d_obj.get("driverId", ""),
            "code": code,
            "number": number,
            "name": full_name,
            "nationality": d_obj.get("nationality", ""),
            "team_id": cid,
            "team": c_obj.get("name", "Privato"),
            "points": pts,
            "wins": wins,
        }
        driver_standings.append(driver_item)

        # Formula di forza (rating pre-gara):
        # combina punti pilota (45%), vittorie (35%) e forza della monoposto (20%)
        t_pts = team_points_map.get(cid, 0.0)
        score = max((pts * 0.45 + wins * 25.0 * 0.35 + (t_pts / 2.0) * 0.20), 1.0)
        driver_calc_items.append({
            "driver": driver_item,
            "score": score,
        })

    # Calcolo quote e probabilità per il prossimo Gran Premio
    predictions = []
    if next_race_item and driver_calc_items:
        # Esponenziale/Softmax normalizzato
        exp_sum = sum(item["score"] ** 2.2 for item in driver_calc_items)
        for item in driver_calc_items:
            prob = (item["score"] ** 2.2) / exp_sum if exp_sum > 0 else (1.0 / len(driver_calc_items))
            prob_pct = round(prob * 100, 1)
            # Quota decimale stile scommessa sportiva (1 / p)
            odds = round(1.0 / prob, 2) if prob > 0 else 999.0
            odds = min(odds, 999.0)

            d = item["driver"]
            predictions.append({
                "driver_id": d["driver_id"],
                "code": d["code"],
                "number": d["number"],
                "name": d["name"],
                "team": d["team"],
                "points": d["points"],
                "wins": d["wins"],
                "win_probability": prob_pct,
                "odds": odds,
            })

        predictions.sort(key=lambda x: x["win_probability"], reverse=True)
        for rank, p in enumerate(predictions, start=1):
            p["rank"] = rank

    next_race = None
    if next_race_item:
        circ = next_race_item.get("Circuit", {})
        loc = circ.get("Location", {})
        next_race = {
            "round": int(next_race_item.get("round", 0)),
            "race_name": next_race_item.get("raceName", ""),
            "circuit_name": circ.get("circuitName", ""),
            "locality": loc.get("locality", ""),
            "country": loc.get("country", ""),
            "date": next_race_item.get("date", ""),
            "time": next_race_item.get("time", ""),
            "predictions": predictions,
        }

    # Calendario della stagione
    calendar = []
    for race in schedule:
        try:
            r_num = int(race.get("round", 0))
        except (ValueError, TypeError):
            continue

        circ = race.get("Circuit", {})
        loc = circ.get("Location", {})
        is_completed = r_num in completed_rounds
        is_next = (next_race is not None and r_num == next_race["round"])

        winner_info = None
        if is_completed:
            race_res = results_by_round.get(r_num, {})
            entries = race_res.get("Results", [])
            if entries:
                first = entries[0]
                d_win = first.get("Driver", {})
                c_win = first.get("Constructor", {})
                w_first = d_win.get("givenName", "")
                w_last = d_win.get("familyName", "")
                winner_info = {
                    "name": f"{w_first} {w_last}".strip(),
                    "code": d_win.get("code") or w_last[:3].upper(),
                    "team": c_win.get("name", ""),
                    "laps": first.get("laps"),
                    "time": first.get("Time", {}).get("time", first.get("status", "Concluso")),
                }

        status = "completed" if is_completed else ("next" if is_next else "upcoming")
        calendar.append({
            "round": r_num,
            "race_name": race.get("raceName", ""),
            "circuit_name": circ.get("circuitName", ""),
            "locality": loc.get("locality", ""),
            "country": loc.get("country", ""),
            "date": race.get("date", ""),
            "time": race.get("time", ""),
            "status": status,
            "winner": winner_info,
        })

    leader = driver_standings[0] if driver_standings else None

    return {
        "season": end_year,
        "generated_at": manifest.get("generated_at"),
        "total_races": len(schedule),
        "completed_races": len(completed_rounds),
        "remaining_races": max(0, len(schedule) - len(completed_rounds)),
        "leader": leader,
        "next_race": next_race,
        "driver_standings": driver_standings,
        "constructor_standings": constructor_standings,
        "calendar": calendar,
    }
