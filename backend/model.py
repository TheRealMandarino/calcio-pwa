import sqlite3
import re
import unicodedata
import pandas as pd
import numpy as np
from scipy.stats import poisson
from backend.stats import compute_team_stats

TEAM_ALIASES = {
    "acmilan": "Milan",
    "asmonaco": "Monaco",
    "asroma": "Roma",
    "atalantabc": "Atalanta",
    "cardiffcity": "Cardiff",
    "brightonandhovealbion": "Brighton",
    "blackburnrovers": "Blackburn",
    "borussiadortmund": "Dortmund",
    "borussiamonchengladbach": "M'gladbach",
    "charltonathletic": "Charlton",
    "boltonwanderers": "Bolton",
    "bayerleverkusen": "Leverkusen",
    "athleticbilbao": "Ath Bilbao",
    "atleticomadrid": "Ath Madrid",
    "caosasuna": "Osasuna",
    "celtavigo": "Celta",
    "coventrycity": "Coventry",
    "deportivolacoruna": "La Coruna",
    "eintrachtfrankfurt": "Ein Frankfurt",
    "elchecf": "Elche",
    "espanyol": "Espanol",
    "fcschalke04": "Schalke 04",
    "fsvmainz05": "Mainz",
    "hamburgersv": "Hamburg",
    "hullcity": "Hull",
    "ipswichtown": "Ipswich",
    "intermilan": "Inter",
    "derbycounty": "Derby",
    "lemansfc": "Le Mans",
    "leedsunited": "Leeds",
    "parissaintgermain": "Paris SG",
    "rclens": "Lens",
    "manchestercity": "Man City",
    "manchesterunited": "Man United",
    "newcastleunited": "Newcastle",
    "nottinghamforest": "Nott'm Forest",
    "rayovallecano": "Vallecano",
    "realbetis": "Betis",
    "realracingclubdesantander": "Santander",
    "realsociedad": "Sociedad",
    "scfreiburg": "Freiburg",
    "scpaderborn": "Paderborn",
    "tsghoffenheim": "Hoffenheim",
    "tottenhamhotspur": "Tottenham",
    "vfbstuttgart": "Stuttgart",
    "westbromwichalbion": "West Brom",
    "wrexhamafc": "Wrexham",
    "queensparkrangers": "QPR",
    "1fckoln": "FC Koln",
    "fckoln": "FC Koln",
    "lincolncity": "Lincoln",
    "prestonnorthend": "Preston",
    "birminghamcity": "Birmingham",
    "norwichcity": "Norwich",
    "stokecity": "Stoke",
    "swanseacity": "Swansea",
    "westhamunited": "West Ham",
    "wolverhamptonwanderers": "Wolves",
    "hellasverona": "Verona",
    "cesenafc": "Cesena",
    "uscatanzaro1929": "Catanzaro",
    "sudtirol": "Sudtirol",
}


def normalize_team_name(team_name):
    normalized = unicodedata.normalize("NFKD", team_name)
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]", "", normalized.lower())


def resolve_team_name(team_name, known_teams):
    normalized = normalize_team_name(team_name)
    alias = TEAM_ALIASES.get(normalized)
    if alias in known_teams:
        return alias
    if team_name in known_teams:
        return team_name
    for candidate in known_teams:
        if normalize_team_name(candidate) == normalized:
            return candidate
    return team_name


def DixonColes_adjustment(x, y, lambda_h, lambda_a, rho):
    if x == 0 and y == 0:
        return 1.0 - (lambda_h * lambda_a * rho)
    elif x == 0 and y == 1:
        return 1.0 + (lambda_h * rho)
    elif x == 1 and y == 0:
        return 1.0 + (lambda_a * rho)
    elif x == 1 and y == 1:
        return 1.0 - rho
    else:
        return 1.0

def get_h2h_factor(
    home_team,
    away_team,
    league=None,
    db_path="data/database.db",
    historical_matches=None,
):
    scope_to_league = league in {"ITA-Serie B", "ENG-Championship"}
    try:
        if historical_matches is None:
            conn = sqlite3.connect(db_path)
            query = """
                SELECT home_team, away_team, home_goals, away_goals
                FROM matches
                WHERE ((home_team = ? AND away_team = ?)
                   OR (home_team = ? AND away_team = ?))
                AND home_goals IS NOT NULL AND away_goals IS NOT NULL
            """
            params = [home_team, away_team, away_team, home_team]
            if scope_to_league:
                query += " AND league = ?"
                params.append(league)
            df = pd.read_sql(query, conn, params=params)
            conn.close()
        else:
            df = historical_matches
            if scope_to_league and "league" in df.columns:
                df = df[df["league"] == league]
            df = df[
                (
                    (df["home_team"] == home_team) & (df["away_team"] == away_team)
                ) | (
                    (df["home_team"] == away_team) & (df["away_team"] == home_team)
                )
            ]

        if len(df) < 2:
            return 1.0, 1.0
        
        home_scored, away_scored = 0, 0
        total = len(df)
        
        for _, row in df.iterrows():
            if row['home_team'] == home_team:
                home_scored += row['home_goals']
                away_scored += row['away_goals']
            else:
                home_scored += row['away_goals']
                away_scored += row['home_goals']
        
        avg_h = home_scored / total
        avg_a = away_scored / total
        
        h2h_h = 1.0 + ((avg_h - 1.3) * 0.03)
        h2h_a = 1.0 + ((avg_a - 1.1) * 0.03)
        
        h2h_h = max(0.95, min(1.05, h2h_h))
        h2h_a = max(0.95, min(1.05, h2h_a))
        
        return h2h_h, h2h_a
    except Exception:
        return 1.0, 1.0

