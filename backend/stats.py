import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

LOWER_LEAGUES = {"ITA-Serie B", "ENG-Championship"}
MIN_VENUE_MATCHES = 8
MIN_VENUE_DIFFERENCE = 0.30


def compute_team_stats(
    db_path="data/database.db",
    half_life_days=90,
    matches_df=None,
    league=None,
    recent_form_weight=0.0,
    recent_form_window=5,
    previous_season_weight=1.0,
    new_team_prior_matches=0,
):
    """
    Calcola le statistiche di attacco e difesa separate per Casa e Trasferta,
    applicando un fattore di Time Decay basato sulla data del match.
    """
    if matches_df is None:
        conn = sqlite3.connect(db_path)
        if league in LOWER_LEAGUES:
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
            query = f"""
                SELECT matches.date, matches.league, matches.home_team, matches.away_team,
                       matches.home_goals, matches.away_goals, matches.home_xg,
                       matches.away_xg, matches.season, {detail_columns}
                FROM matches
                {detail_join}
                WHERE matches.home_goals IS NOT NULL AND matches.away_goals IS NOT NULL
                  AND matches.league = ?
                ORDER BY matches.date ASC
            """
            df = pd.read_sql(query, conn, params=(league,))
        else:
            query = """
                SELECT date, league, home_team, away_team, home_goals, away_goals
                FROM matches
                WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
                ORDER BY date ASC
            """
            df = pd.read_sql(query, conn)
        conn.close()
    else:
        df = matches_df.copy()

    if df.empty:
        return {}, {}, {}, {}, {}, {}, 1.3, 1.1

    if league in LOWER_LEAGUES:
        df = df[df["league"] == league].copy()
        if df.empty:
            return {}, {}, {}, {}, {}, {}, 1.3, 1.1
        df["date"] = pd.to_datetime(df["date"])
        home_xg = (
            pd.to_numeric(df["home_xg"], errors="coerce")
            if "home_xg" in df
            else pd.Series(float("nan"), index=df.index)
        )
        away_xg = (
            pd.to_numeric(df["away_xg"], errors="coerce")
            if "away_xg" in df
            else pd.Series(float("nan"), index=df.index)
        )
        df["_home_scored"] = home_xg.fillna(pd.to_numeric(df["home_goals"], errors="coerce"))
        df["_away_scored"] = away_xg.fillna(pd.to_numeric(df["away_goals"], errors="coerce"))
        reference_date = df["date"].max()
        age_days = (reference_date - df["date"]).dt.days
        df["_weight"] = np.maximum(
            np.exp(-np.log(2) * age_days / half_life_days),
            0.1,
        )
        season_starts = pd.Series(0, index=df.index, dtype=int)
        if "season" in df:
            season_years = df["season"].astype(str).str.extract(r"^(\d{4})")[0]
            season_starts = pd.to_numeric(season_years, errors="coerce").fillna(0).astype(int)
        latest_season = int(season_starts.max())
        if latest_season and previous_season_weight < 1:
            season_age = (latest_season - season_starts).clip(lower=0)
            df["_weight"] *= np.power(previous_season_weight, season_age)
        total_goals = (
            (df["_home_scored"] + df["_away_scored"]) * df["_weight"]
        ).sum()
        total_team_appearances = 2 * df["_weight"].sum()
        league_average = total_goals / total_team_appearances if total_team_appearances else 1.3
        league_average = max(float(league_average), 0.1)

        teams = set(df["home_team"]).union(df["away_team"])
        current_teams = set(df.loc[season_starts == latest_season, "home_team"]).union(
            df.loc[season_starts == latest_season, "away_team"]
        ) if latest_season else set()
        previous_teams = set(
            df.loc[season_starts == latest_season - 1, "home_team"]
        ).union(
            df.loc[season_starts == latest_season - 1, "away_team"]
        ) if latest_season else set()
        new_teams = current_teams - previous_teams if previous_teams else set()
        shot_column_names = {
            "home_shots_on_target",
            "away_shots_on_target",
        }
        has_shot_data = shot_column_names.issubset(df.columns)
        if has_shot_data:
            df["_home_shots_on_target"] = pd.to_numeric(
                df["home_shots_on_target"], errors="coerce"
            )
            df["_away_shots_on_target"] = pd.to_numeric(
                df["away_shots_on_target"], errors="coerce"
            )

        home_aggregates = df.assign(
            _scored_weighted=df["_home_scored"] * df["_weight"],
            _conceded_weighted=df["_away_scored"] * df["_weight"],
        ).groupby("home_team", sort=False).agg(
            scored_weighted=("_scored_weighted", "sum"),
            conceded_weighted=("_conceded_weighted", "sum"),
            weight=("_weight", "sum"),
            appearances=("_weight", "size"),
        ).to_dict(orient="index")
        away_aggregates = df.assign(
            _scored_weighted=df["_away_scored"] * df["_weight"],
            _conceded_weighted=df["_home_scored"] * df["_weight"],
        ).groupby("away_team", sort=False).agg(
            scored_weighted=("_scored_weighted", "sum"),
            conceded_weighted=("_conceded_weighted", "sum"),
            weight=("_weight", "sum"),
            appearances=("_weight", "size"),
        ).to_dict(orient="index")

        recent_adjustments = {}
        if league in LOWER_LEAGUES and recent_form_weight and recent_form_window > 0:
            observations = []
            for _, row in df.iterrows():
                observations.extend((
                    {
                        "team": row["home_team"],
                        "date": row["date"],
                        "scored": row["_home_scored"],
                        "conceded": row["_away_scored"],
                        "shots_for": row.get("_home_shots_on_target", np.nan),
                        "shots_against": row.get("_away_shots_on_target", np.nan),
                    },
                    {
                        "team": row["away_team"],
                        "date": row["date"],
                        "scored": row["_away_scored"],
                        "conceded": row["_home_scored"],
                        "shots_for": row.get("_away_shots_on_target", np.nan),
                        "shots_against": row.get("_home_shots_on_target", np.nan),
                    },
                ))
            observations_by_team = pd.DataFrame(observations).groupby("team", sort=False)

            def relative_rate(recent_rate, baseline_rate):
                return float(np.clip(recent_rate / max(baseline_rate, 0.1), 0.5, 1.5))

            def adjustment(goal_ratio, shot_ratio):
                ratio = goal_ratio
                if shot_ratio is not None:
                    ratio = np.exp(0.7 * np.log(goal_ratio) + 0.3 * np.log(shot_ratio))
                return float(ratio ** recent_form_weight)

            for team, team_observations in observations_by_team:
                recent = team_observations.sort_values("date").tail(recent_form_window)
                baseline_scored = team_observations["scored"].mean()
                baseline_conceded = team_observations["conceded"].mean()
                recent_scored = recent["scored"].mean()
                recent_conceded = recent["conceded"].mean()
                shot_for_baseline = team_observations["shots_for"].mean()
                shot_against_baseline = team_observations["shots_against"].mean()
                shot_for_recent = recent["shots_for"].mean()
                shot_against_recent = recent["shots_against"].mean()
                shot_for_ratio = None
                shot_against_ratio = None
                minimum_shot_samples = max(3, recent_form_window // 2)
                if (
                    recent["shots_for"].notna().sum() >= minimum_shot_samples
                    and pd.notna(shot_for_baseline)
                    and pd.notna(shot_for_recent)
                ):
                    shot_for_ratio = relative_rate(shot_for_recent, shot_for_baseline)
                if (
                    recent["shots_against"].notna().sum() >= minimum_shot_samples
                    and pd.notna(shot_against_baseline)
                    and pd.notna(shot_against_recent)
                ):
                    shot_against_ratio = relative_rate(
                        shot_against_recent,
                        shot_against_baseline,
                    )
                recent_adjustments[team] = (
                    adjustment(
                        relative_rate(recent_scored, baseline_scored),
                        shot_for_ratio,
                    ),
                    adjustment(
                        relative_rate(recent_conceded, baseline_conceded),
                        shot_against_ratio,
                    ),
                )

        team_stats = {}

        def aggregate_value(aggregates, team, key):
            return aggregates.get(team, {}).get(key, 0)

        for team in teams:
            home_weight = aggregate_value(home_aggregates, team, "weight")
            away_weight = aggregate_value(away_aggregates, team, "weight")
            combined_scored = (
                aggregate_value(home_aggregates, team, "scored_weighted")
                + aggregate_value(away_aggregates, team, "scored_weighted")
            )
            combined_conceded = (
                aggregate_value(home_aggregates, team, "conceded_weighted")
                + aggregate_value(away_aggregates, team, "conceded_weighted")
            )
            combined_weight = home_weight + away_weight
            prior_weight = new_team_prior_matches if team in new_teams else 0
            combined_scored += league_average * prior_weight
            combined_conceded += league_average * prior_weight
            combined_weight += prior_weight
            combined_scored_rate = combined_scored / combined_weight if combined_weight else league_average
            combined_conceded_rate = combined_conceded / combined_weight if combined_weight else league_average

            def venue_rate(aggregates, team, metric, weight, reference_rate):
                return (
                    aggregate_value(aggregates, team, metric) + reference_rate * 5
                ) / (weight + 5)

            home_scored_rate = venue_rate(
                home_aggregates, team, "scored_weighted", home_weight, combined_scored_rate
            )
            away_scored_rate = venue_rate(
                away_aggregates, team, "scored_weighted", away_weight, combined_scored_rate
            )
            home_conceded_rate = venue_rate(
                home_aggregates, team, "conceded_weighted", home_weight, combined_conceded_rate
            )
            away_conceded_rate = venue_rate(
                away_aggregates, team, "conceded_weighted", away_weight, combined_conceded_rate
            )

            enough_home = aggregate_value(home_aggregates, team, "appearances") >= MIN_VENUE_MATCHES
            enough_away = aggregate_value(away_aggregates, team, "appearances") >= MIN_VENUE_MATCHES
            split_attack = (
                enough_home
                and enough_away
                and abs(home_scored_rate - away_scored_rate) / max(combined_scored_rate, 0.1)
                >= MIN_VENUE_DIFFERENCE
            )
            split_defense = (
                enough_home
                and enough_away
                and abs(home_conceded_rate - away_conceded_rate) / max(combined_conceded_rate, 0.1)
                >= MIN_VENUE_DIFFERENCE
            )
            team_stats[team] = {
                "home_attack": (
                    home_scored_rate if split_attack else combined_scored_rate
                ) * recent_adjustments.get(team, (1.0, 1.0))[0],
                "away_attack": (
                    away_scored_rate if split_attack else combined_scored_rate
                ) * recent_adjustments.get(team, (1.0, 1.0))[0],
                "home_defense": (
                    home_conceded_rate if split_defense else combined_conceded_rate
                ) * recent_adjustments.get(team, (1.0, 1.0))[1],
                "away_defense": (
                    away_conceded_rate if split_defense else combined_conceded_rate
                ) * recent_adjustments.get(team, (1.0, 1.0))[1],
            }

        home_attack = {
            team: values["home_attack"] / league_average
            for team, values in team_stats.items()
        }
        away_attack = {
            team: values["away_attack"] / league_average
            for team, values in team_stats.items()
        }
        home_defense = {
            team: values["home_defense"] / league_average
            for team, values in team_stats.items()
        }
        away_defense = {
            team: values["away_defense"] / league_average
            for team, values in team_stats.items()
        }
        return (
            home_attack,
            home_defense,
            away_attack,
            away_defense,
            {league: league_average},
            {league: league_average},
            league_average,
            league_average,
        )

    # Converti la colonna data in datetime per calcolare il time decay
    df['date'] = pd.to_datetime(df['date'])
    max_date = df['date'].max()

    home_attack_sum = {}
    home_attack_count = {}
    home_defense_sum = {}
    home_defense_count = {}

    away_attack_sum = {}
    away_attack_count = {}
    away_defense_sum = {}
    away_defense_count = {}

    league_home_goals = {}
    league_away_goals = {}
    league_matches_count = {}

    for _, row in df.iterrows():
        league = row['league']
        h_team = row['home_team']
        a_team = row['away_team']
        h_goals = float(row['home_goals'])
        a_goals = float(row['away_goals'])
        match_date = row['date']

        # Calcolo Time Decay esponenziale
        days_diff = (max_date - match_date).days
        decay_weight = np.exp(-np.log(2) * days_diff / half_life_days)
        if decay_weight < 0.1:
            decay_weight = 0.1

        if league not in league_home_goals:
            league_home_goals[league] = 0.0
            league_away_goals[league] = 0.0
            league_matches_count[league] = 0.0

        league_home_goals[league] += h_goals * decay_weight
        league_away_goals[league] += a_goals * decay_weight
        league_matches_count[league] += decay_weight

        # Accumula per Squadra Casa
        home_attack_sum[h_team] = home_attack_sum.get(h_team, 0.0) + (h_goals * decay_weight)
        home_attack_count[h_team] = home_attack_count.get(h_team, 0.0) + decay_weight
        home_defense_sum[h_team] = home_defense_sum.get(h_team, 0.0) + (a_goals * decay_weight)
        home_defense_count[h_team] = home_defense_count.get(h_team, 0.0) + decay_weight

        # Accumula per Squadra Trasferta
        away_attack_sum[a_team] = away_attack_sum.get(a_team, 0.0) + (a_goals * decay_weight)
        away_attack_count[a_team] = away_attack_count.get(a_team, 0.0) + decay_weight
        away_defense_sum[a_team] = away_defense_sum.get(a_team, 0.0) + (h_goals * decay_weight)
        away_defense_count[a_team] = away_defense_count.get(a_team, 0.0) + decay_weight

    avg_home_by_league = {lg: league_home_goals[lg] / league_matches_count[lg] for lg in league_home_goals}
    avg_away_by_league = {lg: league_away_goals[lg] / league_matches_count[lg] for lg in league_away_goals}

    global_avg_h = sum(league_home_goals.values()) / sum(league_matches_count.values()) if league_matches_count else 1.3
    global_avg_a = sum(league_away_goals.values()) / sum(league_matches_count.values()) if league_matches_count else 1.1

    home_attack_rating = {team: (home_attack_sum[team] / home_attack_count[team]) for team in home_attack_sum}
    home_defense_rating = {team: (home_defense_sum[team] / home_defense_count[team]) for team in home_defense_sum}
    away_attack_rating = {team: (away_attack_sum[team] / away_attack_count[team]) for team in away_attack_sum}
    away_defense_rating = {team: (away_defense_sum[team] / away_defense_count[team]) for team in away_defense_sum}

    return (
        home_attack_rating, home_defense_rating, 
        away_attack_rating, away_defense_rating, 
        avg_home_by_league, avg_away_by_league, 
        global_avg_h, global_avg_a
    )