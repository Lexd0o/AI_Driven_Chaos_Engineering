"""Offline tests for the P3 fixes in plan.py, policy.py, rank.py, discover.py. No cluster, no network.
Run from the repo root: python -m unittest tests.test_p3_fixes -v
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

import yaml

from src import discover, plan, policy, rank

POLICY = yaml.safe_load(open("config/policy.yaml"))
GRAPH = json.load(open("data/graph.json"))
JOURNEY_ROOTS = yaml.safe_load(open("config/planner.yaml"))["journey_roots"]
ENTRY_POINTS = yaml.safe_load(open("config/graph_overrides.yaml"))["entry_points"]


def item(fault="pod_kill", **params):
    params = {"duration_s": 60, **params}
    return {"experiment_id": "exp-x", "fault_type": fault, "mechanism": "chaosmesh",
            "target": {"namespace": "otel-demo", "service": "cart"}, "params": params}


class JourneyTests(unittest.TestCase):
    def setUp(self):
        members = plan.journey_members(GRAPH, JOURNEY_ROOTS, ENTRY_POINTS)
        self.of = lambda svc: plan.expected_journeys(svc, members)

    def test_entry_and_frontend_in_every_journey(self):
        every = sorted(JOURNEY_ROOTS)
        self.assertEqual(self.of("frontend-proxy"), every)
        self.assertEqual(self.of("frontend"), every)

    def test_callers_are_not_over_attributed(self):
        self.assertEqual(self.of("checkout"), ["checkout"])
        self.assertEqual(self.of("payment"), ["checkout"])
        self.assertEqual(self.of("recommendation"), ["ads-recs"])

    def test_shared_dependencies(self):
        self.assertEqual(self.of("cart"), ["cart", "checkout"])
        self.assertEqual(self.of("product-catalog"), ["ads-recs", "browse", "checkout"])


class PolicyTests(unittest.TestCase):
    def test_valid_items_approved(self):
        self.assertEqual(policy.check_item(item(), POLICY), [])
        self.assertEqual(policy.check_item(item("network_delay", latency_ms=200), POLICY), [])
        self.assertEqual(policy.check_item(item("cpu_stress", cpu_workers=2), POLICY), [])

    def test_duration_must_be_positive_number_within_max(self):
        for bad in (0, -5, "60", None, True, 9999):
            self.assertTrue(policy.check_item(item(duration_s=bad), POLICY), bad)

    def test_network_delay_requires_positive_latency(self):
        self.assertTrue(policy.check_item(item("network_delay"), POLICY))
        self.assertTrue(policy.check_item(item("network_delay", latency_ms=0), POLICY))
        self.assertTrue(policy.check_item(item("network_delay", latency_ms=99999), POLICY))

    def test_cpu_stress_requires_positive_integer_workers(self):
        for bad in (None, 0, -1, 1.5, "2", 99):
            params = {} if bad is None else {"cpu_workers": bad}
            self.assertTrue(policy.check_item(item("cpu_stress", **params), POLICY), bad)

    def test_missing_keys_reject_instead_of_keyerror(self):
        no_target = item()
        del no_target["target"]
        no_service = item()
        del no_service["target"]["service"]
        no_fault = item()
        del no_fault["fault_type"]
        for broken in (no_target, no_service, no_fault):
            self.assertTrue(policy.check_item(broken, POLICY))

    def test_missing_forbidden_targets_fails_closed(self):
        loose = {k: v for k, v in POLICY.items() if k != "forbidden_targets"}
        with self.assertRaises(SystemExit):
            policy.check_item(item(), loose)

    def test_forbidden_target_rejected(self):
        bad = item()
        bad["target"]["service"] = "flagd"
        self.assertTrue(policy.check_item(bad, POLICY))


class RankTests(unittest.TestCase):
    def test_empty_graph_writes_empty_list(self):
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "data"))
            for name, body in (("graph", {"nodes": [], "edges": []}), ("risks", [])):
                with open(os.path.join(tmp, "data", f"{name}.json"), "w") as f:
                    json.dump(body, f)
            os.chdir(tmp)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rank.main()
                self.assertEqual(json.load(open("data/ranked_candidates.json")), [])
            finally:
                os.chdir(old)
        self.assertIn("WARNING", out.getvalue())


class DiscoverGuardTests(unittest.TestCase):
    def test_live_zero_edges_refused(self):
        self.assertIsNotNone(discover.refuse_empty_live_graph(0, live=True, allow_no_traces=False))

    def test_flag_fixtures_and_real_edges_allowed(self):
        self.assertIsNone(discover.refuse_empty_live_graph(0, live=True, allow_no_traces=True))
        self.assertIsNone(discover.refuse_empty_live_graph(0, live=False, allow_no_traces=False))
        self.assertIsNone(discover.refuse_empty_live_graph(12, live=True, allow_no_traces=False))


if __name__ == "__main__":
    unittest.main()
