import sqlite3
import os
import soccerdata as sd
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "database.db")


def init_db(db_path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            date TEXT,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            home_goals INTEGER,
            away_goals INTEGER,
            home_xg REAL,
            away_xg REAL,
            status TEXT,
            season TEXT
        )
    """)
    conn.commit()
    conn.close()

def update_soccer_data(db_path=DEFAULT_DB_PATH):
    # Leghe supportate da soccerdata FBref
    leagues = [
        "ITA-Serie A",
        "ENG-Premier League",
        "ESP-La Liga",
        "GER-Bundesliga",
        "FRA-Ligue 1"
    ]
    seasons = ["2021", "2022", "2023", "2024", "2025", "2026"]
    
    print(f"Scraping delle partite per i campionati supportati (stagioni {seasons[0]} - {seasons[-1]})...")
    fbref = sd.FBref(leagues=leagues, seasons=seasons)
    schedule = fbref.read_schedule().reset_index()
    
    matches_data = []
    for idx, row in schedule.iterrows():
        h_score = row.get('home_score')
        a_score = row.get('away_score')
        
        if pd.isna(h_score) or h_score == '':
            h_score = row.get('home_goals')
        if pd.isna(a_score) or a_score == '':
            a_score = row.get('away_goals')
            
        score_col = row.get('score')
        if (pd.isna(h_score) or pd.isna(a_score)) and isinstance(score_col, str) and ('-' in score_col or '–' in score_col):
            sep = '-' if '-' in score_col else '–'
            parts = score_col.split(sep)
            try:
                h_score = int(parts[0].strip())
                a_score = int(parts[1].strip())
            except:
                pass

        try:
            h_goals = int(float(h_score)) if not pd.isna(h_score) and str(h_score) != '' else None
        except:
            h_goals = None
            
        try:
            a_goals = int(float(a_score)) if not pd.isna(a_score) and str(a_score) != '' else None
        except:
            a_goals = None

        h_xg = row.get('home_xg')
        if pd.isna(h_xg): h_xg = None
        
        a_xg = row.get('away_xg')
        if pd.isna(a_xg): a_xg = None

        league_name = row.get('league', '')

        matches_data.append({
            'date': str(row.get('date', '')),
            'league': str(league_name),
            'home_team': row.get('home_team'),
            'away_team': row.get('away_team'),
            'home_goals': h_goals,
            'away_goals': a_goals,
            'home_xg': float(h_xg) if h_xg is not None else None,
            'away_xg': float(a_xg) if a_xg is not None else None,
            'status': str(row.get('status', 'scheduled')),
            'season': str(row.get('season', ''))
        })
    
    df = pd.DataFrame(matches_data)
    completed_count = df['home_goals'].notna().sum()
    print(f"Totale match scaricati: {len(df)} | Match con gol registrati correttamente: {completed_count}")
    
    if not df.empty:
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS matches (
                        date TEXT,
                        league TEXT,
                        home_team TEXT,
                        away_team TEXT,
                        home_goals INTEGER,
                        away_goals INTEGER,
                        home_xg REAL,
                        away_xg REAL,
                        status TEXT,
                        season TEXT
                    )
                """)
                league_names = df['league'].dropna().unique().tolist()
                placeholders = ",".join("?" for _ in league_names)
                conn.execute(
                    f"DELETE FROM matches WHERE league IN ({placeholders})",
                    league_names,
                )
                df.to_sql("matches", conn, if_exists="append", index=False)
        finally:
            conn.close()
    print("Database aggiornato con successo.")

if __name__ == "__main__":
    init_db()
    update_soccer_data()