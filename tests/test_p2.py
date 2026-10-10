"""Tests for P2 pure functions: zscore, find_breach, find_healthy, build_baseline.
Synthetic data only: no shop, Prometheus or Jaeger needed.

Run from the repo root (venv active):   python -m tests.test_p2
(also works with pytest if you have it)
"""
from __future__ import annotations

import sys

from src.baseline import build_baseline
from src.observe import find_breach, find_healthy, zscore

T0 = 1000.0   # fake start time (UTC epoch seconds)
SLO = {"journey": "checkout", "min_success": 0.9, "max_p95_ms": 1500, "window_s": 10, "min_n": 3}


def rows(journey: str, n: int, bad: range = range(0), latency: float = 100.0, step: float = 2.0,
         bad_latency: float | None = None) -> list[dict]:
    """n samples, one every `step` s from T0. Indexes in `bad` fail (ok=0), or get bad_latency if given."""
    out = []
    for i in range(n):
        failing = i in bad
        if bad_latency is not None:
            ok, lat = 1, (bad_latency if failing else latency)
        else:
            ok, lat = (0 if failing else 1), latency
        out.append({"ts": T0 + step * i, "journey": journey, "ok": ok, "latency_ms": lat, "status": 200 if ok else 500})
    return out


# ------------------------------------------------------------------ zscore
def test_zscore_basic():
    assert zscore(7, 5, 2, 0.1) == 1.0


def test_zscore_min_std_stops_huge_values():
    # baseline std is 0, so min_std (0.01) is used instead of dividing by zero
    assert abs(zscore(0.5, 0.99, 0.0, 0.01) - (-49.0)) < 1e-9


def test_zscore_uses_real_std_when_larger():
    assert zscore(10, 0, 5, 1) == 2.0


# ------------------------------------------------------------------ find_breach
def test_breach_none_when_healthy():
    assert find_breach(rows("checkout", 100), SLO) is None


def test_breach_found_at_first_bad_window():
    # samples 20..39 fail (ts 1040..1078). Window at 1040 = 4 ok + 1 fail = 0.8 < 0.9
    s = rows("checkout", 100, bad=range(20, 40))
    assert find_breach(s, SLO) == T0 + 40


def test_breach_needs_min_n_points():
    # only 2 samples in total, both failing: too few to judge
    assert find_breach(rows("checkout", 2, bad=range(2)), SLO) is None


def test_breach_by_latency():
    s = rows("checkout", 100, bad=range(20, 40), bad_latency=3000)   # all succeed but are slow
    assert find_breach(s, SLO) is not None


def test_breach_ignores_other_journeys():
    s = rows("browse", 100, bad=range(20, 40))
    assert find_breach(s, SLO) is None   # SLO is for checkout


# ------------------------------------------------------------------ find_healthy
def test_healthy_after_recovery():
    s = rows("checkout", 100, bad=range(20, 40))
    breach = find_breach(s, SLO)
    # last fail at 1078; first fully clean 10 s window ends at 1088
    assert find_healthy(s, SLO, after_ts=breach, sustain_s=30) == T0 + 88


def test_healthy_none_if_never_recovers():
    s = rows("checkout", 100, bad=range(20, 100))
    breach = find_breach(s, SLO)
    assert find_healthy(s, SLO, after_ts=breach, sustain_s=30) is None


def test_healthy_none_if_recovery_too_short():
    # recovers, but the data ends before 30 s of clean time has passed
    s = rows("checkout", 55, bad=range(20, 40))
    breach = find_breach(s, SLO)
    assert find_healthy(s, SLO, after_ts=breach, sustain_s=30) is None


def test_recovery_time_value():
    s = rows("checkout", 100, bad=range(20, 40))
    breach = find_breach(s, SLO)
    healthy = find_healthy(s, SLO, after_ts=breach)
    assert healthy - breach == 48


# ------------------------------------------------------------------ build_baseline
def two_journeys(n: int, bad: range = range(0)) -> list[dict]:
    return rows("browse", n) + rows("checkout", n, bad=bad)


def test_baseline_valid_when_healthy():
    b = build_baseline(two_journeys(30), T0, 60)
    assert b["valid"] is True and b["issues"] == []
    assert b["journeys"]["checkout"]["success_mean"] == 1.0
    assert b["journeys"]["checkout"]["p50_ms"] == 100.0


def test_baseline_invalid_when_checkout_fails():
    b = build_baseline(two_journeys(30, bad=range(0, 30, 5)), T0, 60)   # 6 of 30 fail = 80%
    assert b["valid"] is False
    assert any("checkout" in issue and "success" in issue for issue in b["issues"])


def test_baseline_invalid_when_no_samples():
    b = build_baseline([], T0, 60)
    assert b["valid"] is False and "no samples recorded" in b["issues"]


def test_baseline_invalid_when_too_short():
    b = build_baseline(two_journeys(5), T0, 10)   # 10 s = only one window
    assert b["valid"] is False
    assert any("usable windows" in issue for issue in b["issues"])


# ------------------------------------------------------------------ runner
if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)