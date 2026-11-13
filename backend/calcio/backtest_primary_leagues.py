import math
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

from backend.calcio.model import calculate_match_probabilities


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "data" / "database.db"
LEAGUES = (
    "ITA-Serie A",
    "ENG-Premier League",
    "ESP-La Liga",
    "GER-Bundesliga",
    "FRA-Ligue 1",
)
MARKETS = ("1", "X", "2")
MIN_HISTORY = 30


def load_matches(db_path=DEFAULT_DB_PATH, leagues=LEAGUES):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in leagues)
    try:
        rows = conn.execute("""
            SELECT date, league, home_team, away_team, home_goals, away_goals, season
            FROM matches
            WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
              AND league IN (""" + placeholders + """)
            ORDER BY date, league, home_team, away_team
        """, tuple(leagues)).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def assign_matchdays(matches):
    matches_by_season = defaultdict(list)
    for match in matches:
        matches_by_season[match["season"]].append(match)

    for season_matches in matches_by_season.values():
        rounds = []
        current_round = []
        current_teams = set()
        round_start = None

        for match in season_matches:
            match_date = date.fromisoformat(match["date"][:10])
            match_teams = {match["home_team"], match["away_team"]}
            if current_round and (
                (match_date - round_start).days > 6
                or current_teams.intersection(match_teams)
            ):
                rounds.append(current_round)
                current_round = []
                current_teams = set()
                round_start = None

            if not current_round:
                round_start = match_date
            current_round.append(match)
            current_teams.update(match_teams)

        if current_round:
            rounds.append(current_round)

        for matchday, round_matches in enumerate(rounds, start=1):
            for match in round_matches:
                match["matchday"] = matchday


def summarize(predictions):
    if not predictions:
        return {"predictions": 0, "accuracy": 0.0, "brier": 0.0, "log_loss": 0.0}
    return {
        "predictions": len(predictions),
        "accuracy": sum(row["correct"] for row in predictions) / len(predictions) * 100,
        "brier": sum(row["brier"] for row in predictions) / len(predictions),
        "log_loss": sum(row["log_loss"] for row in predictions) / len(predictions),
    }


def score_prediction(match, model):
    if match["home_goals"] > match["away_goals"]:
        actual = "1"
    elif match["home_goals"] == match["away_goals"]:
        actual = "X"
    else:
        actual = "2"

    probabilities = {
        "1": model["prob_1"],
        "X": model["prob_X"],
        "2": model["prob_2"],
    }
    predicted = max(MARKETS, key=probabilities.get)
    return {
        "correct": predicted == actual,
        "predicted": predicted,
        "actual": actual,
        "brier": sum(
            (probabilities[market] - (1 if market == actual else 0)) ** 2
            for market in MARKETS
        ),
        "log_loss": -math.log(max(probabilities[actual], 1e-15)),
    }


def longest_losing_streak(predictions):
    longest = []
    current = []
    for prediction in predictions:
        if prediction["correct"]:
            current = []
        else:
            current.append(prediction)
            if len(current) > len(longest):
                longest = current.copy()

    return {
        "length": len(longest),
        "from": longest[0]["date"][:10] if longest else None,
        "to": longest[-1]["date"][:10] if longest else None,
        "matches": [
            f'{prediction["home_team"]} - {prediction["away_team"]}'
            for prediction in longest
        ],
    }


def update_h2h_totals(totals, matches):
    for match in matches:
        pair = tuple(sorted((match["home_team"], match["away_team"])))
        entry = totals.setdefault(
            pair,
            {"matches": 0, "goals_for": defaultdict(float)},
        )
        entry["matches"] += 1
        entry["goals_for"][match["home_team"]] += match["home_goals"]
        entry["goals_for"][match["away_team"]] += match["away_goals"]


def get_cached_h2h_factor(totals, home_team, away_team):
    entry = totals.get(tuple(sorted((home_team, away_team))))
    if entry is None or entry["matches"] < 2:
        return 1.0, 1.0

    total = entry["matches"]
    avg_home = entry["goals_for"][home_team] / total
    avg_away = entry["goals_for"][away_team] / total
    factor_home = max(0.95, min(1.05, 1.0 + ((avg_home - 1.3) * 0.03)))
    factor_away = max(0.95, min(1.05, 1.0 + ((avg_away - 1.1) * 0.03)))
    return factor_home, factor_away


