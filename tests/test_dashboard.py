import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
import dash_data as dd  # noqa: E402

GRAPH = {"nodes": [{"name": "frontend", "stateful": False}, {"name": "cart", "stateful": False},
                   {"name": "valkey", "stateful": True}],
         "edges": [{"from": "frontend", "to": "cart", "source": "jaeger", "calls": 88, "protocol": "http"},
                   {"from": "cart", "to": "valkey", "source": "override", "protocol": "redis"}]}
RANKED = [{"service": "cart", "risk_score": 1.0}, {"service": "frontend", "risk_score": 0.5}]


class DashTests(unittest.TestCase):
    def test_dot(self):
        dot = dd.build_dot(GRAPH, RANKED, {"cart"})
        for n in ("frontend", "cart", "valkey"):
            self.assertIn('"%s"' % n, dot)
        self.assertIn("style=solid", dot)
        self.assertIn("style=dashed", dot)
        self.assertIn("cylinder", dot)
        self.assertIn('label="88"', dot)
        self.assertIn("penwidth=4", dot)

    def test_classify(self):
        cfg = {"probe_path_services": ["frontend-proxy"]}

        def mk(s, j, i="exp-1"):
            return {"experiment_id": i, "target": {"service": s}, "expected_impact": {"journeys": j}}

        self.assertEqual(dd.classify_experiment(mk("frontend-proxy", ["browse"]), cfg, set()), "refused")
        self.assertEqual(dd.classify_experiment(mk("payment", ["checkout"]), cfg, set()), "refused")
        self.assertEqual(dd.classify_experiment(mk("cart", ["cart", "checkout"]), cfg, set()), "not run")
        self.assertEqual(dd.classify_experiment(mk("cart", ["cart"]), cfg, {"exp-1"}), "run")

    def test_timeline(self):
        rep = {"samples": [
            {"observed_at": "2026-10-11T00:00:00+00:00", "stage": "baseline",
             "journeys": {"browse": {"ok": True, "latency_ms": 50.0}}},
            {"observed_at": "2026-10-11T00:00:02+00:00", "stage": "observing",
             "journeys": {"browse": {"ok": False, "latency_ms": 3000.0}, "cart": {"ok": True, "latency_ms": 9}}}]}
        rows = dd.timeline_rows(rep)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["t_s"], 2.0)
        self.assertEqual(rows[1]["ok"], 0)
        m = dd.run_markers({"t_inject": "2026-10-11T00:00:01+00:00", "t_healthy": None}, rep)
        self.assertEqual(m, {"t_inject": 1.0})

    def test_findings(self):
        rep = {"findings": [{"risk_id": "R1", "service": "a", "check": "c", "status": "confirmed",
                             "severity": "High", "fix": {"summary": "s", "files": [{"apply": "kubectl x"}]}}],
               "explained": [{"risk_id": "R1", "explanation": "why", "text_source": "ai"}]}
        r = dd.finding_rows(rep)[0]
        self.assertEqual(r["text_source"], "AI-written")
        self.assertEqual(r["apply_commands"], ["kubectl x"])

    def test_missing_files(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(dd.load_json(Path(d) / "nope.json"), {})
            self.assertEqual(dd.load_json(Path(d) / "nope.json", []), [])
            self.assertEqual(dd.load_runs(Path(d) / "runs"), [])
            self.assertEqual(dd.tail_lines(Path(d) / "x.log"), [])
            self.assertIsNone(dd.file_mtime(Path(d) / "x"))
        self.assertEqual(dd.timeline_rows({}), [])
        self.assertEqual(dd.finding_rows({}), [])
        self.assertIn("digraph", dd.build_dot({}))


class FixTrackingDashTests(unittest.TestCase):
    def test_run_rows_group_by_label(self):
        runs = [{"experiment_id": "e2", "repeat": 1, "outcome": "survived"},
                {"experiment_id": "e1", "repeat": 4, "outcome": "survived", "label": "fix-applied"},
                {"experiment_id": "e1", "repeat": 2, "outcome": "not_recovered"}]
        rows = dd.run_rows(runs)
        self.assertEqual([(r["experiment_id"], r["label"]) for r in rows],
                         [("e1", "current"), ("e1", "fix-applied"), ("e2", "current")])

    def test_labelled_run_does_not_mark_experiment_run(self):
        plan = [{"experiment_id": "e1", "target": {"service": "cart"}, "fault_type": "pod_kill",
                 "expected_impact": {"journeys": ["cart"]}}]
        rows = dd.experiment_rows(plan, {}, [{"experiment_id": "e1", "label": "fix-applied"}])
        self.assertEqual(rows[0]["status"], "not run")

    def test_comparison_rows_and_finding_badge(self):
        scores = {"comparisons": {"rows": [{"experiment_id": "e1", "label": "fx", "delta_S": 5.0,
                                            "before": {"S": 70, "outcome": "not_recovered"},
                                            "after": {"S": 75, "outcome": "survived"}}]}}
        self.assertEqual(dd.comparison_rows(scores)[0]["after_S"], 75)
        self.assertEqual(dd.comparison_rows({}), [])
        fr = dd.finding_rows({"findings": [{"risk_id": "r", "fix_verified": True, "fix_verified_by": "fx"}]})
        self.assertEqual((fr[0]["fix_verified"], fr[0]["fix_verified_by"]), (True, "fx"))


if __name__ == "__main__":
    unittest.main()
