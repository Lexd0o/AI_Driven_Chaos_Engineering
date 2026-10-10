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


def check_item(item: dict, policy: dict) -> list[str]:
    """Return policy violations for one plan item (empty list = approved)."""
    problems = []
    target = item["target"]

    if target["namespace"] != policy["allowed_namespace"]:
        problems.append(f"namespace '{target['namespace']}' is not the allowed namespace "
                         f"'{policy['allowed_namespace']}'")

    if target["service"] in (policy.get("forbidden_targets") or []):
        problems.append(f"service '{target['service']}' is on the forbidden_targets list")

    duration = item.get("params", {}).get("duration_s")
    if duration is None or duration > policy["max_duration_s"]:
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
        latency = item.get("params", {}).get("latency_ms", 0)
        if latency > spec.get("max_latency_ms", 0):
            problems.append(f"latency_ms={latency} exceeds max_latency_ms={spec.get('max_latency_ms')}")
    if fault_type == "packet_loss":
        loss = item.get("params", {}).get("loss_percent", 0)
        if loss > spec.get("max_loss_percent", 0):
            problems.append(f"loss_percent={loss} exceeds max_loss_percent={spec.get('max_loss_percent')}")
    if fault_type == "cpu_stress":
        workers = item.get("params", {}).get("cpu_workers", 0)
        if workers > spec.get("max_cpu_workers", 0):
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
