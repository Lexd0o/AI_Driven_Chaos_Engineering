"""benchmark.py - planner benchmark: does our ranking find a confirmed weakness sooner than chance?

Usage (run from the repo root, after plan/run):
    python -m src.benchmark

Inputs : data/plan_validated.json (experiments with "rank"), data/runs/*.json (run records),
         config/scoring.yaml (benchmark: iterations, seed)
Output : data/benchmark.json (consumed by src/report.py)

Architecture.md section 9 / Decisions.md D-009: this validates the RANKING, not the LLM text.
Run set = experiments with at least one non-inconclusive run record, in plan rank order.
A hit = an experiment that confirmed a weakness (same rule as score.py: a degraded_recovered or
not_recovered outcome, or an aborted run). Metric = 1-based position of the first hit.
Baseline = the same metric over N seeded random orderings of the same run set.
"""
import json
import os
import random
from datetime import datetime, timezone

from src.score import load_json, load_run_records, load_yaml

PLAN_PATH = "data/plan_validated.json"
CONFIG_PATH = "config/scoring.yaml"
OUT_PATH = "data/benchmark.json"

HIT_OUTCOMES = ("degraded_recovered", "not_recovered")
DEFAULT_ITERATIONS = 1000
DEFAULT_SEED = 42


def is_hit_record(record: dict) -> bool:
    return record["outcome"] in HIT_OUTCOMES or bool(record.get("aborted"))


def run_set(plan: list[dict], records: list[dict]) -> tuple[list[str], set]:
    """(experiment ids with >= 1 non-inconclusive record, in plan rank order; ids that are hits)."""
    by_exp: dict = {}
    for r in records:
        if r["outcome"] != "inconclusive":
            by_exp.setdefault(r["experiment_id"], []).append(r)
    ordered = sorted((p for p in plan if p["experiment_id"] in by_exp),
                     key=lambda p: (p.get("rank", 10**6), p["experiment_id"]))
    ids = [p["experiment_id"] for p in ordered]
    hits = {i for i in ids if any(is_hit_record(r) for r in by_exp[i])}
    return ids, hits


def first_hit_position(order: list[str], hits: set) -> int | None:
    for pos, exp_id in enumerate(order, 1):
        if exp_id in hits:
            return pos
    return None


def random_baseline(ids: list[str], hits: set, our_position: int, iterations: int, seed: int) -> dict:
    rng = random.Random(seed)
    positions = []
    for _ in range(iterations):
        order = list(ids)
        rng.shuffle(order)
        positions.append(first_hit_position(order, hits))
    return {"mean": sum(positions) / len(positions),
            "share_as_good": sum(p <= our_position for p in positions) / len(positions)}


def build_benchmark(plan: list[dict], records: list[dict], cfg: dict) -> dict:
    bcfg = cfg.get("benchmark") or {}
    iterations = bcfg.get("iterations", DEFAULT_ITERATIONS)
    seed = bcfg.get("seed", DEFAULT_SEED)
    ids, hits = run_set(plan, records)
    n, k = len(ids), len(hits)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "n_run": n, "n_hits": k,
           "our_order": ids, "hits": [i for i in ids if i in hits], "our_first_hit_position": None,
           "random_mean_first_hit_position": None, "analytic_expected_position": None,
           "share_random_as_good_or_better": None, "iterations": iterations, "seed": seed}
    if k == 0:
        out["status"] = "not_applicable"
        out["caveat"] = ("No confirmed weakness yet - benchmark not applicable "
                         f"({n} experiment(s) run, 0 hits).")
        return out
    pos = first_hit_position(ids, hits)
    base = random_baseline(ids, hits, pos, iterations, seed)
    out.update({"status": "applicable", "our_first_hit_position": pos,
                "random_mean_first_hit_position": round(base["mean"], 2),
                "analytic_expected_position": round((n + 1) / (k + 1), 2),
                "share_random_as_good_or_better": round(base["share_as_good"], 4),
                "caveat": f"Small sample: {n} experiments, {k} hit(s); not statistically significant."})
    return out


def summary_line(b: dict) -> str:
    if b["status"] == "not_applicable":
        return f"wrote {OUT_PATH}: no confirmed weakness yet - benchmark not applicable ({b['n_run']} run, 0 hits)"
    return (f"wrote {OUT_PATH}: our ranking found the first confirmed weakness at position "
            f"{b['our_first_hit_position']} of {b['n_run']}; random orderings average "
            f"{b['random_mean_first_hit_position']:.2f} ({b['share_random_as_good_or_better']:.0%} of "
            f"random orderings do as well) - small sample: {b['n_run']} experiments, {b['n_hits']} hit(s)")


def main() -> None:
    for path in (PLAN_PATH, CONFIG_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found - run the earlier pipeline steps first")
    bench = build_benchmark(load_json(PLAN_PATH), load_run_records(), load_yaml(CONFIG_PATH))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(bench, f, indent=2)
    print(summary_line(bench))


if __name__ == "__main__":
    main()
