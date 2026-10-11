"""Tests for src/score.py. All data below is SYNTHETIC - no files, cluster or network needed.

Run from the repo root:  python -m unittest tests.test_score -v
"""
import unittest

from src.score import build_scores, severity_band

CFG = {
    "weights": {"A": 0.45, "L": 0.20, "R": 0.20, "C": 0.15},
    "journey_weights": {"checkout": 0.4, "cart": 0.3, "browse": 0.2, "ads-recs": 0.1},
    "journey_aliases": {"shipping-quote": "cart"},
    "categories": {"pod_kill": "Redundancy", "network_delay": "Dependency", "cpu_stress": "Capacity"},
    "severity_bands": {"Critical": 30, "High": 15, "Medium": 5},
}
GRAPH = {"generated_at": "synthetic", "nodes": [
    {"name": "svc-a", "criticality": 0.4}, {"name": "svc-b", "criticality": 0.2},
    {"name": "svc-c", "criticality": 0.1}]}


def item(exp_id="exp-1", target="svc-a", journeys=("checkout",), risk_id="R-a", fault="pod_kill"):
    return {"experiment_id": exp_id, "risk_id": risk_id, "fault_type": fault,
            "target": {"service": target}, "expected_impact": {"journeys": list(journeys)},
            "slo": {"success_rate_min": 0.95, "p95_ms_max": 1500}, "recovery_budget_s": 100}


def rec(exp_id="exp-1", outcome="survived", journeys=None, impact=None, post=None, aborted=False):
    if journeys is None:
        journeys = {"checkout": {"A": 1.0, "L": 1.0}}
    return {"experiment_id": exp_id, "outcome": outcome, "journeys": journeys, "aborted": aborted,
            "impact_recovery_s": impact, "post_fault_recovery_s": post, "uncovered_journeys": []}


def risk(rid, service="svc-a", severity="high"):
    return {"risk_id": rid, "service": service, "check": "c", "severity": severity,
            "status": "predicted", "evidence": "e"}


def scores(plan, runs, risks=None, ranked=None, fixes=None):
    return build_scores(plan, runs, GRAPH, risks or [risk("R-a")], ranked or [], fixes or [], CFG)


