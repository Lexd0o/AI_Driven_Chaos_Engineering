"""tests/test_runmetrics.py - synthetic data only (never reported, never a baseline).
Run from the repo folder with the venv active:  python -m tests.test_runmetrics
"""
from src.runmetrics import compute_run_metrics, make_slos

T_INJECT, T_CLEAR = 1000.0, 1060.0
SLOS = make_slos()


def rows_for(journey, start, end, bad_from=None, bad_to=None, step=2):
    """One row every `step` s. Rows with bad_from <= ts < bad_to fail (status 503)."""
    out = []
    t = start
    while t <= end:
        bad = bad_from is not None and bad_from <= t < bad_to
        out.append({"ts": t, "journey": journey, "ok": 0 if bad else 1,
                    "latency_ms": None if bad else 100.0, "status": 503 if bad else 200})
        t += step
    return out


def all_journeys(end, cart_bad=None, browse_bad=None, skip=None):
    rows = []
    for j in ["browse", "cart", "checkout"]:
        if j == skip:
            continue
        bad = None
        if j == "cart":
            bad = cart_bad
        if j == "browse":
            bad = browse_bad
        rows += rows_for(j, 940.0, end, *(bad if bad else (None, None)))
    return sorted(rows, key=lambda r: r["ts"])


def run(rows, targets=("cart",), observed_until=None):
    return compute_run_metrics(rows, targets, SLOS, T_INJECT, T_CLEAR,
                               recovery_budget_s=120, observed_until=observed_until)


def test_survived():
    m = run(all_journeys(1200.0))
    assert m["outcome"] == "survived", m
    assert m["journeys"]["cart"]["A"] == 1.0
    assert m["containment_C"] == 1.0
    assert m["impact_recovery_s"] == 0.0


def test_degraded_recovered():
    m = run(all_journeys(1200.0, cart_bad=(1010.0, 1050.0)))
    assert m["t_breach"] is not None, m
    assert m["outcome"] == "degraded_recovered", m
    assert m["t_healthy"] > m["t_breach"]
    assert m["impact_recovery_s"] > 0
    assert m["journeys"]["cart"]["A"] < 1.0


def test_not_recovered():
    m = run(all_journeys(1200.0, cart_bad=(1010.0, 1300.0)))
    assert m["outcome"] == "not_recovered", m
    assert m["t_healthy"] is None
    assert m["impact_recovery_s"] is None  # no fake number


def test_too_short_to_call_not_recovered():
    # observation stops 10 s after t_clear (budget is 120 s) -> must be inconclusive
    m = run(all_journeys(1070.0, cart_bad=(1010.0, 1300.0)))
    assert m["outcome"] == "inconclusive", m
    assert m["quality"] == "inconclusive"


def test_missing_journey_is_inconclusive():
    m = run(all_journeys(1200.0, skip="browse"))
    assert m["outcome"] == "inconclusive", m
    assert any("browse" in i for i in m["issues"])


def test_containment_drops_when_other_journey_breaks():
    m = run(all_journeys(1200.0, cart_bad=(1010.0, 1050.0), browse_bad=(1010.0, 1050.0)))
    assert m["containment_C"] == 0.5, m  # browse broke, checkout stayed (2 non-target journeys)


def test_gap_in_samples_is_inconclusive():
    rows = [r for r in all_journeys(1200.0) if not (r["journey"] == "cart" and 1020 <= r["ts"] <= 1045)]
    m = run(rows)
    assert m["outcome"] == "inconclusive", m


def test_no_rows_never_crashes():
    m = run([])
    assert m["outcome"] == "inconclusive"


TESTS = [test_survived, test_degraded_recovered, test_not_recovered,
         test_too_short_to_call_not_recovered, test_missing_journey_is_inconclusive,
         test_containment_drops_when_other_journey_breaks,
         test_gap_in_samples_is_inconclusive, test_no_rows_never_crashes]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:  # show which one broke and why
            failed += 1
            print("FAIL", t.__name__, "->", repr(e))
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    raise SystemExit(1 if failed else 0)
