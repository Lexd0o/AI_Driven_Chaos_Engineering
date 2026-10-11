"""score.py - deterministic resilience scoring: run records -> data/scores.json.

Usage (run from the repo root, after plan/run; works with zero runs too):
    python -m src.score

Inputs : data/plan_validated.json, data/runs/*.json (run records only), data/graph.json,
         data/risks.json, data/ranked_candidates.json, reports/fixes/index.json (optional),
         config/scoring.yaml
Output : data/scores.json (consumed by src/report.py)

Formula (Decisions.md D-008, Architecture.md section 8), per affected journey:
    S = 100 x (wA*A + wL*L + wR*R + wC*C) / sum(weights of the components present)
then journey-weighted per run, averaged over repeats, and weight-averaged over experiments.
No LLM is involved: every number here comes from run records and config.

Choices where the spec leaves room:
  - An experiment's R and C are taken from its worst scored repeat (worst outcome, then lowest S).
  - A tested finding's severity_value is (100 - S) x service criticality, banded by config.
"""
import glob
import json
import os
from datetime import datetime, timezone

import yaml

PLAN_PATH = "data/plan_validated.json"
RUNS_GLOB = "data/runs/*.json"
GRAPH_PATH = "data/graph.json"
RISKS_PATH = "data/risks.json"
RANKED_PATH = "data/ranked_candidates.json"
FIXES_PATH = "reports/fixes/index.json"
CONFIG_PATH = "config/scoring.yaml"
OUT_PATH = "data/scores.json"

DEFAULT_JOURNEY_WEIGHT = 0.1
DEFAULT_CRITICALITY = 0.1
OUTCOME_BADNESS = {"survived": 0, "degraded_recovered": 1, "not_recovered": 2}
AUDIT_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# ------------------------------------------------------------------ small helpers

