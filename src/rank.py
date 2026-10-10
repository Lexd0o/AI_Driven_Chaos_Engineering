"""rank.py - static risk ranking: build data/ranked_candidates.json from
data/graph.json, data/risks.json, and config/graph_overrides.yaml (entry_points).

Usage (run from the repo root, after discover.py and audit.py):
    python -m src.rank

Deterministic, no LLM - this is what plan.py turns into experiment hypotheses.
Formula from Architecture.md section 9:
    risk_score = criticality(service) x centrality(service) x (1 + risk_flags(service))
    centrality = max(normalised fan-in, gateway share) + "on checkout path" bonus   (capped at 1)

Fan-in alone scores shared dependencies but gives an entry point zero: frontend-proxy
has no in-cluster callers (its callers are browsers and the load generator, outside the
graph), so it scored 0 despite fronting all traffic. Gateway share fixes that (D-015):
the fraction of the app users can no longer reach from the entry point if this service
is down. max() so a service is never counted twice - a shared dependency scores via
fan-in, a gateway via gateway share.

Ties on risk_score are broken by (1) blast radius - how many services depend on
this one directly or transitively, i.e. how much breaks if it fails - then
(2) inbound call volume seen in Jaeger traces (edge "calls"; 0 for override or
fixture edges), then (3) number of high-severity risks, then (4) name, so the
order is fully deterministic and defensible (it matters for the ranked-vs-random
benchmark).

Reads data/graph.json and data/risks.json (fixture or live, no code change either way)
plus entry_points from config/graph_overrides.yaml.
"""
import json
import os
from collections import Counter, deque

import yaml

GRAPH_PATH = "data/graph.json"
RISKS_PATH = "data/risks.json"
OUT_PATH = "data/ranked_candidates.json"
OVERRIDES_PATH = "config/graph_overrides.yaml"

CHECKOUT_PATH_BONUS = 0.3  # flat centrality bonus for services on checkout's dependency tree
SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}
TOP_RISKS_PER_SERVICE = 5  # audit.py runs 5 checks per node - keep all of them visible to plan.py,
                           # otherwise ties on fixture data silently drop risks before plan.py can
                           # use them to diversify fault types (e.g. network_delay was never surfaced)


def checkout_reachable(graph: dict) -> set[str]:
    """Services reachable from checkout by following outgoing edges, plus checkout itself.
    Approximates "on the buying path" - services whose failure can stop a purchase,
    not just a page load."""
    adjacency: dict[str, list[str]] = {}
    for e in graph["edges"]:
        adjacency.setdefault(e["from"], []).append(e["to"])

    seen = {"checkout"}
    queue = deque(["checkout"])
    while queue:
        node = queue.popleft()
        for nxt in adjacency.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def blast_radius(graph: dict) -> dict[str, int]:
    """For each service, how many other services reach it by following call edges -
    everything upstream that can be hurt when it fails."""
    callers: dict[str, list[str]] = {}
    for e in graph["edges"]:
        callers.setdefault(e["to"], []).append(e["from"])

    radius = {}
    for n in graph["nodes"]:
        seen = {n["name"]}
        queue = deque([n["name"]])
        while queue:
            for caller in callers.get(queue.popleft(), []):
                if caller not in seen:
                    seen.add(caller)
                    queue.append(caller)
        radius[n["name"]] = len(seen) - 1
    return radius


def reachable_from(entries: list[str], adjacency: dict[str, list[str]], removed: str | None = None) -> set[str]:
    """Services users can reach from the entry points by following calls, skipping `removed`."""
    seen = {e for e in entries if e != removed}
    queue = deque(seen)
    while queue:
        for nxt in adjacency.get(queue.popleft(), []):
            if nxt != removed and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def gateway_share(graph: dict, entries: list[str]) -> dict[str, float]:
    """For each service: share of the user-reachable app that becomes unreachable if it is
    down (itself included). 1.0 for a single entry point; ~0 for a leaf. Services users
    don't reach synchronously at all (e.g. Kafka consumers) get 0."""
    adjacency: dict[str, list[str]] = {}
    for e in graph["edges"]:
        adjacency.setdefault(e["from"], []).append(e["to"])
    full = reachable_from(entries, adjacency)
    if not full:
        return {n["name"]: 0.0 for n in graph["nodes"]}
    return {n["name"]: (len(full) - len(reachable_from(entries, adjacency, n["name"]))) / len(full)
            for n in graph["nodes"]}


