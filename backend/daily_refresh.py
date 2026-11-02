import json
from datetime import datetime, timezone
from pathlib import Path

from backend.batch_analyzer import generate_league_json
from backend.lower_league_data import (
    ALL_LEAGUES,
    update_lower_leagues,
    update_scheduled_fixtures,
)
from backend.scraper import init_db, update_soccer_data
from backend.scraper import DEFAULT_DB_PATH
from backend.server import LEAGUES, fetch_odds, load_api_key

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
LEAGUE_NAMES_BY_FILE = {
    f"{league.replace(' ', '_').lower()}.json": league
    for league in ALL_LEAGUES.values()
}


def write_publication_data(bookmaker_odds, odds_available, output_dir=DOCS_DIR):
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    odds_payload = {
        "generated_at": generated_at,
        "leagues": bookmaker_odds,
    }
    status_payload = {
        "generated_at": generated_at,
        "odds_available": odds_available,
    }

    for filename, payload in (
        ("bookmaker-odds.json", odds_payload),
        ("data-status.json", status_payload),
    ):
        output_path = output_dir / filename
        temporary_path = output_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(output_path)


def main():
    init_db(DEFAULT_DB_PATH)
    update_soccer_data(DEFAULT_DB_PATH)
    update_lower_leagues(DEFAULT_DB_PATH)
    api_key = load_api_key()
    bookmaker_odds = {}
    if api_key:
        for filename in LEAGUES:
            events = fetch_odds(filename, api_key)
            bookmaker_odds[filename] = events
            update_scheduled_fixtures(
                DEFAULT_DB_PATH,
                LEAGUE_NAMES_BY_FILE[filename],
                events,
            )
    else:
        print("Quote live non aggiornate: ODDS_API_KEY non configurata.")

    generate_league_json()
    write_publication_data(bookmaker_odds, bool(api_key))


if __name__ == "__main__":
    main()
