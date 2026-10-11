"""Checkout journey probe + per-probe max_latency_ms. urllib is mocked: no network."""
import json
import socket
import unittest
from unittest import mock

from agents.chaos_agent_expanded import guardian


class Resp:
    def __init__(self, status=200, body=None):
        self.status = status
        self._raw = json.dumps(body if body is not None else {}).encode()

    def read(self, n=-1):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(cart=None, checkout=None, calls=None):
    def _open(request, timeout=None):
        if calls is not None:
            calls.append((request.full_url, request.get_method(), json.loads(request.data or b"{}"), timeout))
        if "/api/cart" in request.full_url:
            r = cart if cart is not None else Resp(200, {"userId": "u"})
        else:
            r = checkout if checkout is not None else Resp(200, {"orderId": "abc-123"})
        if isinstance(r, Exception):
            raise r
        return r
    return _open


CFG = {"type": "checkout", "base_url": "http://x:8080", "product_id": "OLJCESPC7Z", "timeout_seconds": 6}


class CheckoutProbe(unittest.TestCase):
    def run_probe(self, **kw):
        calls = []
        with mock.patch("urllib.request.urlopen", fake_urlopen(calls=calls, **kw)):
            return guardian.probe_checkout(CFG), calls

    def test_ok(self):
        s, calls = self.run_probe()
        self.assertTrue(s["ok"])
        self.assertIsNone(s["error"])
        self.assertEqual([c[1] for c in calls], ["POST", "POST"])
        self.assertTrue(calls[0][0].endswith("/api/cart"))
        self.assertEqual(calls[0][2]["item"], {"productId": "OLJCESPC7Z", "quantity": 1})
        self.assertEqual(calls[0][2]["userId"], "chaospilot-probe-checkout")
        self.assertIn("/api/checkout?currencyCode=USD", calls[1][0])
        self.assertEqual(calls[1][2]["userId"], "chaospilot-probe-checkout")
        self.assertTrue(calls[1][2]["email"].endswith("@example.com"))

    def test_cart_failure_skips_checkout(self):
        s, calls = self.run_probe(cart=Resp(500, {}))
        self.assertFalse(s["ok"])
        self.assertEqual(len(calls), 1)

    def test_checkout_failure(self):
        s, _ = self.run_probe(checkout=Resp(500, {"error": "x"}))
        self.assertFalse(s["ok"])

    def test_checkout_http_error_raised(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 422, "declined", {}, None)
        s, _ = self.run_probe(checkout=err)
        self.assertFalse(s["ok"])

    def test_missing_order_id(self):
        s, _ = self.run_probe(checkout=Resp(200, {"shippingCost": {}}))
        self.assertFalse(s["ok"])
        self.assertIn("orderId", s["error"])

    def test_timeout(self):
        s, _ = self.run_probe(checkout=socket.timeout("timed out"))
        self.assertFalse(s["ok"])
        self.assertIn("timed out", s["error"])

    def test_timeout_budget_shared(self):
        _, calls = self.run_probe()
        self.assertLessEqual(calls[1][3], calls[0][3])


class PerProbeLatency(unittest.TestCase):
    def sample(self, get_ms, checkout_ms):
        return {"ok": True, "latency_ms": max(get_ms, checkout_ms), "journeys": {
            "browse": {"ok": True, "latency_ms": get_ms},
            "checkout": {"ok": True, "latency_ms": checkout_ms, "max_latency_ms": 5000}}}

    def test_checkout_uses_override(self):
        s = self.sample(100, 4000)
        self.assertTrue(guardian.healthy(s, {"max_latency_ms": 2000}))
        self.assertFalse(guardian.over_latency(s, 2000))
        self.assertTrue(guardian.over_latency(self.sample(100, 5500), 2000))

    def test_get_probe_still_uses_global(self):
        s = self.sample(2500, 100)
        self.assertFalse(guardian.healthy(s, {"max_latency_ms": 2000}))

    def test_sample_without_journeys(self):
        self.assertTrue(guardian.over_latency({"latency_ms": 3000}, 2000))

    def test_probe_journeys_mixed(self):
        probes = {"browse": {"url": "http://x/api/products"}, "checkout": {**CFG, "max_latency_ms": 5000}}

        def _open(request, timeout=None):
            if request.get_method() == "GET":
                return Resp(200, [1])
            return fake_urlopen()(request, timeout)
        with mock.patch("urllib.request.urlopen", _open):
            s = guardian.probe_journeys(probes, 3)
        self.assertTrue(s["ok"])
        self.assertEqual(list(s["journeys"]), ["browse", "checkout"])
        self.assertNotIn("max_latency_ms", s["journeys"]["browse"])
        self.assertEqual(s["journeys"]["checkout"]["max_latency_ms"], 5000)


if __name__ == "__main__":
    unittest.main()
