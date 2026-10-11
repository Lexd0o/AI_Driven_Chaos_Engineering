"""policy.py - validate data/plan.json against config/policy.yaml (the L0
scope layer in Architecture.md section 5): build data/plan_validated.json.

Usage (run from the repo root, after plan.py):
    python -m src.policy

Architecture.md section 1's core principle: the planner proposes, this
validates, run.py (not yet built) acts. An item that fails any check is
dropped, not clamped - run.py should never receive something this file
didn't approve. Works identically whether plan.json came from the
deterministic planner or the LLM, once that's wired up.
"""
import json
import os

import yaml

PLAN_PATH = "data/plan.json"
POLICY_PATH = "config/policy.yaml"
OUT_PATH = "data/plan_validated.json"


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def check_item(item: dict, policy: dict) -> list[str]:
    """Return policy violations for one plan item (empty list = approved)."""
    if "forbidden_targets" not in policy:
        # fail closed: a policy file that lost this key must not silently allow every target
        raise SystemExit(f"{POLICY_PATH} has no forbidden_targets key - refusing to validate without it")

    problems = []
    target = item.get("target")
    if not isinstance(target, dict) or "service" not in target:
        return ["missing target.service"]
    if "fault_type" not in item:
        return ["missing fault_type"]

    if target.get("namespace") != policy["allowed_namespace"]:
        problems.append(f"namespace '{target.get('namespace')}' is not the allowed namespace "
                         f"'{policy['allowed_namespace']}'")

    if target["service"] in (policy["forbidden_targets"] or []):
        problems.append(f"service '{target['service']}' is on the forbidden_targets list")

    params = item.get("params") or {}
    duration = params.get("duration_s")
    if not is_number(duration) or duration <= 0:
        problems.append(f"duration_s={duration!r} must be a number > 0")
    elif duration > policy["max_duration_s"]:
        problems.append(f"duration_s={duration} exceeds max_duration_s={policy['max_duration_s']}")

    allowed = policy.get("allowed_fault_types") or {}
    fault_type = item["fault_type"]
    if fault_type not in allowed:
        problems.append(f"fault_type '{fault_type}' is not in allowed_fault_types")
        return problems  # nothing below is checkable without a known spec

    spec = allowed[fault_type]
    if item.get("mechanism") != spec.get("mechanism"):
        problems.append(f"mechanism '{item.get('mechanism')}' does not match policy's "
                         f"'{spec.get('mechanism')}' for fault_type '{fault_type}'")

    if fault_type == "network_delay":
        latency = params.get("latency_ms")
        if not is_number(latency) or latency <= 0:
            problems.append(f"latency_ms={latency!r} must be a number > 0")
        elif latency > spec.get("max_latency_ms", 0):
            problems.append(f"latency_ms={latency} exceeds max_latency_ms={spec.get('max_latency_ms')}")
    if fault_type == "cpu_stress":
        workers = params.get("cpu_workers")
        if not is_number(workers) or workers != int(workers) or workers <= 0:
            problems.append(f"cpu_workers={workers!r} must be an integer > 0")
        elif workers > spec.get("max_cpu_workers", 0):
            problems.append(f"cpu_workers={workers} exceeds max_cpu_workers={spec.get('max_cpu_workers')}")

    return problems


def validate_plan(plan: list[dict], policy: dict) -> tuple[list[dict], list[dict]]:
    approved, rejected = [], []
    for item in plan:
        problems = check_item(item, policy)
        if problems:
            rejected.append({**item, "policy_rejected": problems})
        else:
            approved.append(item)
    return approved, rejected


def main() -> None:
    for path in (PLAN_PATH, POLICY_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found - run discover.py, audit.py, rank.py, and plan.py first")

    plan = json.load(open(PLAN_PATH))
    policy = load_yaml(POLICY_PATH)

    approved, rejected = validate_plan(plan, policy)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(approved, f, indent=2)

    print(f"wrote {OUT_PATH}: {len(approved)} approved, {len(rejected)} rejected")
    for r in rejected:
        print(f"  REJECTED {r['experiment_id']} ({r['target']['service']}): {r['policy_rejected']}")


if __name__ == "__main__":
    main()
