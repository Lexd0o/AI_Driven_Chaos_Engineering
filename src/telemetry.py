"""Telemetry access: config, Prometheus, Jaeger (v3 API). P2 (Observability).

Run alone:  python -m src.telemetry        (lists Prometheus metric names + Jaeger services)
Jaeger: this demo serves the v3 API at <frontend-proxy>/jaeger/ui/api/v3 (OTLP-style JSON). Set jaeger_url to that.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "journeys.yaml"

ERROR_CODES = (2, "STATUS_CODE_ERROR")   # OTLP span status: 2 = ERROR


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
    data = _get(load_config()["prometheus_url"] + "/api/v1/label/__name__/values")
    return [n for n in data["data"] if contains in n]


# -------------------------------------------------------------------- Jaeger v3
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _get_v3(path: str, params: dict | None = None):
    """GET <jaeger_url><path>. Returns parsed JSON (dict, or list of dicts if the reply is one JSON per line).
    Returns None when Jaeger says 404 'No traces found'."""
    r = requests.get(load_config()["jaeger_url"].rstrip("/") + path, params=params, timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:  # streamed: one JSON object per line
        return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def _service_name(resource_spans: dict) -> str | None:
    for a in resource_spans.get("resource", {}).get("attributes", []):
        if a.get("key") == "service.name":
            return a.get("value", {}).get("stringValue")
    return None


def jaeger_services() -> list[str]:
    data = _get_v3("/services")
    return (data or {}).get("services", []) if isinstance(data, dict) else []


def jaeger_traces(service: str, start: float, end: float, limit: int = 50,
                  error_only: bool = False) -> list[dict]:
    """start/end are UTC epoch seconds.
    Returns [{"trace_id", "duration_ms", "error", "spans": [{"span_id","parent_id","service","name","kind","error"}]}]."""
    params = {"query.service_name": service, "query.start_time_min": _iso(start),
              "query.start_time_max": _iso(end), "query.num_traces": limit}
    data = _get_v3("/traces", params)
    if not data:
        return []
    chunks = data if isinstance(data, list) else [data]
    traces: dict[str, list[dict]] = {}
    for chunk in chunks:
        for rs in chunk.get("result", {}).get("resourceSpans", []):
            svc = _service_name(rs)
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    traces.setdefault(s["traceId"], []).append({
                        "span_id": s["spanId"], "parent_id": s.get("parentSpanId") or None,
                        "service": svc, "name": s.get("name"), "kind": s.get("kind"),
                        "start_ns": int(s.get("startTimeUnixNano", 0)), "end_ns": int(s.get("endTimeUnixNano", 0)),
                        "error": s.get("status", {}).get("code") in ERROR_CODES,
                    })
    out = []
    for tid, spans in traces.items():
        dur = (max(x["end_ns"] for x in spans) - min(x["start_ns"] for x in spans)) / 1e6
        out.append({"trace_id": tid, "duration_ms": round(dur, 1),
                    "error": any(x["error"] for x in spans), "spans": spans})
    return [t for t in out if t["error"]] if error_only else out


def edges_from_traces(start: float, end: float, limit_per_service: int = 50) -> list[dict]:
    """Caller -> callee edges: a span whose parent span belongs to a different service.
    Returns [{"source", "target", "count"}] (source/target = caller/callee)."""
    counts: Counter = Counter()
    seen: set[str] = set()
    for svc in jaeger_services():
        for trace in jaeger_traces(svc, start, end, limit=limit_per_service):
            if trace["trace_id"] in seen:
                continue
            seen.add(trace["trace_id"])
            owner = {s["span_id"]: s["service"] for s in trace["spans"]}
            for s in trace["spans"]:
                parent = owner.get(s["parent_id"])
                if parent and s["service"] and parent != s["service"]:
                    counts[(parent, s["service"])] += 1
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