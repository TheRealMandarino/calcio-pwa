import argparse
import math
import re
from collections import defaultdict
from datetime import date

from backend.tennis.model import (
    MAX_FATIGUE_SHIFT,
    TennisModelError,
    _finalize_player,
    _missing_player_detail,
    add_player_match,
    empty_stats,
    head_to_head,
    parse_matches,
    recent_load,
    simulate_match,
    surface_display_name,
)
from backend.tennis.tennis_history import fetch_season_csv


DEFAULT_HISTORY_YEARS = 5
INCOMPLETE_SCORE = re.compile(r"\b(W/?O|WO|DEF|ABD)\b", re.IGNORECASE)


def is_incomplete_score(score):
    return not str(score or "").strip() or bool(INCOMPLETE_SCORE.search(str(score)))


def load_tour_matches(tour, start_year, end_year, fetch_season=fetch_season_csv):
    matches = []
    for year in range(start_year, end_year + 1):
        csv_text = fetch_season(tour, year).decode("utf-8-sig")
        season_matches = parse_matches(csv_text, strict=False)
        for index, match in enumerate(season_matches):
            match["tour"] = tour
            match["season"] = year
            match["source_index"] = (year, index)
            matches.append(match)
    matches.sort(key=lambda match: (
        match["date"] or date.max,
        match["source_index"],
    ))
    return matches


def _best_of(match):
    value = match.get("best_of")
    if value in {3, 5, 3.0, 5.0}:
        return int(value)
    return 3


def _tournament_minutes(history, match, player_key):
    total = 0.0
    for previous in history:
        if previous["date"] != match["date"]:
            continue
        if previous["tournament"] != match["tournament"]:
            continue
        if previous["winner_key"] != player_key and previous["loser_key"] != player_key:
            continue
        total += previous["minutes"] or 0
    return total


class WalkForwardBook:
    def __init__(self):
        self.matches = []
        self.surfaces = defaultdict(lambda: {
            "players": {},
            "matches": [],
            "service_points": 0.0,
            "service_points_won": 0.0,
            "return_points": 0.0,
            "return_points_total": 0.0,
        })

    def add(self, match):
        self.matches.append(match)
        surface = match.get("surface")
        if not surface:
            return
        state = self.surfaces[surface]
        state["matches"].append(match)
        for name, key, won in (
            (match["winner_name"], match["winner_key"], True),
            (match["loser_name"], match["loser_key"], False),
        ):
            player = state["players"].setdefault(
                key, {"name": name, "key": key, **empty_stats()}
            )
            if won:
                player["wins"] += 1
            side = match["winner"] if won else match["loser"]
            opponent = match["loser"] if won else match["winner"]
            add_player_match(player, side, opponent)
        for player in (match["winner"], match["loser"]):
            if (
                player["service_points"] is not None
                and player["service_points_won"] is not None
            ):
                state["service_points"] += player["service_points"]
                state["service_points_won"] += player["service_points_won"]
            if (
                player["return_points"] is not None
                and player["return_points_total"] is not None
            ):
                state["return_points"] += player["return_points"]
                state["return_points_total"] += player["return_points_total"]

    def _player(self, surface, key):
        state = self.surfaces.get(surface)
        if not state or key not in state["players"]:
            return None
        global_serve = (
            state["service_points_won"] / state["service_points"]
            if state["service_points"]
            else None
        )
        global_return = (
            state["return_points"] / state["return_points_total"]
            if state["return_points_total"]
            else None
        )
        return _finalize_player(dict(state["players"][key]), global_serve, global_return)

    def predict(self, match):
        player_a_name, player_b_name = sorted(
            (match["winner_name"], match["loser_name"]),
            key=str.casefold,
        )
        player_a = self._player(match["surface"], _key_for(match, player_a_name))
        player_b = self._player(match["surface"], _key_for(match, player_b_name))
        if not player_a or not player_b:
            details = [
                detail for detail in (
                    _missing_player_detail(self.matches, player_a_name, player_a),
                    _missing_player_detail(self.matches, player_b_name, player_b),
                )
                if detail
            ]
            raise TennisModelError(
                "Nessun match storico per uno o entrambi i giocatori su "
                f"{surface_display_name(match['surface'])}. {'; '.join(details)}. "
                "Controlla i nomi e seleziona la superficie reale del match; "
                "le superfici non vengono sostituite automaticamente."
            )
        if (
            player_a["serve_point_rate"] is None
            or player_b["serve_point_rate"] is None
            or player_a["return_point_rate"] is None
            or player_b["return_point_rate"] is None
        ):
            raise TennisModelError(
                "Servono statistiche aggregate dei punti al servizio per entrambi "
                "i giocatori su questa superficie."
            )

        recent_a = recent_load(self.matches, player_a["key"], match["date"])
        recent_b = recent_load(self.matches, player_b["key"], match["date"])
        minutes_a = _tournament_minutes(self.matches, match, player_a["key"])
        minutes_b = _tournament_minutes(self.matches, match, player_b["key"])
        burden_a = recent_a["minutes"] + recent_a["matches"] * 45 + minutes_a
        burden_b = recent_b["minutes"] + recent_b["matches"] * 45 + minutes_b
        fatigue_shift = MAX_FATIGUE_SHIFT * math.tanh((burden_b - burden_a) / 360)
        point_a = (player_a["serve_point_rate"] + (1 - player_b["return_point_rate"])) / 2
        point_b = (player_b["serve_point_rate"] + (1 - player_a["return_point_rate"])) / 2
        point_a = min(0.9, max(0.1, point_a - fatigue_shift))
        point_b = min(0.9, max(0.1, point_b + fatigue_shift))
        prediction = simulate_match(point_a, point_b, _best_of(match), 21.5)
        return {
            "player_a": player_a_name,
            "player_b": player_b_name,
            "prediction": prediction,
            "h2h": head_to_head(
                self.surfaces[match["surface"]]["matches"], player_a, player_b
            ),
        }


