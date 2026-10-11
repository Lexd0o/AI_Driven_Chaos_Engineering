"""discover.py - build data/graph.json (services + who-calls-whom).

Usage (run from the repo root):
    python -m src.discover --fixtures     # offline: uses fixtures/graph.json (no cluster needed)
    python -m src.discover                # live: k8s API + Jaeger traces

Output follows the frozen contract in Architecture.md section 4.1.
Edge direction: "from" calls "to"  (checkout -> payment).
"""
import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone

import yaml

NS = os.getenv("NAMESPACE", "otel-demo")
# Query Jaeger directly (kubectl -n otel-demo port-forward svc/jaeger 16686:16686), not through
# frontend-proxy:8080 - large trace responses pushed the proxy to 88.6/90Mi, nearly OOMKilling it.
JAEGER = os.getenv("JAEGER_URL", "http://localhost:16686/jaeger/ui")
MIN_COUNT = 3  # ignore edges seen fewer times than this (noise)
# Jaeger is in-memory with a 600Mi limit; 200 traces x 1h per service pushed it into an OOMKill
# (which wipes every trace). Small samples are enough to see every edge >= MIN_COUNT times.
TRACE_LIMIT = 50
TRACE_LOOKBACK = "15m"
PAUSE_S = 0.5  # between per-service queries, so Jaeger isn't hit back to back

OVERRIDES_PATH = "config/graph_overrides.yaml"
CRITICALITY_PATH = "config/criticality.yaml"
FIXTURE_PATH = "fixtures/graph.json"
OUT_PATH = "data/graph.json"
MERMAID_PATH = "data/graph.mmd"

STATEFUL_HINTS = ("valkey", "redis", "kafka", "postgres", "postgresql", "opensearch")


def load_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------- nodes (k8s)

def service_name(obj) -> str:
    labels = obj.metadata.labels or {}
    # Verified live (chart 0.42.3): app.kubernetes.io/component is "all-in-one" for jaeger and
    # "server" for prometheus, which would dodge ignore_nodes/forbidden_targets. name == metadata.name.
    return labels.get("app.kubernetes.io/name") or obj.metadata.name


def load_nodes_live() -> list[dict]:
    from kubernetes import client, config  # imported here so --fixtures needs no k8s library

    config.load_kube_config()
    apps = client.AppsV1Api()
    policy = client.PolicyV1Api()
    autoscaling = client.AutoscalingV2Api()

    workloads = [(w, "Deployment") for w in apps.list_namespaced_deployment(NS).items]
    workloads += [(w, "StatefulSet") for w in apps.list_namespaced_stateful_set(NS).items]
    pdbs = policy.list_namespaced_pod_disruption_budget(NS).items
    hpas = autoscaling.list_namespaced_horizontal_pod_autoscaler(NS).items
    crit = load_yaml(CRITICALITY_PATH)

    nodes = []
    for w, wkind in workloads:
        name = service_name(w)
        pod_labels = w.spec.template.metadata.labels or {}
        containers = w.spec.template.spec.containers or []

        has_pdb = any(
            (p.spec.selector and p.spec.selector.match_labels
             and all(pod_labels.get(k) == v for k, v in p.spec.selector.match_labels.items()))
            for p in pdbs
        )
        has_hpa = any(h.spec.scale_target_ref.name == w.metadata.name for h in hpas)

        nodes.append({
            "name": name,
            "kind": "service",
            "replicas": w.spec.replicas if w.spec.replicas is not None else 1,
            "stateful": wkind == "StatefulSet" or any(h in name for h in STATEFUL_HINTS),
            "has_readiness": any(c.readiness_probe for c in containers),
            "has_liveness": any(c.liveness_probe for c in containers),
            "has_limits": any(c.resources and c.resources.limits for c in containers),
            "has_pdb": has_pdb,
            "has_hpa": has_hpa,
            "criticality": crit.get("services", {}).get(name, crit.get("default", 0.1)),
        })
    return nodes


# --------------------------------------------------------------- edges (Jaeger)

def protocol_of(span: dict) -> str:
    tags = {t["key"]: t["value"] for t in span.get("tags", [])}
    if "rpc.system" in tags:
        return str(tags["rpc.system"])  # e.g. grpc
    if "http.method" in tags or "http.request.method" in tags:
        return "http"
    return "unknown"  # [verify] tag names for your chart version


