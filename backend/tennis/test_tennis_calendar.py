import json
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from unittest.mock import patch

from backend import server
from backend.tennis import tennis_api, tennis_calendar


NOW = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)


def competition(match_id, date, players, state="pre"):
    return {
        "id": match_id,
        "date": date,
        "status": {"type": {"state": state}},
        "venue": {"court": "Centre Court"},
        "competitors": [
            {"athlete": {"displayName": player}}
            for player in players
        ],
    }


PAYLOAD = {
    "events": [{
        "name": "Australian Open",
        "venue": {"displayName": "Melbourne, Australia"},
        "groupings": [
            {
                "grouping": {"slug": "mens-singles", "displayName": "Men's Singles"},
                "competitions": [
                    competition("1", "2026-11-07T10:00:00Z", ["Player One", "Player Two"]),
                    competition("2", "2026-11-07T11:00:00Z", ["Player One", "TBD"]),
                    competition("3", "2026-11-07T12:00:00Z", ["Player One", "Player Two"], "post"),
                ],
            },
            {
                "grouping": {"slug": "mens-doubles", "displayName": "Men's Doubles"},
                "competitions": [
                    competition("4", "2026-11-07T13:00:00Z", ["A / B", "C / D"]),
                ],
            },
        ],
    }],
}


class TennisCalendarTests(unittest.TestCase):
    def test_validates_tour_and_day_range(self):
        for tour, days in [("other", 7), ("atp", 0), ("wta", 31), ("atp", "7")]:
            with self.subTest(tour=tour, days=days):
                with self.assertRaises(tennis_calendar.TennisCalendarError):
                    tennis_calendar.validate_request(tour, days)

    def test_parses_upcoming_singles_and_skips_tbd_completed_and_doubles(self):
        matches = tennis_calendar.parse_scoreboard(PAYLOAD, "atp", NOW)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], "1")
        self.assertEqual(matches[0]["playerA"], "Player One")
        self.assertEqual(matches[0]["playerB"], "Player Two")
        self.assertEqual(matches[0]["bestOf"], 5)
        self.assertEqual(matches[0]["venue"], "Melbourne, Australia")
        self.assertEqual(matches[0]["court"], "Centre Court")

    def test_womens_and_regular_mens_matches_are_best_of_three(self):
        payload = {
            "events": [{
                "name": "Stockholm Open",
                "groupings": [{
                    "grouping": {"slug": "mens-singles"},
                    "competitions": [
                        competition("1", "2026-11-07T10:00:00Z", ["Player One", "Player Two"]),
                    ],
                }],
            }],
        }
        self.assertEqual(tennis_calendar.parse_scoreboard(payload, "atp", NOW)[0]["bestOf"], 3)
        self.assertEqual(tennis_calendar.parse_scoreboard(PAYLOAD, "wta", NOW)[0]["bestOf"], 3)

    def test_rejects_malformed_scoreboard(self):
        with self.assertRaisesRegex(tennis_calendar.TennisCalendarError, "formato"):
            tennis_calendar.parse_scoreboard({"events": None}, "atp", NOW)


class TennisCalendarEndpointTests(unittest.TestCase):
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

    def test_calendar_endpoint_returns_fixtures(self):
        calendar = {
            "tour": "ATP",
            "days": 14,
            "start_date": "2026-11-07",
            "matches": [{"playerA": "Player One", "playerB": "Player Two"}],
            "source": "ESPN",
            "source_url": "https://www.espn.com/tennis/",
            "updated_at": "2026-11-07T08:00:00+00:00",
        }
        query = urlencode({"tour": "atp", "days": 14})
        with patch.object(tennis_api, "fetch_tennis_calendar", return_value=calendar) as fetch:
            with urlopen(f"{self.base_url}/api/tennis-calendar?{query}") as response:
                payload = json.load(response)

        self.assertEqual(payload, calendar)
        fetch.assert_called_once_with("atp", 14)

    def test_calendar_endpoint_rejects_invalid_tour(self):
        query = urlencode({"tour": "other", "days": 14})
        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/api/tennis-calendar?{query}")

        self.assertEqual(error.exception.code, 400)
        payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertIn("ATP o WTA", payload["error"])
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
