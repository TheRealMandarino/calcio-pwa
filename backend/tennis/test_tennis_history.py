import csv
import io
import json
import tempfile
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from unittest.mock import patch

from backend import server
from backend.tennis import tennis_api, tennis_history


class TennisHistoryTests(unittest.TestCase):
    def test_validates_circuit_and_season_range(self):
        current_year = date.today().year
        tennis_history.validate_request("atp", 1968, current_year)
        invalid_requests = [
            ("other", 2000, 2001),
            ("wta", 1967, 2000),
            ("atp", 2001, 2000),
            ("atp", 1968, current_year + 1),
            ("atp", "2000", 2001),
            ("wta", 1968, 1968 + tennis_history.MAX_YEARS_PER_REQUEST),
        ]
        for tour, start_year, end_year in invalid_requests:
            with self.subTest(tour=tour, start=start_year, end=end_year):
                with self.assertRaises(tennis_history.TennisHistoryError):
                    tennis_history.validate_request(tour, start_year, end_year)

    def test_combines_seasons_with_different_csv_columns(self):
        season_2024 = (
            b"tourney_date,surface,winner_name,loser_name,score,minutes\n"
            b"2024-01-01,Hard,Player One,Player Two,6-4 6-3,80\n"
        )
        season_2025 = (
            b"surface,winner_name,loser_name,score,w_svpt,w_1stWon,w_2ndWon,"
            b"w_bpSaved,w_bpFaced,l_svpt,l_1stWon,l_2ndWon,l_bpSaved,l_bpFaced\n"
            b"Clay,Player Three,Player Four,7-6(4) 6-4,90,60,40,2,5,90,60,20,1,3\n"
        )
        with patch.object(
            tennis_history,
            "fetch_season_csv",
            side_effect=[season_2024, season_2025],
        ):
            archive = tennis_history.fetch_historical_archive("atp", 2024, 2025)

        rows = list(csv.DictReader(io.StringIO(archive["csv"])))
        self.assertEqual(archive["match_count"], 2)
        self.assertEqual(archive["invalid_stat_matches"], 1)
        self.assertEqual(archive["years"], [2024, 2025])
        self.assertEqual(archive["source"], tennis_history.SOURCE_REPOSITORY)
        self.assertEqual(rows[0]["winner_name"], "Player One")
        self.assertEqual(rows[0]["minutes"], "80")
        self.assertEqual(rows[1]["winner_name"], "Player Three")
        self.assertEqual(rows[1]["w_svpt"], "")
        self.assertEqual(rows[1]["w_1stWon"], "")
        self.assertEqual(rows[1]["l_svpt"], "90")

    def test_downloads_and_caches_a_valid_season(self):
        payload = (
            b"surface,winner_name,loser_name\n"
            b"Clay,Player One,Player Two\n"
        )

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return (
                    "https://raw.githubusercontent.com/"
                    "Aneeshers/tennis-sackmann-archive/main/atp/atp_matches_2024.csv"
                )

            def read(self, size):
                return payload

        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.object(tennis_history, "_cache_directory", return_value=Path(temporary_directory)):
                with patch.object(tennis_history, "urlopen", return_value=FakeResponse()) as mocked:
                    first = tennis_history.fetch_season_csv("atp", 2024)
                    second = tennis_history.fetch_season_csv("atp", 2024)

        self.assertEqual(first, payload)
        self.assertEqual(second, payload)
        mocked.assert_called_once()


class TennisHistoryEndpointTests(unittest.TestCase):
    def setUp(self):
        self.http_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            server.CalcioRequestHandler,
        )
        self.thread = Thread(target=self.http_server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.http_server.server_port}"

    def tearDown(self):
        self.http_server.shutdown()
        self.http_server.server_close()
        self.thread.join()

    def test_root_redirects_to_the_documented_docs_path(self):
        with urlopen(f"{self.base_url}/") as response:
            page = response.read().decode("utf-8")

        self.assertEqual(response.geturl(), f"{self.base_url}/docs/")
        self.assertIn('<script src="tennis/tennis.js"></script>', page)
        with urlopen(f"{self.base_url}/docs/tennis/tennis.js") as response:
            self.assertIn("analyzeMatch", response.read().decode("utf-8"))

    def test_history_endpoint_returns_csv_and_source_headers(self):
        archive = {
            "csv": "surface,winner_name,loser_name\nClay,Player One,Player Two\n",
            "match_count": 1,
            "invalid_stat_matches": 0,
            "years": [2024],
            "source": "LuckyLoser91/TennisCourtLog",
            "source_url": "https://github.com/LuckyLoser91/TennisCourtLog",
        }
        query = urlencode({"tour": "atp", "start": 2024, "end": 2024})
        with patch.object(tennis_api, "fetch_historical_archive", return_value=archive) as fetch:
            with urlopen(f"{self.base_url}/api/tennis-history?{query}") as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers["Content-Type"], "text/csv; charset=utf-8")
                self.assertEqual(response.headers["X-Tennis-Match-Count"], "1")
                self.assertEqual(response.headers["X-Tennis-Source"], archive["source"])

        self.assertEqual(body, archive["csv"])
        fetch.assert_called_once_with("atp", 2024, 2024)

    def test_history_endpoint_rejects_invalid_tour_and_years(self):
        query = urlencode({"tour": "other", "start": 2024, "end": 2024})
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/api/tennis-history?{query}")

        self.assertEqual(error.exception.code, 400)
        payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertIn("ATP o WTA", payload["error"])
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
