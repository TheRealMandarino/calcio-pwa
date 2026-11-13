import unittest

from backend.tennis.backtest import (
    WalkForwardBook,
    format_report,
    run_tennis_backtest,
)
from backend.tennis.model import analyze_match, parse_matches


HEADER = (
    "tourney_date,tourney_name,surface,winner_name,loser_name,score,best_of,minutes,"
    "w_svpt,w_1stWon,w_2ndWon,w_bpSaved,w_bpFaced,l_svpt,l_1stWon,l_2ndWon,l_bpSaved,l_bpFaced"
)


def season_csv(*rows):
    return ("\n".join((HEADER,) + rows) + "\n").encode("utf-8")


HISTORY_2025 = season_csv(
    "20250501,Madrid,Clay,Alpha,Beta,6-4 6-3,3,90,88,52,19,2,4,87,48,16,2,4",
    "20250508,Rome,Clay,Beta,Gamma,6-3 6-3,3,80,80,50,16,1,3,78,40,14,1,4",
    "20250515,Geneva,Clay,Alpha,Gamma,6-2 6-2,3,70,70,46,15,1,2,65,35,10,0,1",
)
TARGET_2026 = season_csv(
    "20260501,Estoril,Clay,Alpha,Beta,6-4 6-2,3,85,82,50,18,2,3,80,42,14,1,4",
    "20260508,Rome,Clay,Gamma,Alpha,6-4 7-6(3),3,120,100,58,20,4,6,96,55,17,3,5",
    "20260515,Lyon,Clay,Beta,Gamma,W/O,3,,80,50,16,1,2,70,40,12,1,3",
)


class TennisBacktestTests(unittest.TestCase):
    def test_walk_forward_uses_only_previous_matches(self):
        matches = parse_matches(
            HISTORY_2025.decode("utf-8") + "\n".join(TARGET_2026.decode("utf-8").splitlines()[1:]),
            strict=False,
        )
        history = parse_matches(HISTORY_2025.decode("utf-8"))
        first_2026 = [match for match in matches if match["date"].year == 2026][0]
        book = WalkForwardBook()
        for match in history:
            book.add(match)

        from_book = book.predict(first_2026)
        from_prefix = analyze_match(history, {
            "player_a": from_book["player_a"],
            "player_b": from_book["player_b"],
            "surface": first_2026["surface"],
            "match_date": first_2026["date"].isoformat(),
            "best_of": 3,
            "game_line": 21.5,
            "tournament_minutes_a": 0,
            "tournament_minutes_b": 0,
        })
        self.assertAlmostEqual(
            from_book["prediction"]["win_a"],
            from_prefix["prediction"]["win_a"],
            places=9,
        )

        leaked = analyze_match(matches, {
            "player_a": from_book["player_a"],
            "player_b": from_book["player_b"],
            "surface": first_2026["surface"],
            "match_date": first_2026["date"].isoformat(),
            "best_of": 3,
            "game_line": 21.5,
            "tournament_minutes_a": 0,
            "tournament_minutes_b": 0,
        })
        self.assertNotAlmostEqual(
            from_book["prediction"]["win_a"],
            leaked["prediction"]["win_a"],
            places=9,
        )

    def test_counts_correct_winners_and_skips_walkovers(self):
        seasons = {2025: HISTORY_2025, 2026: TARGET_2026}

        def fetch_season(tour, year):
            self.assertEqual(tour, "atp")
            return seasons[year]

        reports = run_tennis_backtest(
            tours=("atp",),
            year=2026,
            history_years=2,
            fetch_season=fetch_season,
        )
        report = reports["atp"]
        self.assertEqual(report["matches_available"], 3)
        self.assertEqual(report["skipped"], {"incontro non completato": 1})
        self.assertEqual(report["predictions"], 2)
        self.assertEqual(report["correct"], 1)
        self.assertEqual(report["accuracy"], 50.0)
        self.assertIn("ATP 2026", format_report(reports))
        self.assertIn("1/2", format_report(reports))


if __name__ == "__main__":
    unittest.main()
