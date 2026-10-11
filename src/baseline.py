"""Baseline capture: steady-state numbers per journey. P2 (Observability).

Run alone:  python -m src.baseline --seconds 300        (Day 1: 5 min; Day 2 redo: 600)
Precondition: load generator ON, NO faults running, you hold the experiment lock.

Writes data/baseline.json  (format: P2_Observability.md section 3).
mean/std are taken over short windows (default 10 s), so 'std' reflects normal wobble.
The file carries "valid": false + "issues" if the capture itself looked unhealthy - never use an invalid baseline.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import numpy as np

from src.observe import connect, read_samples, run_sampler
from src.telemetry import ROOT, load_config


def _journey_stats(rows: list[dict], start: float, window_s: int, min_n: int) -> dict:
    ok_lat = [r["latency_ms"] for r in rows if r["ok"]]
    buckets: dict[int, list[dict]] = {}
    for r in rows:
        buckets.setdefault(int((r["ts"] - start) // window_s), []).append(r)
    win_success, win_p95 = [], []
    for b in buckets.values():
        if len(b) < min_n:
            continue
        win_success.append(sum(x["ok"] for x in b) / len(b))
        lat = [x["latency_ms"] for x in b if x["ok"]]
        if lat:
            win_p95.append(float(np.percentile(lat, 95)))
    return {
        "success_overall": sum(r["ok"] for r in rows) / len(rows) if rows else 0.0,
        "success_mean": float(np.mean(win_success)) if win_success else 0.0,
        "success_std": float(np.std(win_success)) if win_success else 0.0,
        "p50_ms": float(np.percentile(ok_lat, 50)) if ok_lat else None,
        "p95_ms": float(np.percentile(ok_lat, 95)) if ok_lat else None,
        "p95_std": float(np.std(win_p95)) if win_p95 else 0.0,
        "n": len(rows),
        "windows": len(win_success),
    }


def build_baseline(rows: list[dict], start: float, duration_s: int, window_s: int = 10,
                   min_success: float = 0.99, min_n: int = 3) -> dict:
    """Pure function: sample rows -> baseline dict (easy to test)."""
    journeys, issues = {}, []
    names = sorted({r["journey"] for r in rows if r.get("journey")})
    if not names:
        issues.append("no samples recorded")
    for name in names:
        st = _journey_stats([r for r in rows if r["journey"] == name], start, window_s, min_n)
        if st["success_overall"] < min_success:
            issues.append(f"{name}: success {st['success_overall']:.3f} < {min_success} (shop not healthy during capture)")
        if st["windows"] < 3:
            issues.append(f"{name}: only {st['windows']} usable windows (capture too short or gaps)")
        journeys[name] = {k: (round(v, 4) if isinstance(v, float) else v)
                          for k, v in st.items() if k not in ("success_overall", "windows")}
        journeys[name]["success_overall"] = round(st["success_overall"], 4)
    return {
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": duration_s,
        "window_s": window_s,
        "load": {"vus": os.getenv("LOAD_VUS", "unknown")},   # set LOAD_VUS to the loadGeneratorVUs value [verify]
        "journeys": journeys,
        "services": {},                                       # per-service Prometheus stats: bonus window / Day 2
        "valid": not issues,
        "issues": issues,
    }


def capture_baseline(duration_s: int = 600, window_s: int = 10, out_path: str | None = None,
                     db_path: str | None = None) -> dict:
    exp_id = f"baseline-{int(time.time())}"
    start = time.time()
    run_sampler(exp_id, duration_s, db_path)
    rows = read_samples(exp_id, db_path)
    result = build_baseline(rows, start, duration_s, window_s)
    path = out_path or str(ROOT / "data" / "baseline.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    os.replace(tmp, path)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--out", default=None)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    print(f"Capturing baseline for {a.seconds}s - no faults, load on. Ctrl+C aborts without saving.")
    res = capture_baseline(a.seconds, a.window, a.out, a.db)
    print(json.dumps(res, indent=2))
    print("VALID" if res["valid"] else "INVALID - do not use: " + "; ".join(res["issues"]))