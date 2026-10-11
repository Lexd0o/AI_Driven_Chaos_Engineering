"""Tests for src/baseline.py and the baseline-derived SLOs in plan.py. No network."""
import unittest
from unittest.mock import MagicMock, patch

from src import baseline, plan

CFG = {"p95_factor": 2.0, "p95_floor_ms": 1000, "success_margin": 0.02, "success_floor": 0.80}
BASE = {"journeys": {
    "browse": {"success_rate": 1.0, "p95_ms": 700, "samples": 10},
    "cart": {"success_rate": 0.9, "p95_ms": 200, "samples": 10},
}}


def resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class TestPercentile(unittest.TestCase):
    def test_nearest_rank(self):
        vals = list(range(1, 101))
        self.assertEqual(baseline.percentile(vals, 50), 50)
        self.assertEqual(baseline.percentile(vals, 95), 95)
        self.assertEqual(baseline.percentile(vals, 99), 99)
        self.assertEqual(baseline.percentile([5, 1, 3], 100), 5)
        self.assertEqual(baseline.percentile([], 95), 0.0)
        self.assertEqual(baseline.percentile([7], 95), 7)


class TestProbe(unittest.TestCase):
    def test_ok_and_expect_products(self):
        with patch("src.baseline.requests.get", return_value=resp(200, {"products": [{"id": 1}]})):
            ok, ms = baseline.probe_once("browse", {"url": "u", "expect_products": True}, 1)
        self.assertTrue(ok)
        self.assertGreaterEqual(ms, 0)

    def test_empty_products_fails(self):
        for payload in ({"products": []}, [], None):
            with patch("src.baseline.requests.get", return_value=resp(200, payload)):
                ok, _ = baseline.probe_once("browse", {"url": "u", "expect_products": True}, 1)
            self.assertFalse(ok)

    def test_non_200_and_exception_fail(self):
        with patch("src.baseline.requests.get", return_value=resp(503)):
            self.assertFalse(baseline.probe_once("cart", {"url": "u"}, 1)[0])
        with patch("src.baseline.requests.get", side_effect=OSError("down")):
            self.assertFalse(baseline.probe_once("cart", {"url": "u"}, 1)[0])

    def test_unknown_typed_and_post_probe_skipped(self):
        with patch("src.baseline.requests.get") as g:
            self.assertIsNone(baseline.probe_once("x", {"url": "u", "type": "weird"}, 1))
            self.assertIsNone(baseline.probe_once("x", {"url": "u", "method": "POST"}, 1))
        g.assert_not_called()

    def test_checkout_uses_probe_checkout(self):
        fake = MagicMock(return_value={"ok": True, "latency_ms": 480.5, "error": None})
        with patch("src.baseline.probe_checkout", fake):
            self.assertEqual(baseline.probe_once("checkout", {"type": "checkout"}, 5), (True, 480.5))
        with patch("src.baseline.probe_checkout", None):
            self.assertIsNone(baseline.probe_once("checkout", {"type": "checkout"}, 5))

    def test_checkout_throttled(self):
        fake = MagicMock(return_value={"ok": True, "latency_ms": 10.0, "error": None})
        probes = {"checkout": {"type": "checkout"}, "cart": {"url": "u"}}
        clock = [0.0]
        with patch("src.baseline.probe_checkout", fake),                 patch("src.baseline.requests.get", return_value=resp(200, {})),                 patch("src.baseline.time.sleep", side_effect=lambda d: clock.__setitem__(0, clock[0] + d)),                 patch("src.baseline.time.monotonic", side_effect=lambda: clock[0]):
            # fake clock advanced by sleep: ~12 ticks of 5s; checkout runs on ticks 0,3,6,9
            samples, _ = baseline.measure(probes, 1, 5, 5, checkout_every_n=3)
        self.assertEqual(len(samples["checkout"]), fake.call_count)
        self.assertLess(len(samples["checkout"]), len(samples["cart"]))