def edges_from_jaeger(names: set[str]) -> Counter:
    import requests

    try:
        services = requests.get(f"{JAEGER}/api/services", timeout=10).json()["data"] or []
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"WARNING: Jaeger /api/services failed ({type(e).__name__}) - no edges can be discovered")
        return Counter()
    if not services:
        print("WARNING: Jaeger returned 0 services - is the load generator running?")
    counts: Counter = Counter()
    seen_traces: set[str] = set()  # a trace touching N services comes back from N queries - count it once
    # Only query k8s workloads: every edge a->b shows up in a's traces, and edges with a
    # non-workload end are dropped anyway. Querying frontend-web (browser spans) hung the
    # in-memory Jaeger past its liveness probe, and a restart wipes all stored traces.
    # ignore_nodes (flagd, jaeger, ...) are dropped later too, so don't load Jaeger by querying them.
    ignored = set(load_yaml(OVERRIDES_PATH).get("ignore_nodes") or [])
    for svc in sorted(s for s in services if s in names and s not in ignored):
        time.sleep(PAUSE_S)
        try:
            r = requests.get(
                f"{JAEGER}/api/traces",
                params={"service": svc, "limit": TRACE_LIMIT, "lookback": TRACE_LOOKBACK},
                timeout=30,
            )
            r.json()  # force the full body now, inside the try
        except (requests.RequestException, ValueError) as e:
            print(f"WARNING: Jaeger traces for '{svc}' failed ({type(e).__name__}) - edges may be missing")
            continue
        if r.status_code != 200:
            print(f"WARNING: Jaeger traces for '{svc}' returned HTTP {r.status_code} - edges may be missing")
            continue
        for trace in r.json().get("data") or []:
            if trace["traceID"] in seen_traces:
                continue
            seen_traces.add(trace["traceID"])
            proc = {pid: p["serviceName"] for pid, p in trace["processes"].items()}
            spans = {s["spanID"]: s for s in trace["spans"]}
            for s in trace["spans"]:
                for ref in s.get("references", []):
                    if ref["refType"] != "CHILD_OF":
                        continue
                    parent = spans.get(ref["spanID"])
                    if not parent:
                        continue
                    a, b = proc[parent["processID"]], proc[s["processID"]]
                    if a != b:
                        counts[(a, b, protocol_of(s))] += 1
    return counts


# ------------------------------------------------------------------- write guard

def refuse_empty_live_graph(jaeger_edges: int, live: bool, allow_no_traces: bool) -> str | None:
    """Reason to refuse writing graph.json, or None. A live run with zero Jaeger edges would
    overwrite a good graph with override edges only (Jaeger unreachable, restarted, no traffic)."""
    if live and jaeger_edges == 0 and not allow_no_traces:
        return ("live run found 0 edges in Jaeger (unreachable, restarted and lost its in-memory "
                "traces, or no traffic yet) - not overwriting the existing graph. Check the "
                "port-forward and the load generator, or pass --allow-no-traces to write anyway.")
    return None


# ------------------------------------------------------------------- overrides

