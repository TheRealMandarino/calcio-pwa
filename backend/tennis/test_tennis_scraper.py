import json
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from unittest.mock import patch

from backend import server
from backend.tennis import tennis_api, tennis_scraper


TOURNAMENT_URL = "https://www.tennisabstract.com/current/2026ATPParis.html"
TOURNAMENT_DATE = "2026-11-02"

PAGE = """
<html>
<head><title>Tennis Abstract: 2026 ATP Paris Results and Forecast</title></head>
<script>
var completedSingles = 'R16: <a>Luca Van Assche</a> (FRA) <a>d.</a> <a>Lorenzo Musetti</a> (ITA) 7-5 3-6 6-4<br/>
QF: (Q)<a>Rinky Hijikata</a> (AUS) <a>d.</a> (3)<a>Tomas Martin Etcheverry</a> (ARG) 2-6 6-3 7-5<br/>
F: <a>O&#39;Connell, Christopher</a> (AUS) <a>d.</a> <a>Player Three</a> (USA) 6-4 6-4';
</script>
</html>
"""


class TennisScraperTests(unittest.TestCase):
    def test_extracts_completed_matches_and_keeps_user_surface_and_date(self):
        result = tennis_scraper.parse_tournament_page(
            PAGE,
            TOURNAMENT_URL,
            "hard_indoor",
            TOURNAMENT_DATE,
        )

        self.assertEqual(result["tournament"], "2026 ATP Paris")
        self.assertEqual(result["surface"], "hard_indoor")
        self.assertEqual(result["tournament_date"], TOURNAMENT_DATE)
        self.assertEqual(len(result["matches"]), 3)
        self.assertEqual(result["matches"][0]["winner"], "Luca Van Assche")
        self.assertEqual(result["matches"][0]["loser"], "Lorenzo Musetti")
        self.assertEqual(result["matches"][1]["winner"], "Rinky Hijikata")
        self.assertEqual(result["matches"][2]["winner"], "O'Connell, Christopher")

    def test_rejects_non_tennisabstract_urls_and_invalid_metadata(self):
        invalid_requests = [
            ("https://example.com/current/2026ATPParis.html", "clay", TOURNAMENT_DATE),
            ("https://www.tennisabstract.com/current/2026ATPParis.html?x=1", "clay", TOURNAMENT_DATE),
            ("https://www.tennisabstract.com/current/2026ATPParis.html", "synthetic", TOURNAMENT_DATE),
            ("https://www.tennisabstract.com/current/2026ATPParis.html", "clay", "2026-02-31"),
        ]
        for url, surface, tournament_date in invalid_requests:
            with self.subTest(url=url, surface=surface, tournament_date=tournament_date):
                with self.assertRaises(tennis_scraper.TennisScraperError):
                    tennis_scraper.validate_request(url, surface, tournament_date)

    def test_reports_when_page_layout_changes_or_has_no_completed_matches(self):
        with self.assertRaisesRegex(tennis_scraper.TennisScraperError, "completedSingles"):
            tennis_scraper.parse_tournament_page(
                "<html><title>ATP Paris</title></html>",
                TOURNAMENT_URL,
                "hard_indoor",
                TOURNAMENT_DATE,
            )
        with self.assertRaisesRegex(tennis_scraper.TennisScraperError, "Nessun match"):
            tennis_scraper.parse_tournament_page(
                "<title>ATP Paris</title><script>var completedSingles = 'bye';</script>",
                TOURNAMENT_URL,
                "hard_indoor",
                TOURNAMENT_DATE,
            )

    def test_fetch_uses_cache_after_a_successful_request(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return TOURNAMENT_URL

            def read(self, size):
                return PAGE.encode("utf-8")

        tennis_scraper.RESULT_CACHE.clear()
        with patch.object(tennis_scraper, "urlopen", return_value=FakeResponse()) as mocked:
            first = tennis_scraper.fetch_tournament_results(
                TOURNAMENT_URL,
                "hard_indoor",
                TOURNAMENT_DATE,
            )
            second = tennis_scraper.fetch_tournament_results(
                TOURNAMENT_URL,
                "hard_indoor",
                TOURNAMENT_DATE,
            )

        self.assertEqual(first, second)
        mocked.assert_called_once()


class TennisScraperEndpointTests(unittest.TestCase):
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

    def test_tennis_results_endpoint_returns_scraped_results(self):
        result = {
            "source": "Tennis Abstract",
            "source_url": TOURNAMENT_URL,
            "tournament": "2026 ATP Paris",
            "surface": "hard_indoor",
            "tournament_date": TOURNAMENT_DATE,
            "matches": [{"winner": "Player One", "loser": "Player Two"}],
        }
        query = urlencode({
            "url": TOURNAMENT_URL,
            "surface": "hard_indoor",
            "date": TOURNAMENT_DATE,
        })
        with patch.object(tennis_api, "fetch_tournament_results", return_value=result) as fetch:
            with urlopen(f"{self.base_url}/api/tennis-results?{query}") as response:
                payload = json.load(response)

        self.assertEqual(payload, result)
        fetch.assert_called_once_with(TOURNAMENT_URL, "hard_indoor", TOURNAMENT_DATE)

    def test_tennis_results_endpoint_rejects_untrusted_url(self):
        query = urlencode({
            "url": "https://example.com/current/2026ATPParis.html",
            "surface": "hard_indoor",
            "date": TOURNAMENT_DATE,
        })
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/api/tennis-results?{query}")

        self.assertEqual(error.exception.code, 400)
        payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertIn("tennisabstract.com", payload["error"])
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
