import sqlite3
from pathlib import Path

from backend.lower_league_data import update_primary_leagues


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "database.db"


def init_db(db_path=DEFAULT_DB_PATH):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
    update_primary_leagues(db_path)

if __name__ == "__main__":
    init_db()
    update_soccer_data()