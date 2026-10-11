"""report.py - analytics agent: turn data/scores.json into a findings report.

Usage (run from the repo root, after `python -m src.score`):
    python -m src.report             # template text, then AI-written explanations if available
    python -m src.report --no-llm    # never call the LLM

Reads data/scores.json (+ data/graph.json, data/ranked_candidates.json for service context) and
writes reports/findings_report.md and reports/findings_report.json.

Rules.md rule 5 / Decisions.md D-005: scores and severities are computed by code (score.py). The
LLM only explains: it gets a facts dict, returns text, and every text is validated (length, known
risk_id, number guard: no number that is absent from the facts). A text that fails is dropped and
the deterministic template text is used, so the report is complete with no LLM at all.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone

import yaml
from pydantic import BaseModel

from src.llm import structured_call

SCORES_PATH = "data/scores.json"
GRAPH_PATH = "data/graph.json"
RANKED_PATH = "data/ranked_candidates.json"
PLAN_PATH = "data/plan_validated.json"
EXECUTOR_PATH = "config/executor.yaml"
CONFIG_PATH = "config/scoring.yaml"
OUT_MD = "reports/findings_report.md"
OUT_JSON = "reports/findings_report.json"

TABLE_ROWS = 15
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

SYSTEM_PROMPT = """You are a reliability analyst writing for an engineering team. A chaos-engineering \
tool tested (or analysed) the OpenTelemetry Demo, an e-commerce microservices app on Kubernetes, and \
deterministic code produced the findings you are given.

Write:
- executive_summary: 2-4 sentences on the overall resilience picture and what to fix first.
- items: one entry per finding in `findings` (use its risk_id exactly) with
  - explanation: why this weakness matters for users (which journeys or services break, the blast \
radius), and what the evidence shows.
  - why_fix_helps: why the recommended fix reduces the risk. If the finding has no fix, say a fix \
still needs to be written.

