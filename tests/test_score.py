"""Offline tests for src/score.py and src/report.py. Synthetic run files only: never reported, never a baseline.
Run from the repo root: python -m unittest tests.test_score -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from src import report, score

T_INJECT, T_CLEAR = 1000.0, 1060.0


def journeys(a=1.0, l=1.0, n=30, **override):
    base = {j: {"n": n, "A": a, "L": l} for j in ("browse", "cart", "checkout")}
    base.update(override)
    return base


def run_file(exp="exp-x", repeat=1, run_id="run-1", outcome="survived", measured=("cart", "checkout"),
             recovery=0.0, c=1.0, quality="ok", aborted=False, forced=False, **kw):
    return {"run_id": run_id, "plan_experiment_id": exp, "repeat": repeat, "outcome": outcome, "quality": quality,
            "journeys": kw.pop("journeys_value", journeys()), "containment_C": c, "impact_recovery_s": recovery,
            "aborted": aborted, "abort_reason": "probe_failure" if aborted else None, "forced_breach": forced,
            "measured_journeys": list(measured), "t_inject": T_INJECT, "t_clear": T_CLEAR,
            "plan": {"recovery_budget_s": 120}, "evidence": kw.pop("evidence", None)}


def plan_item(exp="exp-x", service="cart", risk="R-cart-single_replica_no_pdb", fault="pod_kill"):
    return {"experiment_id": exp, "rank": 1, "risk_id": risk, "fault_type": fault, "mechanism": "chaosmesh",
            "target": {"namespace": "otel-demo", "service": service}, "params": {"duration_s": 60},
            "hypothesis": "A testable hypothesis.", "expected_impact": {"journeys": ["cart", "checkout"]},
            "slo": {"success_rate_min": 0.95, "p95_ms_max": 1500}, "recovery_budget_s": 120}


def no_samples(run_id):
    return []


class ScoreRunTests(unittest.TestCase):
    def scored(self, **kw):
        run = run_file(**kw)
        return score.score_run(run, score.stored_metrics(run))

    def test_survived_scores_100(self):
        r = self.scored()
        self.assertEqual((r["status"], r["S"]), ("scored", 100.0))

    def test_formula_matches_d008_by_hand(self):
        # A 0, L 0, R = 1 - 14/120, C 1  ->  100 x (0.20 x 0.8833 + 0.15) = 32.667
        r = self.scored(outcome="degraded_recovered", recovery=14.0, journeys_value=journeys(a=0.0, l=0.0))
        self.assertAlmostEqual(r["S"], 100 * (0.20 * (1 - 14 / 120) + 0.15), places=6)

    def test_latency_only_degradation_exp003_shape(self):
        # browse L 1.0, checkout L 0.30 weighted 0.2 / 0.4 -> L = 0.5333; R = 1 - 60.25/120
        js = journeys(checkout={"n": 30, "A": 1.0, "L": 0.30})
        r = self.scored(outcome="degraded_recovered", measured=("browse", "checkout"), recovery=60.25,
                        journeys_value=js)
        expected_l = (0.2 * 1.0 + 0.4 * 0.30) / 0.6
        self.assertAlmostEqual(r["L"], expected_l, places=6)
        self.assertAlmostEqual(r["S"], 100 * (0.45 + 0.20 * expected_l + 0.20 * (1 - 60.25 / 120) + 0.15), places=6)

    def test_c_not_applicable_rescales_weights(self):
        r = self.scored(c=None)
        self.assertTrue(r["c_weights_rescaled"])
        self.assertEqual(r["S"], 100.0)                       # all ones stays 100, not 85
        r2 = self.scored(c=None, outcome="degraded_recovered", recovery=60.0, journeys_value=journeys(a=0.5, l=0.5))
        self.assertAlmostEqual(r2["S"], 100 * (0.45 * 0.5 + 0.20 * 0.5 + 0.20 * 0.5) / 0.85, places=6)

    def test_not_recovered_has_zero_r(self):
        r = self.scored(outcome="not_recovered", recovery=None, journeys_value=journeys(a=0.2, l=0.2))
        self.assertEqual(r["R"], 0.0)

    def test_thin_window_is_inconclusive_exp007_shape(self):
        js = journeys(a=0.0, l=0.0, n=2)
        r = self.scored(outcome="degraded_recovered", recovery=10.0, aborted=True,
                        measured=("browse", "cart", "checkout"), c=None, journeys_value=js)
        self.assertEqual(r["status"], "inconclusive")
        self.assertIsNone(r["S"])
        self.assertIn("too few samples", r["reasons"][0])
        self.assertTrue(r["aborted"])

    def test_thin_journey_is_left_out_not_zeroed(self):
        js = journeys(cart={"n": 2, "A": 0.0, "L": 0.0})
        r = self.scored(journeys_value=js)
        self.assertEqual(r["journeys_used"], ["checkout"])
        self.assertEqual(r["journeys_too_thin"], ["cart"])
        self.assertEqual(r["S"], 100.0)

    def test_inconclusive_quality_is_never_scored(self):
        r = self.scored(quality="inconclusive")
        self.assertEqual(r["status"], "inconclusive")
        self.assertIsNone(r["S"])

    def test_severity_steps(self):
        self.assertEqual(score.severity_label(32.7, 0.3)[1], "High")       # 67.3 x 0.3 = 20.2
        self.assertEqual(score.severity_label(80.6, 0.3)[1], "Medium")     # 19.4 x 0.3 = 5.8
        self.assertEqual(score.severity_label(95.0, 0.3)[1], "Low")
        self.assertEqual(score.severity_label(0.0, 0.4)[1], "Critical")


class BuildScoresTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "runs").mkdir()
        self.plan = self.dir / "plan.json"
        self.crit = self.dir / "criticality.yaml"
        self.crit.write_text("default: 0.1\nservices:\n  cart: 0.3\n  frontend: 0.4\n", encoding="utf-8")

    def write_run(self, name, run):
        (self.dir / "runs" / name).write_text(json.dumps(run), encoding="utf-8")

    def build(self, items):
        self.plan.write_text(json.dumps(items), encoding="utf-8")
        return score.build_scores(str(self.dir / "runs"), str(self.plan), str(self.crit), samples_reader=no_samples)

    def test_forced_breach_files_are_never_scored(self):
        self.write_run("exp-x-r1.json", run_file())
        self.write_run("exp-x-r1-forced-run-9.json", run_file(run_id="run-9", forced=True, outcome="inconclusive"))
        scores = self.build([plan_item()])
        self.assertEqual(scores["experiments"][0]["n_runs"], 1)

    def test_repeats_are_averaged_and_prediction_is_mixed(self):
        self.write_run("exp-x-r1.json", run_file(repeat=1, run_id="run-1"))
        self.write_run("exp-x-r2.json", run_file(repeat=2, run_id="run-2", outcome="degraded_recovered", recovery=12.0,
                                                 journeys_value=journeys(a=0.5, l=0.5)))
        exp = self.build([plan_item()])["experiments"][0]
        self.assertEqual((exp["n_runs"], exp["n_scored"]), (2, 2))
        self.assertAlmostEqual(exp["S"], (exp["runs"][0]["S"] + exp["runs"][1]["S"]) / 2)
        self.assertTrue(exp["prediction"].startswith("mixed"))

    def test_overall_is_criticality_weighted_and_has_coverage(self):
        self.write_run("a-r1.json", run_file(exp="exp-a"))                                   # S 100, cart 0.3
        self.write_run("b-r1.json", run_file(exp="exp-b", run_id="run-b", outcome="degraded_recovered", recovery=60.0,
                                             journeys_value=journeys(a=0.0, l=0.0)))
        items = [plan_item("exp-a"), plan_item("exp-b", service="frontend", risk="R-f"),
                 plan_item("exp-c", risk="R-c")]
        scores = self.build(items)
        s_b = scores["experiments"][1]["S"]
        self.assertAlmostEqual(scores["overall"], (0.3 * 100 + 0.4 * s_b) / 0.7)
        self.assertEqual(scores["coverage"], {"planned": 3, "scored": 2, "inconclusive": 0, "not_run": 1})

    def test_no_runs_gives_no_overall(self):
        scores = self.build([plan_item()])
        self.assertIsNone(scores["overall"])
        self.assertEqual(scores["experiments"][0]["status"], "not run")

    def test_recompute_uses_samples_and_ignores_a_late_blip(self):
        # stored file says degraded_recovered (the old exp-006 r1 bug); samples show a clean fault window
        rows = []
        for j in ("browse", "cart", "checkout"):
            t = 940.0
            while t <= 1300.0:
                bad = j == "cart" and 1130.0 <= t < 1170.0
                rows.append({"ts": t, "journey": j, "ok": 0 if bad else 1,
                             "latency_ms": None if bad else 100.0, "status": 503 if bad else 200})
                t += 2.0
        self.write_run("exp-x-r1.json", run_file(outcome="degraded_recovered", recovery=10.0,
                                                 journeys_value=journeys(a=1.0, l=1.0)))
        self.plan.write_text(json.dumps([plan_item()]), encoding="utf-8")
        scores = score.build_scores(str(self.dir / "runs"), str(self.plan), str(self.crit),
                                    samples_reader=lambda run_id: rows)
        r = scores["experiments"][0]["runs"][0]
        self.assertEqual((r["outcome"], r["S"]), ("survived", 100.0))
        self.assertIsNotNone(r["late_breach_ts"])
        self.assertEqual(r["source"], "recomputed from samples")


class ReportTests(unittest.TestCase):
    def make_scores(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "runs").mkdir()
        (d / "crit.yaml").write_text("default: 0.1\nservices:\n  cart: 0.3\n", encoding="utf-8")
        (d / "runs" / "a.json").write_text(json.dumps(run_file(
            exp="exp-a", outcome="degraded_recovered", recovery=14.0, journeys_value=journeys(a=0.0, l=0.0),
            evidence={"trace_ids": ["t1", "t2", "t3", "t4"], "prom_queries": ["q1", "q2"], "quality": "ok"})),
            encoding="utf-8")
        (d / "runs" / "b.json").write_text(json.dumps(run_file(exp="exp-b", run_id="run-b")), encoding="utf-8")
        (d / "runs" / "c.json").write_text(json.dumps(run_file(
            exp="exp-c", run_id="run-c", outcome="degraded_recovered", recovery=10.0, aborted=True,
            measured=("browse", "cart", "checkout"), c=None, journeys_value=journeys(a=0.0, l=0.0, n=2))),
            encoding="utf-8")
        (d / "plan.json").write_text(json.dumps([plan_item("exp-a"), plan_item("exp-b", risk="R-b"),
                                                 plan_item("exp-c", risk="R-c"), plan_item("exp-d", risk="R-d")]),
                                     encoding="utf-8")
        return score.build_scores(str(d / "runs"), str(d / "plan.json"), str(d / "crit.yaml"), samples_reader=no_samples)

    def test_report_sections_and_honesty(self):
        fixes = {"R-cart-single_replica_no_pdb": {"risk_id": "R-cart-single_replica_no_pdb", "severity": "high",
                                                  "summary": "scale cart to 2 replicas",
                                                  "files": [{"path": "x.yaml", "apply": "kubectl apply -f x.yaml"}],
                                                  "manual": None}}
        text = report.build_report(self.make_scores(), fixes, generated_at="test")
        self.assertIn("## Findings: impact seen", text)
        self.assertIn("kubectl apply -f x.yaml", text)                     # fix linked to the finding
        self.assertIn("## Tolerated faults", text)
        self.assertIn("exp-c", text)
        self.assertIn("too few samples", text)                             # inconclusive run explained, not scored
        self.assertIn("stopped early by the guardian", text)
        self.assertIn("1 inconclusive", text)                              # coverage is stated
        self.assertIn("1 not run", text)
        self.assertIn("4 trace(s): `t1`, `t2`, `t3` (+1 more in the run file); 2 Prometheus queries", text)
        self.assertIn("collector quality: ok", text)
        self.assertNotIn("nan", text.lower().replace("finance", ""))


if __name__ == "__main__":
    unittest.main()
