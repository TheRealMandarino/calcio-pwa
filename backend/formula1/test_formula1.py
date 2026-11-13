import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from backend.formula1 import clients, features, updater
from backend.formula1.clients import DataSourceError, JolpicaClient


def _race_result(driver_id, code, constructor_id, position, points, grid):
    return {
        "Driver": {"driverId": driver_id, "code": code},
        "Constructor": {"constructorId": constructor_id},
        "position": str(position),
        "grid": str(grid),
        "points": str(points),
        "status": "Finished",
        "FastestLap": {"Time": {"time": "1:30.000"}},
    }


class JolpicaClientTests(unittest.TestCase):
    def test_paginates_and_merges_race_rows(self):
        pages = [
            {
                "MRData": {
                    "total": "3",
                    "limit": "2",
                    "RaceTable": {
                        "Races": [
                            {"round": "1", "Results": [{"Driver": {"driverId": "a"}}]},
                            {"round": "2", "Results": [{"Driver": {"driverId": "b"}}]},
                        ]
                    },
                }
            },
            {
                "MRData": {
                    "total": "3",
                    "limit": "2",
                    "RaceTable": {
                        "Races": [
                            {"round": "2", "Results": [{"Driver": {"driverId": "c"}}]}
                        ]
                    },
                }
            },
        ]
        client = JolpicaClient()
        with patch.object(client, "_get_json", side_effect=pages) as get_json:
            races = client.fetch_races(2026, "results")

        self.assertEqual(len(races), 2)
        self.assertEqual(len(races[1]["Results"]), 2)
        self.assertEqual(get_json.call_args_list[1].args[1]["offset"], 2)

    def test_rejects_unsupported_collection(self):
        with self.assertRaises(ValueError):
            JolpicaClient().fetch_races(2026, "laps")

    def test_retries_jolpica_rate_limits(self):
        client = JolpicaClient(
            minimum_request_interval=0,
            sleep=lambda _delay: None,
            clock=lambda: 0,
        )
        response = {"MRData": {"RaceTable": {"Races": []}}}
        with patch.object(
            clients,
            "fetch_json",
            side_effect=[
                DataSourceError("rate limited", status_code=429),
                response,
            ],
        ) as fetch:
            result = client._get_json("2026.json")

        self.assertEqual(result, response)
        self.assertEqual(fetch.call_count, 2)


