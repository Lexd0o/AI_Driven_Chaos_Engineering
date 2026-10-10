"""A small HTTP probe guardian. No invented metrics or recovery scores.

Vendored from chaos_agent_expanded. ChaosPilot change: probe_journeys() probes
one GET URL per journey and folds them into a single sample (ok only if every
journey is ok; latency = slowest journey).
"""
import json
import time
import urllib.request

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


def probe_journeys(probes, timeout_seconds):
    """probes: {journey: {"url": ..., "expect_products": bool}} -> one combined sample."""
    journeys = {name: probe({**cfg, "timeout_seconds": timeout_seconds}) for name, cfg in probes.items()}
    errors = [f"{name}: {s['error']}" for name, s in journeys.items() if s["error"]]
    return {"observed_at": now(), "ok": not errors,
            "latency_ms": max(s["latency_ms"] for s in journeys.values()),
            "error": "; ".join(errors) or None, "journeys": journeys}


def healthy(sample, config):
    return sample["ok"] and sample["latency_ms"] <= config["max_latency_ms"]


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
