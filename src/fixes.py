"""fixes.py - fix-as-code: turn each finding in data/risks.json into a Kubernetes YAML
patch under reports/fixes/, plus reports/fixes/index.json listing every patch and the
exact command to apply it.

Usage (run from the repo root, after audit.py):
    python -m src.fixes

Decisions.md D-007: fixes are delivered as patches, never auto-applied. Two kinds of file:
  - strategic-merge patches for the existing Deployment (replicas, probes, limits):
        kubectl -n <ns> patch deployment <svc> --patch-file <file>
  - new objects (PodDisruptionBudget, HorizontalPodAutoscaler):
        kubectl apply -f <file>
A finding with no safe generic patch (stateful single replica, portless container,
HPA on a data store) gets a "manual" note in index.json instead of a guessed patch.

Findings with status "refuted" (set later, once experiments run) are skipped.
Templates and thresholds live in config/fixes.yaml (Rules.md rule 16).
"""
import json
import os

import yaml

RISKS_PATH = "data/risks.json"
GRAPH_PATH = "data/graph.json"
CONFIG_PATH = "config/fixes.yaml"
OUT_DIR = "reports/fixes"
INDEX_PATH = f"{OUT_DIR}/index.json"  # forward slashes: the apply commands must work in bash on Windows too
NAMESPACE = os.getenv("NAMESPACE", "otel-demo")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ------------------------------------------------------------------ building blocks

def container_patch(service: str, fields: dict) -> dict:
    """Strategic-merge patch that edits the container named after the service
    (container name == workload name for every app service, verified live)."""
    return {"spec": {"template": {"spec": {"containers": [{"name": service, **fields}]}}}}


def patch_file(risk: dict, suffix: str, doc: dict) -> dict:
    path = f"{OUT_DIR}/{risk['risk_id']}{suffix}.yaml"
    command = f"kubectl -n {NAMESPACE} patch deployment {risk['service']} --patch-file {path}"
    return {"path": path, "apply": command, "doc": doc}


def object_file(risk: dict, suffix: str, doc: dict) -> dict:
    path = f"{OUT_DIR}/{risk['risk_id']}{suffix}.yaml"
    return {"path": path, "apply": f"kubectl apply -f {path}", "doc": doc}


def pdb(service: str, cfg: dict) -> dict:
    return {
        "apiVersion": "policy/v1",
        "kind": "PodDisruptionBudget",
        "metadata": {"name": f"{service}-pdb", "namespace": NAMESPACE},
        "spec": {"minAvailable": 1, "selector": {"matchLabels": {cfg["selector_label"]: service}}},
    }


def probe_spec(service: str, cfg: dict) -> dict | str:
    """This service's health-check entry from config/fixes.yaml (grpc/http/exec/tcp or "none")."""
    return (cfg.get("probes") or {}).get(service, cfg["default_probe"])


def probe_handler(spec: dict) -> tuple[dict, str]:
    """Kubernetes probe handler for one config entry, plus a short description of it."""
    if "grpc" in spec:
        return {"grpc": {"port": spec["grpc"]}}, f"gRPC health check on port {spec['grpc']}"
    if "http" in spec:
        h = spec["http"]
        return ({"httpGet": {"path": h["path"], "port": h["port"]}},
                f"HTTP GET {h['path']} on port '{h['port']}'")
    if "exec" in spec:
        return {"exec": {"command": spec["exec"]}}, f"command `{' '.join(spec['exec'])}`"
    return {"tcpSocket": {"port": spec["tcp"]}}, f"tcpSocket on port '{spec['tcp']}'"


def result(summary: str, files: list[dict] | None = None, manual: str | None = None) -> dict:
    return {"summary": summary, "files": files or [], "manual": manual}


# ------------------------------------------------------------------ one function per check

def fix_single_replica_no_pdb(risk: dict, node: dict, cfg: dict) -> dict:
    n = cfg["target_replicas"]
    return result(
        f"scale {risk['service']} to {n} replicas and add a PDB (minAvailable=1)",
        [patch_file(risk, "", {"spec": {"replicas": n}}),
         object_file(risk, ".pdb", pdb(risk["service"], cfg))],
    )


def fix_stateful_single_replica(risk: dict, node: dict, cfg: dict) -> dict:
    # Adding replicas to a non-clustered data store splits its data, and a PDB on one
    # replica only blocks voluntary evictions (node drains) - it does nothing for a crash
    # or pod_kill. No generic patch is honest here.
    return result(
        f"make {risk['service']} highly available",
        manual=(f"{risk['service']} is a stateful single replica. Raising replicas without "
                f"replication would split its data; real HA needs a replicated setup (e.g. a "
                f"clustered Helm chart or operator). Not auto-generated."),
    )


def fix_probe(risk: dict, node: dict, cfg: dict, field: str, key: str) -> dict:
    service = risk["service"]
    spec = probe_spec(service, cfg)
    if spec == "none":
        return result(
            f"add a {field} to {service}",
            manual=(f"{service} exposes no container port and no health endpoint (Kafka consumer), "
                    f"so no probe can be generated - needs an app-level health check."),
        )
    handler, described = probe_handler(spec)
    return result(
        f"add a {field} ({described}) to {service}",
        [patch_file(risk, "", container_patch(service, {field: {**handler, **cfg[key]}}))],
    )


