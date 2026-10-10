"""src/runmetrics.py - P2: per-run numbers for P5's scorer (Architecture.md section 7-8).

Pure core:  compute_run_metrics(rows, ...)  -> dict   (no DB, easy to test)
DB wrapper: collect_run_metrics(experiment_id, ...)   (reads the samples table)

What it gives P5, per run:
  A  = successful requests / offered requests, during the fault window
  L  = requests that were ok AND within the latency limit / offered requests
  C  = fraction of NON-target journeys that stayed within SLO (containment)
  t_breach, t_healthy, impact/post-fault recovery, outcome, quality

Rules followed: no breach -> "survived"; never healthy -> "not_recovered"
(only if we watched long enough); missing/thin data -> "inconclusive" (no score).
Run directly:  python -m src.runmetrics --experiment exp-003 --targets cart --inject 1760000000 --clear 1760000060
"""
import argparse
import json
import math

from src.observe import find_breach, find_healthy, read_samples

JOURNEYS = ["browse", "cart", "checkout"]


def make_slos(journeys=None, min_success=0.95, max_p95_ms=1500, window_s=10, min_n=3):
    """One SLO dict per journey. Defaults = Architecture.md 4.3 example. [verify] final values with P4."""
    slos = {}
    for j in journeys or JOURNEYS:
        slos[j] = {"journey": j, "min_success": min_success, "max_p95_ms": max_p95_ms,
                   "window_s": window_s, "min_n": min_n}
    return slos


def _rows_for(rows, journey, t0, t1):
    return [r for r in rows if r.get("journey") == journey and t0 <= r["ts"] <= t1]


def _p95(values):
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(0.95 * len(v)) - 1)]