class ScoreTests(unittest.TestCase):
    def test_perfect_survived_run_is_100(self):
        out = scores([item()], [rec()])
        e = out["experiments"][0]
        self.assertEqual(e["S"], 100.0)
        self.assertEqual(out["resilience_score"], 100.0)
        self.assertEqual(out["score_status"], "scored")

    def test_degraded_half_budget(self):
        # A=1, L=1, R=0.5, C=None -> 100*(0.45+0.2+0.1)/0.85
        e = scores([item()], [rec(outcome="degraded_recovered", impact=50)])["experiments"][0]
        self.assertEqual(e["R"], 0.5)
        self.assertAlmostEqual(e["S"], round(100 * 0.75 / 0.85, 1))

    def test_degraded_falls_back_to_post_fault_then_zero(self):
        e = scores([item()], [rec(outcome="degraded_recovered", post=25)])["experiments"][0]
        self.assertEqual(e["R"], 0.75)
        e = scores([item()], [rec(outcome="degraded_recovered")])["experiments"][0]
        self.assertEqual(e["R"], 0.0)

    def test_not_recovered_r_zero(self):
        e = scores([item()], [rec(outcome="not_recovered", journeys={"checkout": {"A": 0.5, "L": 0.5}})])["experiments"][0]
        self.assertEqual(e["R"], 0.0)
        self.assertAlmostEqual(e["S"], round(100 * (0.225 + 0.1) / 0.85, 1))
        self.assertEqual(e["outcome"], "not_recovered")

    def test_inconclusive_excluded_but_counted(self):
        plan = [item("exp-1"), item("exp-2", risk_id="R-b")]
        runs = [rec("exp-1"), rec("exp-2", outcome="inconclusive", journeys={})]
        out = scores(plan, runs)
        self.assertEqual(out["coverage"]["scored"], 1)
        self.assertEqual(out["coverage"]["run"], 2)
        self.assertEqual(out["coverage"]["inconclusive"], 1)
        self.assertEqual(out["coverage"]["text"], "1 of 2 planned experiments scored (1 inconclusive)")
        self.assertEqual(out["resilience_score"], 100.0)
        self.assertEqual(out["experiments"][1]["outcome"], "inconclusive")

    def test_c_none_renormalises_and_c_present(self):
        # no non-target journey -> C None, perfect S still 100
        e = scores([item()], [rec()])["experiments"][0]
        self.assertIsNone(e["C"])
        self.assertEqual(e["S"], 100.0)
        # non-target journey breaches the SLO -> C = 0, perfect target otherwise
        j = {"checkout": {"A": 1.0, "L": 1.0}, "browse": {"A": 0.5, "L": 1.0}}
        e = scores([item()], [rec(journeys=j)])["experiments"][0]
        self.assertEqual(e["C"], 0.0)
        self.assertAlmostEqual(e["S"], 85.0)

    def test_alias_takes_worst(self):
        j = {"cart": {"A": 1.0, "L": 0.9}, "shipping-quote": {"A": 0.6, "L": 1.0}}
        e = scores([item(journeys=("cart",))], [rec(journeys=j)])["experiments"][0]
        self.assertEqual(e["journeys"]["cart"]["A"], 0.6)
        self.assertEqual(e["journeys"]["cart"]["L"], 0.9)
        self.assertNotIn("shipping-quote", e["journeys"])

    def test_target_fallback_when_expected_not_measured(self):
        j = {"browse": {"A": 1.0, "L": 1.0}}
        e = scores([item(journeys=("checkout",))], [rec(journeys=j)])["experiments"][0]
        self.assertEqual(e["S"], 100.0)
        self.assertIsNone(e["C"])

    def test_repeats_averaged_and_worst_outcome(self):
        runs = [rec(), rec(outcome="not_recovered", journeys={"checkout": {"A": 0.0, "L": 0.0}}),
                rec(outcome="inconclusive", journeys={})]
        e = scores([item()], runs)["experiments"][0]
        self.assertEqual(e["repeats_run"], 3)
        self.assertEqual(e["outcome"], "not_recovered")
        self.assertEqual(e["S"], 50.0)  # mean of 100 and 0
        self.assertEqual(e["R"], 0.0)   # from the worst repeat

    def test_severity_band_boundaries(self):
        b = CFG["severity_bands"]
        self.assertEqual(severity_band(30, b), "Critical")
        self.assertEqual(severity_band(29.99, b), "High")
        self.assertEqual(severity_band(15, b), "High")
        self.assertEqual(severity_band(14.99, b), "Medium")
        self.assertEqual(severity_band(5, b), "Medium")
        self.assertEqual(severity_band(4.99, b), "Low")

    def test_tested_severity_uses_criticality(self):
        # S=50 on svc-a (criticality 0.4) -> 20 -> High
        runs = [rec(outcome="not_recovered", journeys={"checkout": {"A": 0.0, "L": 0.0}}), rec()]
        out = scores([item()], runs)
        e = out["experiments"][0]
        self.assertEqual(e["severity_value"], 20.0)
        self.assertEqual(e["severity"], "High")

    def test_status_mapping(self):
        plan = [item("e1", risk_id="R-1"), item("e2", risk_id="R-2"), item("e3", risk_id="R-3"),
                item("e4", risk_id="R-4"), item("e5", risk_id="R-5")]
        runs = [rec("e1", outcome="degraded_recovered", impact=10), rec("e2"),
                rec("e3", outcome="inconclusive", journeys={}), rec("e5", aborted=True)]
        risks = [risk(f"R-{i}") for i in range(1, 7)]
        out = scores(plan, runs, risks)
        status = {f["risk_id"]: f["status"] for f in out["findings"]}
        self.assertEqual(status, {"R-1": "confirmed", "R-2": "refuted", "R-3": "inconclusive",
                                  "R-4": "predicted", "R-5": "confirmed", "R-6": "predicted"})
        self.assertEqual(out["counts"], {"confirmed": 2, "refuted": 1, "inconclusive": 1, "predicted": 2})
        by = {f["risk_id"]: f for f in out["findings"]}
        self.assertEqual(by["R-1"]["basis"], "tested")
        self.assertEqual(by["R-3"]["basis"], "predicted")
        self.assertEqual(by["R-3"]["severity"], "High")
        self.assertIsNone(by["R-3"]["severity_value"])

    def test_no_runs_not_scored(self):
        plan = [item("e1", risk_id="R-1"), item("e2", risk_id="R-2")]
        out = scores(plan, [], [risk("R-1"), risk("R-2"), risk("R-3")])
        self.assertIsNone(out["resilience_score"])
        self.assertEqual(out["score_status"], "not_scored")
        self.assertEqual(out["coverage"]["text"], "0 of 2 planned experiments run")
        self.assertEqual(len(out["findings"]), 3)
        self.assertEqual(out["experiments"][0]["outcome"], "not_run")
        self.assertIsNone(out["categories"]["Redundancy"]["score"])

    def test_category_and_weighted_overall(self):
        plan = [item("e1", target="svc-a", journeys=("checkout",), risk_id="R-1"),
                item("e2", target="svc-b", journeys=("ads-recs",), risk_id="R-2", fault="cpu_stress")]
        runs = [rec("e1"), rec("e2", outcome="not_recovered", journeys={"ads-recs": {"A": 0.0, "L": 0.0}})]
        out = scores(plan, runs, [risk("R-1"), risk("R-2", "svc-b")])
        self.assertEqual(out["categories"]["Redundancy"], {"score": 100.0, "experiments": 1})
        self.assertEqual(out["categories"]["Capacity"], {"score": 0.0, "experiments": 1})
        self.assertEqual(out["categories"]["Dependency"], {"score": None, "experiments": 0})
        self.assertEqual(out["resilience_score"], 80.0)  # (0.4*100 + 0.1*0) / 0.5

    def test_findings_order_and_fix(self):
        plan = [item("e1", risk_id="R-conf-low"), item("e2", target="svc-b", risk_id="R-conf-high"),
                item("e3", target="svc-c", risk_id="R-ref")]
        bad = {"checkout": {"A": 0.0, "L": 0.0}}
        runs = [rec("e1", outcome="not_recovered", journeys=bad),
                rec("e2", outcome="not_recovered", journeys=bad),
                rec("e3")]
        risks = [risk("R-pred-med-b", "svc-b", "medium"), risk("R-ref", "svc-c"),
                 risk("R-pred-high-c", "svc-c", "high"), risk("R-conf-high", "svc-b"),
                 risk("R-conf-low", "svc-a"), risk("R-pred-high-b", "svc-b", "high"),
                 risk("R-pred-low-a", "svc-a", "low")]
        fixes = [{"risk_id": "R-conf-low", "summary": "do it", "manual": None,
                  "files": [{"path": "p.yaml", "apply": "kubectl apply -f p.yaml", "extra": 1}]}]
        out = scores(plan, runs, risks, ranked=[{"service": "svc-b", "rank": 1}, {"service": "svc-c", "rank": 2}],
                     fixes=fixes)
        order = [f["risk_id"] for f in out["findings"]]
        # confirmed by severity_value desc (svc-a crit 0.4 beats svc-b 0.2), then predicted, refuted last
        self.assertEqual(order, ["R-conf-low", "R-conf-high", "R-pred-high-b", "R-pred-high-c",
                                 "R-pred-med-b", "R-pred-low-a", "R-ref"])
        self.assertEqual([f["rank"] for f in out["findings"]], list(range(1, 8)))
        self.assertEqual(out["findings"][0]["fix"],
                         {"summary": "do it", "files": [{"path": "p.yaml", "apply": "kubectl apply -f p.yaml"}],
                          "manual": None})
        self.assertIsNone(out["findings"][1]["fix"])


if __name__ == "__main__":
    unittest.main()
