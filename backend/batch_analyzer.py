import sqlite3
import json
import pandas as pd
import os
from datetime import datetime
from backend.stats import compute_team_stats
from backend.model import calculate_match_probabilities

def generate_league_json():
    # Trova automaticamente la cartella radice del progetto
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(BASE_DIR, "data", "database.db")
    output_dir = os.path.join(BASE_DIR, "docs")
    
    conn = sqlite3.connect(db_path)
    
    # Data di riferimento odierna
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # Rileva automaticamente la data della prima partita non ancora giocata da oggi in poi
    query_date = "SELECT MIN(date) as next_date FROM matches WHERE home_goals IS NULL AND date >= ?"
    df_date = pd.read_sql(query_date, conn, params=(today_str,))
    
    if not df_date.empty and df_date['next_date'].iloc[0] is not None:
        target_date = df_date['next_date'].iloc[0]
    else:
        target_date = today_str
        
    print(f"Giornata corrente identificata a partire dal: {target_date}")
    
    # Calcoliamo le statistiche UNA SOLA VOLTA ALL'INIZIO (Time Decay + Casa/Trasferta)
    print("Calcolo delle statistiche di squadra in corso...")
    stats_cache = compute_team_stats(db_path)
    
    # Manteniamo i risultati recenti insieme alle partite future.
    query = """
        SELECT date, league, home_team, away_team, home_goals, away_goals
        FROM matches 
        WHERE date >= ?
           OR (
                home_goals IS NOT NULL
                AND away_goals IS NOT NULL
                AND date >= datetime(?, '-7 days')
           )
        ORDER BY league ASC, date ASC
    """
    df = pd.read_sql(query, conn, params=(target_date, target_date))
    conn.close()
    
    os.makedirs(output_dir, exist_ok=True)

    print("Generazione dei file JSON nella cartella docs...")
    league_groups = {
        league_name: group_df
        for league_name, group_df in df.groupby('league')
    }
    for league_name in ("ITA-Serie B", "ENG-Championship"):
        league_groups.setdefault(league_name, df.iloc[0:0].copy())

    if df.empty:
        print("Nessuna partita futura o recente trovata nel database.")

    for league_name, group_df in league_groups.items():
        matches_output = []
        league_stats = (
            compute_team_stats(db_path, league=league_name)
            if league_name in ("ITA-Serie B", "ENG-Championship")
            else stats_cache
        )
        
        for _, row in group_df.iterrows():
            h_team = row['home_team']
            a_team = row['away_team']
            
            calc = calculate_match_probabilities(h_team, a_team, league=league_name, precomputed_stats=league_stats)
            
            p1 = max(calc["prob_1"], 0.03)
            pX = max(calc["prob_X"], 0.03)
            p2 = max(calc["prob_2"], 0.03)
            
            total_p = p1 + pX + p2
            p1, pX, p2 = p1 / total_p, pX / total_p, p2 / total_p
            
            odd_1 = min(round(1.0 / p1, 2), 20.0)
            odd_X = min(round(1.0 / pX, 2), 20.0)
            odd_2 = min(round(1.0 / p2, 2), 20.0)
            is_finished = pd.notna(row['home_goals']) and pd.notna(row['away_goals'])
            
            match_output = {
                "date": str(row['date']),
                "league": league_name,
                "home_team": h_team,
                "away_team": a_team,
                "status": "finished" if is_finished else "scheduled",
                "lambda_home": calc["lambda_home"],
                "lambda_away": calc["lambda_away"],
                "probabilities": {
                    "1": round(p1 * 100, 1),
                    "X": round(pX * 100, 1),
                    "2": round(p2 * 100, 1)
                },
                "odds": {
                    "1": odd_1,
                    "X": odd_X,
                    "2": odd_2
                },
                "exact_scores": calc["exact_scores"]  # Inclusione dei risultati esatti calcolati dal modello
            }
            if is_finished:
                match_output["home_goals"] = int(row['home_goals'])
                match_output["away_goals"] = int(row['away_goals'])
            matches_output.append(match_output)
        
        safe_league_name = league_name.replace(" ", "_").lower()
        file_name = f"{safe_league_name}.json"
        output_path = os.path.join(output_dir, file_name)
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(matches_output, f, indent=4, ensure_ascii=False)
        
        print(f"File generato: {file_name} ({len(matches_output)} partite).")

if __name__ == "__main__":
    generate_league_json()