Rules: use only the facts provided. Do not invent numbers, services, journeys or fixes. Do not \
compute, change or re-rank scores or severities. A finding with basis "predicted" was not tested: \
call it a prediction or risk, never a confirmed failure. Only status "confirmed" may be described \
as a failure that was observed."""


class Explained(BaseModel):
    risk_id: str
    explanation: str
    why_fix_helps: str


class ReportText(BaseModel):
    executive_summary: str
    items: list[Explained]


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def humanise(check: str) -> str:
    return check.replace("_", " ")


# ------------------------------------------------------------------ facts

def service_context(service: str, graph: dict, ranked_by_service: dict) -> dict:
    edges = graph.get("edges", [])
    cand = ranked_by_service.get(service, {})
    return {
        "callers": sorted({e["from"] for e in edges if e["to"] == service}),
        "calls_into": sorted({e["to"] for e in edges if e["from"] == service}),
        "blast_radius": cand.get("blast_radius"),
        "gateway_share": cand.get("gateway_share"),
        "on_checkout_path": cand.get("on_checkout_path"),
    }


def finding_facts(finding: dict, experiments: dict, graph: dict, ranked_by_service: dict) -> dict:
    facts = {k: finding.get(k) for k in (
        "rank", "risk_id", "service", "check", "status", "basis", "severity", "severity_value",
        "evidence", "S", "criticality")}
    exp = experiments.get(finding.get("experiment_id"))
    if exp:
        facts["experiment"] = {k: exp.get(k) for k in (
            "experiment_id", "fault_type", "outcome", "S", "recovery_s", "repeats_run", "uncovered_journeys")}
        facts["experiment"]["journeys"] = exp.get("journeys")
    facts["service_context"] = service_context(finding["service"], graph, ranked_by_service)
    fix = finding.get("fix")
    facts["fix"] = fix["summary"] if fix else None
    if fix and fix.get("manual"):
        facts["fix_manual_note"] = fix["manual"]
    return facts


def report_facts(scores: dict, top: list[dict]) -> dict:
    return {
        "resilience_score": scores.get("resilience_score"),
        "score_status": scores.get("score_status"),
        "coverage": scores.get("coverage"),
        "counts": scores.get("counts"),
        "categories": scores.get("categories"),
        "top_findings": [{k: f[k] for k in ("rank", "risk_id", "service", "check", "status", "severity")}
                         for f in top],
    }


# ------------------------------------------------------------------ badge, runnable experiments

def badge(f: dict) -> str:
    """One human-readable status badge (status and basis say the same thing, so print it once)."""
    exp = f.get("experiment_id")
    status = f.get("status")
    if status == "confirmed":
        text = f"Confirmed by {exp}" if exp else "Confirmed by experiment"
    elif status == "refuted":
        text = f"Refuted by {exp} (survived)" if exp else "Refuted by experiment (survived)"
    elif status == "inconclusive":
        text = f"Inconclusive ({exp})" if exp else "Inconclusive"
    else:
        text = "Predicted (static audit, not yet tested)"
    return f"**{text}** - severity **{f['severity']}**"


def runnable_experiments(experiments: list[dict], plan: list[dict], executor: dict) -> list[dict]:
    """Experiments `python -m src.run --execute` would accept, mirroring run.refusal(): the target
    is not a probe_path_service and at least one expected journey has a GET probe (or the target
    has a target-specific probe). Without a plan file the journeys are unknown, so only the
    probe-path exclusion applies."""
    blocked = set(executor.get("probe_path_services") or [])
    probes = executor.get("journey_probes") or {}
    general = {j for j, c in probes.items() if not (c or {}).get("for_targets")}
    plan_by_id = {p.get("experiment_id"): p for p in plan or []}
    out = []
    for e in experiments:
        if e.get("outcome") not in (None, "not_run"):
            continue
        target = e.get("target")
        if target in blocked:
            continue
        item = plan_by_id.get(e.get("experiment_id"))
        if item is not None:
            journeys = (item.get("expected_impact") or {}).get("journeys") or []
            specific = any(target in ((c or {}).get("for_targets") or []) for c in probes.values())
            if not (specific or any(j in general for j in journeys)):
                continue
        out.append(e)
    return out


# ------------------------------------------------------------------ template text

WHY_FIX = {
    "single_replica_no_pdb": "a second replica keeps serving while one pod is killed or rescheduled, and the "
                             "PodDisruptionBudget stops voluntary evictions (node drains, upgrades) from taking both down.",
    "stateful_single_replica": "a stateful service needs replication or a managed backing store rather than just "
                               "more replicas, because extra copies of a single-writer store would diverge; "
                               "replication lets a failover keep the data available.",
    "no_readiness_probe": "a readiness probe keeps traffic away from a pod until it can serve, so a restart "
                          "does not turn into user-facing errors.",
    "no_liveness_probe": "a liveness probe restarts a process that is hung or frozen instead of leaving it to "
                         "time out every request.",
    "no_resource_limits": "resource limits bound the damage a noisy neighbour or runaway process can do to "
                          "other pods on the same node.",
    "no_hpa_shared_dependency": "a horizontal pod autoscaler adds replicas under load spikes, so a shared "
                                "dependency does not become the bottleneck for every service that calls it.",
}


def role_sentence(f: dict) -> str:
    """One sentence on the service's role, from graph context fields only."""
    ctx = f["service_context"]
    bits = []
    if ctx["callers"]:
        bits.append(f"is called by {', '.join(ctx['callers'])}")
    if ctx["blast_radius"]:
        bits.append(f"has {ctx['blast_radius']} dependent service(s) in its blast radius")
    if ctx["gateway_share"]:
        bits.append(f"carries gateway share {ctx['gateway_share']}")
    if ctx["on_checkout_path"]:
        bits.append("sits on the checkout path")
    if not bits:
        return f"{f['service']} has no recorded callers in the dependency graph."
    return f"{f['service']} " + "; ".join(bits) + "."