def apply_overrides(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    ov = load_yaml(OVERRIDES_PATH)

    ignore = set(ov.get("ignore_nodes") or [])
    nodes = [n for n in nodes if n["name"] not in ignore]
    edges = [e for e in edges if e["from"] not in ignore and e["to"] not in ignore]

    for e in ov.get("remove_edges") or []:
        edges = [x for x in edges if not (x["from"] == e["from"] and x["to"] == e["to"])]

    have = {(e["from"], e["to"]) for e in edges}
    for e in ov.get("add_edges") or []:
        if (e["from"], e["to"]) not in have:
            edges.append({"from": e["from"], "to": e["to"],
                          "protocol": e.get("protocol", "unknown"), "source": "override"})
            have.add((e["from"], e["to"]))

    by_name = {n["name"]: n for n in nodes}
    for svc, fixes in (ov.get("node_fixes") or {}).items():
        if svc in by_name:
            by_name[svc].update(fixes)

    return nodes, edges


# ------------------------------------------------------------------ validation

def validate(graph: dict) -> list[str]:
    problems = []
    names = {n["name"] for n in graph["nodes"]}
    stateful = {n["name"] for n in graph["nodes"] if n["stateful"]}
    fan_in = Counter(e["to"] for e in graph["edges"])
    fan_out = Counter(e["from"] for e in graph["edges"])

    for e in graph["edges"]:
        for end in (e["from"], e["to"]):
            if end not in names:
                problems.append(f"edge {e['from']}->{e['to']}: unknown node '{end}'")
        if e["from"] == e["to"]:
            problems.append(f"self-loop: {e['from']}->{e['to']} (likely a typo in graph_overrides.yaml)")
        if e["from"] in stateful:
            problems.append(f"stateful node as caller: {e['from']}->{e['to']} "
                             f"(data stores shouldn't initiate calls - check the override)")

    for name in names:
        if fan_in.get(name, 0) == 0 and fan_out.get(name, 0) == 0:
            problems.append(f"isolated node: '{name}' has no edges in or out - likely a missed dependency")

    # Edges we expect, taken from the demo's compose files (see graph_overrides.yaml)
    expected = load_yaml(OVERRIDES_PATH).get("expected_edges") or []
    have = {(e["from"], e["to"]) for e in graph["edges"]}
    for e in expected:
        if (e["from"], e["to"]) not in have:
            problems.append(f"expected edge missing: {e['from']}->{e['to']}")

    if fan_in.get("cart", 0) < 2:
        problems.append(f"cart fan-in is {fan_in.get('cart', 0)} (expected >= 2)")

    # A node missing from criticality.yaml silently gets the `default` weight, which
    # feeds straight into audit.py's severity scoring - flag it instead of staying silent.
    crit = load_yaml(CRITICALITY_PATH)
    explicit = set((crit.get("services") or {}).keys())
    for name in sorted(names - explicit):
        problems.append(f"'{name}' has no explicit criticality.yaml entry, using default={crit.get('default', 0.1)}")

    return problems


# -------------------------------------------------------------------- summary

def summarize(graph: dict) -> str:
    """Topology summary derived from the graph itself (not hand-written), so the
    'hubs / shared dependencies / leaf services' claims in dependency mapping
    are reproducible facts rather than something read off the JSON by eye."""
    fan_in = Counter(e["to"] for e in graph["edges"])
    fan_out = Counter(e["from"] for e in graph["edges"])
    names = [n["name"] for n in graph["nodes"]]

    hubs = sorted((n for n in names if fan_out.get(n, 0) >= 2), key=lambda n: -fan_out[n])
    shared = sorted((n for n in names if fan_in.get(n, 0) >= 2), key=lambda n: -fan_in[n])
    leaves = sorted(n for n in names if fan_out.get(n, 0) == 0)

    lines = [
        "topology summary:",
        f"  hubs (fan-out >= 2):       {', '.join(f'{n}({fan_out[n]})' for n in hubs) or 'none'}",
        f"  shared deps (fan-in >= 2): {', '.join(f'{n}({fan_in[n]})' for n in shared) or 'none'}",
        f"  leaf services (fan-out=0): {', '.join(leaves) or 'none'}",
    ]
    return "\n".join(lines)


# -------------------------------------------------------------------- render

def to_node_id(name: str) -> str:
    return name.replace("-", "_")  # mermaid node IDs can't contain hyphens


def export_mermaid(graph: dict) -> str:
    """Render graph.json as a Mermaid flowchart for Architecture.md / the demo video.
    Data stores get a cylinder shape; override-added edges are dashed so the diagram
    keeps the same observed-vs-added distinction the JSON already carries in "source"."""
    lines = ["flowchart LR"]
    for n in graph["nodes"]:
        nid = to_node_id(n["name"])
        lines.append(f'  {nid}[("{n["name"]}")]' if n["stateful"] else f'  {nid}["{n["name"]}"]')
    for e in graph["edges"]:
        a, b = to_node_id(e["from"]), to_node_id(e["to"])
        arrow = "-.->"  if e["source"] == "override" else "-->"
        label = e["protocol"] + (" override" if e["source"] == "override" else "")
        lines.append(f'  {a} {arrow}|{label}| {b}')
    return "\n".join(lines)


# ------------------------------------------------------------------------ main

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", action="store_true", help="use fixtures/graph.json, no cluster")
    parser.add_argument("--allow-no-traces", action="store_true",
                        help="live: write the graph even if Jaeger yielded no edges")
    args = parser.parse_args()

    if args.fixtures:
        base = json.load(open(FIXTURE_PATH))
        nodes, edges = base["nodes"], base["edges"]
    else:
        nodes = load_nodes_live()
        names = {n["name"] for n in nodes}
        edges = []
        for (a, b, proto), n in edges_from_jaeger(names).items():
            if n < MIN_COUNT:
                continue
            if a in names and b in names:
                # calls = times this edge appeared in the sampled traces: a relative traffic
                # signal (rank.py's tie-breaker), not an absolute request rate
                edges.append({"from": a, "to": b, "protocol": proto, "source": "jaeger", "calls": n})
            else:
                print(f"skipped edge {a}->{b} (not a k8s workload; add a node_fix/override if needed)")
        reason = refuse_empty_live_graph(len(edges), True, args.allow_no_traces)
        if reason:
            raise SystemExit(f"ERROR: {reason}")

    nodes, edges = apply_overrides(nodes, edges)
    graph = {"generated_at": datetime.now(timezone.utc).isoformat(), "nodes": nodes, "edges": edges}

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(graph, f, indent=2)
    with open(MERMAID_PATH, "w") as f:
        f.write(export_mermaid(graph))

    by_source = Counter(e["source"] for e in edges)
    print(f"wrote {OUT_PATH}: {len(nodes)} nodes, {len(edges)} edges {dict(by_source)}")
    print(f"wrote {MERMAID_PATH}")
    print(summarize(graph))
    for p in validate(graph):
        print("WARNING:", p)


if __name__ == "__main__":
    main()