"""Offline tests for src/run.py and the vendored agent. No cluster, no network.
Run from the repo root: python -m unittest tests.test_run -v
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from pydantic import ValidationError

from agents.chaos_agent_expanded import core, watchdog
from src import run

POLICY = run.load_yaml("config/policy.yaml")
EXECUTOR = run.load_yaml("config/executor.yaml")


def item(fault="pod_kill", service="cart", journeys=("cart", "checkout"), **params):
    return {"experiment_id": "exp-x", "fault_type": fault, "mechanism": "chaosmesh",
            "target": {"namespace": "otel-demo", "service": service},
            "params": {"duration_s": 60, **params}, "hypothesis": "A testable hypothesis about errors.",
            "expected_impact": {"journeys": list(journeys)},
            "slo": {"success_rate_min": 0.95, "p95_ms_max": 1500}, "recovery_budget_s": 120}


def sample(stage, ok=True, latency=100.0):
    return {"stage": stage, "ok": ok, "latency_ms": latency, "observed_at": "2026-10-11T10:00:00+00:00",
            "journeys": {"cart": {"ok": ok, "latency_ms": latency}}}


class TranslationTests(unittest.TestCase):
    def test_pod_kill_has_no_duration(self):
        plan = run.to_agent_plan(item())
        self.assertEqual((plan.fault_type, plan.duration_seconds), ("pod_kill", 0))

    def test_cpu_stress_becomes_stresschaos(self):
        plan = run.to_agent_plan(item("cpu_stress", cpu_workers=2))
        manifest = core.build_manifest(plan, "otel-demo", {"opentelemetry.io/name": "cart"})
        self.assertEqual(manifest["kind"], "StressChaos")
        self.assertEqual(manifest["spec"]["stressors"]["cpu"]["workers"], 2)
        self.assertEqual(manifest["spec"]["duration"], "60s")

    def test_network_delay_params(self):
        manifest = core.build_manifest(run.to_agent_plan(item("network_delay", latency_ms=200)),
                                       "otel-demo", {"a": "b"})
        self.assertEqual(manifest["spec"]["delay"]["latency"], "200ms")

    def test_duration_over_policy_cap_rejected(self):
        plan = run.to_agent_plan(item("network_delay", duration_s=110, latency_ms=200))
        with self.assertRaises(ValueError):
            core.validate_target(plan, {"targets": {"cart": "cart"}, "max_duration_s": 100})

    def test_mismatched_params_rejected(self):
        with self.assertRaises(ValidationError):
            core.ExperimentPlan.model_validate({"target_service": "cart", "fault_type": "cpu_stress",
                                                "hypothesis": "valid hypothesis", "duration_seconds": 30,
                                                "latency_ms": 10, "cpu_workers": 1})


class RefusalTests(unittest.TestCase):
    def test_probe_path_service_refused(self):
        self.assertIn("port-forward", run.refusal(item(service="frontend-proxy"), POLICY, EXECUTOR))

    def test_checkout_only_refused(self):
        self.assertIn("no GET probe", run.refusal(item("cpu_stress", "payment", ("checkout",), cpu_workers=2),
                                                  POLICY, EXECUTOR))

    def test_policy_rechecked(self):
        self.assertIn("forbidden", run.refusal(item(service="flagd"), POLICY, EXECUTOR))

    def test_target_specific_probe_added(self):
        probes, uncovered = run.pick_probes(item("network_delay", "shipping", latency_ms=200), EXECUTOR)
        self.assertEqual(sorted(probes), ["cart", "shipping-quote"])
        self.assertEqual(uncovered, ["checkout"])


class RecordTests(unittest.TestCase):
    def report(self, **extra):
        return {"run_id": "run-1", "status": "execute_completed", "started_at": "2026-10-11T10:00:00+00:00",
                "fault_injected": True, "samples": [sample("baseline"), sample("during_fault")], **extra}

    def test_no_breach_is_survived(self):
        self.assertEqual(run.run_record(item(), self.report(), 1, [])["outcome"], "survived")

    def test_breach_without_healthy_is_not_recovered(self):
        rec = run.run_record(item(), self.report(t_breach="2026-10-11T10:00:05+00:00"), 1, [])
        self.assertEqual(rec["outcome"], "not_recovered")
        self.assertIsNone(rec["impact_recovery_s"])

    def test_breach_then_healthy(self):
        rec = run.run_record(item(), self.report(t_breach="2026-10-11T10:00:05+00:00",
                                                 t_clear="2026-10-11T10:01:00+00:00",
                                                 t_healthy="2026-10-11T10:01:20+00:00"), 1, [])
        self.assertEqual((rec["outcome"], rec["impact_recovery_s"], rec["post_fault_recovery_s"]),
                         ("degraded_recovered", 75.0, 20.0))

    def test_unconfirmed_injection_is_inconclusive(self):
        self.assertEqual(run.run_record(item(), self.report(fault_injected=False), 1, [])["outcome"],
                         "inconclusive")

    def test_journey_scores_from_samples(self):
        rep = self.report()
        rep["samples"] += [sample("during_fault", ok=False), sample("during_fault", latency=1600.0)]
        cart = run.run_record(item(), rep, 1, [])["journeys"]["cart"]
        self.assertEqual((cart["A"], cart["L"], cart["samples"]), (0.667, 0.333, 3))


class WatchdogTests(unittest.TestCase):
    def test_launches_as_module_and_cancels_without_deleting(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = {"context": "no-such-context", "namespace": "otel-demo"}
            state = watchdog.start_watchdog(settings, "podchaos", "x", "run-t", time.time() + 30, tmp)
            self.assertTrue(Path(state).with_suffix(".ready").exists())
            watchdog.cancel_watchdog(state)
            time.sleep(1)
            self.assertFalse(Path(state).with_suffix(".result.json").exists())


if __name__ == "__main__":
    unittest.main()