class TestSummary(unittest.TestCase):
    def test_report_shape(self):
        samples = {"browse": [(True, float(i)) for i in range(1, 20)] + [(False, 100.0)], "empty": []}
        rep = baseline.build_report(samples, 1, 5, ["n"])
        self.assertEqual(set(rep), {"generated_at", "minutes", "interval_s",
                                    "samples_per_journey", "journeys", "notes"})
        self.assertEqual(rep["samples_per_journey"], 20)
        j = rep["journeys"]["browse"]
        self.assertEqual(set(j), {"success_rate", "p50_ms", "p95_ms", "p99_ms", "max_ms", "samples"})
        self.assertEqual(j["success_rate"], 0.95)
        self.assertEqual(j["max_ms"], 100.0)
        self.assertNotIn("empty", rep["journeys"])


class TestDeriveSlo(unittest.TestCase):
    def test_factor_floor_margin(self):
        slo, src = baseline.derive_slo(["browse", "cart"], BASE, plan.DEFAULT_SLO, CFG)
        self.assertEqual(src, "baseline")
        self.assertEqual(slo["p95_ms_max"], 1400)          # 700 x 2
        self.assertEqual(slo["success_rate_min"], 0.88)    # 0.9 - 0.02
        slo, _ = baseline.derive_slo(["cart"], BASE, plan.DEFAULT_SLO, CFG)
        self.assertEqual(slo["p95_ms_max"], 1000)          # 400 -> floor

    def test_caps_and_floor_on_rate(self):
        slo, _ = baseline.derive_slo(["browse"], BASE, plan.DEFAULT_SLO, CFG)
        self.assertEqual(slo["success_rate_min"], 0.95)    # 0.98 capped
        bad = {"journeys": {"x": {"success_rate": 0.5, "p95_ms": 10, "samples": 3}}}
        slo, _ = baseline.derive_slo(["x"], bad, plan.DEFAULT_SLO, CFG)
        self.assertEqual(slo["success_rate_min"], 0.80)

    def test_missing_journeys_ignored_or_default(self):
        slo, src = baseline.derive_slo(["checkout", "cart"], BASE, plan.DEFAULT_SLO, CFG)
        self.assertEqual((src, slo["p95_ms_max"]), ("baseline", 1000))
        slo, src = baseline.derive_slo(["checkout"], BASE, plan.DEFAULT_SLO, CFG)
        self.assertEqual((src, slo), ("default", plan.DEFAULT_SLO))

    def test_plan_item_gets_source(self):
        cand = [{"service": "cart", "rank": 1, "top_risks": ["r1"], "reason": "x"}]
        risks = {"r1": {"risk_id": "r1", "check": "single_replica", "evidence": "e",
                        "suggested_experiment": "pod_kill"}}
        catalog = {"pod_kill": {"mechanism": "m", "default_duration_s": 30}}
        members = {"cart": {"cart"}}
        items = plan.build_plan(cand, risks, catalog, members, BASE, {}, CFG)
        self.assertEqual(items[0]["slo_source"], "baseline")
        self.assertEqual(items[0]["slo"]["p95_ms_max"], 1000)
        items = plan.build_plan(cand, risks, catalog, members)
        self.assertEqual((items[0]["slo_source"], items[0]["slo"]), ("default", plan.DEFAULT_SLO))

    def test_per_journey_limits(self):
        slo, _ = baseline.derive_slo(["browse", "cart", "checkout"], BASE, plan.DEFAULT_SLO, CFG)
        self.assertEqual(slo["p95_ms_by_journey"], {"browse": 1400, "cart": 1000})  # checkout has no baseline
        self.assertEqual(slo["p95_ms_max"], 1400)
        slo, _ = baseline.derive_slo(["x"], {"journeys": {}}, plan.DEFAULT_SLO, CFG)
        self.assertNotIn("p95_ms_by_journey", slo)

    def test_for_targets_probe_included(self):
        probes = {"shipping-quote": {"for_targets": ["shipping"]}}
        self.assertEqual(plan.slo_journeys("shipping", ["cart"], probes), ["cart", "shipping-quote"])


if __name__ == "__main__":
    unittest.main()