def _peaks(rows, window_s, min_n):
    """Worst error rate and worst p95 over fixed windows (windows with < min_n samples are skipped)."""
    buckets = {}
    for r in rows:
        buckets.setdefault(int(r["ts"] // window_s), []).append(r)
    peak_err, peak_p95 = 0.0, None
    for b in buckets.values():
        if len(b) < min_n:
            continue
        err = 1 - sum(1 for r in b if r["ok"]) / len(b)
        peak_err = max(peak_err, err)
        p95 = _p95([r["latency_ms"] for r in b if r["ok"] and r["latency_ms"] is not None])
        if p95 is not None and (peak_p95 is None or p95 > peak_p95):
            peak_p95 = p95
    return peak_err, peak_p95


def _max_gap(rows, t0, t1):
    ts = sorted([t0] + [r["ts"] for r in rows] + [t1])
    return max(b - a for a, b in zip(ts, ts[1:]))


def compute_run_metrics(rows, targets, slos, t_inject, t_clear, recovery_budget_s=120,
                        observed_until=None, sample_interval_s=2, min_coverage=0.8,
                        max_gap_s=10, sustain_s=30):
    """rows = journey rows from the samples table (dicts with ts, journey, ok, latency_ms, status)."""
    rows = [r for r in rows if r.get("journey")]
    if observed_until is None:
        observed_until = max((r["ts"] for r in rows), default=t_clear)
    targets = list(targets)
    others = [j for j in slos if j not in targets]
    issues = []

    # --- A and L per journey, during the fault window ---
    journeys = {}
    for j in slos:
        w = _rows_for(rows, j, t_inject, t_clear)
        n = len(w)
        ok = sum(1 for r in w if r["ok"])
        fast = sum(1 for r in w if r["ok"] and r["latency_ms"] is not None
                   and r["latency_ms"] <= slos[j]["max_p95_ms"])
        journeys[j] = {"n": n, "A": ok / n if n else None, "L": fast / n if n else None}

    # --- data quality (missing telemetry -> inconclusive, Rules.md #20) ---
    expected = max(1.0, (t_clear - t_inject) / sample_interval_s)
    for j in slos:
        w = _rows_for(rows, j, t_inject, t_clear)
        if not w:
            issues.append(f"no samples for journey '{j}' during the fault window")
        elif len(w) / expected < min_coverage:
            issues.append(f"journey '{j}': only {len(w)} of ~{int(expected)} expected samples")
        elif _max_gap(w, t_inject, t_clear) > max_gap_s:
            issues.append(f"journey '{j}': gap longer than {max_gap_s}s in samples")

    # --- breach and recovery (same helpers P4 uses) ---
    after = [r for r in rows if r["ts"] >= t_inject]
    breaches = [find_breach(after, slos[j]) for j in targets]
    breaches = [b for b in breaches if b is not None]
    t_breach = min(breaches) if breaches else None

    t_healthy = None
    if t_breach is not None:
        healthy = [find_healthy(after, slos[j], t_breach, sustain_s) for j in targets]
        if all(h is not None for h in healthy):
            t_healthy = max(healthy)

    # --- containment C: non-target journeys that never breached during the fault window ---
    c = None
    if others:
        stayed = 0
        for j in others:
            w = [r for r in rows if t_inject <= r["ts"] <= t_clear]
            if find_breach(w, slos[j]) is None:
                stayed += 1
        c = stayed / len(others)

    # --- outcome ---
    if t_breach is not None and t_healthy is None and observed_until - t_clear < recovery_budget_s:
        issues.append("observation ended before the recovery budget; cannot call 'not recovered'")
    if issues:
        outcome = "inconclusive"
    elif t_breach is None:
        outcome = "survived"
    elif t_healthy is not None:
        outcome = "degraded_recovered"
    else:
        outcome = "not_recovered"

    impact_recovery = None
    post_fault = None
    if outcome == "survived":
        impact_recovery = post_fault = 0.0
    elif outcome == "degraded_recovered":
        impact_recovery = t_healthy - t_breach
        post_fault = max(0.0, t_healthy - t_clear)

    # --- peaks over target journeys ---
    peak_err, peak_p95 = 0.0, None
    for j in targets:
        e, p = _peaks(_rows_for(rows, j, t_inject, observed_until), slos[j]["window_s"], slos[j]["min_n"])
        peak_err = max(peak_err, e)
        if p is not None and (peak_p95 is None or p > peak_p95):
            peak_p95 = p

    return {
        "targets": targets,
        "t_inject": t_inject, "t_clear": t_clear,
        "t_breach": t_breach, "t_healthy": t_healthy,
        "outcome": outcome,
        "impact_recovery_s": impact_recovery,      # t_healthy - t_breach
        "post_fault_recovery_s": post_fault,       # max(0, t_healthy - t_clear)
        "peak_error_rate": peak_err, "peak_p95_ms": peak_p95,
        "journeys": journeys,
        "containment_C": c,
        "quality": "ok" if not issues else "inconclusive",
        "issues": issues,
    }


def collect_run_metrics(experiment_id, targets, t_inject, t_clear, slos=None,
                        recovery_budget_s=120, db_path=None, **kw):
    """Reads this experiment's samples from SQLite and computes the run metrics. Never raises on empty data."""
    slos = slos or make_slos()
    rows = [r for r in read_samples(experiment_id, db_path=db_path) if r.get("journey")]
    result = compute_run_metrics(rows, targets, slos, t_inject, t_clear, recovery_budget_s, **kw)
    result["experiment_id"] = experiment_id
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--experiment", required=True)
    p.add_argument("--targets", required=True, help="comma list, e.g. cart or checkout,cart")
    p.add_argument("--inject", type=float, required=True, help="t_inject, UTC epoch seconds")
    p.add_argument("--clear", type=float, required=True, help="t_clear, UTC epoch seconds")
    p.add_argument("--budget", type=int, default=120)
    p.add_argument("--db", default=None)
    a = p.parse_args()
    out = collect_run_metrics(a.experiment, a.targets.split(","), a.inject, a.clear,
                              recovery_budget_s=a.budget, db_path=a.db)
    print(json.dumps(out, indent=2))
