"""plan.py - planner: build data/plan.json from data/ranked_candidates.json,
data/risks.json, data/graph.json, and config/fault_catalog.yaml.

Usage (run from the repo root, after discover.py, audit.py, and rank.py):
    python -m src.plan                  # deterministic plan, then LLM-written text if available
    python -m src.plan --deterministic  # never call the LLM

Two steps (Architecture.md section 9, Decisions.md D-005/D-006):
  1. Deterministic: ranking -> targets, fault types, params, SLOs. Always runs.
  2. LLM (config/planner.yaml): rewrites only hypothesis, reason, and
     expected_impact.journeys for each item. Its output is validated with
     pydantic plus validate_llm_item(); it can never change a target, fault
     type, or param. An item that fails validation keeps its template text, and
     any failure of the whole call (no credentials, API error, refusal) keeps
     the deterministic plan - section 9 point 4.

Either way the output is the same data/plan.json contract (Architecture.md
section 4.3), so policy.py and run.py never need to know which planner wrote it.
"""
import argparse
import json
import os
from typing import Literal

import yaml
from pydantic import BaseModel

RANKED_PATH = "data/ranked_candidates.json"
RISKS_PATH = "data/risks.json"
CATALOG_PATH = "config/fault_catalog.yaml"
GRAPH_PATH = "data/graph.json"
BASELINE_PATH = "data/baseline.json"  # P2; optional until it exists
PLANNER_CONFIG_PATH = "config/planner.yaml"
OUT_PATH = "data/plan.json"
NAMESPACE = os.getenv("NAMESPACE", "otel-demo")

MAX_EXPERIMENTS = 8  # Plan.md S5 target: 6-8 experiments covering >= 3 fault types

# Placeholders until P2's baseline.json exists (Architecture.md section 6: "tune after baseline")
DEFAULT_SLO = {"success_rate_min": 0.95, "p95_ms_max": 1500}
DEFAULT_RECOVERY_BUDGET_S = 120


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def hypothesis_for(service: str, risk: dict) -> str:
    return (f"{service}: {risk['check'].replace('_', ' ')} ({risk['evidence']}) - "
            f"expect {service}'s callers to see errors or latency until the fault clears.")


def journey_members(graph: dict, journey_roots: dict) -> dict[str, set[str]]:
    """Services each user journey (D-008) depends on: its root services (config/planner.yaml),
    everything they call, and everything on the call path from the entry point down to them
    (so frontend-proxy and frontend belong to every journey). Replace with P2's measured
    journey map (T1.3) once it exists."""
    down: dict[str, list[str]] = {}
    up: dict[str, list[str]] = {}
    for e in graph["edges"]:
        down.setdefault(e["from"], []).append(e["to"])
        up.setdefault(e["to"], []).append(e["from"])

    def walk(start: list[str], adjacency: dict) -> set[str]:
        seen, stack = set(start), list(start)
        while stack:
            for nxt in adjacency.get(stack.pop(), []):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    return {j: walk(roots, down) | walk(roots, up) for j, roots in journey_roots.items()}


def expected_journeys(service: str, members: dict[str, set[str]]) -> list[str]:
    return sorted(j for j, services in members.items() if service in services)


def choose_risk(candidate_risks: list[dict], used_counts: dict, catalog: dict) -> dict | None:
    """Among this candidate's top risks (already ordered by severity), prefer
    whichever fault type has been used least so far in the plan, so the plan
    naturally spans multiple fault types (gate G4: >= 3 types) instead of
    always picking the single highest-severity check. Falls back to severity
    order on ties, since min() keeps the first match."""
    eligible = [r for r in candidate_risks if r["suggested_experiment"] in catalog]
    if not eligible:
        return None
    return min(eligible, key=lambda r: used_counts.get(r["suggested_experiment"], 0))


def build_plan(candidates: list[dict], risks_by_id: dict, catalog: dict,
               members: dict[str, set[str]]) -> list[dict]:
    items = []
    used_counts: dict[str, int] = {}
    for candidate in candidates:
        if len(items) >= MAX_EXPERIMENTS:
            break
        candidate_risks = [risks_by_id[rid] for rid in candidate["top_risks"]]
        risk = choose_risk(candidate_risks, used_counts, catalog)
        if risk is None:
            if candidate_risks:
                print(f"skipped {candidate['service']}: none of its risk flags "
                      f"map to a fault_type in {CATALOG_PATH}")
            continue

        fault_type = risk["suggested_experiment"]
        spec = catalog[fault_type]
        used_counts[fault_type] = used_counts.get(fault_type, 0) + 1

        items.append({
            "experiment_id": f"exp-{len(items) + 1:03d}",
            "rank": candidate["rank"],
            "risk_id": risk["risk_id"],
            "fault_type": fault_type,
            "mechanism": spec["mechanism"],
            "target": {"namespace": NAMESPACE, "service": candidate["service"]},
            "params": {"duration_s": spec["default_duration_s"], **spec.get("default_params", {})},
            "hypothesis": hypothesis_for(candidate["service"], risk),
            "reason": candidate["reason"],
            "expected_impact": {"journeys": expected_journeys(candidate["service"], members)},
            "slo": DEFAULT_SLO,
            "recovery_budget_s": DEFAULT_RECOVERY_BUDGET_S,
            "planner": "deterministic",
        })
    return items


