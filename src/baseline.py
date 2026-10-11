"""baseline.py - measure steady-state journey health before any fault: data/baseline.json.

Usage (run from the repo root, with the shop port-forwarded as in config/executor.yaml):
    python -m src.baseline [--minutes N] [--interval S]

Every interval it GETs each journey probe from config/executor.yaml `journey_probes`
(`type: checkout` goes through the guardian's probe_checkout, every checkout_every_n-th tick since it places a real order; other non-GET probes are skipped), records ok + latency,
and writes success rate and latency percentiles per journey. plan.py turns these into SLOs
(derive_slo below; factors live in config/planner.yaml `baseline:`). Read-only: GETs only.
Ctrl+C stops early and still writes what it has.
"""
import argparse
import json
import math
import os
import time
from datetime import datetime, timezone

import requests
import yaml

BASELINE_PATH = "data/baseline.json"
PLANNER_CONFIG_PATH = "config/planner.yaml"
EXECUTOR_CONFIG_PATH = "config/executor.yaml"


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0-100). Empty list -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


try:
    from agents.chaos_agent_expanded.guardian import probe_checkout
except Exception:  # keep baseline usable without the agent package; checkout is then skipped
    probe_checkout = None


def checkout_probe(name: str, probe: dict, timeout_s: float) -> tuple[bool, float] | None:
    """Synthetic checkout (places one real demo order) via the guardian's probe_checkout."""
    if probe_checkout is None:
        return None
    sample = probe_checkout({"timeout_seconds": timeout_s, **probe})
    return bool(sample["ok"]), float(sample["latency_ms"])


def probe_once(name: str, probe: dict, timeout_s: float) -> tuple[bool, float] | None:
    """One sample: (ok, latency_ms), or None when this probe is not measured."""
    if probe.get("type") == "checkout":
        return checkout_probe(name, probe, timeout_s)
    if probe.get("type") or str(probe.get("method", "GET")).upper() != "GET":
        return None  # unknown synthetic/non-GET probe: not measured
    start = time.perf_counter()
    try:
        resp = requests.get(probe["url"], timeout=timeout_s)
        ok = resp.status_code == 200
        if ok and probe.get("expect_products"):
            data = resp.json()
            products = data.get("products") if isinstance(data, dict) else data
            ok = bool(products)
    except Exception:
        ok = False
    return ok, (time.perf_counter() - start) * 1000


def summarize(samples: dict[str, list[tuple[bool, float]]]) -> dict:
    out = {}
    for name, rows in samples.items():
        if not rows:
            continue
        lat = [ms for _, ms in rows]
        out[name] = {
            "success_rate": round(sum(1 for ok, _ in rows if ok) / len(rows), 4),
            "p50_ms": round(percentile(lat, 50), 1),
            "p95_ms": round(percentile(lat, 95), 1),
            "p99_ms": round(percentile(lat, 99), 1),
            "max_ms": round(max(lat), 1),
            "samples": len(rows),
        }
    return out


def build_report(samples: dict, minutes: float, interval_s: float, notes: list[str]) -> dict:
    journeys = summarize(samples)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "minutes": minutes,
        "interval_s": interval_s,
        "samples_per_journey": max((j["samples"] for j in journeys.values()), default=0),
        "journeys": journeys,
        "notes": notes,
    }


def measure(probes: dict, minutes: float, interval_s: float, timeout_s: float,
            checkout_every_n: int = 3) -> tuple[dict, list[str]]:
    samples: dict[str, list] = {name: [] for name in probes}
    skipped: set[str] = set()
    ticks = -1  # first tick is 0, so checkout is sampled at tick 0, n, 2n...
    end = time.monotonic() + minutes * 60
    next_report = time.monotonic() + 60
    try:
        while True:
            tick = time.monotonic()
            ticks += 1
            for name, probe in probes.items():
                if probe.get("type") == "checkout" and ticks % max(checkout_every_n, 1):
                    continue  # throttled: each checkout sample places an order
                result = probe_once(name, probe, timeout_s)
                if result is None:
                    skipped.add(name)
                else:
                    samples[name].append(result)
            if time.monotonic() >= next_report:
                next_report += 60
                n = max((len(v) for v in samples.values()), default=0)
                bad = sum(1 for v in samples.values() for ok, _ in v if not ok)
                print(f"  {n} samples/journey so far, {bad} failed")
            if time.monotonic() + interval_s >= end:
                break
            time.sleep(max(0.0, interval_s - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print("interrupted - writing partial baseline")
    notes = [f"{n}: not measured (no usable probe function)" for n in sorted(skipped)]
    return samples, notes


def derive_slo(journeys: list[str], baseline: dict, default_slo: dict, cfg: dict) -> tuple[dict, str]:
    """SLO for an item from baseline stats of its journeys. Returns (slo, "baseline"|"default").
    p95_ms_max = max(floor, round(max baseline p95 x factor));
    success_rate_min = min(0.95, max(floor, min baseline success_rate - margin))."""
    stats = [baseline["journeys"][j] for j in journeys if j in baseline.get("journeys", {})]
    if not stats:
        return dict(default_slo), "default"
    p95 = max(cfg["p95_floor_ms"], round(max(s["p95_ms"] for s in stats) * cfg["p95_factor"]))
    rate = min(0.95, max(cfg["success_floor"],
                         min(s["success_rate"] for s in stats) - cfg["success_margin"]))
    by_journey = {j: max(cfg["p95_floor_ms"], round(baseline["journeys"][j]["p95_ms"] * cfg["p95_factor"]))
                  for j in journeys if j in baseline.get("journeys", {})}
    return {"success_rate_min": round(rate, 3), "p95_ms_max": p95,
            "p95_ms_by_journey": by_journey}, "baseline"


def main() -> None:
    cfg = load_yaml(PLANNER_CONFIG_PATH).get("baseline", {})
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=cfg.get("minutes", 5))
    parser.add_argument("--interval", type=float, default=cfg.get("interval_s", 5))
    args = parser.parse_args()
    timeout_s = cfg.get("timeout_s", 5)

    probes = load_yaml(EXECUTOR_CONFIG_PATH).get("journey_probes") or {}
    if not probes:
        raise SystemExit(f"no journey_probes in {EXECUTOR_CONFIG_PATH}")
    print(f"baseline: {args.minutes} min, every {args.interval}s, probes {list(probes)}")

    samples, notes = measure(probes, args.minutes, args.interval, timeout_s,
                             cfg.get("checkout_every_n", 3))
    report = build_report(samples, args.minutes, args.interval, notes)
    os.makedirs("data", exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    parts = [f"{n} ok={j['success_rate']:.2%} p95={j['p95_ms']}ms" for n, j in report["journeys"].items()]
    print(f"wrote {BASELINE_PATH}: {report['samples_per_journey']} samples/journey; " + "; ".join(parts))


if __name__ == "__main__":
    main()
