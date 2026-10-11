"""remediate.py - the remediation agent: detects which weaknesses matter and picks the fix for each.

    python -m src.remediate            # run the agent, write reports/remediation.json
    python -m src.remediate --verify   # after fixes were applied and re-tested: score before -> after

What the agent does (an LLM, Groq, config/planner.yaml):
  DETECT  reads the audit findings (data/risks.json) together with what the chaos experiments measured
          (data/scores.json), decides which weaknesses are real and which to fix first, and says why.
  FIX     for each one, chooses among the candidate patches already generated in reports/fixes/index.json
          and names the experiment that will prove the fix worked.

What it can NOT do, by construction (Decisions.md D-007, Rules.md):
  - It never touches the cluster. Its output is a JSON file; applying a patch stays a separate step in the
    Control tab (server dry-run, typed `apply-fix`, saved undo patch, roll back).
  - It cannot invent a patch. It can only name a risk_id that exists in the index and an experiment id that
    exists in the approved plan; anything else is dropped (validate_decisions).
  - No key, an API error or invalid output -> the deterministic ranking is used and the file says so.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parent.parent
try:  # GROQ_API_KEY from the repo's .env (git-ignored), if python-dotenv is installed; a real env var still wins
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass
RISKS_PATH = ROOT / "data" / "risks.json"
SCORES_PATH = ROOT / "data" / "scores.json"
PLAN_PATH = ROOT / "data" / "plan_validated.json"
INDEX_PATH = ROOT / "reports" / "fixes" / "index.json"
OUT_PATH = ROOT / "reports" / "remediation.json"
PLANNER_CFG = ROOT / "config" / "planner.yaml"

SEV = {"high": 0, "medium": 1, "low": 2}
IMPACT = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
MAX_ITEMS = 12
MIN_TEXT, MAX_TEXT = 20, 500


# ------------------------------------------------------------------ evidence
def _load(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def gather(risks, fixes, scores, plan) -> list[dict]:
    """One row per finding that has a fix (or a manual note), with whatever the experiments measured."""
    exp_by_risk = {}
    for e in (scores or {}).get("experiments", []):
        exp_by_risk.setdefault(e.get("risk_id"), e)
    plan_by_risk = {p.get("risk_id"): p["experiment_id"] for p in plan or []}
    risk_by_id = {r["risk_id"]: r for r in risks or []}
    rows = []
    for fx in fixes or []:
        risk = risk_by_id.get(fx["risk_id"], {})
        exp = exp_by_risk.get(fx["risk_id"]) or {}
        rows.append({
            "risk_id": fx["risk_id"], "service": fx["service"], "check": fx["check"],
            "audit_severity": fx["severity"], "evidence": risk.get("evidence", ""),
            "fix": fx["summary"], "has_patch": bool(fx.get("files")), "manual": fx.get("manual"),
            "experiment_id": exp.get("experiment_id") or plan_by_risk.get(fx["risk_id"]),
            "measured_S": exp.get("S"), "measured_severity": exp.get("severity"),
            "measured_status": exp.get("status"), "prediction": exp.get("prediction"),
        })
    return rows


def deterministic_rank(rows: list[dict]) -> list[dict]:
    """Fallback order: measured damage first, then audit severity, patches before manual notes."""
    def key(r):
        measured = IMPACT.get(r["measured_severity"], 4)
        s = r["measured_S"] if r["measured_S"] is not None else 101
        return (measured, SEV.get(r["audit_severity"], 9), not r["has_patch"], s, r["risk_id"])
    return sorted(rows, key=key)


# ------------------------------------------------------------------ LLM contract
class Decision(BaseModel):
    risk_id: str
    priority: int = Field(ge=1, le=99)
    real_weakness: bool = True
    diagnosis: str
    why_this_fix: str
    verify_with: str | None = None


class Decisions(BaseModel):
    decisions: list[Decision]


SYSTEM_PROMPT = """You are a reliability engineer reviewing chaos-engineering results for a Kubernetes shop
(OpenTelemetry demo). You get findings from a static audit plus, where an experiment was run, what was measured
(measured_S 0-100, higher is more resilient; measured_severity). Decide which weaknesses are real and fix first.
Rules: use only the risk_id values given; never invent a fix (the patch for each risk_id already exists);
a finding the experiment showed to be harmless (survived, S near 100) is lower priority than one that broke
a user journey; prefer findings that were measured over ones only predicted. verify_with must be the
experiment_id given for that finding, or null. diagnosis: what is wrong and what the evidence shows (1-2 sentences).
why_this_fix: why the listed patch addresses it (1 sentence). Reply with JSON only:
{"decisions": [{"risk_id": "...", "priority": 1, "real_weakness": true, "diagnosis": "...",
"why_this_fix": "...", "verify_with": "exp-004"}]}  priority 1 = fix first."""


def call_groq(rows: list[dict], cfg: dict) -> Decisions | None:
    if not os.getenv("GROQ_API_KEY"):
        print("agent skipped: GROQ_API_KEY not set - using the deterministic ranking")
        return None
    try:
        from groq import Groq
    except ImportError:
        print("agent skipped: `groq` not installed (pip install groq) - using the deterministic ranking")
        return None
    slim = [{k: r[k] for k in ("risk_id", "service", "check", "audit_severity", "evidence", "fix", "has_patch",
                               "experiment_id", "measured_S", "measured_severity", "measured_status")} for r in rows]
    try:
        resp = Groq().chat.completions.create(
            model=cfg.get("groq_model", "llama-3.3-70b-versatile"), temperature=0.2, max_tokens=4000,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": json.dumps(slim)}])
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            print("agent skipped: incomplete output - using the deterministic ranking")
            return None
        return Decisions.model_validate_json(choice.message.content)
    except (Exception, ValidationError) as e:  # noqa: BLE001
        print(f"agent skipped: {type(e).__name__}: {str(e)[:160]} - using the deterministic ranking")
        return None


def validate_decisions(decisions: Decisions, rows: list[dict]) -> tuple[list[Decision], list[str]]:
    """Keep only decisions that refer to a real finding with sane text. Returns (kept, reasons dropped)."""
    by_id = {r["risk_id"]: r for r in rows}
    kept, dropped, seen = [], [], set()
    for d in decisions.decisions:
        row = by_id.get(d.risk_id)
        if row is None:
            dropped.append(f"{d.risk_id}: not a known finding")
        elif d.risk_id in seen:
            dropped.append(f"{d.risk_id}: duplicate")
        elif not (MIN_TEXT <= len(d.diagnosis) <= MAX_TEXT and MIN_TEXT <= len(d.why_this_fix) <= MAX_TEXT):
            dropped.append(f"{d.risk_id}: text length out of bounds")
        else:
            if d.verify_with not in (None, row["experiment_id"]):
                d = d.model_copy(update={"verify_with": row["experiment_id"]})
            seen.add(d.risk_id)
            kept.append(d)
    return kept, dropped


# ------------------------------------------------------------------ build the report
def build(rows: list[dict], decisions: Decisions | None, model: str, fixes: list[dict]) -> dict:
    ordered = deterministic_rank(rows)
    kept, dropped = ([], [])
    if decisions is not None:
        kept, dropped = validate_decisions(decisions, rows)
    by_id = {r["risk_id"]: r for r in rows}
    fix_by_id = {f["risk_id"]: f for f in fixes}
    items = []
    if kept:
        chosen = sorted((d for d in kept if d.real_weakness), key=lambda d: (d.priority, d.risk_id))[:MAX_ITEMS]
        for n, d in enumerate(chosen, 1):
            r = by_id[d.risk_id]
            items.append(_item(n, r, fix_by_id[d.risk_id], d.diagnosis, d.why_this_fix, d.verify_with))
        agent = f"llm:{model}"
    else:
        for n, r in enumerate(ordered[:MAX_ITEMS], 1):
            sev = f" Measured: S={r['measured_S']} ({r['measured_severity']})." if r["measured_S"] is not None else ""
            items.append(_item(n, r, fix_by_id[r["risk_id"]],
                               f"{r['check'].replace('_', ' ')} on {r['service']}. {r['evidence']}.{sev}",
                               f"Generated patch: {r['fix']}.", r["experiment_id"]))
        agent = "deterministic-fallback"
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), "agent": agent,
            "dropped": dropped, "items": items,
            "note": "Suggestions only. Nothing here is applied; use the Control tab (dry-run, typed apply-fix, roll back)."}


def _item(n, row, fix, diagnosis, why, verify_with) -> dict:
    return {"priority": n, "risk_id": row["risk_id"], "service": row["service"], "check": row["check"],
            "audit_severity": row["audit_severity"], "diagnosis": diagnosis, "fix_summary": row["fix"],
            "why_this_fix": why, "files": fix.get("files") or [], "manual": fix.get("manual"),
            "verify_with": verify_with, "before": {"S": row["measured_S"], "severity": row["measured_severity"]}}


def run(use_llm: bool = True) -> dict:
    risks, fixes = _load(RISKS_PATH, []), _load(INDEX_PATH, [])
    if not fixes:
        raise SystemExit("reports/fixes/index.json not found - run python -m src.fixes first")
    rows = gather(risks, fixes, _load(SCORES_PATH, {}), _load(PLAN_PATH, []))
    try:
        cfg = yaml.safe_load(PLANNER_CFG.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    decisions = call_groq(rows, cfg) if use_llm else None
    report = build(rows, decisions, cfg.get("groq_model", "?"), fixes)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ------------------------------------------------------------------ verify (before -> after)
def verify(report: dict, scores: dict) -> list[dict]:
    """Compare the score recorded when the agent ran with the latest score of the verifying experiment."""
    now = {e["experiment_id"]: e for e in (scores or {}).get("experiments", [])}
    out = []
    for it in report.get("items", []):
        exp = now.get(it.get("verify_with") or "")
        if not exp:
            continue
        b, a = it["before"]["S"], exp.get("S")
        verdict = ("not re-tested" if a is None or b is None else
                   "improved" if a - b >= 5 else "worse" if b - a >= 5 else "unchanged")
        out.append({"risk_id": it["risk_id"], "experiment": exp["experiment_id"], "before": b, "after": a,
                    "severity_after": exp.get("severity"), "verdict": verdict})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verify", action="store_true", help="show before -> after for the experiments named in the report")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM and use the deterministic ranking")
    args = ap.parse_args()
    if args.verify:
        report = _load(OUT_PATH, None)
        if not report:
            raise SystemExit("no reports/remediation.json - run python -m src.remediate first")
        for v in verify(report, _load(SCORES_PATH, {})):
            print(f"{v['risk_id']}: {v['experiment']} S {v['before']} -> {v['after']} ({v['verdict']})")
        return
    report = run(use_llm=not args.no_llm)
    print(f"agent: {report['agent']} · {len(report['items'])} suggestion(s) -> {OUT_PATH.relative_to(ROOT)}")
    for it in report["items"]:
        print(f"  {it['priority']}. {it['service']}: {it['fix_summary']}")
    for d in report["dropped"]:
        print(f"  dropped: {d}")


if __name__ == "__main__":
    main()
