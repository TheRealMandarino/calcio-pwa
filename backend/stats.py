import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime

LOWER_LEAGUES = {"ITA-Serie B", "ENG-Championship"}
MIN_VENUE_MATCHES = 8
MIN_VENUE_DIFFERENCE = 0.30


def compute_team_stats(db_path="data/database.db", half_life_days=90, matches_df=None, league=None):
    """
    Calcola le statistiche di attacco e difesa separate per Casa e Trasferta,
    applicando un fattore di Time Decay basato sulla data del match.
    """
    if matches_df is None:
        conn = sqlite3.connect(db_path)
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
        total_goals = (
            (df["_home_scored"] + df["_away_scored"]) * df["_weight"]
        ).sum()
        total_team_appearances = 2 * df["_weight"].sum()
        league_average = total_goals / total_team_appearances if total_team_appearances else 1.3
        league_average = max(float(league_average), 0.1)

        team_stats = {}
        for team in set(df["home_team"]).union(df["away_team"]):
            home = df[df["home_team"] == team]
            away = df[df["away_team"] == team]
            combined_scored = (
                (home["_home_scored"] * home["_weight"]).sum()
                + (away["_away_scored"] * away["_weight"]).sum()
            )
            combined_conceded = (
                (home["_away_scored"] * home["_weight"]).sum()
                + (away["_home_scored"] * away["_weight"]).sum()
            )
            combined_weight = home["_weight"].sum() + away["_weight"].sum()
            combined_scored_rate = combined_scored / combined_weight if combined_weight else league_average
            combined_conceded_rate = combined_conceded / combined_weight if combined_weight else league_average

            def venue_rate(frame, scored_column, reference_rate):
                weight = frame["_weight"].sum()
                return (
                    (frame[scored_column].astype(float) * frame["_weight"]).sum()
                    + reference_rate * 5
                ) / (weight + 5)

            home_scored_rate = venue_rate(home, "_home_scored", combined_scored_rate)
            away_scored_rate = venue_rate(away, "_away_scored", combined_scored_rate)
            home_conceded_rate = venue_rate(home, "_away_scored", combined_conceded_rate)
            away_conceded_rate = venue_rate(away, "_home_scored", combined_conceded_rate)

            enough_home = len(home) >= MIN_VENUE_MATCHES
            enough_away = len(away) >= MIN_VENUE_MATCHES
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
                "home_attack": home_scored_rate if split_attack else combined_scored_rate,
                "away_attack": away_scored_rate if split_attack else combined_scored_rate,
                "home_defense": home_conceded_rate if split_defense else combined_conceded_rate,
                "away_defense": away_conceded_rate if split_defense else combined_conceded_rate,
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