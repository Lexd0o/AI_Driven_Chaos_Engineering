"""A small HTTP probe guardian. No invented metrics or recovery scores.

Vendored from chaos_agent_expanded. ChaosPilot change: probe_journeys() probes
one GET URL per journey and folds them into a single sample (ok only if every
journey is ok; latency = slowest journey).
"""
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .core import now


def probe(config):
    start = time.monotonic()
    error = None
    try:
        request = urllib.request.Request(config["url"], headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=config.get("timeout_seconds", 3)) as response:
            data = response.read(2_000_000)
            if not 200 <= response.status < 300:
                raise ValueError(f"HTTP {response.status}")
            if config.get("expect_products", False):
                body = json.loads(data)
                products = body if isinstance(body, list) else body.get("products", []) if isinstance(body, dict) else []
                if not isinstance(products, list) or not products:
                    raise ValueError("Expected a nonempty product list; verify the probe URL")
    except Exception as exc:
        error = str(exc)
    elapsed = (time.monotonic() - start) * 1000
    return {"observed_at": now(), "ok": error is None, "latency_ms": round(elapsed, 2), "error": error}


CHECKOUT_PERSON = {  # clearly fake test customer, same shape as load-generator people.json
    "email": "chaospilot-probe@example.com",
    "address": {"streetAddress": "1 Chaos Probe Way", "zipCode": "00000", "city": "Testville",
                "state": "CA", "country": "United States"},
    "userCurrency": "USD",
    "creditCard": {"creditCardNumber": "4432-8015-6152-0454", "creditCardExpirationMonth": 1,
                   "creditCardExpirationYear": 2039, "creditCardCvv": 672},
}


def _post_json(url, body, timeout):
    data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
        if not 200 <= response.status < 300:
            raise ValueError(f"HTTP {response.status}")
        return json.loads(raw) if raw else {}


def probe_checkout(config):
    """POST /api/cart (1 known product) then POST /api/checkout. ok = both 200 and an order id came back;
    latency = both calls together. timeout_seconds bounds the WHOLE probe (shared deadline)."""
    start = time.monotonic()
    error = None
    timeout = config.get("timeout_seconds", 6)
    base = config.get("base_url", "http://localhost:8080").rstrip("/")
    user = config.get("user_id", "chaospilot-probe-checkout")
    try:
        _post_json(f"{base}/api/cart", {"item": {"productId": config.get("product_id", "OLJCESPC7Z"),
                                                 "quantity": 1}, "userId": user},
                   max(timeout - (time.monotonic() - start), 0.1))
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            raise TimeoutError("checkout probe timed out after cart call")
        order = _post_json(f"{base}/api/checkout?currencyCode={config.get('currency_code', 'USD')}",
                           {**CHECKOUT_PERSON, "userId": user}, remaining)
        if not isinstance(order, dict) or not order.get("orderId"):
            raise ValueError("checkout response has no orderId")
    except Exception as exc:
        error = str(exc) or type(exc).__name__
    elapsed = (time.monotonic() - start) * 1000
    return {"observed_at": now(), "ok": error is None, "latency_ms": round(elapsed, 2), "error": error}


def _run_probe(cfg, timeout_seconds):
    if cfg.get("type") == "checkout":
        sample = probe_checkout({**cfg, "timeout_seconds": cfg.get("timeout_seconds", timeout_seconds)})
    else:
        sample = probe({**cfg, "timeout_seconds": timeout_seconds})
    if cfg.get("max_latency_ms") is not None:  # per-probe override; GET probes without it are unchanged
        sample["max_latency_ms"] = cfg["max_latency_ms"]
    return sample


def probe_journeys(probes, timeout_seconds):
    """probes: {journey: {"url": ..., "expect_products": bool} | {"type": "checkout", ...}} -> one combined sample.
    Probes run concurrently so a slow checkout (up to its own timeout_seconds) does not delay the GETs;
    the poll still waits for the slowest probe, so one sample takes up to max(timeout) not the sum."""
    with ThreadPoolExecutor(max_workers=max(len(probes), 1)) as pool:
        futures = {name: pool.submit(_run_probe, cfg, timeout_seconds) for name, cfg in probes.items()}
        journeys = {name: f.result() for name, f in futures.items()}
    errors = [f"{name}: {s['error']}" for name, s in journeys.items() if s["error"]]
    return {"observed_at": now(), "ok": not errors,
            "latency_ms": max(s["latency_ms"] for s in journeys.values()),
            "error": "; ".join(errors) or None, "journeys": journeys}


def over_latency(sample, default_ms):
    """True if any journey exceeds its own max_latency_ms (when set) or default_ms."""
    journeys = sample.get("journeys")
    if not journeys:
        return sample["latency_ms"] > default_ms
    return any(j["latency_ms"] > j.get("max_latency_ms", default_ms) for j in journeys.values())


def healthy(sample, config):
    return sample["ok"] and not over_latency(sample, config["max_latency_ms"])


class Guardian:
    def __init__(self, config):
        self.config = config
        self.bad_since = None

    def observe(self, sample, clock):
        if healthy(sample, self.config):
            self.bad_since = None
            return False
        if self.bad_since is None:
            self.bad_since = clock
        return clock - self.bad_since >= self.config["breach_seconds"]