def template_explanation(f: dict) -> tuple[str, str]:
    """Deterministic (explanation, why_fix_helps) for one finding's facts."""
    check = humanise(f["check"])
    if f["status"] == "confirmed":
        lead = f"Confirmed by experiment: {f['service']} - {check}."
    elif f["status"] == "refuted":
        lead = f"Tested and not confirmed: {f['service']} - {check} did not cause a user-visible failure."
    elif f["status"] == "inconclusive":
        lead = f"Tested but inconclusive: {f['service']} - {check}."
    else:
        lead = f"Prediction (not yet tested): {f['service']} - {check}."
    parts = [lead, f"Evidence: {f['evidence']}.", role_sentence(f), f"Severity: {f['severity']}."]
    mech = WHY_FIX.get(f["check"])
    if f["fix"] and mech:
        why = f"{mech[0].upper()}{mech[1:]}"
    elif f["fix"]:
        why = f"Recommended fix: {f['fix']}"
    elif mech:
        why = f"No automated fix is available yet. In general, {mech}"
    else:
        why = "No automated fix is available yet; this needs a manual change."
    return " ".join(parts), why


def template_summary(rf: dict) -> str:
    cov = rf["coverage"] or {}
    if rf["score_status"] == "scored" and rf["resilience_score"] is not None:
        head = f"Resilience score is {rf['resilience_score']} / 100 ({cov.get('text', '')})."
    else:
        head = (f"Not scored yet: {cov.get('run', 0)} of {cov.get('planned', 0)} planned experiments "
                f"have been run.")
    c = rf["counts"] or {}
    tail = (f" Findings: {c.get('confirmed', 0)} confirmed, {c.get('refuted', 0)} refuted, "
            f"{c.get('inconclusive', 0)} inconclusive, {c.get('predicted', 0)} predicted (untested).")
    if rf["top_findings"]:
        t = rf["top_findings"][0]
        tail += f" Highest-ranked weakness: {t['service']} - {humanise(t['check'])} ({t['severity']})."
    return head + tail


# ------------------------------------------------------------------ validation

def number_tokens(text: str) -> set[str]:
    return set(NUMBER_RE.findall(text))


def validate_text(text: str, facts_json: str, cfg: dict) -> list[str]:
    """Length bounds + number guard. Empty list = accept."""
    problems = []
    n = len(text.strip())
    if not cfg["min_text_chars"] <= n <= cfg["max_text_chars"]:
        problems.append(f"{n} chars (allowed {cfg['min_text_chars']}-{cfg['max_text_chars']})")
    invented = sorted(number_tokens(text) - number_tokens(facts_json))
    if invented:
        problems.append(f"numbers not in facts: {', '.join(invented)}")
    return problems


def validate_item(item: Explained, facts_by_id: dict, seen: set, cfg: dict) -> list[str]:
    if item.risk_id not in facts_by_id:
        return [f"unknown risk_id '{item.risk_id}'"]
    if item.risk_id in seen:
        return [f"duplicate risk_id '{item.risk_id}'"]
    facts_json = json.dumps(facts_by_id[item.risk_id])
    problems = []
    for field in ("explanation", "why_fix_helps"):
        problems += [f"{field}: {p}" for p in validate_text(getattr(item, field), facts_json, cfg)]
    return problems


def apply_ai_text(llm: ReportText, rfacts: dict, facts_by_id: dict, cfg: dict) -> tuple[str | None, dict]:
    """Returns (validated summary or None, {risk_id: (explanation, why_fix_helps)})."""
    summary = None
    problems = validate_text(llm.executive_summary, json.dumps(rfacts), cfg)
    if problems:
        print(f"  dropped AI executive summary: {'; '.join(problems)}")
    else:
        summary = llm.executive_summary.strip()
    accepted, seen = {}, set()
    for item in llm.items:
        problems = validate_item(item, facts_by_id, seen, cfg)
        if problems:
            print(f"  dropped AI text for {item.risk_id}: {'; '.join(problems)}")
            continue
        seen.add(item.risk_id)
        accepted[item.risk_id] = (item.explanation.strip(), item.why_fix_helps.strip())
    return summary, accepted


# ------------------------------------------------------------------ build

