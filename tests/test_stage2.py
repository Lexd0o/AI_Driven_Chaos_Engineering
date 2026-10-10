"""Stage 2 tests (D-025): controller live mode, guardian additions, sampler stop event, runs table.
Offline: fake kubectl, fake guard, fake sampler. No cluster, no shop, no Groq. Numbers inside the fakes are
made up for the tests and mean nothing.

Run from the repo folder (venv active):  python -m unittest tests.test_stage2 -v
"""
import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from src import observe
from src.chaos import controller, guardian
from tests.test_guardian import FAILED, HEALTHY, SETTINGS as GUARD_SETTINGS, FakeEnv

SETTINGS = {
    "context": "docker-desktop",
    "namespace": "otel-demo",
    "targets": {"catalog": "product-catalog"},
    "target_journeys": {"catalog": ["browse", "checkout"]},
    "safety": {
        "latency_multiplier": 3,
        "latency_floor_ms": 1500,
        "error_ratio_max": 0.05,
        "consecutive_breaches": 3,
        "max_experiment_seconds": 90,
        "recovery_timeout_seconds": 180,
        "recovery_healthy_probes": 5,
    },
}

PLAN = {
    "target_service": "catalog",
    "fault_type": "packet_loss",
    "hypothesis": "A test hypothesis for the Stage 2 checks.",
    "duration_seconds": 30,
    "latency_ms": 0,
    "loss_percent": 10,
}

DEPLOYMENT = {
    "metadata": {"generation": 1},
    "spec": {
        "replicas": 1,
        "selector": {"matchLabels": {"opentelemetry.io/name": "product-catalog"}},
    },
    "status": {"observedGeneration": 1, "updatedReplicas": 1, "readyReplicas": 1},
}

HEALTHY_SLI = {"success_rate": 1.0, "p50_ms": 50.0, "p95_ms": 120.0, "n": 10}
BROKEN_SLI = {"success_rate": 0.2, "p50_ms": None, "p95_ms": None, "n": 10}


def fresh_baseline(age_s=0):
    captured = datetime.now(timezone.utc) - timedelta(seconds=age_s)

    return {
        "captured_at": captured.isoformat(),
        "seconds": 60,
        "probe_count": 30,
        "probe_success_rate": 1.0,
        "probe_p50_ms": 500.0,
        "probe_p95_ms": 700.0,
        "prometheus": {"error_ratio": 0.0, "p95_ms": 100.0, "rps": 2.0},
        "pod_health": {"desired": 1, "ready": 1, "restarts": 0, "fully_ready": True},
    }


class FakeKube:
    # Stands in for executor.kubectl: remembers chaos objects, supports the calls the controller makes.
    def __init__(self):
        self.objects = {}
        self.existing = []
        self.created = []
        self.deleted = []
        self.delete_error = None

    def __call__(self, settings, args, body=None):
        if args[:2] == ["get", "deployment"]:
            return json.dumps(DEPLOYMENT)

        if args[:3] == ["create", "--dry-run=server", "-f"]:
            return "{}"

        if args[:2] == ["get", "podchaos,networkchaos"]:
            return json.dumps({"items": self.existing + list(self.objects.values())})

        if args[:2] == ["create", "-f"]:
            obj = json.loads(json.dumps(body))
            obj["metadata"]["uid"] = "uid-1"
            obj["status"] = {"conditions": [
                {"type": "Selected", "status": "True"},
                {"type": "AllInjected", "status": "True"},
            ]}
            self.objects[obj["metadata"]["name"]] = obj
            self.created.append(obj)
            return json.dumps(obj)

        if args[0] == "get" and args[1] in ("networkchaos", "podchaos"):
            obj = self.objects.get(args[2])
            return json.dumps(obj) if obj else ""

        if args[0] == "delete":
            if self.delete_error:
                raise RuntimeError(self.delete_error)
            self.objects.pop(args[2], None)
            self.deleted.append(args[2])
            return ""

        raise AssertionError(f"unexpected kubectl call: {args}")


class FakeThread:
    alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass


class LiveRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
        (self.dir / "experiment.json").write_text(json.dumps(PLAN), encoding="utf-8")
        (self.dir / "baseline.json").write_text(json.dumps(fresh_baseline()), encoding="utf-8")

        self.kube = FakeKube()
        self.clock = [1000.0]
        self.sli = HEALTHY_SLI
        self.guard_outcome = "completed"
        self.guard_reason = None
        self.guard_verified_gone = True
        self.guard_leaves_object = False
        self.guard_kills_sampler = False
        self.guard_calls = []
        self.metrics_calls = []
        self.samplers = []
        self.sampler_runs = []
        self.saved = []
        self.other_rows = 0
        self.output = ""

        patches = [
            mock.patch.object(controller, "DATA_DIR", self.dir),
            mock.patch.object(controller, "SETTINGS_PATH", self.dir / "settings.json"),
            mock.patch.object(controller, "EXPERIMENT_PATH", self.dir / "experiment.json"),
            mock.patch.object(controller, "kubectl", self.kube),
            mock.patch.object(controller, "now", lambda: self.clock[0]),
            mock.patch.object(controller, "sleep", self.fake_sleep),
            mock.patch.object(controller, "start_sampler", self.fake_start_sampler),
            mock.patch.object(controller, "latest_sli", lambda journey, window: self.sli),
            mock.patch.object(controller, "recent_sample_count", lambda window: self.other_rows),
            mock.patch.object(controller, "collect_run_metrics", self.fake_metrics),
            mock.patch.object(controller, "collect_evidence", self.fake_evidence),
            mock.patch.object(controller, "save_run", self.saved.append),
            mock.patch.object(controller.guardian, "guard", self.fake_guard),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # ---- fakes
    def fake_sleep(self, seconds):
        self.clock[0] += seconds

    def fake_start_sampler(self, run_id):
        sampler = {"thread": FakeThread(), "stop": threading.Event(), "errors": []}
        self.samplers.append(sampler)
        self.sampler_runs.append(run_id)
        return sampler

    def fake_guard(self, name, kind, baseline, thresholds, max_seconds, settings=None, deployment=None):
        self.guard_calls.append({
            "name": name, "kind": kind, "baseline": baseline, "thresholds": dict(thresholds),
            "max_seconds": max_seconds, "deployment": deployment,
        })
        self.clock[0] += 35                              # the fault ran for a while
        if not self.guard_leaves_object:
            self.kube.objects.pop(name, None)            # the kill switch deleted the object
        if self.guard_kills_sampler:
            self.samplers[-1]["thread"].alive = False
        breached = self.guard_outcome == "breach"
        return {
            "experiment": name, "kind": kind, "thresholds": thresholds, "latency_limit_ms": 2100,
            "outcome": self.guard_outcome, "interrupted": self.guard_outcome == "interrupted",
            "breach": {"breached": breached, "reason": self.guard_reason, "values": None},
            "detected_epoch": self.clock[0] if breached else None,
            "kill_switch": {"command": "kubectl delete ...", "reason": self.guard_reason or self.guard_outcome,
                            "at": "x", "at_epoch": self.clock[0], "verified_gone": self.guard_verified_gone,
                            "error": None},
            "recovery": {"recovered": True, "recovery_seconds": 8.0}, "recovery_seconds": 8.0, "timeline": [],
        }

    def fake_metrics(self, run_id, targets, t_inject, t_clear, recovery_budget_s=120, **kw):
        self.metrics_calls.append({"run_id": run_id, "targets": targets, "t_inject": t_inject,
                                   "t_clear": t_clear, "budget": recovery_budget_s})
        return {
            "targets": targets, "t_inject": t_inject, "t_clear": t_clear,
            "t_breach": t_inject + 5, "t_healthy": t_clear + 10, "outcome": "degraded_recovered",
            "impact_recovery_s": 40.0, "post_fault_recovery_s": 10.0, "peak_error_rate": 0.4,
            "peak_p95_ms": 900.0, "containment_C": 1.0, "quality": "ok", "issues": [],
            "journeys": {j: {"n": 15, "A": 0.6, "L": 0.5} for j in ("browse", "cart", "checkout")},
            "experiment_id": run_id,
        }

    def fake_evidence(self, run_id, start, end, **kw):
        return {"trace_ids": ["trace-1"], "prom_queries": ["q1"], "quality": "ok", "notes": [], "info": []}

    # ---- helper
    def run_controller(self, *extra, yes=True):
        argv = ["--execute", "--skip-plan", *(["--yes"] if yes else []), *extra]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            try:
                controller.main(argv)
                code = 0
            except SystemExit as exit_:
                code = exit_.code
        self.output = buffer.getvalue()
        files = list((self.dir / "reports").glob("*.json"))
        self.assertEqual(len(files), 1)
        return code, json.loads(files[0].read_text(encoding="utf-8"))

    def stages(self, report):
        return [e["stage"] for e in report["events"]]

    # ---- the normal run
    def test_happy_path(self):
        code, report = self.run_controller()

        self.assertEqual(code, 0, self.output)
        self.assertEqual(report["status"], "execute_completed")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertTrue(report["deployment_ready_after"])
        self.assertEqual(self.kube.objects, {})
        self.assertFalse((self.dir / ".controller.lock").exists())

        # one experiment id everywhere
        run_id = report["run_id"]
        created = self.kube.created[0]
        self.assertEqual(created["metadata"]["name"], f"reslab-{run_id}")
        self.assertEqual(created["metadata"]["labels"]["reslab-run-id"], run_id)
        self.assertEqual(self.sampler_runs, [run_id])
        self.assertEqual(self.metrics_calls[0]["run_id"], run_id)
        self.assertEqual(self.saved[0]["experiment_id"], run_id)

        # guardian gets the object name, kind, P1's thresholds and the settings' time limit
        call = self.guard_calls[0]
        self.assertEqual(call["name"], f"reslab-{run_id}")
        self.assertEqual(call["kind"], "NetworkChaos")
        self.assertEqual(call["max_seconds"], 90)
        self.assertEqual(call["deployment"], "product-catalog")
        self.assertEqual(call["thresholds"]["latency_floor_ms"], 1500)
        self.assertNotIn("max_experiment_seconds", call["thresholds"])
        self.assertEqual(call["baseline"]["probe_p95_ms"], 700.0)

        # P2 metrics use t_inject and the verified kill-switch time, the journeys from settings, the default budget
        m = self.metrics_calls[0]
        self.assertEqual(m["targets"], ["browse", "checkout"])
        self.assertEqual(m["t_inject"], report["t_inject_epoch"])
        self.assertEqual(m["t_clear"], report["guard"]["kill_switch"]["at_epoch"])
        self.assertEqual(m["budget"], 120)
        self.assertLess(report["t_inject_epoch"], report["t_clear_epoch"])

        # sampling continued for budget + 30 s after t_clear
        self.assertGreaterEqual(self.clock[0], report["t_clear_epoch"] + 120 + 30)
        self.assertTrue(self.samplers[0]["stop"].is_set())

        # runs row
        row = self.saved[0]
        self.assertEqual(row["outcome"], "degraded_recovered")
        self.assertFalse(row["aborted"])
        self.assertIsNone(row["abort_reason"])
        self.assertIsNone(row["t_detect"])
        self.assertFalse(row["forced_breach"])
        self.assertEqual(row["target"], "catalog")
        self.assertEqual(row["evidence"]["trace_ids"], ["trace-1"])
        self.assertEqual(report["run_record"], row)

        # order of stages: nothing is injected before the checks, cleanup comes before the recovery observation
        s = self.stages(report)
        order = ["checking_guard_baseline", "starting_sampler", "pre_observation", "checking_shop_health",
                 "submitting", "injection_confirmed", "guarding", "guard_finished", "cleanup",
                 "post_observation", "collecting_metrics", "metrics_saved"]
        self.assertEqual([x for x in s if x in order], order)

        # D-024: the guardian's recovery number is labelled, and no top-level recovery_seconds exists
        self.assertIn("D-024", report["guard_note"])
        self.assertNotIn("recovery_seconds", report)

    def test_pod_kill_uses_podchaos_kind(self):
        plan = {**PLAN, "fault_type": "pod_kill", "duration_seconds": 0, "loss_percent": 0}
        (self.dir / "experiment.json").write_text(json.dumps(plan), encoding="utf-8")

        code, report = self.run_controller()

        self.assertEqual(code, 0, self.output)
        self.assertEqual(self.guard_calls[0]["kind"], "PodChaos")
        self.assertEqual(report["resource_type"], "podchaos")

    def test_budget_and_pre_seconds_options(self):
        code, report = self.run_controller("--budget", "60", "--pre-seconds", "10")

        self.assertEqual(code, 0, self.output)
        self.assertEqual(self.metrics_calls[0]["budget"], 60)
        self.assertGreaterEqual(self.clock[0], report["t_clear_epoch"] + 60 + 30)

    # ---- --force-breach
    def test_force_breach_lowers_thresholds_and_labels_the_run(self):
        self.guard_outcome = "breach"
        self.guard_reason = "probe_latency_exceeded"

        code, report = self.run_controller("--force-breach")

        self.assertEqual(code, 0, self.output)
        thresholds = self.guard_calls[0]["thresholds"]
        self.assertEqual(thresholds["latency_floor_ms"], 1)
        self.assertEqual(thresholds["latency_multiplier"], 0)
        self.assertEqual(thresholds["error_ratio_max"], 0.05)       # only the latency rule changes
        self.assertTrue(report["forced_breach"])
        self.assertEqual(report["forced_breach_thresholds"], controller.FORCE_BREACH)

        row = self.saved[0]
        self.assertTrue(row["forced_breach"])
        self.assertEqual(row["outcome"], "inconclusive")           # cut short on purpose: never scored
        self.assertEqual(report["run_metrics"]["outcome"], "degraded_recovered")   # the computed numbers are kept
        self.assertTrue(row["aborted"])
        self.assertEqual(row["abort_reason"], "probe_latency_exceeded")
        self.assertIsNotNone(row["t_detect"])
        self.assertIn("FORCED BREACH", self.output)

    def test_force_breach_needs_execute(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            controller.main(["--mock", "--skip-plan", "--force-breach"])

    # ---- refusals: nothing may be injected
    def assert_nothing_injected(self, report):
        self.assertEqual(self.kube.created, [])
        self.assertFalse(report["fault_injected"])
        self.assertEqual(self.metrics_calls, [])
        self.assertEqual(self.saved, [])
        self.assertFalse((self.dir / ".controller.lock").exists())
        for sampler in self.samplers:
            self.assertTrue(sampler["stop"].is_set())

    def test_missing_baseline_refuses(self):
        (self.dir / "baseline.json").unlink()

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertIn("No guard baseline", report["error"])
        self.assertEqual(self.sampler_runs, [])               # refused before the sampler even started
        self.assert_nothing_injected(report)

    def test_old_baseline_refuses(self):
        (self.dir / "baseline.json").write_text(json.dumps(fresh_baseline(age_s=2 * 3600)), encoding="utf-8")

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertIn("min old", report["error"])
        self.assert_nothing_injected(report)

    def test_unhealthy_shop_blocks_injection(self):
        self.sli = BROKEN_SLI

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertIn("nothing injected", report["error"])
        self.assertFalse(report["shop_gate"]["ok"])
        self.assertEqual(len(self.samplers), 1)               # the sampler had started and was stopped again
        self.assert_nothing_injected(report)

    def test_existing_chaos_object_blocks(self):
        self.kube.existing = [{"metadata": {"name": "someone-elses-chaos"}}]

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertIn("Existing chaos resources", report["error"])
        self.assert_nothing_injected(report)

    def test_target_without_journey_map_refuses(self):
        settings = {**SETTINGS, "target_journeys": {}}
        (self.dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertIn("target_journeys", report["error"])
        self.assert_nothing_injected(report)

    def test_other_sampler_blocks_before_confirmation(self):
        self.other_rows = 6

        with mock.patch("builtins.input") as typed:
            code, report = self.run_controller(yes=False)

        self.assertEqual(code, 1)
        self.assertIn("Another sampler", report["error"])
        typed.assert_not_called()                    # refused before asking to confirm
        self.assertEqual(self.samplers, [])          # our own sampler never started
        self.assert_nothing_injected(report)

    def test_confirmation_required(self):
        with mock.patch("builtins.input", return_value="yes please"):
            code, report = self.run_controller(yes=False)

        self.assertEqual(code, 1)
        self.assertIn("Not confirmed", report["error"])
        self.assert_nothing_injected(report)

    def test_typed_confirmation_accepted(self):
        with mock.patch("builtins.input", return_value=controller.CONFIRM_WORD):
            code, report = self.run_controller(yes=False)

        self.assertEqual(code, 0, self.output)
        self.assertEqual(len(self.kube.created), 1)

    # ---- failures after injection start: the fault must still be cleaned up
    def test_injection_timeout_still_cleans_up(self):
        real_wait_for = controller.wait_for

        def wait_for(settings, resource, name, predicate, timeout):
            if resource != "deployment":
                raise TimeoutError("injection never confirmed")
            return real_wait_for(settings, resource, name, predicate, timeout)

        with mock.patch.object(controller, "wait_for", wait_for):
            code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertEqual(self.kube.deleted, [f"reslab-{report['run_id']}"])
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(self.guard_calls, [])
        self.assertEqual(self.metrics_calls, [])               # no t_inject -> no metrics
        self.assertEqual(self.saved, [])
        self.assertTrue(self.samplers[0]["stop"].is_set())

    def test_cleanup_failure_stops_before_measurement(self):
        self.guard_leaves_object = True                        # guardian could not remove it
        self.kube.delete_error = "kubectl delete failed"

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertFalse(report["cleanup_confirmed"])
        self.assertIn("FAULT MAY STILL BE ACTIVE", self.output)
        self.assertIn("guardian --abort --all", self.output)
        self.assertEqual(self.metrics_calls, [])
        self.assertEqual(self.saved, [])

    def test_unverified_kill_switch_uses_cleanup_time_as_t_clear(self):
        self.guard_verified_gone = False
        self.guard_leaves_object = True                        # the controller's own cleanup removes it

        code, report = self.run_controller()

        self.assertEqual(code, 0, self.output)
        self.assertEqual(self.kube.deleted, [f"reslab-{report['run_id']}"])
        self.assertEqual(report["t_clear_epoch"], report["cleanup_observed_epoch"])
        self.assertEqual(self.metrics_calls[0]["t_clear"], report["cleanup_observed_epoch"])

    def test_interrupted_guard_skips_observation_but_keeps_a_row(self):
        self.guard_outcome = "interrupted"

        code, report = self.run_controller()

        self.assertEqual(code, 1)
        self.assertEqual(report["observation_skipped"], "interrupted")
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(len(self.saved), 1)
        self.assertTrue(self.saved[0]["aborted"])
        self.assertLess(self.clock[0], report["t_clear_epoch"] + 100)   # did not wait out the budget

    def test_dead_sampler_after_injection_still_produces_a_row(self):
        self.guard_kills_sampler = True

        code, report = self.run_controller()

        self.assertEqual(code, 0, self.output)
        self.assertIn("Sampler stopped unexpectedly", report["observation_problem"])
        self.assertEqual(len(self.saved), 1)                   # P2's metrics will call it inconclusive if data is missing


class SamplerThreadTests(unittest.TestCase):
    # controller.start_sampler with run_sampler and the database faked out.
    def test_runs_the_sampler_with_the_run_id_and_a_stop_event(self):
        seen = {}

        def fake_run_sampler(experiment_id, stop_after_s, db_path, stop_event):
            seen.update(experiment_id=experiment_id, stop_after_s=stop_after_s, db_path=db_path, stop=stop_event)

        with mock.patch.object(controller, "run_sampler", fake_run_sampler), \
                mock.patch.object(controller, "connect") as connect:
            sampler = controller.start_sampler("run-z")
            sampler["thread"].join(timeout=5)

        connect.assert_called_once()                       # database set up before the thread starts
        self.assertEqual(seen["experiment_id"], "run-z")
        self.assertIsNone(seen["stop_after_s"])            # runs until stopped
        self.assertIs(seen["stop"], sampler["stop"])
        self.assertEqual(sampler["errors"], [])

    def test_a_sampler_crash_is_recorded_and_seen(self):
        def crashing_run_sampler(*args):
            raise RuntimeError("database is gone")

        with mock.patch.object(controller, "run_sampler", crashing_run_sampler), \
                mock.patch.object(controller, "connect"):
            sampler = controller.start_sampler("run-z")
            sampler["thread"].join(timeout=5)

        self.assertEqual(sampler["errors"], ["database is gone"])
        with self.assertRaises(RuntimeError) as caught:
            controller.check_sampler(sampler)
        self.assertIn("database is gone", str(caught.exception))

    def test_stop_sampler_sets_the_event_and_accepts_none(self):
        controller.stop_sampler(None)
        sampler = {"thread": FakeThread(), "stop": threading.Event(), "errors": []}
        controller.stop_sampler(sampler)
        self.assertTrue(sampler["stop"].is_set())


class PureFunctionTests(unittest.TestCase):
    def test_baseline_ok(self):
        self.assertEqual(controller.baseline_problems(fresh_baseline(), 0.05), [])

    def test_baseline_too_old(self):
        problems = controller.baseline_problems(fresh_baseline(age_s=31 * 60), 0.05)
        self.assertEqual(len(problems), 1)
        self.assertIn("31 min old", problems[0])

    def test_baseline_bad_values(self):
        b = fresh_baseline()
        b["probe_success_rate"] = 0.5
        b["probe_p95_ms"] = None
        b["prometheus"]["error_ratio"] = 0.2
        self.assertEqual(len(controller.baseline_problems(b, 0.05)), 3)

    def test_baseline_without_timestamp(self):
        b = fresh_baseline()
        del b["captured_at"]
        self.assertIn("captured_at is missing or unreadable", controller.baseline_problems(b, 0.05))

    def test_journeys_for(self):
        self.assertEqual(controller.journeys_for(SETTINGS, "catalog"), ["browse", "checkout"])
        with self.assertRaises(ValueError):
            controller.journeys_for(SETTINGS, "payment")
        with self.assertRaises(ValueError):
            controller.journeys_for({"target_journeys": {"catalog": ["browse", "nope"]}}, "catalog")

    def test_settings_file_maps_the_approved_target(self):
        settings = json.loads((controller.SETTINGS_PATH).read_text(encoding="utf-8"))
        for target in settings["targets"]:
            self.assertTrue(controller.journeys_for(settings, target))

    def test_run_record_for_natural_end_and_for_breach(self):
        report = {"run_id": "run-x", "mode": "execute", "t_start_epoch": 1.0, "forced_breach": False}
        plan = mock.Mock(target_service="catalog", fault_type="packet_loss")
        metrics = {k: None for k in (
            "t_inject", "t_clear", "t_breach", "t_healthy", "outcome", "quality", "peak_error_rate",
            "peak_p95_ms", "impact_recovery_s", "post_fault_recovery_s", "containment_C")}
        metrics["journeys"] = {}
        natural = {"outcome": "completed", "breach": {"reason": None}, "detected_epoch": None}
        breach = {"outcome": "breach", "breach": {"reason": "probe_failure"}, "detected_epoch": 5.0}

        a = controller.build_run_record(report, plan, metrics, natural, None, Path("r.json"))
        b = controller.build_run_record(report, plan, metrics, breach, {"trace_ids": ["t"]}, Path("r.json"))

        self.assertFalse(a["aborted"])
        self.assertIsNone(a["abort_reason"])
        self.assertEqual(a["evidence"]["trace_ids"], [])
        self.assertTrue(b["aborted"])
        self.assertEqual(b["abort_reason"], "probe_failure")
        self.assertEqual(b["t_detect"], 5.0)
        self.assertEqual(set(a), set(observe.RUN_COLUMNS))


class GuardianStage2Tests(unittest.TestCase):
    ITEMS = [
        {"kind": "NetworkChaos", "metadata": {"name": "reslab-run-abc", "labels": {"reslab-run-id": "run-abc"}}},
        {"kind": "PodChaos", "metadata": {"name": "reslab-experiment"}},
        {"kind": "NetworkChaos", "metadata": {"name": "someone-elses-chaos"}},
    ]

    def abort(self, delete_all):
        def fake_kubectl(settings, args, timeout=30, namespaced=True):
            return json.dumps({"items": self.ITEMS}) if args[0] == "get" else ""

        with mock.patch.object(guardian.monitor, "kubectl", fake_kubectl), \
                contextlib.redirect_stdout(io.StringIO()):
            return guardian.abort(GUARD_SETTINGS, delete_all)

    def test_abort_matches_the_controllers_objects_only(self):
        self.assertEqual(
            self.abort(False), ["networkchaos/reslab-run-abc", "podchaos/reslab-experiment"]
        )

    def test_abort_all_still_deletes_everything(self):
        self.assertEqual(len(self.abort(True)), 3)

    def test_is_ours(self):
        self.assertTrue(guardian.is_ours({"metadata": {"name": "reslab-run-1"}}))
        self.assertTrue(guardian.is_ours({"metadata": {"name": "x", "labels": {"reslab-run-id": "r"}}}))
        self.assertFalse(guardian.is_ours({"metadata": {"name": "x", "labels": None}}))

    def test_kill_switch_returns_epoch_times(self):
        env = FakeEnv([FAILED])
        before = time.time()
        result = guardian.guard(
            "reslab-run-abc", "NetworkChaos", {"probe_p95_ms": 700}, {}, 90,
            deps=env.deps(), settings=GUARD_SETTINGS,
        )

        self.assertEqual(result["outcome"], "breach")
        self.assertGreaterEqual(result["detected_epoch"], before)
        self.assertGreaterEqual(result["kill_switch"]["at_epoch"], result["detected_epoch"])
        self.assertTrue(result["kill_switch"]["verified_gone"])

    def test_no_detection_time_without_a_breach(self):
        env = FakeEnv([HEALTHY], states=["running", "finished"])
        result = guardian.guard(
            "reslab-run-abc", "NetworkChaos", {"probe_p95_ms": 700}, {}, 90,
            deps=env.deps(), settings=GUARD_SETTINGS,
        )

        self.assertEqual(result["outcome"], "completed")
        self.assertIsNone(result["detected_epoch"])
        self.assertIn("at_epoch", result["kill_switch"])


class OtherSamplerCheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "test.db")
        p = mock.patch.object(controller, "connect", lambda: observe.connect(self.db))
        p.start()
        self.addCleanup(p.stop)

    def add_row(self, ts, journey="browse"):
        con = observe.connect(self.db)
        con.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts, None, journey, 1, 10.0, 200, None, None, None))
        con.commit()
        con.close()

    def test_counts_only_recent_journey_rows(self):
        t = time.time()
        self.add_row(t - 3)                          # another sampler, just now
        self.add_row(t - 100)                        # an old run
        self.add_row(t - 2, journey=None)            # a per-service row, not a journey probe
        self.assertEqual(controller.recent_sample_count(15), 1)

    def test_empty_database_is_fine(self):
        self.assertEqual(controller.recent_sample_count(15), 0)
        controller.check_no_other_sampler()          # does not raise

    def test_recent_rows_raise(self):
        self.add_row(time.time() - 1)
        with self.assertRaises(RuntimeError):
            controller.check_no_other_sampler()