def _key_for(match, name):
    if name == match["winner_name"]:
        return match["winner_key"]
    return match["loser_key"]


def _skip_reason(match):
    if match["surface"] is None:
        return "superficie non riconosciuta"
    if match["date"] is None:
        return "data assente"
    if is_incomplete_score(match["score"]):
        return "incontro non completato"
    return None


def _confidence_buckets(predictions):
    """Raggruppa le predizioni per fascia di confidenza del favorito,
    con scomposizione dettagliata per i match vicini (es. 50-52%, 52-55%).
    """
    edges = [0.50, 0.52, 0.55, 0.60, 0.65, 0.70, 0.80, 1.01]
    labels = ["50-52%", "52-55%", "55-60%", "60-65%", "65-70%", "70-80%", "80%+"]
    buckets = {label: {"total": 0, "correct": 0} for label in labels}
    for item in predictions:
        p = item["favorite_probability"]
        for i, upper in enumerate(edges[1:]):
            if p < upper:
                buckets[labels[i]]["total"] += 1
                if item["correct"]:
                    buckets[labels[i]]["correct"] += 1
                break
    result = {}
    for label in labels:
        b = buckets[label]
        result[label] = {
            "total": b["total"],
            "correct": b["correct"],
            "accuracy": 100.0 * b["correct"] / b["total"] if b["total"] else None,
        }
    return result


def _metrics(predictions):
    count = len(predictions)
    if not count:
        return {
            "predictions": 0,
            "correct": 0,
            "accuracy": 0.0,
            "brier": 0.0,
            "log_loss": 0.0,
            "avg_favorite_probability": 0.0,
            "by_confidence": {},
        }
    correct = sum(1 for item in predictions if item["correct"])
    return {
        "predictions": count,
        "correct": correct,
        "accuracy": 100.0 * correct / count,
        "brier": sum(item["brier"] for item in predictions) / count,
        "log_loss": sum(item["log_loss"] for item in predictions) / count,
        "avg_favorite_probability": sum(
            item["favorite_probability"] for item in predictions
        ) / count,
        "by_confidence": _confidence_buckets(predictions),
    }


