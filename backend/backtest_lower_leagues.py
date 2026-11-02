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
    "recent_form_weight": 0.0,
    "recent_form_window": 5,
    "previous_season_weight": 1.0,
    "new_team_prior_matches": 0,
}
FEATURE_PROFILES = (
    {},
    {"recent_form_weight": 0.3},
    {"previous_season_weight": 0.6, "new_team_prior_matches": 8},
    {
        "recent_form_weight": 0.3,
        "previous_season_weight": 0.6,
        "new_team_prior_matches": 8,
    },
)
TUNING_GRID = [
    {
        **DEFAULT_PARAMETERS,
        **profile,
        "half_life_days": 90,
        "shrinkage": 0.4,
    }
    for profile in FEATURE_PROFILES
]
TUNING_GRID.extend(
    {
        **DEFAULT_PARAMETERS,
        **FEATURE_PROFILES[-1],
        "half_life_days": half_life,
        "shrinkage": shrinkage,
    }
    for half_life, shrinkage in ((60, 0.2), (180, 0.6))
)


def stats_parameters(parameters):
    return {
        key: parameters[key]
        for key in (
            "half_life_days",
            "recent_form_weight",
            "recent_form_window",
            "previous_season_weight",
            "new_team_prior_matches",
        )
    }


def load_matches(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        has_details = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lower_league_details'"
        ).fetchone()
        detail_columns = (
            "details.home_shots_on_target, details.away_shots_on_target"
            if has_details
            else "NULL AS home_shots_on_target, NULL AS away_shots_on_target"
        )
        detail_join = (
            """
            LEFT JOIN lower_league_details AS details
                ON details.league = matches.league
               AND details.date = matches.date
               AND details.home_team = matches.home_team
               AND details.away_team = matches.away_team
            """
            if has_details
            else ""
        )
        rows = conn.execute(f"""
            SELECT matches.date, matches.league, matches.home_team, matches.away_team,
                   matches.home_goals, matches.away_goals, matches.home_xg,
                   matches.away_xg, matches.season, {detail_columns}
            FROM matches
            {detail_join}
            WHERE matches.league IN (?, ?)
              AND matches.home_goals IS NOT NULL
              AND matches.away_goals IS NOT NULL
            ORDER BY matches.league, matches.date, matches.home_team, matches.away_team
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


def run_backtest(db_path=DEFAULT_DB_PATH, min_history=MIN_HISTORY, parameters_by_league=None):
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
        parameters = {
            **DEFAULT_PARAMETERS,
            **(parameters_by_league or {}).get(league, {}),
        }
        history = []
        h2h_totals = {}
        predictions = []
        season_predictions = defaultdict(list)
        matches_by_date = defaultdict(list)
        for match in matches_by_league[league]:
            matches_by_date[match["date"][:10]].append(match)

        for match_date in sorted(matches_by_date):
            todays_matches = matches_by_date[match_date]
            if len(history) >= min_history:
                history_frame = pd.DataFrame(history)
                stats = compute_team_stats(
                    matches_df=history_frame,
                    league=league,
                    **stats_parameters(parameters),
                )
                for match in todays_matches:
                    model = calculate_match_probabilities(
                        match["home_team"],
                        match["away_team"],
                        league=league,
                        precomputed_stats=stats,
                        model_parameters=parameters,
                        h2h_factor=get_cached_h2h_factor(
                            h2h_totals,
                            match["home_team"],
                            match["away_team"],
                        ),
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
            update_h2h_totals(h2h_totals, todays_matches)

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


def evaluate_season(
    league,
    matches,
    season,
    parameters,
    min_history=MIN_HISTORY,
    shared_stats_cache=None,
):
    parameters = {**DEFAULT_PARAMETERS, **parameters}
    history = sorted(
        (match for match in matches if match["season"] < season),
        key=lambda match: match["date"],
    )
    predictions = []
    stats_cache = shared_stats_cache if shared_stats_cache is not None else {}
    h2h_cache = {}
    matches_by_date = defaultdict(list)
    for match in matches:
        if match["season"] == season:
            matches_by_date[match["date"][:10]].append(match)

    for match_date in sorted(matches_by_date):
        todays_matches = matches_by_date[match_date]
        if len(history) >= min_history:
            history_frame = pd.DataFrame(history)
            stats_key = (
                league,
                season,
                match_date,
                *stats_parameters(parameters).values(),
            )
            if stats_key not in stats_cache:
                stats_cache[stats_key] = compute_team_stats(
                    matches_df=history_frame,
                    league=league,
                    **stats_parameters(parameters),
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
    shared_stats_cache = {}
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
                shared_stats_cache,
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
            shared_stats_cache,
        )
        baseline_holdout = evaluate_season(
            league,
            league_matches,
            holdout_season,
            DEFAULT_PARAMETERS,
            min_history,
            shared_stats_cache,
        )
        tuned_holdout = evaluate_season(
            league,
            league_matches,
            holdout_season,
            selected_parameters,
            min_history,
            shared_stats_cache,
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