def centrality_of(name: str, fan_in: Counter, max_fan_in: int, on_checkout_path: set[str],
                  gateway: dict[str, float]) -> float:
    normalised_fan_in = (fan_in.get(name, 0) / max_fan_in) if max_fan_in else 0.0
    bonus = CHECKOUT_PATH_BONUS if name in on_checkout_path else 0.0
    return min(1.0, max(normalised_fan_in, gateway.get(name, 0.0)) + bonus)


def build_ranking(graph: dict, risks: list[dict], entry_points: list[str]) -> list[dict]:
    fan_in = Counter(e["to"] for e in graph["edges"])
    max_fan_in = max(fan_in.values(), default=1)
    on_checkout_path = checkout_reachable(graph)
    radius = blast_radius(graph)
    gateway = gateway_share(graph, entry_points)
    inbound_calls = Counter()
    for e in graph["edges"]:
        inbound_calls[e["to"]] += e.get("calls", 0)

    risks_by_service: dict[str, list[dict]] = {}
    for r in risks:
        risks_by_service.setdefault(r["service"], []).append(r)

    candidates = []
    for node in graph["nodes"]:
        name = node["name"]
        centrality = centrality_of(name, fan_in, max_fan_in, on_checkout_path, gateway)
        service_risks = risks_by_service.get(name, [])
        risk_score = node["criticality"] * centrality * (1 + len(service_risks))

        top_risks = sorted(service_risks, key=lambda r: SEVERITY_ORDER.get(r["severity"], 0),
                           reverse=True)[:TOP_RISKS_PER_SERVICE]
        on_path = name in on_checkout_path

        candidates.append({
            "service": name,
            "risk_score": round(risk_score, 4),
            "criticality": node["criticality"],
            "centrality": round(centrality, 4),
            "fan_in": fan_in.get(name, 0),
            "on_checkout_path": on_path,
            "risk_flags": len(service_risks),
            "blast_radius": radius[name],
            "inbound_calls": inbound_calls[name],
            "gateway_share": round(gateway[name], 4),
            "high_risks": sum(1 for r in service_risks if r["severity"] == "high"),
            "top_risks": [r["risk_id"] for r in top_risks],
            "reason": (
                f"{name}: criticality={node['criticality']}, fan_in={fan_in.get(name, 0)}"
                f"{', on checkout path' if on_path else ''}, {len(service_risks)} structural risk flag(s), "
                f"{radius[name]} service(s) depend on it"
                + (f", gateway to {gateway[name]:.0%} of the app" if gateway[name] >= 0.25 else "")
            ),
        })

    candidates.sort(key=lambda c: (-c["risk_score"], -c["blast_radius"], -c["inbound_calls"],
                                   -c["high_risks"], c["service"]))
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
    return candidates


def main() -> None:
    for path in (GRAPH_PATH, RISKS_PATH):
        if not os.path.exists(path):
            raise SystemExit(f"{path} not found - run `python -m src.discover` and `python -m src.audit` first")

    graph = json.load(open(GRAPH_PATH))
    risks = json.load(open(RISKS_PATH))
    overrides = yaml.safe_load(open(OVERRIDES_PATH)) if os.path.exists(OVERRIDES_PATH) else {}
    names = {n["name"] for n in graph["nodes"]}
    entry_points = [e for e in (overrides or {}).get("entry_points") or [] if e in names]
    if not entry_points:
        print(f"WARNING: no entry_points in {OVERRIDES_PATH} match a graph node - gateway share is 0 "
              f"for every service, so entry points score 0 again")
    ranking = build_ranking(graph, risks, entry_points)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(ranking, f, indent=2)

    top = ranking[0]
    print(f"wrote {OUT_PATH}: {len(ranking)} candidates ranked")
    print(f"  #1 {top['service']} (score={top['risk_score']}) - {top['reason']}")


if __name__ == "__main__":
    main()
