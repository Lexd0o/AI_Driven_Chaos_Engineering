"""score.py - P5: deterministic scores for finished runs (Architecture.md section 8, D-008).

    S = 100 x (0.45 A + 0.20 L + 0.20 R + 0.15 C)

  A  successful requests / offered requests during the fault window   (per journey, weighted by journey criticality)
  L  requests that were ok AND within the latency limit / offered     (same weighting)
  R  1 if survived; max(0, 1 - impact_recovery / recovery_budget) if degraded_recovered; 0 if not_recovered
  C  fraction of NON-target journeys that stayed within SLO (containment)

Rules (Rules.md, Architecture.md section 7):
  * Code computes every number; no LLM. Nothing is invented: a missing piece is "n/a" or the run is inconclusive.
  * Forced-breach runs (kill-switch tests) are never scored.
  * A journey needs at least min_n samples in the fault window to count; with none left the run is inconclusive.
  * C is "n/a" when every probed journey is a target (nothing left to contain). The score then uses A, L and R with
    their weights scaled to add up to 1, and says so. (Decision taken with Joy, 11 Oct.)
  * Recovery numbers are recomputed from the stored samples when data/runs.db has them, so a later fix to
    src/runmetrics.py reaches old runs. Without samples the run file's stored numbers are used and labelled.

Usage (repo root, venv active):
    python -m src.score            # prints the table, writes data/scores.json
Overall score = sum(criticality x S) / sum(criticality), always shown with coverage (Rules.md #19).
"""
import argparse
import glob
import json
import os

import yaml

from src.observe import read_samples
from src.runmetrics import compute_run_metrics, make_slos

RUNS_DIR = "data/runs"
PLAN_PATH = "data/plan_validated.json"
CRITICALITY_PATH = "config/criticality.yaml"
OUT_PATH = "data/scores.json"

WEIGHTS = {"A": 0.45, "L": 0.20, "R": 0.20, "C": 0.15}
JOURNEY_WEIGHTS = {"checkout": 0.4, "cart": 0.3, "browse": 0.2, "ads-recs": 0.1}
SEVERITY_STEPS = [(30, "Critical"), (15, "High"), (5, "Medium")]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_run_files(runs_dir=RUNS_DIR):
    """Run files written by src/run.py. Forced-breach files are skipped here and never scored."""
    runs = []
    for path in sorted(glob.glob(os.path.join(runs_dir, "*.json"))):
        run = load_json(path)
        if run.get("forced_breach") or "-forced-" in os.path.basename(path):
            continue
        run["_file"] = path
        runs.append(run)
    return sorted(runs, key=lambda r: (r.get("plan_experiment_id") or "", r.get("repeat") or 0))


def stored_metrics(run):
    """The numbers as the run file kept them (used when the samples are not available)."""
    return {
        "outcome": run["outcome"], "quality": run["quality"], "issues": [],
        "journeys": run["journeys"], "containment_C": run.get("containment_C"),
        "impact_recovery_s": run.get("impact_recovery_s"), "late_breach_ts": None,
        "source": "stored run file",
    }


def fresh_metrics(run, samples_reader=read_samples):
    """Recompute from the samples table with the current runmetrics rules; fall back to the stored numbers."""
    try:
        rows = [r for r in samples_reader(run["run_id"]) if r.get("journey")]
    except Exception:  # noqa: BLE001 - a missing or locked db must not stop scoring
        rows = []
    if not rows:
        return stored_metrics(run)
    budget = (run.get("plan") or {}).get("recovery_budget_s") or 120
    metrics = compute_run_metrics(rows, run["measured_journeys"], make_slos(), run["t_inject"], run["t_clear"],
                                  recovery_budget_s=budget)
    metrics["source"] = "recomputed from samples"
    return metrics


def weighted_mean(values_by_journey):
    """Mean weighted by journey criticality; None when there is nothing to average."""
    total = sum(JOURNEY_WEIGHTS.get(j, 0.1) for j in values_by_journey)
    if not total:
        return None
    return sum(JOURNEY_WEIGHTS.get(j, 0.1) * v for j, v in values_by_journey.items()) / total


def recovery_score(outcome, impact_recovery_s, budget_s):
    if outcome == "survived":
        return 1.0
    if outcome == "not_recovered":
        return 0.0
    if outcome == "degraded_recovered" and impact_recovery_s is not None:
        return max(0.0, 1 - impact_recovery_s / budget_s)
    return None


def severity_label(s_value, criticality):
    """Architecture.md section 8: (100 - S) x criticality -> Critical >= 30, High >= 15, Medium >= 5, else Low."""
    impact = (100 - s_value) * criticality
    for limit, label in SEVERITY_STEPS:
        if impact >= limit:
            return impact, label
    return impact, "Low"


