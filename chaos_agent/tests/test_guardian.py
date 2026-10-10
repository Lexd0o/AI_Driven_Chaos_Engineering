import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import guardian


SETTINGS = {"context": "docker-desktop", "namespace": "otel-demo"}
BASELINE = {"probe_p95_ms": 700}
HEALTHY = {"ok": True, "latency_ms": 500, "status": 200}
SLOW = {"ok": True, "latency_ms": 5000, "status": 200}
FAILED = {"ok": False, "latency_ms": 5000, "status": None}
READY = {"desired": 1, "ready": 1, "restarts": 0, "fully_ready": True}
NOT_READY = {**READY, "ready": 0, "fully_ready": False}


class FakeEnv:
    # Scripted probes; the fake clock advances only on sleep.
    def __init__(self, probes, health=None, prom=None, states=None):
        self.clock = 0.0
        self.probes = list(probes)
        self.health = list(health or [])
        self.prom = prom or {"error_ratio": 0.0, "p95_ms": 100, "rps": 2}
        self.states = list(states or [])
        self.deleted = []
        self.gone = False

    def deps(self):
        def probe():
            return self.probes.pop(0) if len(self.probes) > 1 else self.probes[0]

        def health():
            if len(self.health) > 1:
                return self.health.pop(0)
            return self.health[0] if self.health else READY

        def state(kind, name):
            return self.states.pop(0) if self.states else "running"

        def delete(args):
            self.deleted.append(args)
            self.gone = True

        def sleep(seconds):
            self.clock += seconds

        return {
            "probe": probe,
            "prometheus": lambda: self.prom,
            "health": health,
            "state": state,
            "exists": lambda kind, name: not self.gone,
            "delete": delete,
            "now": lambda: self.clock,
            "sleep": sleep,
        }


def run(env, kind="NetworkChaos", max_seconds=90, **thresholds):
    return guardian.guard(
        "reslab-experiment", kind, BASELINE, thresholds, max_seconds,
        deps=env.deps(), settings=SETTINGS,
    )


class GuardianTests(unittest.TestCase):
    def test_latency_breach_after_n_consecutive(self):
        env = FakeEnv([HEALTHY, HEALTHY, SLOW, SLOW, SLOW, HEALTHY])
        result = run(env)

        self.assertEqual(result["outcome"], "breach")
        self.assertEqual(result["breach"]["reason"], "probe_latency_exceeded")
        slow = [e for e in result["timeline"]
                if e["event"] == "probe" and e["latency_ms"] == 5000]
        self.assertEqual(len(slow), 3)

    def test_single_blip_does_not_breach(self):
        env = FakeEnv([HEALTHY, SLOW, HEALTHY, SLOW, HEALTHY, HEALTHY])
        result = run(env, max_seconds=10)

        self.assertFalse(result["breach"]["breached"])
        self.assertEqual(result["outcome"], "max_seconds_exceeded")

    def test_probe_failure_breach(self):
        env = FakeEnv([HEALTHY, FAILED, FAILED, FAILED, HEALTHY])
        result = run(env)

        self.assertEqual(result["breach"]["reason"], "probe_failure")

    def test_error_ratio_breach(self):
        env = FakeEnv([HEALTHY])
        env.prom = {"error_ratio": 0.2, "p95_ms": 100, "rps": 2}
        result = run(env)

        self.assertEqual(result["breach"]["reason"], "error_ratio_exceeded")

    def test_kill_switch_command_and_verification(self):
        env = FakeEnv([FAILED])
        result = run(env)

        self.assertEqual(
            env.deleted,
            [[
                "delete", "networkchaos", "reslab-experiment",
                "-n", "otel-demo", "--wait=false", "--ignore-not-found",
            ]],
        )
        self.assertTrue(result["kill_switch"]["verified_gone"])
        self.assertIn("delete networkchaos reslab-experiment",
                      result["kill_switch"]["command"])

    def test_recovery_measured(self):
        env = FakeEnv([SLOW, SLOW, SLOW, HEALTHY])
        result = run(env, recovery_healthy_probes=5)

        self.assertTrue(result["recovery"]["recovered"])
        self.assertGreaterEqual(result["recovery_seconds"], 8)
        self.assertIsNotNone(result["recovery_seconds"])

    def test_recovery_timeout(self):
        env = FakeEnv([FAILED])
        result = run(env, recovery_timeout_seconds=20)

        self.assertFalse(result["recovery"]["recovered"])
        self.assertIsNone(result["recovery_seconds"])

    def test_natural_end_still_measures_recovery(self):
        env = FakeEnv([HEALTHY], states=["running", "running", "finished"])
        result = run(env)

        self.assertEqual(result["outcome"], "completed")
        self.assertTrue(result["recovery"]["recovered"])

    def test_pod_kill_waits_for_replacement(self):
        env = FakeEnv(
            [HEALTHY],
            health=[READY, NOT_READY, NOT_READY, READY],
        )
        result = run(env, kind="PodChaos")

        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(env.deleted[0][1], "podchaos")

    def test_keyboard_interrupt_triggers_kill_switch(self):
        env = FakeEnv([HEALTHY])
        deps = env.deps()
        calls = {"n": 0}

        def probe():
            calls["n"] += 1
            if calls["n"] == 3:
                raise KeyboardInterrupt
            return HEALTHY

        deps["probe"] = probe
        result = guardian.guard(
            "reslab-experiment", "PodChaos", BASELINE, {}, 90,
            deps=deps, settings=SETTINGS,
        )

        self.assertEqual(result["outcome"], "interrupted")
        self.assertEqual(len(env.deleted), 1)

    def test_guardian_error_triggers_kill_switch(self):
        env = FakeEnv([HEALTHY])
        deps = env.deps()
        calls = {"n": 0}

        def probe():
            calls["n"] += 1
            if calls["n"] == 2:
                raise FileNotFoundError("kubectl not found")
            return HEALTHY

        deps["probe"] = probe
        result = guardian.guard(
            "reslab-experiment", "NetworkChaos", BASELINE, {}, 90,
            deps=deps, settings=SETTINGS,
        )

        self.assertEqual(result["outcome"], "guardian_error")
        self.assertEqual(len(env.deleted), 1)
        self.assertTrue(result["kill_switch"]["verified_gone"])

    def test_latency_limit(self):
        limits = {"latency_multiplier": 3, "latency_floor_ms": 1500}
        self.assertEqual(guardian.latency_limit({"probe_p95_ms": 700}, limits), 2100)
        self.assertEqual(guardian.latency_limit({"probe_p95_ms": 100}, limits), 1500)


if __name__ == "__main__":
    unittest.main()
