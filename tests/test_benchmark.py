import unittest

from src import benchmark as b


def plan(n):
    return [{"experiment_id": f"e{i}", "rank": i} for i in range(1, n + 1)]


def rec(exp, outcome, aborted=False):
    return {"experiment_id": exp, "outcome": outcome, "aborted": aborted}


class BenchmarkTests(unittest.TestCase):
    def test_first_hit_position(self):
        recs = [rec("e1", "survived"), rec("e2", "survived"), rec("e3", "not_recovered")]
        out = b.build_benchmark(plan(3), recs, {})
        self.assertEqual(out["status"], "applicable")
        self.assertEqual(out["our_first_hit_position"], 3)
        self.assertEqual(out["hits"], ["e3"])

    def test_order_follows_rank_not_plan_listing(self):
        p = [{"experiment_id": "b", "rank": 2}, {"experiment_id": "a", "rank": 1}]
        out = b.build_benchmark(p, [rec("a", "degraded_recovered"), rec("b", "survived")], {})
        self.assertEqual(out["our_order"], ["a", "b"])
        self.assertEqual(out["our_first_hit_position"], 1)

    def test_no_hits_not_applicable(self):
        out = b.build_benchmark(plan(2), [rec("e1", "survived"), rec("e2", "survived")], {})
        self.assertEqual(out["status"], "not_applicable")
        self.assertIsNone(out["our_first_hit_position"])
        self.assertIsNone(out["random_mean_first_hit_position"])
        self.assertIn("not applicable", out["caveat"])

    def test_inconclusive_only_excluded(self):
        recs = [rec("e1", "inconclusive"), rec("e2", "survived"), rec("e3", "not_recovered"),
                rec("e3", "inconclusive")]
        out = b.build_benchmark(plan(3), recs, {})
        self.assertEqual(out["our_order"], ["e2", "e3"])
        self.assertEqual(out["n_run"], 2)
        self.assertEqual(out["our_first_hit_position"], 2)

    def test_aborted_counts_as_hit(self):
        out = b.build_benchmark(plan(2), [rec("e1", "survived"), rec("e2", "survived", True)], {})
        self.assertEqual(out["hits"], ["e2"])

    def test_baseline_reproducible_and_close_to_analytic(self):
        cfg = {"benchmark": {"iterations": 1000, "seed": 42}}
        recs = [rec("e1", "survived"), rec("e2", "survived"), rec("e3", "not_recovered")]
        a = b.build_benchmark(plan(3), recs, cfg)
        c = b.build_benchmark(plan(3), recs, cfg)
        self.assertEqual(a["random_mean_first_hit_position"], c["random_mean_first_hit_position"])
        self.assertEqual(a["analytic_expected_position"], 2.0)
        self.assertAlmostEqual(a["random_mean_first_hit_position"], 2.0, delta=0.1)

    def test_share_computation(self):
        ids = ["a", "b", "c"]
        base = b.random_baseline(ids, {"a"}, 3, 200, 1)
        self.assertEqual(base["share_as_good"], 1.0)  # position <= 3 always
        base = b.random_baseline(ids, {"a"}, 1, 3000, 1)
        self.assertAlmostEqual(base["share_as_good"], 1 / 3, delta=0.04)

    def test_summary_line(self):
        recs = [rec("e1", "not_recovered"), rec("e2", "survived"), rec("e3", "survived")]
        line = b.summary_line(b.build_benchmark(plan(3), recs, {}))
        self.assertIn("position 1 of 3", line)
        self.assertIn("small sample: 3 experiments, 1 hit", line)


if __name__ == "__main__":
    unittest.main()