def compute_primary_stats(
    home_team_ids,
    away_team_ids,
    league_ids,
    home_goals,
    away_goals,
    match_dates,
    teams,
    leagues,
    half_life_days=90,
):
    if not len(match_dates):
        return {}, {}, {}, {}, {}, {}, 1.3, 1.1

    age_days = (match_dates[-1] - match_dates).astype("timedelta64[D]").astype(int)
    weights = np.maximum(np.exp(-np.log(2) * age_days / half_life_days), 0.1)
    team_count = len(teams)
    league_count = len(leagues)

    def totals(indices, values, size):
        return np.bincount(indices, weights=values, minlength=size)

    home_weights = totals(home_team_ids, weights, team_count)
    away_weights = totals(away_team_ids, weights, team_count)
    home_attack_sums = totals(home_team_ids, home_goals * weights, team_count)
    home_defense_sums = totals(home_team_ids, away_goals * weights, team_count)
    away_attack_sums = totals(away_team_ids, away_goals * weights, team_count)
    away_defense_sums = totals(away_team_ids, home_goals * weights, team_count)

    league_weights = totals(league_ids, weights, league_count)
    league_home_goals = totals(league_ids, home_goals * weights, league_count)
    league_away_goals = totals(league_ids, away_goals * weights, league_count)
    avg_home = {
        league: league_home_goals[index] / league_weights[index]
        for index, league in enumerate(leagues)
        if league_weights[index]
    }
    avg_away = {
        league: league_away_goals[index] / league_weights[index]
        for index, league in enumerate(leagues)
        if league_weights[index]
    }
    total_weight = league_weights.sum()
    global_avg_home = league_home_goals.sum() / total_weight if total_weight else 1.3
    global_avg_away = league_away_goals.sum() / total_weight if total_weight else 1.1

    home_attack = {
        team: home_attack_sums[index] / home_weights[index]
        for index, team in enumerate(teams)
        if home_weights[index]
    }
    home_defense = {
        team: home_defense_sums[index] / home_weights[index]
        for index, team in enumerate(teams)
        if home_weights[index]
    }
    away_attack = {
        team: away_attack_sums[index] / away_weights[index]
        for index, team in enumerate(teams)
        if away_weights[index]
    }
    away_defense = {
        team: away_defense_sums[index] / away_weights[index]
        for index, team in enumerate(teams)
        if away_weights[index]
    }
    return (
        home_attack,
        home_defense,
        away_attack,
        away_defense,
        avg_home,
        avg_away,
        global_avg_home,
        global_avg_away,
    )


