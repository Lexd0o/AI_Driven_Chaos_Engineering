"""audit.py - static audit: build data/risks.json (predicted risks) from data/graph.json.

Usage (run from the repo root, after discover.py has produced data/graph.json):
    python -m src.audit

This stage makes no cluster calls and runs no traces. Every check reads only the
structural facts discover.py already put on each node (replicas, has_pdb,
has_readiness, has_liveness, has_limits, has_hpa, stateful, criticality) plus
fan-in computed from the edges. Findings are predictions to be confirmed or
refuted by real experiments later (status stays "predicted" here).

Output follows the frozen contract in Architecture.md section 4.2.
"""
import json
import os
from collections import Counter

GRAPH_PATH = "data/graph.json"
OUT_PATH = "data/risks.json"

FAN_IN_HIGH = 2  # a service with >= this many distinct callers is a "shared dependency"


def fan_in_counts(edges: list[dict]) -> Counter:
    return Counter(e["to"] for e in edges)


def severity_from_criticality(criticality: float, bump: int = 0) -> str:
    """Map criticality (+ extra risk factors) to a severity band.

    Cut points mirror the bands already used in config/criticality.yaml
    (critical=0.4, high=0.3, medium=0.2, low=0.1) so severity stays consistent
    with that single source of truth instead of inventing a second scale.
    """
    score = criticality + bump * 0.1
    if score >= 0.4:
        return "high"
    if score >= 0.2:
        return "medium"
    return "low"


def check_single_replica(node: dict, fan_in: int) -> dict | None:
    if node["replicas"] > 1 or node["has_pdb"]:
        return None
    bump = (1 if fan_in >= FAN_IN_HIGH else 0) + (1 if node["stateful"] else 0)
    return {
        "check": "stateful_single_replica" if node["stateful"] else "single_replica_no_pdb",
        "severity": severity_from_criticality(node["criticality"], bump),
        "evidence": f"replicas={node['replicas']}, has_pdb={node['has_pdb']}, "
                    f"stateful={node['stateful']}, fan_in={fan_in}",
        "suggested_experiment": "pod_kill",
    }


def check_no_readiness(node: dict, fan_in: int) -> dict | None:
    if node["has_readiness"]:
        return None
    return {
        "check": "no_readiness_probe",
        "severity": severity_from_criticality(node["criticality"]),
        "evidence": "has_readiness=False - a restarted pod may receive traffic before it's ready",
        "suggested_experiment": "pod_kill",
    }


def check_no_liveness(node: dict, fan_in: int) -> dict | None:
    if node["has_liveness"]:
        return None
    return {
        "check": "no_liveness_probe",
        "severity": severity_from_criticality(node["criticality"]),
        "evidence": "has_liveness=False - a hung (not crashed) process won't be auto-restarted",
        "suggested_experiment": "cpu_stress",
    }


def check_no_limits(node: dict, fan_in: int) -> dict | None:
    if node["has_limits"]:
        return None
    return {
        "check": "no_resource_limits",
        "severity": severity_from_criticality(node["criticality"]),
        "evidence": "has_limits=False - exposed to noisy-neighbour CPU/memory pressure",
        "suggested_experiment": "cpu_stress",
    }


def check_no_hpa_shared(node: dict, fan_in: int) -> dict | None:
    if node["has_hpa"] or fan_in < FAN_IN_HIGH:
        return None
    return {
        "check": "no_hpa_shared_dependency",
        "severity": severity_from_criticality(node["criticality"]),
        "evidence": f"has_hpa=False, {fan_in} callers - can't absorb a traffic or latency spike",
        "suggested_experiment": "network_delay",
    }


CHECKS = [check_single_replica, check_no_readiness, check_no_liveness, check_no_limits, check_no_hpa_shared]


def build_risks(graph: dict) -> list[dict]:
    fan_in = fan_in_counts(graph["edges"])
    nodes = sorted(graph["nodes"], key=lambda n: (-n["criticality"], n["name"]))

    risks = []
    for node in nodes:
        for check in CHECKS:
            finding = check(node, fan_in.get(node["name"], 0))
            if finding is None:
                continue
            risks.append({
                "risk_id": f"R-{node['name']}-{finding['check']}",
                "service": node["name"],
                "status": "predicted",
                **finding,
            })
    return risks


def main() -> None:
    if not os.path.exists(GRAPH_PATH):
        raise SystemExit(f"{GRAPH_PATH} not found - run `python -m src.discover` first")

    graph = json.load(open(GRAPH_PATH))
    risks = build_risks(graph)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(risks, f, indent=2)

    by_severity = Counter(r["severity"] for r in risks)
    print(f"wrote {OUT_PATH}: {len(risks)} risks across {len(graph['nodes'])} services {dict(by_severity)}")


if __name__ == "__main__":
    main()
