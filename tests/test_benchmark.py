"""Offline tests for src/benchmark.py (synthetic orderings only).
Run from the repo root: python -m unittest tests.test_benchmark -v
"""
import unittest

from src import benchmark


def exp(rank, prediction="not confirmed (no impact seen)", severity="Low", s=100.0):
    return {"experiment_id": f"exp-{rank:03d}", "rank": rank, "prediction": prediction, "severity": severity, "S": s}


class FirstHitTests(unittest.TestCase):
    def test_first_hit_position(self):
        self.assertEqual(benchmark.first_hit_position([False, False, True, True]), 3)
        self.assertIsNone(benchmark.first_hit_position([False, False]))


class CompareTests(unittest.TestCase):
    def test_hit_first_is_best_possible(self):
        r = benchmark.compare([True, False, False, False])
        self.assertEqual(r["ours"], 1)
        self.assertAlmostEqual(r["random_mean"], 2.5)                    # (n + 1) / (k + 1) = 5 / 2
        self.assertAlmostEqual(r["random_as_good_or_better"], 0.25)      # one hit in four: 1/4 of orderings start with it
        self.assertTrue(r["exact"])
        self.assertEqual(r["orderings"], 24)

    def test_expected_position_matches_formula(self):
        r = benchmark.compare([False, True, True, False, False, False])  # n = 6, k = 2 -> (6 + 1) / (2 + 1)
        self.assertAlmostEqual(r["random_mean"], 7 / 3)

    def test_no_hit_returns_none(self):
        self.assertIsNone(benchmark.compare([False, False, False]))

    def test_large_sample_is_seeded_and_repeatable(self):
        hits = [False] * 9 + [True]
        a, b = benchmark.compare(hits), benchmark.compare(hits)
        self.assertEqual(a, b)
        self.assertFalse(a["exact"])
        self.assertEqual(a["orderings"], benchmark.RANDOM_ORDERINGS)


class BuildTests(unittest.TestCase):
    def scores(self):
        return {"experiments": [
            exp(1, s=None),                                                       # not run: left out
            exp(2),
            exp(3, "confirmed", "Medium", 82.0),
            exp(4, "confirmed", "High", 32.7),
            exp(5),
            exp(7, s=None),                                                       # inconclusive: left out
            exp(6),
        ]}

    def test_only_scored_experiments_in_rank_order(self):
        result = benchmark.build_benchmark(self.scores())
        self.assertEqual(result["order"], ["exp-002", "exp-003", "exp-004", "exp-005", "exp-006"])
        self.assertEqual(result["any_finding"]["ours"], 2)
        self.assertEqual(result["serious_finding"]["ours"], 3)
        self.assertEqual(result["any_finding"]["n_experiments"], 5)

    def test_sentences_state_sample_size_and_do_not_overclaim(self):
        text = " ".join(benchmark.describe(benchmark.build_benchmark(self.scores())))
        self.assertIn("5 scored experiments", text)
        self.assertIn("well or better", text)

    def test_no_finding_says_so(self):
        result = benchmark.build_benchmark({"experiments": [exp(1), exp(2)]})
        self.assertIsNone(result["any_finding"])
        self.assertIn("nothing to compare", " ".join(benchmark.describe(result)))


if __name__ == "__main__":
    unittest.main()
