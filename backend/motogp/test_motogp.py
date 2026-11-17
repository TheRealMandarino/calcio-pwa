import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from backend.motogp.clients import MotoGPClient
from backend.motogp.features import build_pre_race_features
from backend.motogp.predictor import build_motogp_view_payload
from backend.motogp.updater import ArchiveUpdater


def _result(rider_id, name, position, points, team="Team A"):
    return {
        "position": position,
        "points": points,
        "status": "Finished",
        "rider": {"id": rider_id, "full_name": name, "number": 1},
        "team": {"id": team, "name": team},
    }


def _event(event_id, event_date, sessions, status="FINISHED"):
    return {
        "id": event_id,
        "name": f"Grand Prix {event_id}",
        "short_name": event_id[:3].upper(),
        "date_start": event_date,
        "date_end": event_date,
        "status": status,
        "country": "Italy",
        "circuit": {
            "id": "circuit-1",
            "name": "Italian Circuit",
            "place": "Mugello",
            "nation": "ITA",
        },
        "sessions": sessions,
    }


class PreRaceFeatureTests(unittest.TestCase):
    def test_features_use_only_completed_sessions_before_the_target(self):
        prior_season = {
            "year": 2025,
            "events": [
                _event("old", "2025-06-01", [{
                    "type": "RAC",
                    "date": "2025-06-01",
                    "classification": [_result("rider-1", "Rider One", 4, 13)],
                }]),
            ],
        }
        season = {
            "year": 2026,
            "events": [
                _event("round-1", "2026-03-01", [
                    {
                        "type": "SPR",
                        "date": "2026-03-07T10:00:00+00:00",
                        "classification": [_result("rider-1", "Rider One", 1, 12)],
                    },
                    {
                        "type": "RAC",
                        "date": "2026-03-08T10:00:00+00:00",
                        "classification": [_result("rider-1", "Rider One", 2, 20)],
                    },
                ]),
            ],
        }

        rows = build_pre_race_features(season, [prior_season])
        target_race = next(row for row in rows if row["session_type"] == "RAC")

        self.assertEqual(target_race["features"]["rider_sessions_before"], 2)
        self.assertEqual(target_race["features"]["rider_points_before"], 12)
        self.assertEqual(target_race["features"]["circuit_sessions_before"], 2)
        self.assertEqual(target_race["target"]["finish_position"], 2)
        self.assertEqual(target_race["target"]["points"], 20)


class MotoGPClientTests(unittest.TestCase):
    def test_fetch_events_uses_the_results_season_uuid(self):
        client = MotoGPClient()
        with patch.object(client, "_get_json", return_value=[]) as get_json:
            client.fetch_events("season-uuid")

        get_json.assert_called_once_with(
            "results/events",
            {"seasonUuid": "season-uuid"},
        )

    def test_fetch_categories_uses_the_results_season_uuid(self):
        client = MotoGPClient()
        with patch.object(client, "_get_json", return_value=[]) as get_json:
            client.fetch_categories("season-uuid")

        get_json.assert_called_once_with(
            "results/categories",
            {"seasonUuid": "season-uuid"},
        )


class MotoGPArchiveTests(unittest.TestCase):
    def test_archive_update_writes_calendar_features_and_prediction_payload(self):
        finished_event = _event("finished", "2026-03-01", [])
        missing_result_event = _event("missing-result", "2026-03-02", [])
        upcoming_event = _event("upcoming", "2026-03-08", [], status="SCHEDULED")

        class FakeMotoGPClient:
            def fetch_seasons(self):
                return [{"id": "season-2026", "year": 2026}]

            def fetch_categories(self, _year):
                return [{"id": "category-motogp", "name": "MotoGP™"}]

            def fetch_events(self, _year):
                return [finished_event, missing_result_event, upcoming_event]

            def fetch_sessions(self, event_id, _category_id):
                if event_id == "finished":
                    return [{
                        "id": "race-session",
                        "type": "RAC",
                        "date": "2026-03-01T10:00:00+00:00",
                        "status": "FINISHED",
                    }]
                if event_id == "missing-result":
                    return []
                return [{
                    "id": "upcoming-session",
                    "type": "RAC",
                    "date": "2026-03-08T10:00:00+00:00",
                    "status": "NOT-STARTED",
                }]

            def fetch_classification(self, _session_id):
                return {
                    "classification": [
                        _result("rider-1", "Rider One", 1, 25),
                        _result("rider-2", "Rider Two", 2, 20, "Team B"),
                    ]
                }

            def fetch_standings(self, _season_id, _category_id):
                return {
                    "classification": [
                        {
                            "position": 1,
                            "rider": {"id": "rider-1", "full_name": "Rider One"},
                            "team": {"name": "Team A"},
                            "points": 25,
                            "race_wins": 1,
                            "sprint_wins": 0,
                            "podiums": 1,
                        },
                        {
                            "position": 2,
                            "rider": {"id": "rider-2", "full_name": "Rider Two"},
                            "team": {"name": "Team B"},
                            "points": 20,
                            "race_wins": 0,
                            "sprint_wins": 0,
                            "podiums": 1,
                        },
                    ]
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            updater = ArchiveUpdater(
                temp_dir,
                client=FakeMotoGPClient(),
                now=lambda: date(2026, 3, 2),
            )
            manifest = updater.update(2026, 2026)
            payload = build_motogp_view_payload(temp_dir, today="2026-03-02")
            feature_path = Path(temp_dir) / "features" / "2026.json"
            features = json.loads(feature_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["seasons"][0]["completed_races"], 1)
        self.assertEqual(features["rows"][0]["target"]["finish_position"], 1)
        self.assertEqual(payload["completed_races"], 1)
        self.assertEqual(payload["calendar"][1]["status"], "unavailable")
        self.assertEqual(payload["next_race"]["name"], "Grand Prix upcoming")
        self.assertEqual(payload["leader"]["name"], "Rider One")
        self.assertEqual(len(payload["next_race"]["predictions"]), 2)
        self.assertEqual(
            len(payload["next_race"]["race_predictions"]),
            2,
        )
        self.assertEqual(
            len(payload["next_race"]["sprint_predictions"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