class FeatureEngineeringTests(unittest.TestCase):
    def _season(self):
        return {
            "year": 2026,
            "results": [
                {
                    "round": "1",
                    "raceName": "Race One",
                    "date": "2026-03-01",
                    "Circuit": {"circuitId": "track"},
                    "Results": [
                        _race_result("driver", "DRV", "team", 1, 25, 1),
                        _race_result("teammate", "TMM", "team", 3, 15, 3),
                    ],
                },
                {
                    "round": "2",
                    "raceName": "Race Two",
                    "date": "2026-03-08",
                    "Circuit": {"circuitId": "other"},
                    "Results": [
                        _race_result("driver", "DRV", "team", 2, 18, 2),
                        _race_result("teammate", "TMM", "team", 4, 12, 4),
                    ],
                },
            ],
            "qualifying": [
                {
                    "round": "1",
                    "QualifyingResults": [{
                        "Driver": {"driverId": "driver"},
                        "position": "1",
                        "Q1": "1:30.000",
                        "Q2": "1:29.000",
                    }],
                },
                {
                    "round": "2",
                    "QualifyingResults": [{
                        "Driver": {"driverId": "driver"},
                        "position": "3",
                        "Q1": "1:31.000",
                    }],
                },
            ],
            "sprints": [
                {
                    "round": "2",
                    "SprintResults": [{
                        "Driver": {"driverId": "driver"},
                        "Constructor": {"constructorId": "team"},
                        "position": "1",
                        "points": "8",
                    }],
                },
            ],
        }

    def test_features_use_only_information_from_prior_rounds(self):
        race_data = {
            "1": {
                "data": {
                    "drivers": [{"driver_number": 1, "name_acronym": "DRV"}],
                    "laps": [
                        {"driver_number": 1, "lap_duration": 90.0},
                        {"driver_number": 1, "lap_duration": 80.0, "is_pit_out_lap": True},
                    ],
                    "pit": [{"driver_number": 1}],
                    "stints": [{"driver_number": 1, "compound": "MEDIUM"}],
                }
            },
            "2": {
                "data": {
                    "drivers": [{"driver_number": 1, "name_acronym": "DRV"}],
                    "laps": [{"driver_number": 1, "lap_duration": 1.0}],
                }
            },
        }
        rows = features.build_pre_race_features(self._season(), race_data)
        race_two_driver = next(
            row for row in rows
            if row["round"] == 2 and row["driver_id"] == "driver"
        )

        self.assertEqual(rows[1]["features"]["driver_races_before"], 0)
        self.assertEqual(rows[1]["features"]["driver_points_before"], 0)
        self.assertEqual(rows[1]["features"]["constructor_points_before"], 0)
        self.assertEqual(race_two_driver["features"]["driver_points_before"], 33)
        self.assertEqual(race_two_driver["features"]["constructor_points_before"], 48)
        self.assertEqual(race_two_driver["features"]["constructor_avg_finish_last_3"], 2)
        self.assertEqual(race_two_driver["features"]["sprint_position_before_race"], 1)
        self.assertEqual(race_two_driver["features"]["driver_avg_finish_last_3"], 1)
        self.assertEqual(
            race_two_driver["features"]["prior_non_pit_out_lap_mean_seconds_last_3"],
            90,
        )
        self.assertEqual(race_two_driver["target"]["finish_position"], 2)

    def test_qualifying_time_parser_handles_minutes_and_invalid_values(self):
        self.assertEqual(features._time_seconds("1:23.456"), 83.456)
        self.assertIsNone(features._time_seconds("invalid"))

    def test_features_use_previous_seasons_but_reset_championship_points(self):
        prior_season = {
            "year": 2025,
            "results": [{
                "round": "24",
                "Circuit": {"circuitId": "track"},
                "Results": [_race_result("driver", "DRV", "team", 5, 12, 4)],
            }],
        }
        prior_race_data = {
            2025: {
                "24": {
                    "data": {
                        "drivers": [{"driver_number": 1, "name_acronym": "DRV"}],
                        "laps": [{"driver_number": 1, "lap_duration": 85.0}],
                    }
                }
            }
        }

        rows = features.build_pre_race_features(
            self._season(),
            prior_seasons=[prior_season],
            prior_race_data_by_season=prior_race_data,
        )

        self.assertEqual(rows[0]["features"]["driver_races_before"], 1)
        self.assertEqual(rows[0]["features"]["driver_points_before"], 0)
        self.assertEqual(rows[0]["features"]["driver_avg_finish_last_3"], 5)
        self.assertEqual(rows[0]["features"]["circuit_races_before"], 1)
        self.assertEqual(
            rows[0]["features"]["prior_non_pit_out_lap_mean_seconds_last_3"], 85
        )


