"""benchmark.py - P5: did the planner's ranked order find problems sooner than a random order would?
(Architecture.md section 9, D-009: this checks the RANKING, not the LLM text.)

For the experiments that were scored, in the planner's rank order, we take the position of the first
  * finding        = an experiment whose prediction was confirmed (user-visible impact seen)
  * serious finding = a confirmed experiment with severity High or Critical
and compare it with the same position under random orderings of the same experiments.
Up to 8 experiments every ordering is counted exactly (8! = 40,320); above that, 1,000 seeded random orderings.

    python -m src.benchmark          # reads data/scores.json (run src.score first), prints the result
Small sample: the result is stated with its sample size and is not a proof.
"""
import itertools
import json
import random

SERIOUS = ("High", "Critical")
EXACT_LIMIT = 8
RANDOM_ORDERINGS = 1000
SEED = 0


def first_hit_position(hits):
    """1-based position of the first True in a list of booleans; None when there is none."""
    for position, hit in enumerate(hits, start=1):
        if hit:
            return position
    return None


def random_positions(hits):
    """First-hit position for every ordering (exact for small n, seeded sample otherwise)."""
    n = len(hits)
    if n <= EXACT_LIMIT:
        orderings = itertools.permutations(hits)
    else:
        rng = random.Random(SEED)
        orderings = (rng.sample(hits, n) for _ in range(RANDOM_ORDERINGS))
    return [first_hit_position(list(order)) for order in orderings]


def compare(hits):
    """Ours vs random for one definition of 'hit'. None when there is no hit (nothing to compare)."""
    ours = first_hit_position(hits)
    if ours is None:
        return None
    random_runs = random_positions(hits)
    return {
        "n_experiments": len(hits), "n_hits": sum(hits), "ours": ours,
        "random_mean": sum(random_runs) / len(random_runs),
        "random_as_good_or_better": sum(1 for p in random_runs if p <= ours) / len(random_runs),
        "orderings": len(random_runs), "exact": len(hits) <= EXACT_LIMIT,
    }


def build_benchmark(scores):
    scored = sorted((e for e in scores["experiments"] if e["S"] is not None), key=lambda e: e["rank"])
    finding = [e["prediction"].startswith(("confirmed", "mixed")) for e in scored]
    serious = [f and e["severity"] in SERIOUS for f, e in zip(finding, scored)]
    return {
        "order": [e["experiment_id"] for e in scored],
        "any_finding": compare(finding),
        "serious_finding": compare(serious),
        "note": "Small sample: experiments scored so far, in the planner's order. This checks the ranking, not the LLM text.",
    }


def describe(result):
    """Plain sentences for the report and the terminal; never claims more than the numbers show."""
    lines = []
    for label, key in (("confirmed finding", "any_finding"), ("High or Critical finding", "serious_finding")):
        r = result[key]
        if r is None:
            lines.append(f"No experiment gave a {label} yet, so there is nothing to compare for it.")
            continue
        which = f"all {r['orderings']:,}" if r["exact"] else f"{r['orderings']:,} random"
        lines.append(
            f"The first {label} came at position {r['ours']} in the planner's order, versus {r['random_mean']:.1f} on average "
            f"over {which} orderings of the same {r['n_experiments']} "
            f"scored experiments ({r['n_hits']} of them hits). A random order would do this well or better "
            f"{100 * r['random_as_good_or_better']:.0f}% of the time.")
    return lines


def main():
    with open("data/scores.json", encoding="utf-8") as f:
        scores = json.load(f)
    result = build_benchmark(scores)
    print("Order (scored experiments):", ", ".join(result["order"]))
    for line in describe(result):
        print(line)
    print(result["note"])


if __name__ == "__main__":
    main()
