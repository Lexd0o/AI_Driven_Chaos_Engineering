"""Journey probes: synthetic browse / cart / checkout transactions. P2 (Observability).

Each probe returns {"ok": bool, "latency_ms": float, "status": int}
  ok          True only if every step returned 2xx
  latency_ms  total time for the whole journey
  status      HTTP status of the last step run (the failing one if it failed); 0 = no response/timeout

Each journey uses its own fake user: "<probe_user>-<journey>" (e.g. probe-user-p2-checkout), so one journey
can never empty or change another journey's cart while they run in parallel.

Run alone:  python -m src.probe
"""
from __future__ import annotations

import time

import requests

from src.telemetry import load_config


def _fill(obj, user: str, product: str):
    """Replace {user}/{product} placeholders anywhere in a nested dict/list/str."""
    if isinstance(obj, str):
        return obj.replace("{user}", user).replace("{product}", product)
    if isinstance(obj, dict):
        return {k: _fill(v, user, product) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, user, product) for v in obj]
    return obj


def journey_user(name: str, cfg: dict) -> str:
    """One user per journey, derived from probe_user."""
    return f"{cfg['probe_user']}-{name}"


def run_journey(name: str, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    steps = cfg["journeys"][name]["steps"]
    user, product = journey_user(name, cfg), cfg["probe_product_id"]
    base, timeout = cfg["base_url"].rstrip("/"), cfg.get("timeout_s", 5)

    status, ok = 0, True
    t0 = time.perf_counter()
    with requests.Session() as s:
        for step in steps:
            try:
                r = s.request(
                    step["method"], base + step["path"],
                    params=_fill(step.get("params"), user, product),
                    json=_fill(step.get("json"), user, product),
                    timeout=timeout,
                )
                status = r.status_code
                if not 200 <= r.status_code < 300:
                    ok = False
                    break
            except requests.RequestException:
                status, ok = 0, False
                break
    latency_ms = (time.perf_counter() - t0) * 1000
    return {"ok": ok, "latency_ms": round(latency_ms, 1), "status": status}


def probe_browse() -> dict:
    return run_journey("browse")


def probe_cart() -> dict:
    return run_journey("cart")


def probe_checkout() -> dict:
    return run_journey("checkout")


if __name__ == "__main__":
    for fn in (probe_browse, probe_cart, probe_checkout):
        print(fn.__name__, fn())