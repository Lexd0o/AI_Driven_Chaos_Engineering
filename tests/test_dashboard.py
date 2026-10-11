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


if __name__ == "__main__":
    unittest.main()
