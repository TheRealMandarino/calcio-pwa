import unittest

from backend.tennis.model import (
    TennisModelError,
    analyze_match,
    normalize_surface,
    parse_csv,
    parse_matches,
    summarize_surface,
)


CSV = "\n".join([
    "tourney_date,tourney_name,surface,winner_name,loser_name,score,best_of,minutes,"
    "w_svpt,w_1stWon,w_2ndWon,w_bpSaved,w_bpFaced,l_svpt,l_1stWon,l_2ndWon,l_bpSaved,l_bpFaced",
    '20251001,"Rome, Open",Clay,Alpha,Beta,7-6(4) 6-4,3,120,100,60,20,4,6,100,54,18,3,5',
    "20251003,Rome Open,Clay,Beta,Alpha,6-4 7-6(5),3,110,95,58,18,3,5,96,55,17,4,6",
    "20251005,Rome Open,Clay,Alpha,Beta,6-4 6-3,3,90,88,52,19,2,4,87,48,16,2,4",
    "20251006,Indoor Open,Hard,Alpha,Beta,6-2 6-2,3,70,70,46,15,1,2,65,35,10,0,1",
])


class TennisModelTests(unittest.TestCase):
    def test_csv_parser_supports_quoted_commas_and_normalizes_headers(self):
        rows = parse_csv(CSV)
        self.assertEqual(rows[0]["tourneyname"], "Rome, Open")
        self.assertEqual(rows[0]["winnername"], "Alpha")
        self.assertEqual(normalize_surface("Hard"), "hard_unspecified")
        self.assertEqual(normalize_surface("Hard Indoor"), "hard_indoor")
        self.assertEqual(normalize_surface("Hard Outdoor"), "hard_outdoor")

    def test_surface_aggregates_exclude_other_surfaces(self):
        matches = parse_matches(CSV)
        clay = summarize_surface(matches, "clay")
        alpha = clay["players"]["alpha"]
        beta = clay["players"]["beta"]

        self.assertEqual(len(clay["matches"]), 3)
        self.assertEqual(alpha["matches"], 3)
        self.assertEqual(alpha["wins"], 2)
        self.assertEqual(beta["wins"], 1)
        self.assertEqual(alpha["tie_breaks"], 2)
        self.assertEqual(alpha["tie_breaks_won"], 1)
        self.assertGreater(alpha["hold_rate"], 0)
        self.assertLess(alpha["hold_rate"], 1)
        self.assertGreater(alpha["break_rate"], 0)
        self.assertLess(alpha["break_rate"], 1)
        self.assertGreater(alpha["break_point_save_rate"], 0)
        self.assertLessEqual(alpha["break_point_save_rate"], 1)
        self.assertGreaterEqual(alpha["break_point_conversion_rate"], 0)
        self.assertLessEqual(alpha["break_point_conversion_rate"], 1)
        self.assertEqual(len(summarize_surface(matches, "hard_indoor")["matches"]), 0)
        self.assertEqual(len(summarize_surface(matches, "hard_unspecified")["matches"]), 1)

    def test_match_probabilities_include_scores_and_fatigue(self):
        matches = parse_matches(CSV)
        result = analyze_match(matches, {
            "player_a": "Alpha",
            "player_b": "Beta",
            "surface": "clay",
            "match_date": "2025-10-10",
            "best_of": "3",
            "game_line": "21.5",
            "tournament_minutes_a": "120",
            "tournament_minutes_b": "0",
        })

        self.assertEqual(result["h2h"]["matches"], 3)
        self.assertEqual(result["recent_a"]["matches"], 4)
        self.assertLess(result["fatigue_shift"], 0)
        prediction = result["prediction"]
        self.assertAlmostEqual(prediction["total_probability"], 1, places=9)
        self.assertAlmostEqual(prediction["win_a"] + prediction["win_b"], 1, places=9)
        self.assertAlmostEqual(
            prediction["over"] + prediction["under"] + prediction["push"],
            1,
            places=9,
        )
        self.assertGreater(prediction["expected_games"], 12)
        self.assertLess(prediction["expected_games"], 60)
        self.assertGreaterEqual(prediction["at_least_one_tie_break"], 0)
        self.assertLessEqual(prediction["at_least_one_tie_break"], 1)
        self.assertGreater(prediction["set_scores"]["2-0"], 0)
        self.assertGreater(prediction["set_scores"]["0-2"], 0)

    def test_best_of_five_and_no_surface_fallback(self):
        matches = parse_matches(CSV)
        result = analyze_match(matches, {
            "player_a": "Alpha",
            "player_b": "Beta",
            "surface": "clay",
            "match_date": "2025-10-10",
            "best_of": "5",
            "game_line": "21.5",
            "tournament_minutes_a": "0",
            "tournament_minutes_b": "0",
        })
        self.assertGreater(result["prediction"]["set_scores"]["3-0"], 0)
        self.assertGreater(result["prediction"]["set_scores"]["0-3"], 0)
        with self.assertRaises(TennisModelError) as error:
            analyze_match(matches, {
                "player_a": "Alpha",
                "player_b": "Beta",
                "surface": "hard_indoor",
                "match_date": "2025-10-10",
                "best_of": "3",
                "game_line": "21.5",
            })
        self.assertIn("cemento indoor", str(error.exception))
        self.assertIn("cemento non specificato (1)", str(error.exception))

    def test_unknown_players_are_distinguished_from_other_surfaces(self):
        matches = parse_matches(CSV)
        with self.assertRaises(TennisModelError) as error:
            analyze_match(matches, {
                "player_a": "Alpha",
                "player_b": "Unknown Player",
                "surface": "hard_indoor",
                "match_date": "2025-10-10",
                "best_of": "3",
                "game_line": "21.5",
            })
        self.assertIn(
            "Unknown Player: nessun match nello storico caricato o nome non riconosciuto",
            str(error.exception),
        )

    def test_invalid_headers_and_inconsistent_serve_totals_are_rejected(self):
        with self.assertRaises(TennisModelError) as error:
            parse_matches("surface,winner_name\nClay,Alpha")
        self.assertIn("Colonne obbligatorie", str(error.exception))
        invalid = CSV.replace(",100,60,20,4,6,", ",100,90,20,4,6,")
        with self.assertRaises(TennisModelError) as error:
            parse_matches(invalid)
        self.assertIn("superiori ai punti giocati", str(error.exception))


if __name__ == "__main__":
    unittest.main()
