"""Recovery agent bridge: run report + probe samples -> agent input; verify, find, recommend. No network, no cluster."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import recover

T0, T_INJ, T_CLR = 1_800_000_000.0, 1_800_000_030.0, 1_800_000_090.0
HAVE_AGENT = importlib.util.find_spec("langgraph") is not None and importlib.util.find_spec("langchain_groq") is not None


def iso(t):
    return recover._iso(t)


def report(fault="pod_kill", manifest=None, injected=True):
    plan = {"target_service": "cart", "fault_type": fault, "duration_seconds": 60, "latency_ms": 0,
            "loss_percent": 0, "cpu_workers": 2}
    kinds = {"pod_kill": ("PodChaos", {"action": "pod-kill"}),
             "cpu_stress": ("StressChaos", {"duration": "60s", "stressors": {"cpu": {"workers": 2, "load": 100}}})}
    kind, spec = kinds[fault]
    man = manifest or {"kind": kind, "metadata": {"name": "reslab-run-abc", "labels": {"reslab-run-id": "run-abc"}}, "spec": spec}
    return {"run_id": "run-abc", "mode": "execute", "status": "execute_completed", "plan": plan, "manifest": man,
            "resource_name": "reslab-run-abc", "fault_injected": injected,
            "fault_confirmed_at": iso(T_INJ + 0.5), "cleanup_confirmed": True, "cleanup_observed_at": iso(T_CLR),
            "t_start_epoch": T0, "t_inject_epoch": T_INJ, "t_clear_epoch": T_CLR, "plan_experiment_id": "exp-004",
            "guard": {"outcome": "completed", "recovery_seconds": 4.0},
            "events": [{"stage": "submitting", "observed_at": iso(T_INJ - 1)},
                       {"stage": "injection_confirmed", "observed_at": iso(T_INJ + 0.5)},
                       {"stage": "cleanup", "observed_at": iso(T_CLR - 1)}]}


def rows(fail_during=True):
    out = []
    for i in range(0, 130, 2):
        t = T0 + i
        during = T_INJ <= t <= T_CLR
        bad = fail_during and during and t < T_INJ + 40
        out.append({"ts": t, "journey": "cart", "ok": not bad, "latency_ms": 40.0 if not bad else 0.0,
                    "status": 200 if not bad else 503})
    return out


class ContractTests(unittest.TestCase):
    def test_samples_are_staged_by_the_runs_own_timestamps(self):
        c = recover.build_contract(report(), rows(), None, None)
        stages = [s["stage"] for s in c["samples"]]
        self.assertEqual(stages[0], "baseline")
        self.assertIn("during_fault", stages)
        self.assertEqual(stages[-1], "after_cleanup")
        bad = [s for s in c["samples"] if not s["ok"]]
        self.assertTrue(all(s["error"] == "http 503" for s in bad))

    def test_resource_snapshot_and_stop_reason(self):
        r = report()
        r["guard"] = {"outcome": "breach"}
        c = recover.build_contract(r, [], {"nodes": [{"name": "cart", "replicas": 1}]}, None)
        self.assertEqual(c["stop_reason"], "probe_threshold_breach")
        self.assertEqual(c["snapshot"]["services"], [{"id": "cart", "replicas": 1}])
        self.assertEqual(c["resource"], {"name": "reslab-run-abc"})

    def test_patches_follow_the_recommended_action(self):
        fixes = [{"risk_id": "R1", "service": "cart", "check": "single_replica_no_pdb", "summary": "scale", "files": [{"path": "a.yaml"}], "manual": None},
                 {"risk_id": "R2", "service": "ad", "check": "single_replica_no_pdb", "summary": "x", "files": [], "manual": None}]
        self.assertEqual([p["risk_id"] for p in recover.patches_for("cart", "add_replicas", fixes)], ["R1"])
        self.assertEqual(recover.patches_for("cart", "set_timeouts", fixes), [])

    def test_bridge_never_imports_or_calls_the_chaos_agent(self):
        text = Path(recover.__file__).read_text(encoding="utf-8")
        for banned in ("src.chaos", "from src import chaos", "kubectl", "subprocess"):
            self.assertNotIn(banned, text.replace("never imports src.chaos", "").replace("it never imports src.chaos", ""))


@unittest.skipUnless(HAVE_AGENT, "pip install -r agents/recovery_agent/requirements.txt")
class EndToEndTests(unittest.TestCase):
    def run_agent(self, rep, sample_rows):
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "runs.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE samples (ts REAL, experiment_id TEXT, journey TEXT, ok INTEGER, latency_ms REAL, status INTEGER, service TEXT, metric TEXT, value REAL)")
        con.executemany("INSERT INTO samples (ts, experiment_id, journey, ok, latency_ms, status) VALUES (?,?,?,?,?,?)",
                        [(r["ts"], "run-abc", r["journey"], int(r["ok"]), r["latency_ms"], r["status"]) for r in sample_rows])
        con.commit()
        con.close()
        (tmp / "run-abc.json").write_text(json.dumps(rep), encoding="utf-8")
        graph = tmp / "graph.json"
        graph.write_text(json.dumps({"nodes": [{"name": "cart", "replicas": 1}]}), encoding="utf-8")
        fixes = tmp / "index.json"
        fixes.write_text(json.dumps([{"risk_id": "R-cart", "service": "cart", "check": "single_replica_no_pdb",
                                      "summary": "scale cart to 2 replicas", "files": [{"path": "reports/fixes/x.yaml"}], "manual": None}]))
        with mock.patch.object(recover, "DB_PATH", db), mock.patch.object(recover, "GRAPH_PATH", graph), \
                mock.patch.object(recover, "FIXES_INDEX", fixes), mock.patch.object(recover, "OUT_DIR", tmp / "out"), \
                mock.patch.object(recover, "PLAN_PATH", tmp / "none.json"):
            real_read = recover.read_samples
            with mock.patch.object(recover, "read_samples", lambda rid, j, db_path=db: real_read(rid, j, db)):
                return recover.analyze(tmp / "run-abc.json", use_llm=False)

    def test_real_failure_is_confirmed_explained_and_gets_a_solution(self):
        analysis, paths = self.run_agent(report(), rows())
        self.assertEqual(analysis["verification"]["verdict"], "CONFIRMED")
        self.assertEqual(analysis["narrative"]["source"], "fallback_template")
        ids = [f["id"] for f in analysis["findings"]]
        self.assertIn("error_rate", ids)
        self.assertIn("single_replica", ids)
        actions = {r["action_id"]: r for r in analysis["recommendations"]}
        self.assertIn("add_replicas", actions)
        self.assertEqual(actions["add_replicas"]["patches"][0]["risk_id"], "R-cart")
        self.assertIn("Concrete patches", Path(paths["markdown"]).read_text(encoding="utf-8"))

    def test_no_effect_is_partial_not_confirmed(self):
        analysis, _ = self.run_agent(report(), rows(fail_during=False))
        self.assertEqual(analysis["verification"]["verdict"], "PARTIAL")

    def test_fault_that_was_never_injected_is_unconfirmed(self):
        rep = report(injected=False)
        rep["fault_confirmed_at"] = None
        analysis, _ = self.run_agent(rep, rows())
        self.assertEqual(analysis["verification"]["verdict"], "UNCONFIRMED")
        self.assertEqual(analysis["findings"], [])

    def test_manifest_that_does_not_match_the_plan_is_unconfirmed(self):
        bad = {"kind": "NetworkChaos", "metadata": {"name": "reslab-run-abc", "labels": {"reslab-run-id": "run-abc"}}, "spec": {}}
        analysis, _ = self.run_agent(report(manifest=bad), rows())
        self.assertEqual(analysis["verification"]["verdict"], "UNCONFIRMED")

    def test_cpu_stress_is_understood(self):
        analysis, _ = self.run_agent(report("cpu_stress"), rows())
        self.assertEqual(analysis["verification"]["verdict"], "CONFIRMED")
        check = next(c for c in analysis["verification"]["checks"] if c["name"] == "manifest_matches_plan")
        self.assertTrue(check["passed"], check)


if __name__ == "__main__":
    unittest.main()
