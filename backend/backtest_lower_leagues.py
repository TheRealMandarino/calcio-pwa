import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

from backend.model import calculate_match_probabilities, get_h2h_factor
from backend.stats import compute_team_stats


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT_DIR / "data" / "database.db"
LEAGUES = ("ITA-Serie B", "ENG-Championship")
MARKETS = ("1", "X", "2")
MIN_HISTORY = 30
DEFAULT_PARAMETERS = {
    "half_life_days": 90,
    "shrinkage": 0.4,
    "home_advantage": 1.05,
    "rho": -0.11,
    "use_h2h": True,
}
TUNING_GRID = [
    {
        "half_life_days": half_life,
        "shrinkage": shrinkage,
        "home_advantage": home_advantage,
        "rho": rho,
        "use_h2h": use_h2h,
    }
    for half_life in (60, 90, 180)
    for shrinkage in (0.2, 0.4, 0.6)
    for home_advantage in (1.0, 1.05)
    for rho in (-0.11, -0.05, 0.0)
    for use_h2h in (False, True)
]


def load_matches(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT date, league, home_team, away_team, home_goals, away_goals,
                   home_xg, away_xg, season
            FROM matches
            WHERE league IN (?, ?)
              AND home_goals IS NOT NULL
              AND away_goals IS NOT NULL
            ORDER BY league, date, home_team, away_team
        """, LEAGUES).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def outcome_for_match(match):
    if match["home_goals"] > match["away_goals"]:
        return "1"
    if match["home_goals"] == match["away_goals"]:
        return "X"
    return "2"


def summarize_predictions(predictions):
    if not predictions:
        return {
            "predictions": 0,
            "accuracy": 0.0,
            "brier": 0.0,
            "log_loss": 0.0,
            "calibration": [],
        }

    calibration_bins = [
        {"lower": index / 10, "upper": (index + 1) / 10, "count": 0, "confidence": 0.0, "accuracy": 0.0}
        for index in range(10)
    ]
    for prediction in predictions:
        bin_index = min(int(prediction["confidence"] * 10), 9)
        bucket = calibration_bins[bin_index]
        bucket["count"] += 1
        bucket["confidence"] += prediction["confidence"]
        bucket["accuracy"] += prediction["correct"]

    calibration = [
        {
            "range": f"{bucket['lower']:.1f}-{bucket['upper']:.1f}",
            "count": bucket["count"],
            "confidence": bucket["confidence"] / bucket["count"],
            "accuracy": bucket["accuracy"] / bucket["count"],
        }
        for bucket in calibration_bins
        if bucket["count"]
    ]

    return {
        "predictions": len(predictions),
        "accuracy": sum(item["correct"] for item in predictions) / len(predictions) * 100,
        "brier": sum(item["brier"] for item in predictions) / len(predictions),
        "log_loss": sum(item["log_loss"] for item in predictions) / len(predictions),
        "calibration": calibration,
    }


def run_backtest(db_path=DEFAULT_DB_PATH, min_history=MIN_HISTORY):
    matches = load_matches(db_path)
    if not matches:
        raise ValueError("Non ci sono risultati storici per Serie B o Championship nel database.")

    matches_by_league = defaultdict(list)
    for match in matches:
        match["home_goals"] = int(match["home_goals"])
        match["away_goals"] = int(match["away_goals"])
        matches_by_league[match["league"]].append(match)

    reports = {}
    for league in LEAGUES:
        history = []
        predictions = []
        season_predictions = defaultdict(list)
        matches_by_date = defaultdict(list)
        for match in matches_by_league[league]:
            matches_by_date[match["date"][:10]].append(match)

        for match_date in sorted(matches_by_date):
            todays_matches = matches_by_date[match_date]
            if len(history) >= min_history:
                history_frame = pd.DataFrame(history)
                stats = compute_team_stats(matches_df=history_frame, league=league)
                for match in todays_matches:
                    model = calculate_match_probabilities(
                        match["home_team"],
                        match["away_team"],
                        league=league,
                        precomputed_stats=stats,
                        historical_matches=history,
                    )
                    probabilities = {
                        "1": model["prob_1"],
                        "X": model["prob_X"],
                        "2": model["prob_2"],
                    }
                    result = outcome_for_match(match)
                    predicted = max(MARKETS, key=probabilities.get)
                    confidence = probabilities[predicted]
                    prediction = {
                        "correct": predicted == result,
                        "brier": sum(
                            (probabilities[market] - (1 if market == result else 0)) ** 2
                            for market in MARKETS
                        ),
                        "log_loss": -math.log(max(probabilities[result], 1e-15)),
                        "confidence": confidence,
                    }
                    predictions.append(prediction)
                    season_predictions[match["season"]].append(prediction)

            history.extend(todays_matches)

        reports[league] = {
            "matches_available": len(matches_by_league[league]),
            "first_date": matches_by_league[league][0]["date"][:10],
            "last_date": matches_by_league[league][-1]["date"][:10],
            **summarize_predictions(predictions),
            "seasons": {
                season: summarize_predictions(season_predictions[season])
                for season in sorted(season_predictions)
            },
        }
    return reports


def evaluate_season(league, matches, season, parameters, min_history=MIN_HISTORY):
    history = sorted(
        (match for match in matches if match["season"] < season),
        key=lambda match: match["date"],
    )
    predictions = []
    stats_cache = {}
    h2h_cache = {}
    matches_by_date = defaultdict(list)
    for match in matches:
        if match["season"] == season:
            matches_by_date[match["date"][:10]].append(match)

    for match_date in sorted(matches_by_date):
        todays_matches = matches_by_date[match_date]
        if len(history) >= min_history:
            history_frame = pd.DataFrame(history)
            stats_key = (match_date, parameters["half_life_days"])
            if stats_key not in stats_cache:
                stats_cache[stats_key] = compute_team_stats(
                    matches_df=history_frame,
                    half_life_days=parameters["half_life_days"],
                    league=league,
                )

            for match in todays_matches:
                pair_key = (match_date, match["home_team"], match["away_team"])
                if not parameters["use_h2h"]:
                    h2h_factor = (1.0, 1.0)
                else:
                    if pair_key not in h2h_cache:
                        h2h_cache[pair_key] = get_h2h_factor(
                            match["home_team"],
                            match["away_team"],
                            league=league,
                            historical_matches=history_frame,
                        )
                    h2h_factor = h2h_cache[pair_key]

                model = calculate_match_probabilities(
                    match["home_team"],
                    match["away_team"],
                    league=league,
                    precomputed_stats=stats_cache[stats_key],
                    model_parameters=parameters,
                    h2h_factor=h2h_factor,
                )
                probabilities = {
                    "1": model["prob_1"],
                    "X": model["prob_X"],
                    "2": model["prob_2"],
                }
                result = outcome_for_match(match)
                predicted = max(MARKETS, key=probabilities.get)
                predictions.append({
                    "correct": predicted == result,
                    "brier": sum(
                        (probabilities[market] - (1 if market == result else 0)) ** 2
                        for market in MARKETS
                    ),
                    "log_loss": -math.log(max(probabilities[result], 1e-15)),
                    "confidence": probabilities[predicted],
                })

        history.extend(todays_matches)

    return summarize_predictions(predictions)


def optimize_previous_season(db_path=DEFAULT_DB_PATH, min_history=MIN_HISTORY):
    matches = load_matches(db_path)
    matches_by_league = defaultdict(list)
    for match in matches:
        match["home_goals"] = int(match["home_goals"])
        match["away_goals"] = int(match["away_goals"])
        matches_by_league[match["league"]].append(match)

    results = {}
    for league in LEAGUES:
        league_matches = matches_by_league[league]
        seasons = sorted({match["season"] for match in league_matches})
        if len(seasons) < 2:
            raise ValueError(f"Servono almeno due stagioni concluse per ottimizzare {league}.")

        validation_season = seasons[-2]
        holdout_season = seasons[-1]
        candidates = []
        for parameters in TUNING_GRID:
            metrics = evaluate_season(
                league,
                league_matches,
                validation_season,
                parameters,
                min_history,
            )
            candidates.append((metrics, parameters))

        validation_metrics, selected_parameters = max(
            candidates,
            key=lambda item: (
                item[0]["accuracy"],
                -item[0]["brier"],
                -item[0]["log_loss"],
            ),
        )
        baseline_validation = evaluate_season(
            league,
            league_matches,
            validation_season,
            DEFAULT_PARAMETERS,
            min_history,
        )
        baseline_holdout = evaluate_season(
            league,
            league_matches,
            holdout_season,
            DEFAULT_PARAMETERS,
            min_history,
        )
        tuned_holdout = evaluate_season(
            league,
            league_matches,
            holdout_season,
            selected_parameters,
            min_history,
        )
        results[league] = {
            "validation_season": validation_season,
            "holdout_season": holdout_season,
            "selected_parameters": selected_parameters,
            "validation_metrics": validation_metrics,
            "baseline_validation": baseline_validation,
            "baseline_holdout": baseline_holdout,
            "tuned_holdout": tuned_holdout,
        }
    return results


def main():
    results = optimize_previous_season()
    print("OTTIMIZZAZIONE WALK-FORWARD - MODELLO POISSON + DIXON-COLES")
    print("Parametri scelti sulla stagione precedente; valutazione solo sulla stagione successiva.")
    print("Selezione su accuratezza validation; Brier e log loss controllano le probabilità.")
    for league, result in results.items():
        baseline = result["baseline_holdout"]
        tuned = result["tuned_holdout"]
        print(f"\n{league}")
        print(f"  Validation: {result['validation_season']} | Holdout: {result['holdout_season']}")
        print(f"  Parametri selezionati: {result['selected_parameters']}")
        print(
            f"  Validation accuracy: {result['validation_metrics']['accuracy']:.2f}% "
            f"(baseline {result['baseline_validation']['accuracy']:.2f}%)"
        )
        print(
            f"  Holdout baseline: accuracy={baseline['accuracy']:.2f}%, "
            f"Brier={baseline['brier']:.4f}, log loss={baseline['log_loss']:.4f}"
        )
        print(
            f"  Holdout tuned   : accuracy={tuned['accuracy']:.2f}%, "
            f"Brier={tuned['brier']:.4f}, log loss={tuned['log_loss']:.4f}"
        )


if __name__ == "__main__":
    main()