# ------------------------------------------------------------------ LLM text (step 2)

SYSTEM_PROMPT = """You write experiment hypotheses for a chaos-engineering tool that tests the \
OpenTelemetry Demo, an e-commerce microservices app, on Kubernetes.

The experiments are already chosen and ranked by deterministic code. Each one's target service, \
fault type, and parameters are fixed; you cannot change them. For every experiment, write:
- hypothesis: one falsifiable sentence predicting what users will observe while the fault is active \
and when it should recover. Name the measurable symptom (errors, latency, which page or action fails) \
and ground it in the evidence given (replicas, probes, callers, call volume).
- reason: one sentence on why this experiment is worth running, citing the graph facts given \
(callers, blast radius, call volume, risk flags).
- journeys: the user journeys the fault should affect, chosen only from allowed_journeys.

Use only facts present in the input. Do not invent services, metrics, or numbers. \
Return exactly one entry per experiment_id."""

Journey = Literal["browse", "cart", "checkout", "ads-recs"]  # keep in sync with config/planner.yaml


class LLMItem(BaseModel):
    experiment_id: str
    hypothesis: str
    reason: str
    journeys: list[Journey]


class LLMPlanText(BaseModel):
    items: list[LLMItem]


def llm_context(plan: list[dict], candidates: list[dict], risks_by_id: dict, graph: dict, cfg: dict) -> str:
    """Everything the LLM may use, as JSON. Facts only - no instructions."""
    by_service = {c["service"]: c for c in candidates}
    experiments = []
    for item in plan:
        svc = item["target"]["service"]
        c = by_service[svc]
        risk = risks_by_id[item["risk_id"]]
        experiments.append({
            "experiment_id": item["experiment_id"],
            "service": svc,
            "fault_type": item["fault_type"],
            "params": item["params"],
            "risk": {"check": risk["check"], "severity": risk["severity"], "evidence": risk["evidence"]},
            "callers": sorted({e["from"] for e in graph["edges"] if e["to"] == svc}),
            "calls_into": sorted({e["to"] for e in graph["edges"] if e["from"] == svc}),
            "fan_in": c["fan_in"],
            "blast_radius": c.get("blast_radius"),
            "inbound_calls_sampled": c.get("inbound_calls"),
            "on_checkout_path": c["on_checkout_path"],
            "template_hypothesis": item["hypothesis"],
        })
    baseline = json.load(open(BASELINE_PATH)) if os.path.exists(BASELINE_PATH) else "not measured yet"
    return json.dumps({"allowed_journeys": cfg["journeys"], "baseline": baseline,
                       "experiments": experiments}, indent=1)


def validate_llm_item(item: LLMItem, known_ids: set[str], cfg: dict) -> list[str]:
    """Checks pydantic's schema can't express. Empty list = accept."""
    problems = []
    if item.experiment_id not in known_ids:
        problems.append(f"unknown experiment_id '{item.experiment_id}'")
    for field in ("hypothesis", "reason"):
        n = len(getattr(item, field).strip())
        if not cfg["min_text_chars"] <= n <= cfg["max_text_chars"]:
            problems.append(f"{field} is {n} chars (allowed {cfg['min_text_chars']}-{cfg['max_text_chars']})")
    if not item.journeys:
        problems.append("no journeys")
    extra = set(item.journeys) - set(cfg["journeys"])
    if extra:
        problems.append(f"journeys {sorted(extra)} not allowed")
    return problems


GROQ_JSON_INSTRUCTIONS = """

Reply with JSON only, exactly this shape:
{"items": [{"experiment_id": "exp-001", "hypothesis": "...", "reason": "...", "journeys": ["browse"]}]}"""


def call_llm_groq(context: str, cfg: dict) -> LLMPlanText | None:
    """Same contract as call_llm, through Groq (GROQ_API_KEY). The output goes through the same
    pydantic model and validate_llm_item(), so it can never change a target, fault or param."""
    if not os.getenv("GROQ_API_KEY"):
        print("LLM skipped: GROQ_API_KEY not set - keeping deterministic text")
        return None
    try:
        from groq import Groq
    except ImportError:
        print("LLM skipped: `groq` not installed (pip install groq) - keeping deterministic text")
        return None
    try:
        response = Groq().chat.completions.create(
            model=cfg["groq_model"],
            temperature=0.2,
            max_tokens=min(cfg["max_tokens"], 8000),
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT + GROQ_JSON_INSTRUCTIONS},
                      {"role": "user", "content": context}],
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            print("LLM skipped: incomplete output (length) - keeping deterministic text")
            return None
        return LLMPlanText.model_validate_json(choice.message.content)
    except Exception as e:  # noqa: BLE001 - any API or schema failure keeps the deterministic plan
        print(f"LLM skipped: {type(e).__name__}: {str(e)[:160]} - keeping deterministic text")
        return None