def calculate_match_probabilities(
    home_team,
    away_team,
    league=None,
    precomputed_stats=None,
    historical_matches=None,
    model_parameters=None,
    h2h_factor=None,
):
    parameters = model_parameters or {}
    history = None
    needs_history = precomputed_stats is None or (
        h2h_factor is None and parameters.get("use_h2h", True)
    )
    if historical_matches is not None and needs_history:
        history = pd.DataFrame(historical_matches)
        if league in {"ITA-Serie B", "ENG-Championship"} and "league" in history.columns:
            history = history[history["league"] == league]
        if precomputed_stats is None:
            precomputed_stats = compute_team_stats(
                matches_df=history,
                half_life_days=parameters.get("half_life_days", 90),
                league=league,
            )

    if precomputed_stats:
        home_attack, home_defense, away_attack, away_defense, league_avg_h, league_avg_a, global_avg_h, global_avg_a = precomputed_stats
    else:
        home_attack, home_defense, away_attack, away_defense, league_avg_h, league_avg_a, global_avg_h, global_avg_a = compute_team_stats()

    home_team = resolve_team_name(home_team, home_attack.keys())
    away_team = resolve_team_name(away_team, away_attack.keys())
    
    if league and league in league_avg_h:
        avg_h = league_avg_h[league]
        avg_a = league_avg_a[league]
    else:
        avg_h = global_avg_h
        avg_a = global_avg_a

    # --- ELIMINAZIONE DIFFERENZA CASA/TRASFERTA ---
    # Unifichiamo la media gol di riferimento rendendola neutra
    neutral_avg = (avg_h + avg_a) / 2.0
    avg_h = neutral_avg
    avg_a = neutral_avg

    raw_att_h = home_attack.get(home_team, 1.0)
    raw_def_a = away_defense.get(away_team, 1.0)
    raw_att_a = away_attack.get(away_team, 1.0)
    raw_def_h = home_defense.get(home_team, 1.0)

    # Shrinkage a 0.4 per limitare i picchi dovuti al poco storico
    hw_weight = parameters.get("shrinkage", 0.4)
    att_h = 1.0 + (raw_att_h - 1.0) * hw_weight
    def_a = 1.0 + (raw_def_a - 1.0) * hw_weight
    att_a = 1.0 + (raw_att_a - 1.0) * hw_weight
    def_h = 1.0 + (raw_def_h - 1.0) * hw_weight

    # Fattore campo completamente azzerato (1.0)
    default_home_advantage = 1.05 if league in {"ITA-Serie B", "ENG-Championship"} else 1.0
    home_advantage = parameters.get("home_advantage", default_home_advantage)
    default_rho = -0.11 if league != "GER-Bundesliga" else -0.05
    rho = parameters.get("rho", default_rho)
    max_goals = 9 if league == "GER-Bundesliga" else 7

    # Il fattore H2H resta attivo per personalizzare la sfida sugli scontri diretti
    if h2h_factor is not None:
        h2h_h, h2h_a = h2h_factor
    elif not parameters.get("use_h2h", True):
        h2h_h, h2h_a = 1.0, 1.0
    else:
        h2h_h, h2h_a = get_h2h_factor(
            home_team,
            away_team,
            league=league,
            historical_matches=history if historical_matches is not None else None,
        )

    lambda_home = max(att_h * def_a * avg_h * home_advantage * h2h_h, 0.70)
    lambda_away = max(att_a * def_h * avg_a * h2h_a, 0.65)

    matrix = np.zeros((max_goals, max_goals))

    for i in range(max_goals):
        for j in range(max_goals):
            p_home = poisson.pmf(i, lambda_home)
            p_away = poisson.pmf(j, lambda_away)
            dc = DixonColes_adjustment(i, j, lambda_home, lambda_away, rho)
            matrix[i, j] = p_home * p_away * max(dc, 0.0)

    if np.sum(matrix) > 0:
        matrix /= np.sum(matrix)

    prob_1 = np.sum(np.tril(matrix, -1))
    prob_X = np.sum(np.diag(matrix))
    prob_2 = np.sum(np.triu(matrix, 1))

    total = prob_1 + prob_X + prob_2
    if total > 0:
        prob_1 /= total
        prob_X /= total
        prob_2 /= total
    else:
        prob_1, prob_X, prob_2 = 0.33, 0.34, 0.33

    # --- CALCOLO RISULTATI ESATTI ---
    exact_scores = []
    for i in range(min(max_goals, 5)):
        for j in range(min(max_goals, 5)):
            p_score = matrix[i, j] if i < matrix.shape[0] and j < matrix.shape[1] else 0
            if p_score > 0:
                odd_score = min(round(1.0 / p_score, 2), 50.0)
                exact_scores.append({
                    "score": f"{i}-{j}",
                    "probability": round(float(p_score) * 100, 1),
                    "odd": odd_score
                })
    
    exact_scores = sorted(exact_scores, key=lambda x: x["probability"], reverse=True)[:5]

    return {
        "lambda_home": round(lambda_home, 2),
        "lambda_away": round(lambda_away, 2),
        "prob_1": float(prob_1),
        "prob_X": float(prob_X),
        "prob_2": float(prob_2),
        "exact_scores": exact_scores
    }