def run_tennis_backtest(
    tours=("atp", "wta"),
    year=None,
    history_years=DEFAULT_HISTORY_YEARS,
    fetch_season=fetch_season_csv,
):
    target_year = date.today().year if year is None else year
    start_year = target_year - history_years + 1
    reports = {}
    for tour in tours:
        matches = load_tour_matches(tour, start_year, target_year, fetch_season)
        book = WalkForwardBook()
        evaluated = []
        skipped = defaultdict(int)
        for match in matches:
            if match["season"] == target_year:
                reason = _skip_reason(match)
                if reason:
                    skipped[reason] += 1
                else:
                    try:
                        analysis = book.predict(match)
                    except TennisModelError as error:
                        message = str(error)
                        if "Nessun match storico" in message:
                            skipped["storico insufficiente sulla superficie"] += 1
                        elif "statistiche aggregate" in message:
                            skipped["statistiche servizio assenti"] += 1
                        else:
                            skipped["modello non applicabile"] += 1
                    else:
                        prediction = analysis["prediction"]
                        p_winner = (
                            prediction["win_a"]
                            if analysis["player_a"] == match["winner_name"]
                            else prediction["win_b"]
                        )
                        p_winner = min(1 - 1e-15, max(1e-15, p_winner))
                        if prediction["win_a"] > prediction["win_b"]:
                            favorite = analysis["player_a"]
                        elif prediction["win_b"] > prediction["win_a"]:
                            favorite = analysis["player_b"]
                        else:
                            favorite = None
                        evaluated.append({
                            "date": match["date"].isoformat(),
                            "tournament": match["tournament"],
                            "surface": match["surface"],
                            "winner": match["winner_name"],
                            "loser": match["loser_name"],
                            "predicted_winner": favorite,
                            "p_winner": p_winner,
                            "favorite_probability": max(
                                prediction["win_a"], prediction["win_b"]
                            ),
                            "correct": favorite == match["winner_name"],
                            "brier": (p_winner - 1) ** 2 + (1 - p_winner) ** 2,
                            "log_loss": -math.log(p_winner),
                        })
            book.add(match)

        by_surface = {
            surface: _metrics([item for item in evaluated if item["surface"] == surface])
            for surface in sorted({item["surface"] for item in evaluated})
        }
        reports[tour] = {
            "tour": tour,
            "year": target_year,
            "history_years": list(range(start_year, target_year + 1)),
            "matches_available": sum(
                1 for item in matches if item["season"] == target_year
            ),
            "skipped": dict(skipped),
            "surfaces": by_surface,
            **_metrics(evaluated),
        }
    return reports


def format_report(reports):
    lines = []
    for tour, report in reports.items():
        lines.append(f"BACKTEST WALK-FORWARD TENNIS - {tour.upper()} {report['year']}")
        lines.append(
            "Modello a stati del modulo tennis; statistiche, fatica e H2H usano "
            "solo match precedenti."
        )
        lines.append(
            "Le date sono quelle di inizio torneo dei CSV Sackmann, non il giorno "
            "esatto del match."
        )
        lines.append(
            f"  Storico usato             : {report['history_years'][0]}-"
            f"{report['history_years'][-1]}"
        )
        lines.append(f"  Match della stagione       : {report['matches_available']}")
        lines.append(f"  Pronostici valutati        : {report['predictions']}")
        lines.append(
            f"  Esiti corretti             : {report['correct']}/"
            f"{report['predictions']}"
        )
        lines.append(f"  Accuratezza vincitore      : {report['accuracy']:.2f}%")
        lines.append(f"  Brier score                : {report['brier']:.4f}")
        lines.append(f"  Log loss                   : {report['log_loss']:.4f}")
        lines.append(
            f"  Prob. media del favorito   : {report['avg_favorite_probability'] * 100:.2f}%"
        )
        if report["skipped"]:
            lines.append("  Esclusi")
            for reason, count in sorted(report["skipped"].items()):
                lines.append(f"    {reason}: {count}")
        if report["surfaces"]:
            lines.append("  Per superficie")
            for surface, metrics in report["surfaces"].items():
                lines.append(
                    f"    {surface}: {metrics['correct']}/{metrics['predictions']} "
                    f"({metrics['accuracy']:.2f}%)"
                )
        if report.get("by_confidence"):
            lines.append("  Per fascia di confidenza del favorito")
            lines.append(
                f"    {'Fascia':<10}  {'Match':>5}  {'Corretti':>8}  {'Accuratezza':>11}"
            )
            for label, data in report["by_confidence"].items():
                if data["total"] == 0:
                    continue
                acc_str = f"{data['accuracy']:.1f}%" if data["accuracy"] is not None else "  —"
                lines.append(
                    f"    {label:<10}  {data['total']:>5}  {data['correct']:>8}  {acc_str:>11}"
                )
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Backtest walk-forward del modello tennis: quante volte l'esito "
            "e' stato previsto correttamente."
        )
    )
    parser.add_argument(
        "--tour",
        choices=("atp", "wta", "both"),
        default="both",
        help="circuito da valutare (default: atp e wta)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="stagione da valutare (default: anno corrente)",
    )
    parser.add_argument(
        "--history-years",
        type=int,
        default=DEFAULT_HISTORY_YEARS,
        help="quante stagioni di storico usare, inclusa quella valutata",
    )
    args = parser.parse_args(argv)
    tours = ("atp", "wta") if args.tour == "both" else (args.tour,)
    reports = run_tennis_backtest(
        tours=tours,
        year=args.year,
        history_years=args.history_years,
    )
    print(format_report(reports))


if __name__ == "__main__":
    main()