class ObserveStage2Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "test.db")
        observe.connect(self.db).close()   # set the database up first: a fresh file races when two threads open it at once
        for p in (
            mock.patch.object(observe, "run_journey", lambda name, cfg: {"ok": True, "latency_ms": 10.0, "status": 200}),
            mock.patch.object(observe, "sample_services", lambda cfg, ts, exp: []),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_sampler_stops_when_the_event_is_set(self):
        stop = threading.Event()
        thread = threading.Thread(target=observe.run_sampler, args=("exp-stop", None, self.db, stop), daemon=True)
        thread.start()

        deadline = time.time() + 5
        while time.time() < deadline and not observe.read_samples("exp-stop", self.db):
            time.sleep(0.05)

        stop.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        rows = observe.read_samples("exp-stop", self.db)
        self.assertGreaterEqual(len(rows), 3)                  # one tick = browse, cart, checkout
        self.assertEqual({r["experiment_id"] for r in rows}, {"exp-stop"})

    def test_sampler_still_stops_after_seconds(self):
        observe.run_sampler("exp-time", 1, self.db)
        self.assertGreaterEqual(len(observe.read_samples("exp-time", self.db)), 3)

    def test_runs_table_round_trip(self):
        record = {
            "experiment_id": "run-1", "repeat": 1, "mode": "execute", "target": "catalog",
            "fault_type": "packet_loss", "t_start": 1.0, "t_inject": 2.0, "t_breach": None, "t_clear": 3.0,
            "aborted": True, "abort_reason": "probe_failure", "outcome": "survived", "quality": "ok",
            "forced_breach": False, "journeys": {"browse": {"A": 1.0, "L": 1.0, "n": 15}},
            "evidence": {"trace_ids": ["t1"]},
        }
        observe.save_run(record, self.db)
        observe.save_run({**record, "outcome": "not_recovered"}, self.db)   # same id: replaces

        rows = observe.read_runs(self.db)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "not_recovered")
        self.assertEqual(rows[0]["aborted"], 1)
        self.assertEqual(rows[0]["forced_breach"], 0)
        self.assertIsNone(rows[0]["t_breach"])
        self.assertEqual(rows[0]["journeys"]["browse"]["n"], 15)
        self.assertEqual(rows[0]["evidence"]["trace_ids"], ["t1"])


if __name__ == "__main__":
    unittest.main()