def build_report(scores: dict, graph: dict, ranked: list[dict], cfg: dict, use_llm: bool,
                 plan: list[dict] | None = None, executor: dict | None = None) -> dict:
    """Pure data -> report dict (no file I/O except through structured_call)."""
    llm_cfg = cfg["llm"]
    ranked_by_service = {c["service"]: c for c in ranked}
    experiments = {e["experiment_id"]: e for e in scores.get("experiments", [])}
    findings = sorted(scores.get("findings", []), key=lambda f: f["rank"])
    top = findings[:llm_cfg["explain_top"]]
    nested_w = scores.get("weights") or {}  # score.py: {"weights": {A,L,R,C}, "journey_weights": {...}}

    facts_by_id = {f["risk_id"]: finding_facts(f, experiments, graph, ranked_by_service) for f in top}
    rfacts = report_facts(scores, top)

    summary_ai, ai_items = None, {}
    if use_llm and top:
        user = json.dumps({"report": rfacts, "findings": list(facts_by_id.values())}, indent=1)
        llm = structured_call(SYSTEM_PROMPT, user, ReportText, llm_cfg, "AI explanations")
        if llm is not None:
            summary_ai, ai_items = apply_ai_text(llm, rfacts, facts_by_id, llm_cfg)

    explained = []
    for f in top:
        facts = facts_by_id[f["risk_id"]]
        if f["risk_id"] in ai_items:
            explanation, why, source = (*ai_items[f["risk_id"]], "ai")
        else:
            explanation, why = template_explanation(facts)
            source = "template"
        explained.append({**f, "explanation": explanation, "why_fix_helps": why, "text_source": source})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scores_generated_at": scores.get("generated_at"),
        "graph_generated_at": scores.get("graph_generated_at") or graph.get("generated_at"),
        "score": {k: scores.get(k) for k in (
            "resilience_score", "score_status", "score_note", "coverage", "categories", "weights")},
        "counts": scores.get("counts"),
        "executive_summary": {"text": summary_ai or template_summary(rfacts),
                              "text_source": "ai" if summary_ai else "template"},
        "findings": findings,
        "explained": explained,
        "experiments": scores.get("experiments", []),
        "runnable_experiments": [
            {"experiment_id": e["experiment_id"], "target": e["target"], "fault_type": e.get("fault_type")}
            for e in runnable_experiments(scores.get("experiments", []), plan or [], executor or {})],
        "config": {"journey_weights": nested_w.get("journey_weights") or cfg.get("journey_weights"),
                   "weights": nested_w.get("weights") or cfg.get("weights")},
    }


# ------------------------------------------------------------------ markdown

def cell(value) -> str:
    return "-" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return out


def next_step_lines(runnable: list[dict]) -> list[str]:
    lines = ["Next step:", "", "```", "python -m src.run --list      # shows which approved experiments are runnable"]
    if runnable:
        for e in runnable[:3]:
            lines.append(f"python -m src.run --exp {e['experiment_id']} --preview   # then --execute "
                         f"({e['target']}, {e.get('fault_type')})")
    else:
        lines.append("# no approved experiment is currently runnable; see the --list output for the reasons")
    lines += ["python -m src.score && python -m src.report", "```", ""]
    if runnable:
        ids = ", ".join(e["experiment_id"] for e in runnable)
        lines += [f"Runnable now: {ids}. Experiments on the probe path (frontend-proxy) or with only a "
                  "checkout journey (no GET probe) are refused by `src.run`.", ""]
    return lines


def score_section(score: dict, runnable: list[dict] | None = None) -> list[str]:
    cov = score.get("coverage") or {}
    if score.get("score_status") == "scored" and score.get("resilience_score") is not None:
        lines = [f"## Resilience score: {score['resilience_score']:.1f} / 100", "",
                 f"Coverage: {cov.get('text', '')}", ""]
        cats = score.get("categories") or {}
        rows = [[name, "-" if c.get("score") is None else f"{c['score']:.1f}", c.get("experiments", 0)]
                for name, c in cats.items()]
        return lines + table(["Category", "Score", "Experiments"], rows) + [""]
    return [
        "## Resilience score", "",
        f"**Not scored yet - {cov.get('run', 0)} of {cov.get('planned', 0)} planned experiments run**", "",
        score.get("score_note") or "", "",
    ] + next_step_lines(runnable or [])


def fix_block(f: dict) -> list[str]:
    fix = f.get("fix")
    if not fix:
        return ["**Recommended fix:** none available yet.", ""]
    lines = [f"**Recommended fix:** {fix['summary']}", ""]
    cmds = [x["apply"] for x in fix.get("files", []) if x.get("apply")]
    if cmds:
        lines += ["```"] + cmds + ["```", ""]
    if fix.get("manual"):
        lines += [f"Manual step: {fix['manual']}", ""]
    return lines