def fix_no_readiness_probe(risk: dict, node: dict, cfg: dict) -> dict:
    return fix_probe(risk, node, cfg, "readinessProbe", "readiness_probe")


def fix_no_liveness_probe(risk: dict, node: dict, cfg: dict) -> dict:
    # Uses each service's real health endpoint where one exists (config/fixes.yaml `probes`).
    # Those endpoints catch a dead or frozen server, not one stuck request handler.
    return fix_probe(risk, node, cfg, "livenessProbe", "liveness_probe")


def fix_no_resource_limits(risk: dict, node: dict, cfg: dict) -> dict:
    limits = cfg["default_limits"]
    return result(
        f"set resource limits on {risk['service']} ({limits['cpu']} CPU, {limits['memory']})",
        [patch_file(risk, "", container_patch(risk["service"], {"resources": {"limits": limits}}))],
    )


def fix_no_hpa_shared_dependency(risk: dict, node: dict, cfg: dict) -> dict:
    service = risk["service"]
    if node.get("stateful"):
        return result(
            f"absorb load spikes on {service}",
            manual=f"{service} is a data store; horizontal autoscaling doesn't apply without replication.",
        )
    h = cfg["hpa"]
    return result(
        f"autoscale {service} {h['min_replicas']}-{h['max_replicas']} replicas at "
        f"{h['cpu_utilization_pct']}% CPU (needs metrics-server)",
        [object_file(risk, ".hpa", {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": f"{service}-hpa", "namespace": NAMESPACE},
            "spec": {
                "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": service},
                "minReplicas": h["min_replicas"],
                "maxReplicas": h["max_replicas"],
                "metrics": [{"type": "Resource", "resource": {
                    "name": "cpu",
                    "target": {"type": "Utilization", "averageUtilization": h["cpu_utilization_pct"]},
                }}],
            },
        })],
    )


FIXERS = {
    "single_replica_no_pdb": fix_single_replica_no_pdb,
    "stateful_single_replica": fix_stateful_single_replica,
    "no_readiness_probe": fix_no_readiness_probe,
    "no_liveness_probe": fix_no_liveness_probe,
    "no_resource_limits": fix_no_resource_limits,
    "no_hpa_shared_dependency": fix_no_hpa_shared_dependency,
}


# ------------------------------------------------------------------ main

def header(risk: dict, f: dict) -> str:
    return (f"# Fix for {risk['risk_id']} ({risk['severity']}, status={risk['status']})\n"
            f"# Evidence: {risk['evidence']}\n"
            f"# Apply:    {f['apply']}\n"
            f"# Not applied automatically (Decisions.md D-007).\n")


def remove_previous_output() -> None:
    """Delete the patches the last run wrote, so a finding that disappeared (or got
    refuted) doesn't leave a stale patch behind."""
    if not os.path.exists(INDEX_PATH):
        return
    for entry in json.load(open(INDEX_PATH)):
        for f in entry["files"]:
            if os.path.exists(f["path"]):
                os.remove(f["path"])


def build_fixes(risks: list[dict], nodes: dict, cfg: dict) -> list[dict]:
    entries = []
    for risk in sorted(risks, key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), r["risk_id"])):
        if risk["status"] == "refuted":
            continue
        fixer = FIXERS.get(risk["check"])
        if fixer is None:
            print(f"WARNING: no fixer for check '{risk['check']}' ({risk['risk_id']})")
            continue
        fix = fixer(risk, nodes.get(risk["service"], {}), cfg)
        entries.append({
            "risk_id": risk["risk_id"], "service": risk["service"], "check": risk["check"],
            "severity": risk["severity"], "status": risk["status"], **fix,
        })
    return entries


def main() -> None:
    for path in (RISKS_PATH, GRAPH_PATH, CONFIG_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found - run discover.py and audit.py first")

    risks = json.load(open(RISKS_PATH))
    nodes = {n["name"]: n for n in json.load(open(GRAPH_PATH))["nodes"]}
    cfg = load_yaml(CONFIG_PATH)

    os.makedirs(OUT_DIR, exist_ok=True)
    remove_previous_output()
    entries = build_fixes(risks, nodes, cfg)

    by_id = {r["risk_id"]: r for r in risks}
    for entry in entries:
        for f in entry["files"]:
            with open(f["path"], "w") as out:
                out.write(header(by_id[entry["risk_id"]], f))
                yaml.safe_dump(f.pop("doc"), out, sort_keys=False)

    with open(INDEX_PATH, "w") as f:
        json.dump(entries, f, indent=2)

    n_files = sum(len(e["files"]) for e in entries)
    n_manual = sum(1 for e in entries if e["manual"])
    print(f"wrote {INDEX_PATH}: {len(entries)} findings -> {n_files} patch file(s), {n_manual} manual")


if __name__ == "__main__":
    main()
