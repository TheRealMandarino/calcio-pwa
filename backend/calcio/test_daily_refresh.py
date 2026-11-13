import json
import tempfile
import unittest
from pathlib import Path

from backend.calcio.daily_refresh import LEAGUE_NAMES_BY_FILE, write_publication_data


class PublicationDataTests(unittest.TestCase):
    def test_maps_api_files_to_database_leagues(self):
        self.assertEqual(len(LEAGUE_NAMES_BY_FILE), 7)
        self.assertEqual(LEAGUE_NAMES_BY_FILE["ita-serie_a.json"], "ITA-Serie A")
        self.assertEqual(LEAGUE_NAMES_BY_FILE["eng-championship.json"], "ENG-Championship")

    def test_writes_public_odds_and_refresh_status(self):
        bookmaker_odds = {
            "ita-serie_a.json": [{
                "home_team": "Roma",
                "away_team": "Inter",
                "odds": {"1": {"price": 2.6, "bookmaker": "Bookmaker"}},
            }],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            write_publication_data(bookmaker_odds, True, output_dir)

            odds = json.loads((output_dir / "bookmaker-odds.json").read_text(encoding="utf-8"))
            status = json.loads((output_dir / "data-status.json").read_text(encoding="utf-8"))

        self.assertEqual(odds["leagues"], bookmaker_odds)
        self.assertEqual(odds["generated_at"], status["generated_at"])
        self.assertTrue(status["odds_available"])


if __name__ == "__main__":
    unittest.main()
