import json
from urllib.error import HTTPError, URLError

from backend.batch_analyzer import generate_league_json
from backend.lower_league_data import update_lower_leagues, update_scheduled_fixtures
from backend.scraper import init_db, update_soccer_data
from backend.scraper import DEFAULT_DB_PATH
from backend.server import fetch_odds, load_api_key


def main():
    init_db(DEFAULT_DB_PATH)
    update_soccer_data(DEFAULT_DB_PATH)
    update_lower_leagues(DEFAULT_DB_PATH)
    api_key = load_api_key()
    if api_key:
        for filename, league in (
            ("ita-serie_b.json", "ITA-Serie B"),
            ("eng-championship.json", "ENG-Championship"),
        ):
            try:
                events = fetch_odds(filename, api_key)
                update_scheduled_fixtures(DEFAULT_DB_PATH, league, events)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as error:
                print(f"Calendario {league} non aggiornato da The Odds API: {error}")
    else:
        print("Calendario Championship futuro non aggiornato: ODDS_API_KEY non configurata.")
    generate_league_json()


if __name__ == "__main__":
    main()
