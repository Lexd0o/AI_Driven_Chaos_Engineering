"""Offline tests for src/run.py (hybrid run path) and P4's vendored watchdog. No cluster, no network.
Run from the repo root: python -m unittest tests.test_run -v
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from agents.chaos_agent_expanded import watchdog
from src import run
from src.chaos.executor import ExperimentPlan, build_manifest

POLICY = run.load_yaml("config/policy.yaml")
EXECUTOR = run.load_yaml("config/executor.yaml")
P2_JOURNEYS = ["browse", "cart", "checkout"]


def item(fault="pod_kill", service="cart", journeys=("cart", "checkout"), **params):
    return {"experiment_id": "exp-x", "rank": 4, "fault_type": fault, "mechanism": "chaosmesh",
            "target": {"namespace": "otel-demo", "service": service},
            "params": {"duration_s": 60, **params}, "hypothesis": "A testable hypothesis about errors.",
            "expected_impact": {"journeys": list(journeys)},
            "slo": {"success_rate_min": 0.95, "p95_ms_max": 1500}, "recovery_budget_s": 120}


class TranslationTests(unittest.TestCase):
    def test_pod_kill_has_no_duration(self):
        plan = run.to_controller_plan(item())
        self.assertEqual((plan.fault_type, plan.duration_seconds), ("pod_kill", 0))

    def test_cpu_stress_becomes_stresschaos(self):
        plan = run.to_controller_plan(item("cpu_stress", "currency", ("browse",), cpu_workers=2))
        manifest = build_manifest(plan, "otel-demo", {"opentelemetry.io/name": "currency"})
        self.assertEqual(manifest["kind"], "StressChaos")
        self.assertEqual(manifest["spec"]["stressors"]["cpu"]["workers"], 2)
        self.assertEqual(manifest["spec"]["duration"], "60s")

    def test_network_delay_params(self):
        manifest = build_manifest(run.to_controller_plan(item("network_delay", latency_ms=200)),
                                  "otel-demo", {"a": "b"})
        self.assertEqual((manifest["kind"], manifest["spec"]["delay"]["latency"]), ("NetworkChaos", "200ms"))

    def test_packet_loss_params(self):
        manifest = build_manifest(run.to_controller_plan(item("packet_loss", loss_percent=10)),
                                  "otel-demo", {"a": "b"})
        self.assertEqual(manifest["spec"]["loss"]["loss"], "10")

    def test_mismatched_params_rejected(self):
        with self.assertRaises(ValidationError):
            ExperimentPlan.model_validate({"target_service": "cart", "fault_type": "cpu_stress",
                                           "hypothesis": "valid hypothesis", "duration_seconds": 30,
                                           "latency_ms": 10, "cpu_workers": 1})

    def test_cpu_workers_only_for_cpu_stress(self):
        with self.assertRaises(ValidationError):
            ExperimentPlan.model_validate({"target_service": "cart", "fault_type": "network_delay",
                                           "hypothesis": "valid hypothesis", "duration_seconds": 30,
                                           "latency_ms": 10, "cpu_workers": 1})


class RefusalTests(unittest.TestCase):
    def test_probe_path_service_refused(self):
        self.assertIn("port-forward", run.refusal(item(service="frontend-proxy"), POLICY, EXECUTOR, P2_JOURNEYS))

    def test_checkout_only_is_now_runnable(self):
        # P2 probes checkout (POST), so a payment fault can be measured (P4's GET probes could not).
        self.assertIsNone(run.refusal(item("cpu_stress", "payment", ("checkout",), cpu_workers=2),
                                      POLICY, EXECUTOR, P2_JOURNEYS))

    def test_nothing_measurable_refused(self):
        self.assertIn("none of the expected journeys",
                      run.refusal(item(service="ad", journeys=("ads-recs",)), POLICY, EXECUTOR, P2_JOURNEYS))

    def test_policy_rechecked(self):
        self.assertIn("forbidden", run.refusal(item(service="flagd"), POLICY, EXECUTOR, P2_JOURNEYS))

    def test_packet_loss_capped_by_policy(self):
        self.assertIn("max_loss_percent",
                      run.refusal(item("packet_loss", loss_percent=50), POLICY, EXECUTOR, P2_JOURNEYS))

    def test_duration_over_controller_cap_refused(self):
        self.assertIn("does not fit the controller",
                      run.refusal(item("network_delay", duration_s=90, latency_ms=200), POLICY, EXECUTOR,
                                  P2_JOURNEYS))


class JourneyTests(unittest.TestCase):
    def test_not_probed_journeys_listed(self):
        measured, not_probed = run.measured_journeys(item(journeys=("ads-recs", "cart")), P2_JOURNEYS)
        self.assertEqual((measured, not_probed), (["cart"], ["ads-recs"]))

    def test_jaeger_verified_journeys_added(self):
        # chaos_settings.json: product-catalog -> browse, cart, checkout (cart verified in Jaeger, 11 Oct)
        measured, _ = run.measured_journeys(item(service="product-catalog", journeys=("browse",)), P2_JOURNEYS)
        self.assertEqual(measured, ["browse", "cart", "checkout"])

    def test_settings_point_at_the_item(self):
        settings = run.controller_settings(item(service="shipping"), POLICY, EXECUTOR, ["cart", "checkout"])
        self.assertEqual(settings["targets"], {"shipping": "shipping"})
        self.assertEqual(settings["target_journeys"], {"shipping": ["cart", "checkout"]})
        self.assertIn("safety", settings)   # guard thresholds still come from chaos_settings.json


class MainTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.plan_path = self.dir / "plan.json"
        self.plan_path.write_text(json.dumps([item("network_delay", "shipping", latency_ms=200)]))
        executor = {**EXECUTOR, "runs_dir": str(self.dir / "runs"), "events_log": str(self.dir / "events.log"),
                    "stop_path": str(self.dir / "STOP")}
        self.calls = []
        for p in (mock.patch.object(run, "PLAN_PATH", str(self.plan_path)),
                  mock.patch.object(run, "load_yaml", lambda path: POLICY if "policy" in path else executor),
                  mock.patch.object(run, "next_repeat", lambda exp: 2),
                  mock.patch.object(run.controller, "run_experiment", self.fake_run)):
            p.start()
            self.addCleanup(p.stop)

    def fake_run(self, args, plan=None, settings=None, meta=None):
        self.calls.append({"args": args, "plan": plan, "settings": settings, "meta": meta})
        record = {"experiment_id": "run-abc", "plan_experiment_id": meta["plan_experiment_id"],
                  "repeat": meta["repeat"], "outcome": "survived", "forced_breach": args.force_breach,
                  "report_path": "data/chaos/reports/run-abc.json"}
        return {"run_id": "run-abc", "status": "execute_completed" if args.execute else "mock_completed",
                "run_record": record if args.execute else None}

    def test_execute_drives_the_controller_and_writes_the_run_file(self):
        run.main(["--exp", "exp-x", "--execute", "--yes"])
        call = self.calls[0]
        self.assertTrue(call["args"].execute and call["args"].skip_plan and call["args"].yes)
        self.assertEqual(call["args"].budget, 120)                       # from the plan item
        self.assertEqual((call["plan"].fault_type, call["plan"].latency_ms), ("network_delay", 200))
        self.assertEqual(call["meta"], {"plan_experiment_id": "exp-x", "repeat": 2})
        saved = json.loads((self.dir / "runs" / "exp-x-r2.json").read_text())
        self.assertEqual((saved["run_id"], saved["outcome"], saved["plan"]["rank"]), ("run-abc", "survived", 4))
        self.assertEqual(saved["measured_journeys"], ["cart", "checkout"])

    def test_mock_writes_no_run_file(self):
        run.main(["--exp", "exp-x"])
        self.assertTrue(self.calls[0]["args"].mock)
        self.assertFalse((self.dir / "runs").exists())

    def test_refused_item_never_reaches_the_controller(self):
        self.plan_path.write_text(json.dumps([item(service="frontend-proxy")]))
        with self.assertRaises(SystemExit):
            run.main(["--exp", "exp-x", "--execute", "--yes"])
        self.assertEqual(self.calls, [])

    def test_forced_breach_file_is_kept_apart(self):
        run.main(["--exp", "exp-x", "--execute", "--yes", "--force-breach"])
        self.assertTrue(list((self.dir / "runs").glob("exp-x-r2-forced-run-abc.json")))


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
