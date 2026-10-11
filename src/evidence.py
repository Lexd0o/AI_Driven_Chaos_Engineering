"""Evidence collector: which traces and queries back a run's findings. P2 (Observability).

Run alone:  python -m src.evidence --seconds 600 [--slow-ms 1500] [--experiment exp-003]
            (looks at the last N seconds; prints the evidence dict)

collect_evidence(experiment_id, start, end) -> dict  (P2_Observability.md section 3)
  trace_ids    flat list: failed traces first, then slow ones (what the report cites)
  prom_queries the PromQL used
Extra keys (safe to ignore): failed_traces / slow_traces details, error_services, top_error_services, counts, quality, notes, info.

Never raises: if Jaeger or Prometheus is unreachable the dict says so in `notes` and quality = "partial".
`notes` = problems that lower quality. `info` = harmless remarks (does NOT lower quality).
Limits:
  - Jaeger returns at most `limit` recent traces per query, so a very busy window can still miss some failures.
    A note is added when the limit is reached.
  - Traces longer than MAX_TRACE_MS are long-lived sessions (not requests) and are ignored (info).
  - Traces that are our own Jaeger API calls (url contains /jaeger/) are ignored (info): without this, our
    own queries that return 404 'No traces found' would be counted as failures.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter

from src.telemetry import jaeger_traces, load_config, prom_range

MAX_TRACE_MS = 60_000   # traces longer than this are long-lived sessions, not requests


def _summaries(traces: list[dict], max_ids: int) -> list[dict]:
    out = []
    for t in traces[:max_ids]:
        bad = sorted({s["service"] for s in t["spans"] if s["error"] and s["service"]})
        out.append({"trace_id": t["trace_id"], "duration_ms": t["duration_ms"], "error_services": bad})
    return out


def _peak_errors(start: float, end: float, promql: str, notes: list[str]) -> list[dict]:
    """Peak errors/s per service over the window, top 5. [] if Prometheus fails."""
    try:
        series = prom_range(promql, start, end, step_s=10)
    except Exception as e:  # noqa: BLE001
        notes.append(f"prometheus unreachable: {e}")
        return []
    peaks = []
    for s in series:
        vals = [float(v[1]) for v in s["values"] if float(v[1]) == float(v[1])]  # drop NaN
        svc = s["metric"].get("service_name")
        if svc and vals and max(vals) > 0:
            peaks.append({"service": svc, "peak_errors_per_s": round(max(vals), 3)})
    return sorted(peaks, key=lambda p: -p["peak_errors_per_s"])[:5]


def collect_evidence(experiment_id: str, start: float, end: float, services: list[str] | None = None,
                     slow_ms: float = 1500, max_ids: int = 10, limit: int = 1000) -> dict:
    """start/end are UTC epoch seconds (VM clock). services = Jaeger services to search (default: the entry point)."""
    cfg = load_config()
    services = services or ["frontend-proxy"]   # every user request passes the proxy
    notes: list[str] = []
    info: list[str] = []

    seen: dict[str, dict] = {}
    for svc in services:
        try:
            for t in jaeger_traces(svc, start, end, limit=limit):
                seen.setdefault(t["trace_id"], t)
        except Exception as e:  # noqa: BLE001
            notes.append(f"jaeger unreachable for {svc}: {e}")
    traces = list(seen.values())
    if not traces and not notes:
        notes.append("no traces found in the window")
    if len(traces) >= limit:
        notes.append(f"trace limit {limit} reached: window probably truncated, some failures may be missing")

    long_lived = [t for t in traces if t["duration_ms"] > MAX_TRACE_MS]
    own = [t for t in traces if t not in long_lived and any("/jaeger/" in s.get("url", "") for s in t["spans"])]
    skip = {t["trace_id"] for t in long_lived + own}
    traces = [t for t in traces if t["trace_id"] not in skip]
    if long_lived:
        info.append(f"ignored {len(long_lived)} trace(s) longer than {MAX_TRACE_MS // 1000}s (long-lived sessions)")
    if own:
        info.append(f"ignored {len(own)} trace(s) that are Jaeger API calls (our own tooling)")

    failed = sorted((t for t in traces if t["error"]), key=lambda t: -t["duration_ms"])
    slow = sorted((t for t in traces if not t["error"] and t["duration_ms"] >= slow_ms), key=lambda t: -t["duration_ms"])
    err_services = Counter(s["service"] for t in failed for s in t["spans"] if s["error"] and s["service"])

    queries = cfg.get("service_queries", {})
    top_errors = _peak_errors(start, end, queries["errors_per_s"], notes) if "errors_per_s" in queries else []

    failed_s, slow_s = _summaries(failed, max_ids), _summaries(slow, max_ids)
    return {
        "experiment_id": experiment_id,
        "window": {"start": start, "end": end},
        "trace_ids": [t["trace_id"] for t in failed_s + slow_s],
        "prom_queries": list(queries.values()),
        "failed_traces": failed_s,
        "slow_traces": slow_s,
        "counts": {"traces_seen": len(traces), "failed": len(failed), "slow": len(slow),
                   "ignored_long_lived": len(long_lived), "ignored_own_queries": len(own), "slow_ms": slow_ms},
        "error_services": dict(err_services.most_common()),
        "top_error_services": top_errors,
        "quality": "partial" if notes else "ok",
        "notes": notes,
        "info": info,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--slow-ms", type=float, default=1500)
    ap.add_argument("--experiment", default="adhoc")
    a = ap.parse_args()
    now = time.time()
    print(json.dumps(collect_evidence(a.experiment, now - a.seconds, now, slow_ms=a.slow_ms), indent=2))