def run_backtest(db_path=DEFAULT_DB_PATH, min_history=MIN_HISTORY, leagues=LEAGUES):
    leagues = tuple(leagues)
    matches = load_matches(db_path, leagues)
    if not matches:
        raise ValueError("Non ci sono risultati conclusi nel database.")
    assign_matchdays(matches)

    matches_by_date = defaultdict(list)
    for match in matches:
        match["home_goals"] = int(match["home_goals"])
        match["away_goals"] = int(match["away_goals"])
        matches_by_date[match["date"][:10]].append(match)

    ordered_matches = [
        match
        for match_date in sorted(matches_by_date)
        for match in matches_by_date[match_date]
    ]
    teams = sorted({
        team
        for match in ordered_matches
        for team in (match["home_team"], match["away_team"])
    })
    leagues = sorted({match["league"] for match in ordered_matches})
    team_ids = {team: index for index, team in enumerate(teams)}
    league_ids = {league: index for index, league in enumerate(leagues)}
    home_team_ids = np.array([team_ids[match["home_team"]] for match in ordered_matches])
    away_team_ids = np.array([team_ids[match["away_team"]] for match in ordered_matches])
    match_league_ids = np.array([league_ids[match["league"]] for match in ordered_matches])
    home_goals = np.array([match["home_goals"] for match in ordered_matches], dtype=float)
    away_goals = np.array([match["away_goals"] for match in ordered_matches], dtype=float)
    match_dates = np.array(
        [np.datetime64(match["date"][:10]) for match in ordered_matches],
        dtype="datetime64[D]",
    )
    date_offsets = {}
    offset = 0
    for match_date in sorted(matches_by_date):
        offset += len(matches_by_date[match_date])
        date_offsets[match_date] = offset

    h2h_totals = {}
    predictions = {league: [] for league in leagues}
    predictions_by_season = {
        league: defaultdict(list)
        for league in leagues
    }

    for match_date in sorted(matches_by_date):
        todays_matches = matches_by_date[match_date]
        test_matches = [match for match in todays_matches if match["league"] in leagues]
        history_count = date_offsets[match_date] - len(todays_matches)
        if history_count >= min_history and test_matches:
            stats = compute_primary_stats(
                home_team_ids[:history_count],
                away_team_ids[:history_count],
                match_league_ids[:history_count],
                home_goals[:history_count],
                away_goals[:history_count],
                match_dates[:history_count],
                teams,
                leagues,
            )
            for match in test_matches:
                model = calculate_match_probabilities(
                    match["home_team"],
                    match["away_team"],
                    league=match["league"],
                    precomputed_stats=stats,
                    h2h_factor=get_cached_h2h_factor(
                        h2h_totals,
                        match["home_team"],
                        match["away_team"],
                    ),
                )
                prediction = score_prediction(match, model)
                prediction.update({
                    "date": match["date"],
                    "season": match["season"],
                    "matchday": match["matchday"],
                    "home_team": match["home_team"],
                    "away_team": match["away_team"],
                })
                predictions[match["league"]].append(prediction)
                predictions_by_season[match["league"]][match["season"]].append(prediction)

        update_h2h_totals(h2h_totals, todays_matches)

    reports = {}
    for league in leagues:
        league_matches = [match for match in matches if match["league"] == league]
        matchday_predictions = defaultdict(list)
        season_predictions = defaultdict(list)
        for prediction in predictions[league]:
            matchday_predictions[
                (prediction["season"], prediction["matchday"])
            ].append(prediction)
            season_predictions[prediction["season"]].append(prediction)

        reports[league] = {
            "matches_available": len(league_matches),
            "first_date": league_matches[0]["date"][:10] if league_matches else None,
            "last_date": league_matches[-1]["date"][:10] if league_matches else None,
            **summarize(predictions[league]),
            "longest_losing_streak": longest_losing_streak(predictions[league]),
            "matchdays": [
                {
                    "season": season,
                    "matchday": matchday,
                    "from": min(row["date"][:10] for row in rows),
                    "to": max(row["date"][:10] for row in rows),
                    "predictions": len(rows),
                    "correct": sum(row["correct"] for row in rows),
                    "accuracy": summarize(rows)["accuracy"],
                    "longest_losing_streak": longest_losing_streak(rows)["length"],
                }
                for (season, matchday), rows in sorted(matchday_predictions.items())
            ],
            "seasons": {
                season: {
                    **summarize(rows),
                    "longest_losing_streak": longest_losing_streak(rows),
                }
                for season, rows in sorted(season_predictions.items())
            },
        }
    return reports


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backtest walk-forward dei campionati principali.")
    parser.add_argument(
        "--league",
        choices=LEAGUES,
        help="Limita l'analisi a un campionato, per esempio ITA-Serie A.",
    )
    args = parser.parse_args()
    leagues = (args.league,) if args.league else LEAGUES
    reports = run_backtest(leagues=leagues)
    print(f"BACKTEST WALK-FORWARD - {', '.join(leagues)}")
    print("Poisson + Dixon-Coles; statistiche e H2H usano solo risultati precedenti.")
    print("Le partite dello stesso giorno non entrano nello storico dei pronostici.")
    for league, report in reports.items():
        print(f"\n{league} ({report['first_date']} - {report['last_date']})")
        print(f"  Partite concluse disponibili: {report['matches_available']}")
        print(f"  Pronostici valutati        : {report['predictions']}")
        print(f"  Accuratezza 1X2            : {report['accuracy']:.2f}%")
        print(f"  Brier score                : {report['brier']:.4f}")
        print(f"  Log loss                   : {report['log_loss']:.4f}")
        streak = report["longest_losing_streak"]
        print(
            f"  Massima striscia errata   : {streak['length']} "
            f"({streak['from']} - {streak['to']})"
        )
        if streak["matches"]:
            print(f"    {'; '.join(streak['matches'])}")
        for season, metrics in report["seasons"].items():
            season_streak = metrics["longest_losing_streak"]
            print(
                f"    {season}: n={metrics['predictions']}, "
                f"accuracy={metrics['accuracy']:.2f}%, "
                f"Brier={metrics['brier']:.4f}, "
                f"log loss={metrics['log_loss']:.4f}, "
                f"striscia max={season_streak['length']} "
                f"({season_streak['from']} - {season_streak['to']})"
            )
        if args.league:
            latest_season = report["matchdays"][-1]["season"]
            print(
                f"  Turni {latest_season} (ricostruiti da date e squadre; "
                "non presenti come campo ufficiale nel database)"
            )
            print("  Giornata    Intervallo                  Corretti/Pronostici  Accuracy  Striscia")
            for matchday in report["matchdays"]:
                if matchday["season"] != latest_season:
                    continue
                print(
                    f"  G{matchday['matchday']:02d}         "
                    f"{matchday['from']} - {matchday['to']}   "
                    f"{matchday['correct']:>2}/{matchday['predictions']:<2}                "
                    f"{matchday['accuracy']:>5.1f}%      "
                    f"{matchday['longest_losing_streak']}"
                )


if __name__ == "__main__":
    main()