class ArchiveUpdaterTests(unittest.TestCase):
    def test_updates_season_archive_and_pre_race_features(self):
        season_data = {
            "schedule": [{
                "round": "1",
                "raceName": "Race One",
                "date": "2026-03-01",
            }],
            "results": [{
                "round": "1",
                "raceName": "Race One",
                "date": "2026-03-01",
                "Circuit": {"circuitId": "track"},
                "Results": [_race_result("driver", "DRV", "team", 1, 25, 1)],
            }],
            "qualifying": [{
                "round": "1",
                "QualifyingResults": [{
                    "Driver": {"driverId": "driver"},
                    "position": "1",
                    "Q1": "1:30.000",
                }],
            }],
            "standings": {
                "drivers": [{"DriverStandings": [{"position": "1"}]}],
                "constructors": [{"ConstructorStandings": [{"position": "1"}]}],
            },
        }

        class FakeJolpica:
            def fetch_schedule(self, _year):
                return season_data["schedule"]

            def fetch_races(self, _year, collection):
                return season_data.get(collection, [])

            def fetch_standings(self, _year, standings_type):
                return season_data["standings"][
                    "drivers" if standings_type == "driver" else "constructors"
                ]

        class FakeOpenF1:
            def fetch_sessions(self, _year):
                return []

        with tempfile.TemporaryDirectory() as temp_dir:
            archive_updater = updater.ArchiveUpdater(
                temp_dir,
                jolpica=FakeJolpica(),
                openf1=FakeOpenF1(),
                now=lambda: datetime(2026, 12, 1, tzinfo=timezone.utc),
            )
            manifest = archive_updater.update(2026, 2026)
            season_path = Path(temp_dir) / "seasons" / "2026.json"
            feature_path = Path(temp_dir) / "features" / "2026.json"
            saved_season = json.loads(season_path.read_text(encoding="utf-8"))
            saved_features = json.loads(feature_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["seasons"][0]["completed_races"], 1)
        self.assertEqual(saved_season["standings"]["drivers"][0]["DriverStandings"][0]["position"], "1")
        self.assertEqual(saved_features["rows"][0]["features"]["driver_races_before"], 0)

    def test_collects_grand_prix_not_sprint_timing(self):
        sessions = [
            {
                "session_key": "sprint",
                "session_type": "Race",
                "session_name": "Sprint",
                "date_start": "2026-03-14T03:00:00+00:00",
                "date_end": "2026-03-14T05:00:00+00:00",
            },
            {
                "session_key": "race",
                "session_type": "Race",
                "session_name": "Race",
                "date_start": "2026-03-15T07:00:00+00:00",
                "date_end": "2026-03-15T09:00:00+00:00",
            },
        ]

        class FakeOpenF1:
            requested_sessions = []

            def fetch_sessions(self, _year):
                return sessions

            def fetch_session_data(self, session_key, include_details=False):
                self.requested_sessions.append(session_key)
                return {"laps": [{"lap_number": 1}], "drivers": []}, []

        with tempfile.TemporaryDirectory() as temp_dir:
            cached_event_path = Path(temp_dir) / "races" / "2026-02.json"
            cached_event_path.parent.mkdir(parents=True)
            cached_event_path.write_text(
                json.dumps({
                    "session": {"session_key": "sprint"},
                    "data": {"laps": [{"lap_number": 1}], "drivers": []},
                }),
                encoding="utf-8",
            )
            client = FakeOpenF1()
            archive_updater = updater.ArchiveUpdater(
                temp_dir,
                openf1=client,
                now=lambda: datetime(2026, 3, 16, tzinfo=timezone.utc),
            )
            downloaded, unavailable = archive_updater._collect_lap_data(
                2026,
                {"schedule": [{"round": "2", "date": "2026-03-15"}]},
                2026,
                include_details=False,
            )
            saved_event = json.loads(
                (
                    Path(temp_dir)
                    / "races"
                    / "2026-02.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(downloaded, 1)
        self.assertEqual(unavailable, [])
        self.assertEqual(client.requested_sessions, ["race"])
        self.assertEqual(saved_event["session"]["session_key"], "race")

    def test_rejects_year_ranges_outside_history_or_future(self):
        archive_updater = updater.ArchiveUpdater(
            now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        for start, end in ((2015, 2020), (2025, 2027), (2027, 2026)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    archive_updater.update(start, end)


class SourceValidationTests(unittest.TestCase):
    def test_invalid_json_is_reported_as_source_error(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return b"not-json"

        with patch.object(clients, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(DataSourceError):
                clients.fetch_json("https://example.test/data.json")

    def test_default_data_directory_can_be_configured(self):
        with patch.dict(
            "os.environ",
            {"CALCIOPWA_FORMULA1_DATA": "C:\\f1-cache"},
            clear=False,
        ):
            self.assertEqual(
                updater.default_data_directory(),
                Path("C:\\f1-cache"),
            )


class PredictorTests(unittest.TestCase):
    def test_build_formula1_view_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            manifest = {
                "schema_version": 1,
                "end_year": 2026,
                "generated_at": "2026-11-08T20:00:00Z",
                "seasons": [{"year": 2026}],
            }
            with (temp_path / "manifest.json").open("w", encoding="utf-8") as f:
                json.dump(manifest, f)

            seasons_dir = temp_path / "seasons"
            seasons_dir.mkdir(parents=True)
            season_data = {
                "schedule": [
                    {
                        "round": "1",
                        "raceName": "Bahrain Grand Prix",
                        "date": "2026-03-01",
                        "Circuit": {"circuitName": "Bahrain Circuit", "Location": {"locality": "Sakhir", "country": "Bahrain"}},
                    },
                    {
                        "round": "2",
                        "raceName": "Saudi Arabian Grand Prix",
                        "date": "2026-03-08",
                        "Circuit": {"circuitName": "Jeddah Circuit", "Location": {"locality": "Jeddah", "country": "Saudi Arabia"}},
                    },
                ],
                "results": [
                    {
                        "round": "1",
                        "raceName": "Bahrain Grand Prix",
                        "Results": [
                            {
                                "position": "1",
                                "Driver": {"driverId": "verstappen", "code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                                "Constructor": {"constructorId": "red_bull", "name": "Red Bull"},
                                "Time": {"time": "1:31:44.742"},
                            }
                        ],
                    }
                ],
                "standings": {
                    "drivers": [
                        {
                            "DriverStandings": [
                                {
                                    "position": "1",
                                    "points": "25",
                                    "wins": "1",
                                    "Driver": {"driverId": "verstappen", "code": "VER", "givenName": "Max", "familyName": "Verstappen"},
                                    "Constructors": [{"constructorId": "red_bull", "name": "Red Bull"}],
                                },
                                {
                                    "position": "2",
                                    "points": "18",
                                    "wins": "0",
                                    "Driver": {"driverId": "norris", "code": "NOR", "givenName": "Lando", "familyName": "Norris"},
                                    "Constructors": [{"constructorId": "mclaren", "name": "McLaren"}],
                                },
                            ]
                        }
                    ],
                    "constructors": [
                        {
                            "ConstructorStandings": [
                                {
                                    "position": "1",
                                    "points": "25",
                                    "wins": "1",
                                    "Constructor": {"constructorId": "red_bull", "name": "Red Bull"},
                                },
                                {
                                    "position": "2",
                                    "points": "18",
                                    "wins": "0",
                                    "Constructor": {"constructorId": "mclaren", "name": "McLaren"},
                                },
                            ]
                        }
                    ],
                },
            }
            with (seasons_dir / "2026.json").open("w", encoding="utf-8") as f:
                json.dump(season_data, f)

            from backend.formula1.predictor import build_formula1_view_payload
            payload = build_formula1_view_payload(temp_path)
            self.assertEqual(payload["season"], 2026)
            self.assertEqual(payload["completed_races"], 1)
            self.assertEqual(payload["total_races"], 2)
            self.assertIsNotNone(payload["next_race"])
            self.assertEqual(payload["next_race"]["round"], 2)
            self.assertEqual(payload["next_race"]["race_name"], "Saudi Arabian Grand Prix")
            self.assertGreater(len(payload["next_race"]["predictions"]), 0)
            top_pred = payload["next_race"]["predictions"][0]
            self.assertEqual(top_pred["code"], "VER")
            self.assertGreater(top_pred["win_probability"], 50)
            self.assertGreater(top_pred["odds"], 1.0)
            self.assertEqual(len(payload["calendar"]), 2)
            self.assertEqual(payload["calendar"][0]["status"], "completed")
            self.assertEqual(payload["calendar"][1]["status"], "next")

    def test_run_formula1_backtest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            features_dir = temp_path / "features"
            features_dir.mkdir(parents=True)
            mock_rows = {
                "rows": [
                    {
                        "round": 1,
                        "driver_id": "verstappen",
                        "features": {"grid_position": 1, "driver_avg_finish_last_3": 1.0, "constructor_avg_finish_last_3": 1.0, "driver_points_before": 25.0},
                        "target": {"finish_position": 1},
                    },
                    {
                        "round": 1,
                        "driver_id": "norris",
                        "features": {"grid_position": 2, "driver_avg_finish_last_3": 2.0, "constructor_avg_finish_last_3": 2.0, "driver_points_before": 18.0},
                        "target": {"finish_position": 2},
                    },
                    {
                        "round": 1,
                        "driver_id": "leclerc",
                        "features": {"grid_position": 3, "driver_avg_finish_last_3": 3.0, "constructor_avg_finish_last_3": 3.0, "driver_points_before": 15.0},
                        "target": {"finish_position": 3},
                    },
                ]
            }
            with (features_dir / "2026.json").open("w", encoding="utf-8") as f:
                json.dump(mock_rows, f)

            from backend.formula1.backtest import run_formula1_backtest
            report = run_formula1_backtest(temp_path, 2026, 2026)
            self.assertEqual(report["total_races"], 1)
            self.assertEqual(report["winner_top1_hits"], 1)
            self.assertEqual(report["favorite_on_podium_hits"], 1)
            self.assertEqual(report["podium_distribution"][3], 1)


if __name__ == "__main__":
    unittest.main()