def score_run(run, metrics):
    """One run -> dict with status 'scored' or 'inconclusive' (and the reasons). Never guesses."""
    result = {
        "run_id": run["run_id"], "repeat": run.get("repeat"), "outcome": metrics["outcome"],
        "source": metrics["source"], "aborted": bool(run.get("aborted")), "abort_reason": run.get("abort_reason"),
        "late_breach_ts": metrics.get("late_breach_ts"), "impact_recovery_s": metrics.get("impact_recovery_s"),
        "S": None, "A": None, "L": None, "R": None, "C": metrics.get("containment_C"),
        "c_weights_rescaled": False, "journeys_used": [], "journeys_too_thin": [], "reasons": [],
        "evidence": run.get("evidence"),   # trace ids + PromQL the collector recorded for this run (src/evidence.py)
    }
    min_n = {j: slo["min_n"] for j, slo in make_slos().items()}
    targets = run.get("measured_journeys") or []

    if metrics["quality"] != "ok" or metrics["outcome"] == "inconclusive":
        result["status"] = "inconclusive"
        result["reasons"] = list(metrics.get("issues") or []) or ["run quality is inconclusive"]
        return result

    usable = {j: metrics["journeys"][j] for j in targets
              if j in metrics["journeys"] and metrics["journeys"][j]["n"] >= min_n.get(j, 3)}
    result["journeys_used"] = sorted(usable)
    result["journeys_too_thin"] = sorted(j for j in targets if j not in usable)
    if not usable:
        thin = ", ".join(f"{j} n={metrics['journeys'].get(j, {}).get('n', 0)}" for j in targets)
        result["status"] = "inconclusive"
        result["reasons"] = [f"too few samples in the fault window to score ({thin})"]
        return result

    budget = (run.get("plan") or {}).get("recovery_budget_s") or 120
    a_value = weighted_mean({j: v["A"] for j, v in usable.items()})
    l_value = weighted_mean({j: v["L"] for j, v in usable.items()})
    r_value = recovery_score(metrics["outcome"], metrics.get("impact_recovery_s"), budget)
    if r_value is None:
        result["status"] = "inconclusive"
        result["reasons"] = ["no recovery time to score"]
        return result

    c_value = metrics.get("containment_C")
    weights = dict(WEIGHTS)
    if c_value is None:
        weights.pop("C")
        scale = sum(weights.values())
        result["c_weights_rescaled"] = True
        s_value = 100 * (weights["A"] * a_value + weights["L"] * l_value + weights["R"] * r_value) / scale
    else:
        s_value = 100 * (weights["A"] * a_value + weights["L"] * l_value + weights["R"] * r_value
                         + weights["C"] * c_value)

    result.update({"status": "scored", "S": s_value, "A": a_value, "L": l_value, "R": r_value})
    return result


def prediction_status(scored_runs):
    """Was the planner's hypothesis (impact on the expected journeys) seen? Honest about mixed repeats."""
    if not scored_runs:
        return "inconclusive"
    hit = sum(1 for r in scored_runs if r["outcome"] in ("degraded_recovered", "not_recovered"))
    if hit == len(scored_runs):
        return "confirmed"
    if hit == 0:
        return "not confirmed (no impact seen)"
    return f"mixed (impact in {hit} of {len(scored_runs)} runs)"


def build_scores(runs_dir=RUNS_DIR, plan_path=PLAN_PATH, criticality_path=CRITICALITY_PATH,
                 samples_reader=read_samples):
    plan = load_json(plan_path)
    criticality = load_yaml(criticality_path)
    runs = load_run_files(runs_dir)

    experiments = []
    for item in plan:
        service = item["target"]["service"]
        crit = (criticality.get("services") or {}).get(service, criticality.get("default", 0.1))
        run_results = [score_run(r, fresh_metrics(r, samples_reader)) for r in runs
                       if r.get("plan_experiment_id") == item["experiment_id"]]
        scored = [r for r in run_results if r["status"] == "scored"]
        mean_s = sum(r["S"] for r in scored) / len(scored) if scored else None
        entry = {
            "experiment_id": item["experiment_id"], "rank": item["rank"], "risk_id": item.get("risk_id"),
            "fault_type": item["fault_type"], "target": service, "criticality": crit,
            "hypothesis": item["hypothesis"], "expected_journeys": item["expected_impact"]["journeys"],
            "runs": run_results, "n_runs": len(run_results), "n_scored": len(scored),
            "S": mean_s, "prediction": prediction_status(scored),
            "severity_impact": None, "severity": None,
        }
        if mean_s is not None:
            entry["severity_impact"], entry["severity"] = severity_label(mean_s, crit)
        entry["status"] = ("scored" if scored else "inconclusive" if run_results else "not run")
        experiments.append(entry)

    scored_exps = [e for e in experiments if e["S"] is not None]
    denominator = sum(e["criticality"] for e in scored_exps)
    overall = sum(e["criticality"] * e["S"] for e in scored_exps) / denominator if denominator else None
    return {
        "formula": "S = 100 x (0.45 A + 0.20 L + 0.20 R + 0.15 C)", "weights": WEIGHTS,
        "journey_weights": JOURNEY_WEIGHTS, "experiments": experiments, "overall": overall,
        "coverage": {"planned": len(plan), "scored": len(scored_exps),
                     "inconclusive": sum(1 for e in experiments if e["status"] == "inconclusive"),
                     "not_run": sum(1 for e in experiments if e["status"] == "not run")},
    }


def fmt(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def print_table(scores):
    print(f"{'exp':<8}{'fault':<14}{'target':<17}{'runs':<6}{'outcome':<20}{'S':>6}  severity")
    for e in scores["experiments"]:
        outcomes = ",".join(r["outcome"] for r in e["runs"]) or "-"
        print(f"{e['experiment_id']:<8}{e['fault_type']:<14}{e['target']:<17}{e['n_scored']}/{e['n_runs']:<4}"
              f"{outcomes[:19]:<20}{fmt(e['S'], 1):>6}  {e['severity'] or e['status']}")
    cov = scores["coverage"]
    print(f"\nOverall {fmt(scores['overall'], 1)} / 100 - coverage: {cov['scored']} of {cov['planned']} approved "
          f"experiments scored ({cov['inconclusive']} inconclusive, {cov['not_run']} not run)")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score the finished runs (D-008)")
    parser.add_argument("--runs-dir", default=RUNS_DIR)
    args = parser.parse_args(argv)

    scores = build_scores(args.runs_dir)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=2)
    print_table(scores)
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