def call_llm(context: str, cfg: dict) -> LLMPlanText | None:
    """One structured call. Returns None (with the reason printed) on any failure."""
    if cfg.get("provider", "anthropic") == "groq":
        return call_llm_groq(context, cfg)
    try:
        import anthropic
    except ImportError:
        print("LLM skipped: `anthropic` not installed (pip install anthropic) - keeping deterministic text")
        return None

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.parse(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            thinking={"type": "adaptive"},
            output_config={"effort": cfg["effort"]},
            # On a safety-classifier decline, re-run server-side on Anthropic's recommended model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
            output_format=LLMPlanText,
        )
    except anthropic.AuthenticationError:
        print("LLM skipped: credentials rejected - keeping deterministic text")
        return None
    except anthropic.RateLimitError:
        print("LLM skipped: rate limited - keeping deterministic text")
        return None
    except anthropic.APIStatusError as e:
        print(f"LLM skipped: API error {e.status_code} - keeping deterministic text")
        return None
    except anthropic.APIConnectionError:
        print("LLM skipped: can't reach the API - keeping deterministic text")
        return None
    except anthropic.AnthropicError as e:
        print(f"LLM skipped: {type(e).__name__}: {str(e)[:120]} - keeping deterministic text")
        return None
    except TypeError as e:
        # The SDK raises a plain TypeError when no credentials are configured at all
        # (O-2 still open). Anything else is a real bug and must surface.
        if "authentication method" not in str(e):
            raise
        print("LLM skipped: no credentials (set ANTHROPIC_API_KEY or run `ant auth login`) "
              "- keeping deterministic text")
        return None

    if response.stop_reason == "refusal":
        print("LLM skipped: request was declined - keeping deterministic text")
        return None
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        print(f"LLM skipped: incomplete output (stop_reason={response.stop_reason}) - keeping deterministic text")
        return None
    return response.parsed_output


def apply_llm_text(plan: list[dict], llm: LLMPlanText, cfg: dict) -> int:
    """Copy validated text fields onto plan items in place. Returns how many items changed.
    Only hypothesis, reason, and expected_impact are ever taken from the LLM."""
    by_id = {item["experiment_id"]: item for item in plan}
    applied = 0
    for out in llm.items:
        problems = validate_llm_item(out, set(by_id), cfg)
        if problems:
            print(f"  dropped LLM text for {out.experiment_id}: {'; '.join(problems)}")
            continue
        item = by_id[out.experiment_id]
        if item["planner"] == "llm":
            print(f"  dropped duplicate LLM text for {out.experiment_id}")
            continue
        item["hypothesis"] = out.hypothesis.strip()
        item["reason"] = out.reason.strip()
        item["expected_impact"] = {"journeys": sorted(set(out.journeys))}
        item["planner"] = "llm"
        applied += 1
    missing = [i["experiment_id"] for i in plan if i["planner"] != "llm"]
    if missing:
        print(f"  kept template text for: {', '.join(missing)}")
    return applied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deterministic", action="store_true", help="never call the LLM")
    args = parser.parse_args()

    for path in (RANKED_PATH, RISKS_PATH, CATALOG_PATH, GRAPH_PATH, PLANNER_CONFIG_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found - run discover.py, audit.py, and rank.py first")

    candidates = json.load(open(RANKED_PATH))
    risks = json.load(open(RISKS_PATH))
    risks_by_id = {r["risk_id"]: r for r in risks}
    catalog = load_yaml(CATALOG_PATH)

    cfg = load_yaml(PLANNER_CONFIG_PATH)
    graph = json.load(open(GRAPH_PATH))
    plan = build_plan(candidates, risks_by_id, catalog, journey_members(graph, cfg["journey_roots"]))

    if not args.deterministic and plan:
        llm = call_llm(llm_context(plan, candidates, risks_by_id, graph, cfg), cfg)
        if llm is not None:
            n = apply_llm_text(plan, llm, cfg)
            model = cfg["groq_model"] if cfg.get("provider") == "groq" else cfg["model"]
            for item in plan:
                if item["planner"] == "llm":
                    item["planner_model"] = model
            print(f"LLM ({model}) wrote text for {n}/{len(plan)} experiments")

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(plan, f, indent=2)

    fault_types = {item["fault_type"] for item in plan}
    planners = "/".join(sorted({i["planner"] for i in plan}))
    print(f"wrote {OUT_PATH}: {len(plan)} experiments, {len(fault_types)} fault type(s): "
          f"{sorted(fault_types)}, planner: {planners}")
    if len(fault_types) < 3:
        print(f"WARNING: only {len(fault_types)} fault type(s) planned - Plan.md targets >= 3 (gate G4)")


if __name__ == "__main__":
    main()
