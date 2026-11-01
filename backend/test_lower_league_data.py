import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from backend.backtest_lower_leagues import run_backtest
from backend.lower_league_data import (
    download_lower_league_data,
    parse_season_csv,
    seasons_to_import,
    update_scheduled_fixtures,
    update_lower_leagues,
)
from backend.model import calculate_match_probabilities, get_h2h_factor, resolve_team_name
from backend.scraper import init_db
from backend.stats import compute_team_stats


CSV_CONTENT = (
    b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HxG,AxG,HS,AS,HST,AST,HC,AC,AvgCH,AvgCD,AvgCA\r\n"
    b"I2,22/08/25,19:30,Pescara,Cesena,1,3,1.62,0.93,12,19,4,9,3,5,3.60,3.20,2.10\r\n"
    b"I2,23/08/25,18:00,Empoli,Padova,,,,,,,,,,,,,,\r\n"
)


class LowerLeagueDataTests(unittest.TestCase):
    @staticmethod
    def csv_for_season(url):
        season = url.split("/")[-2]
        year_suffix = season[:2]
        content = CSV_CONTENT.replace(b"22/08/25", f"22/08/{year_suffix}".encode()).replace(
            b"23/08/25",
            f"23/08/{year_suffix}".encode(),
        )
        if season != "2627":
            content = content.replace(b"1.62,0.93,", b",,")
        return content

    def test_imports_three_latest_seasons(self):
        self.assertEqual(
            seasons_to_import(date(2026, 11, 1)),
            [("2425", "2024/2025"), ("2526", "2025/2026"), ("2627", "2026/2027")],
        )

    def test_parses_scores_time_stats_and_closing_odds_without_fabricating_xg(self):
        content_without_xg = CSV_CONTENT.replace(b"1.62,0.93,", b",,")
        matches, details = parse_season_csv(
            content_without_xg,
            "I2",
            "ITA-Serie B",
            "2025/2026",
        )

        self.assertEqual(matches[0], (
            "2025-08-22 19:30:00", "ITA-Serie B", "Pescara", "Cesena",
            1, 3, None, None, "finished", "2025/2026",
        ))
        self.assertEqual(matches[1][8], "scheduled")
        self.assertEqual(details[0][4:], (12, 19, 4, 9, 3, 5, 3.6, 3.2, 2.1))

    def test_imports_xg_only_when_csv_provides_it(self):
        xg_content = CSV_CONTENT.replace(b"22/08/25", b"22/08/26")
        current_matches, _ = parse_season_csv(
            xg_content,
            "I2",
            "ITA-Serie B",
            "2026/2027",
        )
        old_content = CSV_CONTENT.replace(b"1.62,0.93,", b",,")
        previous_matches, _ = parse_season_csv(
            old_content,
            "I2",
            "ITA-Serie B",
            "2025/2026",
        )

        self.assertEqual(current_matches[0][6:8], (1.62, 0.93))
        self.assertEqual(previous_matches[0][6:8], (None, None))

    def test_downloads_both_leagues_for_each_season(self):
        requested = []

        def fake_fetch(url):
            requested.append(url)
            return self.csv_for_season(url)

        matches, _ = download_lower_league_data(date(2026, 11, 1), fetcher=fake_fetch)

        self.assertEqual(len(requested), 6)
        self.assertTrue(any("/2425/I2.csv" in url for url in requested))
        self.assertTrue(any("/2627/E1.csv" in url for url in requested))
        self.assertEqual(len(matches), 12)

    def test_database_import_preserves_other_leagues_and_stores_nullable_xg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "matches.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE matches (
                    date TEXT, league TEXT, home_team TEXT, away_team TEXT,
                    home_goals INTEGER, away_goals INTEGER, home_xg REAL,
                    away_xg REAL, status TEXT, season TEXT
                )
            """)
            conn.execute(
                "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-08-01 15:00:00", "ITA-Serie A", "Milan", "Inter", 1, 0, None, None, "finished", "2026/2027"),
            )
            conn.commit()
            conn.close()

            update_lower_leagues(
                db_path,
                today=date(2026, 11, 1),
                fetcher=self.csv_for_season,
            )

            conn = sqlite3.connect(db_path)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM matches WHERE league='ITA-Serie A'").fetchone()[0],
                1,
            )
            imported = conn.execute(
                "SELECT home_goals, away_goals, home_xg, away_xg FROM matches WHERE league='ITA-Serie B' AND season='2026/2027' LIMIT 1"
            ).fetchone()
            self.assertEqual(imported, (1, 3, 1.62, 0.93))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM lower_league_details").fetchone()[0],
                12,
            )
            conn.close()

    def test_championship_fixtures_replace_only_unplayed_matches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "fixtures.db")
            init_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.executemany(
                "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("2026-11-02 19:00:00", "ENG-Championship", "Old", "Fixture", None, None, None, None, "scheduled", "2026/2027"),
                    ("2026-10-30 19:00:00", "ENG-Championship", "Played", "Fixture", 1, 0, None, None, "finished", "2026/2027"),
                ],
            )
            conn.commit()
            conn.close()

            update_scheduled_fixtures(
                db_path,
                "ENG-Championship",
                [
                    {
                        "home_team": "Blackburn Rovers",
                        "away_team": "Stoke City",
                        "commence_time": "2026-11-03T19:45:00Z",
                    },
                    {
                        "home_team": "Old Match",
                        "away_team": "Already Played",
                        "commence_time": "2026-10-31T19:45:00Z",
                    },
                ],
                now=datetime(2026, 11, 1, tzinfo=timezone.utc),
            )

            conn = sqlite3.connect(db_path)
            scheduled = conn.execute(
                "SELECT date, home_team FROM matches WHERE league=? AND home_goals IS NULL",
                ("ENG-Championship",),
            ).fetchall()
            completed_count = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE league=? AND home_goals IS NOT NULL",
                ("ENG-Championship",),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(scheduled, [("2026-11-03 20:45:00", "Blackburn Rovers")])
            self.assertEqual(completed_count, 1)
            update_scheduled_fixtures(
                db_path,
                "ENG-Championship",
                [],
                now=datetime(2026, 11, 1, tzinfo=timezone.utc),
            )
            conn = sqlite3.connect(db_path)
            retained_count = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE league=? AND home_goals IS NULL",
                ("ENG-Championship",),
            ).fetchone()[0]
            conn.close()
            self.assertEqual(retained_count, 1)

    def test_scheduled_fixtures_support_serie_b_and_team_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "serie-b.db")
            init_db(db_path)

            update_scheduled_fixtures(
                db_path,
                "ITA-Serie B",
                [{
                    "home_team": "Cesena FC",
                    "away_team": "US Catanzaro 1929",
                    "commence_time": "2026-11-07T14:00:00Z",
                }],
                now=datetime(2026, 11, 1, tzinfo=timezone.utc),
            )

            conn = sqlite3.connect(db_path)
            fixture = conn.execute(
                "SELECT home_team, away_team FROM matches WHERE league='ITA-Serie B' AND home_goals IS NULL"
            ).fetchone()
            conn.close()
            self.assertEqual(fixture, ("Cesena FC", "US Catanzaro 1929"))

        self.assertEqual(resolve_team_name("Blackburn Rovers", {"Blackburn"}), "Blackburn")
        self.assertEqual(resolve_team_name("Norwich City", {"Norwich"}), "Norwich")
        self.assertEqual(resolve_team_name("S\u00fcdtirol", {"Sudtirol"}), "Sudtirol")

    def test_walk_forward_backtest_reports_both_leagues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "backtest.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE matches (
                    date TEXT, league TEXT, home_team TEXT, away_team TEXT,
                    home_goals INTEGER, away_goals INTEGER, home_xg REAL,
                    away_xg REAL, season TEXT
                )
            """)
            for league, team in (("ITA-Serie B", "Team B"), ("ENG-Championship", "Team C")):
                start = date(2025, 8, 1)
                for day in range(32):
                    conn.execute(
                        "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"{start + timedelta(days=day)} 15:00:00", league, team, f"Opponent {day}", 2, 0, None, None, "2025/2026"),
                    )
                conn.execute(
                    "INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("2025-10-01 15:00:00", league, team, "Final Opponent", 2, 0, None, None, "2025/2026"),
                )
            conn.commit()
            conn.close()

            reports = run_backtest(db_path, min_history=30)

            self.assertEqual(reports["ITA-Serie B"]["predictions"], 3)
            self.assertEqual(reports["ENG-Championship"]["predictions"], 3)
            self.assertEqual(
                reports["ITA-Serie B"]["seasons"]["2025/2026"]["predictions"],
                3,
            )
            model = calculate_match_probabilities(
                "A",
                "B",
                league="ITA-Serie B",
                historical_matches=[
                    {"date": "2025-01-01", "league": "ITA-Serie B", "home_team": "A", "away_team": "B", "home_goals": 2, "away_goals": 0},
                    {"date": "2025-01-02", "league": "ITA-Serie B", "home_team": "B", "away_team": "A", "home_goals": 0, "away_goals": 1},
                ],
            )
            self.assertAlmostEqual(model["prob_1"] + model["prob_X"] + model["prob_2"], 1.0)

    def test_lower_league_stats_pool_venues_unless_difference_is_material(self):
        balanced = []
        split = []
        start = date(2025, 1, 1)
        for index in range(10):
            balanced.extend([
                {
                    "date": str(start + timedelta(days=index * 2)),
                    "league": "ITA-Serie B",
                    "home_team": "Balanced",
                    "away_team": f"Opponent {index}",
                    "home_goals": 2,
                    "away_goals": 1,
                },
                {
                    "date": str(start + timedelta(days=index * 2 + 1)),
                    "league": "ITA-Serie B",
                    "home_team": f"Opponent away {index}",
                    "away_team": "Balanced",
                    "home_goals": 1,
                    "away_goals": 2,
                },
            ])
            split.extend([
                {
                    "date": str(start + timedelta(days=index * 2)),
                    "league": "ENG-Championship",
                    "home_team": "Split",
                    "away_team": f"Opponent {index}",
                    "home_goals": 3,
                    "away_goals": 1,
                },
                {
                    "date": str(start + timedelta(days=index * 2 + 1)),
                    "league": "ENG-Championship",
                    "home_team": f"Opponent away {index}",
                    "away_team": "Split",
                    "home_goals": 1,
                    "away_goals": 1,
                },
            ])

        balanced_stats = compute_team_stats(
            matches_df=pd.DataFrame(balanced),
            league="ITA-Serie B",
        )
        split_stats = compute_team_stats(
            matches_df=pd.DataFrame(balanced + split),
            league="ENG-Championship",
        )
        balanced_home_attack, _, balanced_away_attack = (
            balanced_stats[0],
            balanced_stats[1],
            balanced_stats[2],
        )
        split_home_attack, _, split_away_attack = (
            split_stats[0],
            split_stats[1],
            split_stats[2],
        )

        self.assertAlmostEqual(balanced_home_attack["Balanced"], balanced_away_attack["Balanced"])
        self.assertGreater(split_home_attack["Split"], split_away_attack["Split"])

    def test_lower_league_stats_use_xg_when_available_and_goals_when_missing(self):
        matches = pd.DataFrame([
            {
                "date": "2026-10-01",
                "league": "ITA-Serie B",
                "home_team": "XG Team",
                "away_team": "Opponent",
                "home_goals": 0,
                "away_goals": 0,
                "home_xg": 2.0,
                "away_xg": 0.5,
            },
            {
                "date": "2026-10-02",
                "league": "ITA-Serie B",
                "home_team": "Opponent 2",
                "away_team": "XG Team",
                "home_goals": 0,
                "away_goals": 0,
                "home_xg": None,
                "away_xg": None,
            },
        ])
        stats_with_xg = compute_team_stats(matches_df=matches, league="ITA-Serie B")
        goals_only = matches.copy()
        goals_only["home_xg"] = None
        goals_only["away_xg"] = None
        stats_with_goals = compute_team_stats(matches_df=goals_only, league="ITA-Serie B")

        self.assertGreater(stats_with_xg[0]["XG Team"], stats_with_goals[0]["XG Team"])

    def test_home_advantage_is_small_and_only_applied_to_requested_leagues(self):
        neutral_stats = (
            {"Team A": 1.0},
            {"Team A": 1.0, "Team B": 1.0},
            {"Team B": 1.0},
            {"Team A": 1.0, "Team B": 1.0},
            {"ITA-Serie B": 1.0, "ITA-Serie A": 1.0},
            {"ITA-Serie B": 1.0, "ITA-Serie A": 1.0},
            1.0,
            1.0,
        )
        serie_b = calculate_match_probabilities(
            "Team A",
            "Team B",
            league="ITA-Serie B",
            precomputed_stats=neutral_stats,
        )
        serie_a = calculate_match_probabilities(
            "Team A",
            "Team B",
            league="ITA-Serie A",
            precomputed_stats=neutral_stats,
        )

        self.assertEqual(serie_b["lambda_home"], 1.05)
        self.assertEqual(serie_b["lambda_away"], 1.0)
        self.assertEqual(serie_a["lambda_home"], 1.0)
        self.assertEqual(serie_a["lambda_away"], 1.0)

    def test_h2h_scope_is_only_changed_for_the_two_lower_leagues(self):
        history = pd.DataFrame([
            {
                "league": "ITA-Serie A",
                "home_team": "A",
                "away_team": "B",
                "home_goals": 4,
                "away_goals": 0,
            },
            {
                "league": "ITA-Serie B",
                "home_team": "A",
                "away_team": "B",
                "home_goals": 0,
                "away_goals": 3,
            },
        ])

        main_league_factor = get_h2h_factor(
            "A", "B", league="ITA-Serie A", historical_matches=history
        )
        lower_league_factor = get_h2h_factor(
            "A", "B", league="ITA-Serie B", historical_matches=history
        )

        self.assertNotEqual(main_league_factor, lower_league_factor)
        self.assertEqual(
            main_league_factor,
            get_h2h_factor("A", "B", historical_matches=history),
        )


if __name__ == "__main__":
    unittest.main()
