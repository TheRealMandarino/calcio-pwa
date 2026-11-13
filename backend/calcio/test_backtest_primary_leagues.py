import unittest

from backend.calcio.backtest_primary_leagues import assign_matchdays, longest_losing_streak


class PrimaryLeagueBacktestTests(unittest.TestCase):
    def test_assigns_matches_to_rounds_across_weekend_dates(self):
        matches = [
            {
                "date": "2025-08-22 20:00:00",
                "season": "2025/2026",
                "home_team": "A",
                "away_team": "B",
            },
            {
                "date": "2025-08-24 15:00:00",
                "season": "2025/2026",
                "home_team": "C",
                "away_team": "D",
            },
            {
                "date": "2025-08-29 20:00:00",
                "season": "2025/2026",
                "home_team": "A",
                "away_team": "C",
            },
        ]

        assign_matchdays(matches)

        self.assertEqual([match["matchday"] for match in matches], [1, 1, 2])

    def test_longest_losing_streak_returns_fixture_details(self):
        predictions = [
            {"correct": False, "date": "2025-08-22 20:00:00", "home_team": "A", "away_team": "B"},
            {"correct": False, "date": "2025-08-23 20:00:00", "home_team": "C", "away_team": "D"},
            {"correct": True, "date": "2025-08-24 20:00:00", "home_team": "E", "away_team": "F"},
            {"correct": False, "date": "2025-08-29 20:00:00", "home_team": "G", "away_team": "H"},
        ]

        self.assertEqual(
            longest_losing_streak(predictions),
            {
                "length": 2,
                "from": "2025-08-22",
                "to": "2025-08-23",
                "matches": ["A - B", "C - D"],
            },
        )


if __name__ == "__main__":
    unittest.main()
