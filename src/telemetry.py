"""Telemetry access: config, Prometheus, Jaeger. P2 (Observability).

Run alone:  python -m src.telemetry        (lists Prometheus metric names + Jaeger services)
Everything cluster-specific is marked [verify].
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "journeys.yaml"


def load_config() -> dict:
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if os.getenv("PROBE_BASE_URL"):
        cfg["base_url"] = os.environ["PROBE_BASE_URL"]
    if os.getenv("PROMETHEUS_URL"):
        cfg["prometheus_url"] = os.environ["PROMETHEUS_URL"]
    if os.getenv("JAEGER_URL"):
        cfg["jaeger_url"] = os.environ["JAEGER_URL"]
    return cfg


def _get(url: str, params: dict | None = None, timeout: float = 10) -> dict:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- Prometheus
def prom_query(promql: str, at: float | None = None) -> list[dict]:
    """Instant query. Returns Prometheus 'result' list."""
    params = {"query": promql}
    if at is not None:
        params["time"] = at
    data = _get(load_config()["prometheus_url"] + "/api/v1/query", params)
    return data["data"]["result"]


def prom_range(promql: str, start: float, end: float, step_s: int = 2) -> list[dict]:
    params = {"query": promql, "start": start, "end": end, "step": step_s}
    data = _get(load_config()["prometheus_url"] + "/api/v1/query_range", params)
    return data["data"]["result"]


def prom_metric_names(contains: str = "") -> list[str]:
    """Use on Day 1 to find the real span-metric names [verify]."""
    data = _get(load_config()["prometheus_url"] + "/api/v1/label/__name__/values")
    names = data["data"]
    return [n for n in names if contains in n]


# -------------------------------------------------------------------- Jaeger
def jaeger_services() -> list[str]:
    return _get(load_config()["jaeger_url"] + "/api/services")["data"] or []


def jaeger_traces(service: str, start: float, end: float, limit: int = 50,
                  error_only: bool = False) -> list[dict]:
    """start/end are UTC epoch seconds (Jaeger wants microseconds)."""
    params = {"service": service, "start": int(start * 1e6), "end": int(end * 1e6), "limit": limit}
    if error_only:
        params["tags"] = json.dumps({"error": "true"})
    return _get(load_config()["jaeger_url"] + "/api/traces", params)["data"] or []


def edges_from_traces(start: float, end: float, limit_per_service: int = 50) -> list[dict]:
    """Caller -> callee edges from parent/child spans in different services.

    Returns [{"source", "target", "count"}]. [verify against Architecture.md 4.1 edge format]
    """
    counts: Counter = Counter()
    for svc in jaeger_services():
        for trace in jaeger_traces(svc, start, end, limit=limit_per_service):
            procs = {pid: p["serviceName"] for pid, p in trace.get("processes", {}).items()}
            span_service = {s["spanID"]: procs.get(s["processID"]) for s in trace["spans"]}
            for s in trace["spans"]:
                child = procs.get(s["processID"])
                for ref in s.get("references", []):
                    if ref.get("refType") != "CHILD_OF":
                        continue
                    parent = span_service.get(ref["spanID"])
                    if parent and child and parent != child:
                        counts[(parent, child)] += 1
    return [{"source": a, "target": b, "count": c} for (a, b), c in sorted(counts.items())]


if __name__ == "__main__":
    print("Prometheus metrics containing 'calls' or 'duration':")
    try:
        for n in prom_metric_names("calls") + prom_metric_names("duration"):
            print("  ", n)
    except Exception as e:  # noqa: BLE001
        print("  Prometheus not reachable:", e)
    print("Jaeger services:")
    try:
        print("  ", jaeger_services())
    except Exception as e:  # noqa: BLE001
        print("  Jaeger not reachable:", e)