def render_markdown(report: dict) -> str:
    score = report["score"]
    es = report["executive_summary"]
    L = ["# ChaosPilot findings report", "",
         f"Snapshot: scores generated {report['scores_generated_at']}, "
         f"graph generated {report['graph_generated_at']}", ""]
    L += score_section(score, report.get("runnable_experiments"))

    label = "AI-written - numbers come from code" if es["text_source"] == "ai" else "Template"
    L += ["## Executive summary", "", f"_{label}_", "", es["text"], ""]

    L += ["## Weaknesses ranked by severity", ""]
    rows = [[f["rank"], f["severity"], f["status"], f["service"], humanise(f["check"]),
             (f["fix"] or {}).get("summary") if f.get("fix") else "no fix yet"]
            for f in report["findings"][:TABLE_ROWS]]
    L += table(["Rank", "Severity", "Status", "Service", "Weakness", "Fix"], rows) + [""]

    L += ["## Top findings explained", ""]
    for f in report["explained"]:
        src = "AI-written" if f["text_source"] == "ai" else "Template"
        L += [f"### {f['rank']}. {f['service']}: {humanise(f['check'])}", "",
              badge(f), "",
              f"**Evidence:** {f['evidence']}", "",
              f"**Why it matters:** {f['explanation']}", "",
              f"**Why the fix helps:** {f['why_fix_helps']}", ""]
        L += fix_block(f)
        L += [f"_Text source: {src}_", ""]

    c = report["counts"] or {}
    L += ["## Predicted vs confirmed", ""]
    L += table(["Confirmed", "Refuted", "Inconclusive", "Predicted"],
               [[c.get("confirmed", 0), c.get("refuted", 0), c.get("inconclusive", 0), c.get("predicted", 0)]]) + [""]

    L += ["## Experiments", ""]
    if report["experiments"]:
        L += table(["Id", "Target", "Fault", "Outcome", "S"],
                   [[e["experiment_id"], e["target"], e["fault_type"], e["outcome"], e.get("S")]
                    for e in report["experiments"]]) + [""]
    else:
        L += ["No experiments have been scored yet.", ""]

    jw = report["config"].get("journey_weights") or {}
    w = report["config"].get("weights") or {}
    jw_text = ", ".join(f"{k} {v}" for k, v in jw.items())
    L += ["## How the score works", "",
          f"- Per affected journey: S = 100 x (A x {w.get('A')} + L x {w.get('L')} + R x {w.get('R')} + "
          f"C x {w.get('C')}) (Decisions.md D-008).",
          f"- Overall score = sum(journey weight x S) / sum(journey weight). Journey weights: {jw_text}.",
          "- Severity of a tested finding = (100 - S) x service criticality; untested findings are "
          "predictions ranked by structural risk.",
          "- The AI only explains. Scores and severities are computed by code; every number in "
          "AI-written text is checked against the facts and dropped if invented.", ""]
    return "\n".join(L)


# ------------------------------------------------------------------ main

def write_outputs(report: dict) -> None:
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="never call the LLM")
    args = parser.parse_args()

    if not os.path.exists(SCORES_PATH):
        raise SystemExit(f"{SCORES_PATH} not found - run `python -m src.score` first")

    scores = load_json(SCORES_PATH, {})
    cfg = load_yaml(CONFIG_PATH)
    executor = load_yaml(EXECUTOR_PATH) if os.path.exists(EXECUTOR_PATH) else {}
    report = build_report(scores, load_json(GRAPH_PATH, {}), load_json(RANKED_PATH, []), cfg,
                          use_llm=not args.no_llm, plan=load_json(PLAN_PATH, []), executor=executor)
    write_outputs(report)

    n_ai = sum(1 for f in report["explained"] if f["text_source"] == "ai")
    status = (f"score {report['score']['resilience_score']}" if report["score"]["score_status"] == "scored"
              else "score not scored")
    print(f"wrote {OUT_MD}: {len(report['findings'])} findings, {len(report['explained'])} explained "
          f"(AI: {n_ai}, template: {len(report['explained']) - n_ai}), {status}")


if __name__ == "__main__":
    main()
