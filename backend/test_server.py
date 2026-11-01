import json
import time
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from backend import server


class OddsNormalizationTests(unittest.TestCase):
    def test_serie_b_is_mapped_to_supported_odds_api_sport(self):
        self.assertEqual(
            server.LEAGUES["ita-serie_b.json"],
            "soccer_italy_serie_b",
        )

    def test_selects_best_decimal_odds_for_each_result(self):
        events = [{
            "home_team": "Roma",
            "away_team": "Inter",
            "commence_time": "2026-11-02T19:45:00Z",
            "bookmakers": [
                {
                    "key": "book_a",
                    "title": "Book A",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Roma", "price": 2.4},
                            {"name": "Draw", "price": 3.2},
                            {"name": "Inter", "price": 3.1},
                        ],
                    }],
                },
                {
                    "key": "book_b",
                    "title": "Book B",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Roma", "price": 2.6},
                            {"name": "Draw", "price": 3.0},
                            {"name": "Inter", "price": 3.4},
                        ],
                    }],
                },
            ],
        }]

        self.assertEqual(server.normalize_events(events), [{
            "home_team": "Roma",
            "away_team": "Inter",
            "commence_time": "2026-11-02T19:45:00Z",
            "odds": {
                "1": {"price": 2.6, "bookmaker": "Book B"},
                "X": {"price": 3.2, "bookmaker": "Book A"},
                "2": {"price": 3.4, "bookmaker": "Book B"},
            },
        }])

    def test_ignores_invalid_and_non_match_winner_prices(self):
        events = [{
            "home_team": "Roma",
            "away_team": "Inter",
            "bookmakers": [{
                "key": "book_a",
                "markets": [
                    {
                        "key": "totals",
                        "outcomes": [{"name": "Over", "price": 1.8}],
                    },
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Roma", "price": 1},
                            {"name": "Draw", "price": "invalid"},
                        ],
                    },
                ],
            }],
        }]

        self.assertEqual(server.normalize_events(events), [])

    def test_fetch_uses_european_decimal_match_winner_market(self):
        response_body = json.dumps([{
            "home_team": "Roma",
            "away_team": "Inter",
            "bookmakers": [],
        }]).encode("utf-8")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return response_body

        server.odds_cache.clear()
        with patch.object(server, "urlopen", return_value=FakeResponse()) as mocked_urlopen:
            result = server.fetch_odds("ita-serie_a.json", "test-key")

        request = mocked_urlopen.call_args.args[0]
        self.assertIn("/soccer_italy_serie_a/odds/", request.full_url)
        self.assertIn("regions=eu", request.full_url)
        self.assertIn("markets=h2h", request.full_url)
        self.assertIn("oddsFormat=decimal", request.full_url)
        self.assertEqual(result, [])

    def test_daily_refresh_runs_once_and_persists_success_for_today(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "refresh-state.json"
            with (
                patch.object(server, "REFRESH_STATE_PATH", state_path),
                patch.object(server, "today_in_italy", return_value="2026-11-01"),
                patch.object(server, "run_daily_refresh") as run_refresh,
            ):
                initial = server.start_daily_refresh()
                self.assertEqual(initial["status"], "running")

                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    with server.REFRESH_LOCK:
                        state = dict(server.refresh_job)
                    if state["status"] != "running":
                        break
                    time.sleep(0.01)

                self.assertEqual(state["status"], "complete")
                self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), {
                    "date": "2026-11-01",
                    "status": "complete",
                })
                self.assertEqual(server.start_daily_refresh()["status"], "complete")
                run_refresh.assert_called_once()

    def test_unexpected_refresh_error_does_not_leave_job_running(self):
        with (
            patch.object(server, "run_daily_refresh", side_effect=RuntimeError("test failure")),
            patch.object(server, "refresh_job", {"status": "running", "message": ""}),
        ):
            server.refresh_worker("2026-11-01")
            self.assertEqual(server.refresh_job["status"], "failed")
            self.assertIn("test failure", server.refresh_job["message"])

    def test_local_server_serves_app_and_reports_missing_api_key(self):
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.CalcioRequestHandler)
        thread = Thread(target=http_server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{http_server.server_port}"

        try:
            with urlopen(f"{base_url}/docs/index.html") as response:
                page = response.read().decode("utf-8")
            self.assertIn("Aggiorna quote e orari", page)

            with patch.object(server, "load_api_key", return_value=None):
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base_url}/api/odds?league=ita-serie_a.json")
            self.assertEqual(error.exception.code, 503)
            self.assertIn("ODDS_API_KEY", error.exception.read().decode("utf-8"))
            error.exception.close()

            with patch.object(server, "load_api_key", return_value=None):
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base_url}/api/odds?league=ita-serie_b.json")
            self.assertEqual(error.exception.code, 503)
            self.assertIn("ODDS_API_KEY", error.exception.read().decode("utf-8"))
            error.exception.close()

            with self.assertRaises(HTTPError) as error:
                urlopen(f"{base_url}/backend/server.py")
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