def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_run_records(pattern: str = RUNS_GLOB) -> list[dict]:
    """Only files with both experiment_id and outcome are run records (the directory also
    holds raw agent reports)."""
    records = []
    for path in sorted(glob.glob(pattern)):
        try:
            data = load_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "experiment_id" in data and "outcome" in data:
            records.append(data)
    return records


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def r1(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def weighted_mean(pairs: list[tuple[float, float]]) -> float | None:
    """pairs of (weight, value) -> sum(w*v)/sum(w), None when empty."""
    total = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / total if total else None


def severity_band(value: float, bands: dict) -> str:
    for name, minimum in sorted(bands.items(), key=lambda kv: -kv[1]):
        if value >= minimum:
            return name
    return "Low"


# ------------------------------------------------------------------ one run record

def alias_journeys(journeys: dict, aliases: dict) -> dict:
    """Map probe names to D-008 journeys; when two map to the same one keep the worst A and L."""
    merged: dict = {}
    for name, m in journeys.items():
        j = aliases.get(name, name)
        if j in merged:
            merged[j] = {"A": min(merged[j]["A"], m["A"]), "L": min(merged[j]["L"], m["L"])}
        else:
            merged[j] = {"A": m["A"], "L": m["L"]}
    return merged


def recovery_score(record: dict, item: dict) -> float:
    outcome = record["outcome"]
    if outcome == "survived":
        return 1.0
    if outcome == "not_recovered":
        return 0.0
    recovery_s = record.get("impact_recovery_s")
    if recovery_s is None:
        recovery_s = record.get("post_fault_recovery_s")
    if recovery_s is None:
        return 0.0
    return max(0.0, 1 - recovery_s / item["recovery_budget_s"])


def journey_score(a: float, l: float, r: float, c: float | None, weights: dict) -> float:
    parts = {"A": a, "L": l, "R": r, "C": c}
    present = {k: v for k, v in parts.items() if v is not None}
    return 100 * sum(weights[k] * v for k, v in present.items()) / sum(weights[k] for k in present)


def score_record(record: dict, item: dict, cfg: dict) -> dict | None:
    """Score one run record. None = inconclusive (no usable measurement)."""
    journeys = record.get("journeys") or {}
    if record["outcome"] == "inconclusive" or not journeys:
        return None
    measured = alias_journeys(journeys, cfg.get("journey_aliases") or {})
    expected = item["expected_impact"]["journeys"]
    targets = [j for j in measured if j in expected] or list(measured)
    others = [j for j in measured if j not in targets]
    slo_min = item["slo"]["success_rate_min"]
    if others:
        c = sum(measured[j]["A"] >= slo_min and measured[j]["L"] >= slo_min for j in others) / len(others)
    else:
        c = None
    r = recovery_score(record, item)
    jw = cfg["journey_weights"]
    per_journey = {}
    for j, m in measured.items():
        s = journey_score(m["A"], m["L"], r, c, cfg["weights"]) if j in targets else None
        per_journey[j] = {"A": m["A"], "L": m["L"], "S": r1(s)}
    s_run = weighted_mean([(jw.get(j, DEFAULT_JOURNEY_WEIGHT),
                            journey_score(measured[j]["A"], measured[j]["L"], r, c, cfg["weights"]))
                           for j in targets])
    return {"S": s_run, "R": r, "C": c, "outcome": record["outcome"],
            "aborted": bool(record.get("aborted")), "recovery_s": record.get("impact_recovery_s"),
            "journeys": per_journey, "uncovered": record.get("uncovered_journeys") or []}


# ------------------------------------------------------------------ one experiment

def score_experiment(item: dict, records: list[dict], graph_crit: dict, cfg: dict) -> dict:
    scored = [s for s in (score_record(r, item, cfg) for r in records) if s]
    jw = cfg["journey_weights"]
    weight = max((jw.get(j, DEFAULT_JOURNEY_WEIGHT) for j in item["expected_impact"]["journeys"]),
                 default=DEFAULT_JOURNEY_WEIGHT)
    row = {
        "experiment_id": item["experiment_id"], "target": item["target"]["service"],
        "fault_type": item["fault_type"], "category": cfg["categories"].get(item["fault_type"]),
        "risk_id": item.get("risk_id"), "repeats_run": len(records),
        "outcome": "not_run", "S": None, "R": None, "C": None, "recovery_s": None,
        "journeys": {}, "uncovered_journeys": [], "weight": weight,
        "severity": None, "severity_value": None,
        "_scored": scored, "_inconclusive": len(records) - len(scored),
    }
    if not records:
        return row
    if not scored:
        row["outcome"] = "inconclusive"
        return row
    worst = max(scored, key=lambda s: (OUTCOME_BADNESS[s["outcome"]], -s["S"]))
    row["outcome"] = worst["outcome"]
    row["S"] = r1(mean([s["S"] for s in scored]))
    row["R"], row["C"] = r1(worst["R"], 3), r1(worst["C"], 3)
    row["recovery_s"] = r1(mean([s["recovery_s"] for s in scored if s["recovery_s"] is not None]))
    row["journeys"] = merge_journeys(scored)
    row["uncovered_journeys"] = sorted({j for s in scored for j in s["uncovered"]})
    row["severity_value"] = r1((100 - row["S"]) * graph_crit.get(row["target"], DEFAULT_CRITICALITY), 2)
    row["severity"] = severity_band((100 - row["S"]) * graph_crit.get(row["target"], DEFAULT_CRITICALITY),
                                    cfg["severity_bands"])
    return row


def merge_journeys(scored: list[dict]) -> dict:
    """Per-journey A, L, S averaged across the scored repeats that measured that journey."""
    names = sorted({j for s in scored for j in s["journeys"]})
    merged = {}
    for j in names:
        ms = [s["journeys"][j] for s in scored if j in s["journeys"]]
        s_values = [m["S"] for m in ms if m["S"] is not None]
        merged[j] = {"A": r1(mean([m["A"] for m in ms]), 3), "L": r1(mean([m["L"] for m in ms]), 3),
                     "S": r1(mean(s_values))}
    return merged


# ------------------------------------------------------------------ findings

def risk_status(exp: dict) -> str:
    scored = exp["_scored"]
    if scored:
        bad = any(s["outcome"] in ("degraded_recovered", "not_recovered") or s["aborted"] for s in scored)
        return "confirmed" if bad else "refuted"
    return "inconclusive" if exp["repeats_run"] else "predicted"


def fix_for(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return {"summary": entry.get("summary"),
            "files": [{"path": f["path"], "apply": f["apply"]} for f in entry.get("files") or []],
            "manual": entry.get("manual")}


def build_findings(risks: list[dict], experiments: list[dict], crit: dict, ranked: list[dict],
                   fixes_index: list[dict]) -> list[dict]:
    by_risk: dict = {}
    for exp in experiments:
        rid = exp["risk_id"]
        if rid and exp["repeats_run"]:
            # several experiments for one risk: keep the one with the worst evidence
            if rid not in by_risk or (exp["severity_value"] or 0) > (by_risk[rid]["severity_value"] or 0):
                by_risk[rid] = exp
    fixes = {f["risk_id"]: f for f in fixes_index}
    service_rank = {r["service"]: r["rank"] for r in ranked}
    findings = []
    for risk in risks:
        exp = by_risk.get(risk["risk_id"])
        status = risk_status(exp) if exp else "predicted"
        tested = status in ("confirmed", "refuted")
        findings.append({
            "risk_id": risk["risk_id"], "service": risk["service"], "check": risk["check"],
            "status": status, "basis": "tested" if tested else "predicted",
            "severity": exp["severity"] if tested else str(risk["severity"]).title(),
            "severity_value": exp["severity_value"] if tested else None,
            "evidence": risk.get("evidence"),
            "experiment_id": exp["experiment_id"] if exp else None,
            "S": exp["S"] if tested else None,
            "criticality": crit.get(risk["service"], DEFAULT_CRITICALITY),
            "fix": fix_for(fixes.get(risk["risk_id"])),
            "_audit": risk["severity"], "_rank": service_rank.get(risk["service"], 10**6),
        })
    findings.sort(key=finding_order)
    for i, f in enumerate(findings, 1):
        f.pop("_audit"), f.pop("_rank")
        f["rank"] = i
        # keep "rank" first for readability
    return [{"rank": f["rank"], **{k: v for k, v in f.items() if k != "rank"}} for f in findings]


def finding_order(f: dict) -> tuple:
    if f["status"] == "confirmed":
        return (0, -f["severity_value"], f["risk_id"])
    if f["status"] == "refuted":
        return (2, f["risk_id"])
    return (1, AUDIT_SEVERITY_ORDER.get(str(f["_audit"]).lower(), 9), -f["criticality"],
            f["_rank"], f["risk_id"])


# ------------------------------------------------------------------ whole report

def coverage_of(planned: int, experiments: list[dict]) -> dict:
    run = sum(1 for e in experiments if e["repeats_run"])
    scored = sum(1 for e in experiments if e["_scored"])
    inconclusive = run - scored
    if run == 0:
        text = f"0 of {planned} planned experiments run"
    else:
        text = f"{scored} of {planned} planned experiments scored"
        if inconclusive:
            text += f" ({inconclusive} inconclusive)"
    return {"planned": planned, "run": run, "scored": scored, "inconclusive": inconclusive, "text": text}


def overall(experiments: list[dict]) -> float | None:
    return r1(weighted_mean([(e["weight"], e["S"]) for e in experiments if e["S"] is not None]))


def build_scores(plan: list[dict], runs: list[dict], graph: dict, risks: list[dict],
                 ranked: list[dict], fixes_index: list[dict], cfg: dict) -> dict:
    crit = {n["name"]: n.get("criticality", DEFAULT_CRITICALITY) for n in graph.get("nodes", [])}
    runs_by_exp: dict = {}
    for r in runs:
        runs_by_exp.setdefault(r["experiment_id"], []).append(r)
    experiments = [score_experiment(item, runs_by_exp.get(item["experiment_id"], []), crit, cfg)
                   for item in plan]
    findings = build_findings(risks, experiments, crit, ranked, fixes_index)
    coverage = coverage_of(len(plan), experiments)
    score = overall(experiments)
    if score is not None:
        note = ("Journey-weighted per D-008: each experiment's S counts in proportion to the "
                "criticality of the journeys it can affect (checkout 0.4, cart 0.3, browse 0.2, ads-recs 0.1).")
    elif coverage["run"] == 0:
        note = "No experiments have run yet, so no resilience score exists; findings are predicted from the static audit only."
    else:
        note = "Every experiment run so far was inconclusive, so no resilience score exists yet."
    categories = {}
    for cat in dict.fromkeys(cfg["categories"].values()):
        in_cat = [e for e in experiments if e["category"] == cat]
        categories[cat] = {"score": overall(in_cat), "experiments": sum(1 for e in in_cat if e["S"] is not None)}
    for e in experiments:
        e.pop("_scored"), e.pop("_inconclusive")
    counts = {k: sum(1 for f in findings if f["status"] == k)
              for k in ("confirmed", "refuted", "inconclusive", "predicted")}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph_generated_at": graph.get("generated_at"),
        "coverage": coverage, "resilience_score": score,
        "score_status": "scored" if score is not None else "not_scored", "score_note": note,
        "weights": {"weights": cfg["weights"], "journey_weights": cfg["journey_weights"]},
        "categories": categories, "experiments": experiments, "findings": findings, "counts": counts,
    }


def summary_line(scores: dict) -> str:
    cov, c = scores["coverage"], scores["counts"]
    if scores["score_status"] == "scored":
        return (f"wrote {OUT_PATH}: resilience score {scores['resilience_score']}/100 "
                f"({cov['text']}); findings: {c['confirmed']} confirmed, {c['refuted']} refuted, "
                f"{c['predicted']} predicted")
    n = len(scores["findings"])
    tail = "all predicted" if c["predicted"] == n else (
        f"{c['confirmed']} confirmed, {c['refuted']} refuted, {c['inconclusive']} inconclusive, "
        f"{c['predicted']} predicted")
    return f"wrote {OUT_PATH}: not scored - {cov['text']}; {n} findings ranked ({tail})"


def main() -> None:
    for path in (PLAN_PATH, GRAPH_PATH, RISKS_PATH, CONFIG_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found - run the earlier pipeline steps first")
    scores = build_scores(
        load_json(PLAN_PATH), load_run_records(), load_json(GRAPH_PATH), load_json(RISKS_PATH),
        load_json(RANKED_PATH, []), load_json(FIXES_PATH, []), load_yaml(CONFIG_PATH))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(scores, f, indent=2)
    print(summary_line(scores))


if __name__ == "__main__":
    main()
