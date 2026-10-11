"""Service map from Jaeger dependencies (last 1 h) + kubectl workloads. Ported from P1's app_mapper.py.

Run from the repo folder:  python -m src.chaos.app_mapper
Writes data/chaos/app_map.json and data/chaos/app_map.md, and refreshes config/service_snapshot.json for the planner.
"""
import json
import sys
import time
from datetime import datetime, timezone
from src.chaos import DATA_DIR, OVERRIDES_PATH, SNAPSHOT_PATH
from src.chaos.k8s_monitor import kubectl, load_settings, proxy_get

LOOKBACK_MS = 3600 * 1000
JAEGER = "jaeger:16686"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# Jaeger UI is served under /jaeger/ui in this deployment.
def jaeger_dependencies(settings):
    query = f"endTs={int(time.time() * 1000)}&lookback={LOOKBACK_MS}"
    last_error = None

    for prefix in ("jaeger/ui/", ""):
        try:
            body = json.loads(
                proxy_get(
                    settings, JAEGER, f"{prefix}api/dependencies?{query}"
                )
            )
            return body["data"]

        except Exception as error:
            last_error = error

    raise RuntimeError(f"Jaeger dependencies unavailable: {last_error}")


def workload_info(settings):
    workloads = {}

    for kind in ("deployments", "statefulsets"):
        items = json.loads(kubectl(settings, ["get", kind, "-o", "json"]))

        for item in items["items"]:
            name = item["metadata"]["name"]
            desired = item["spec"].get("replicas", 1)
            ready = item.get("status", {}).get("readyReplicas", 0)

            workloads[name] = {
                "kind": kind[:-1],
                "replicas": desired,
                "ready": ready,
                "healthy": ready >= desired,
            }

    services = json.loads(kubectl(settings, ["get", "services", "-o", "json"]))

    for item in services["items"]:
        name = item["metadata"]["name"]
        ports = [port["port"] for port in item["spec"].get("ports", [])]

        if name in workloads:
            workloads[name]["service_ports"] = ports
        else:
            workloads[name] = {"kind": "service_only", "service_ports": ports}

    return workloads


def apply_overrides(edges, overrides):
    removed = {tuple(pair) for pair in overrides.get("remove_edges", [])}
    edges = {k: v for k, v in edges.items() if k not in removed}

    for edge in overrides.get("add_edges", []):
        key = (edge["parent"], edge["child"])
        edges.setdefault(key, {"call_count": edge.get("call_count", 0)})
        edges[key]["manual"] = True

    return edges


def build_map(settings):
    overrides = json.loads(
        OVERRIDES_PATH.read_text(encoding="utf-8")
    )

    edges = {}

    for dep in jaeger_dependencies(settings):
        # Drop self-calls such as payment -> payment.
        if dep["parent"] != dep["child"]:
            edges[(dep["parent"], dep["child"])] = {
                "call_count": dep["callCount"]
            }

    edges = apply_overrides(edges, overrides)
    workloads = workload_info(settings)

    names = set(workloads) | {n for pair in edges for n in pair}
    services = {}

    for name in sorted(names):
        services[name] = {
            "workload": workloads.get(name),
            "upstream": sorted(p for p, c in edges if c == name),
            "downstream": sorted(c for p, c in edges if p == name),
            "note": overrides.get("notes", {}).get(name),
        }

    return {
        "generated_at": utc_now(),
        "namespace": settings["namespace"],
        "lookback": "1h",
        "services": services,
        "edges": [
            {"parent": p, "child": c, **data}
            for (p, c), data in sorted(edges.items())
        ],
    }


def mermaid(app_map):
    lines = ["graph LR"]

    for edge in app_map["edges"]:
        parent = edge["parent"].replace("-", "_")
        child = edge["child"].replace("-", "_")
        lines.append(
            f'    {parent}["{edge["parent"]}"] -->|{edge["call_count"]}| '
            f'{child}["{edge["child"]}"]'
        )

    return "\n".join(lines)


def write_markdown(app_map):
    lines = [
        "# Application map",
        "",
        f"Generated {app_map['generated_at']} "
        f"(namespace {app_map['namespace']}, Jaeger lookback 1h).",
        "",
        "```mermaid",
        mermaid(app_map),
        "```",
        "",
        "| Service | Kind | Ready | Upstream | Downstream |",
        "|---|---|---|---|---|",
    ]

    for name, info in app_map["services"].items():
        work = info["workload"] or {}
        ready = (
            f"{work['ready']}/{work['replicas']}"
            if "replicas" in work else "-"
        )
        lines.append(
            f"| {name} | {work.get('kind', 'trace-only')} | {ready} | "
            f"{', '.join(info['upstream']) or '-'} | "
            f"{', '.join(info['downstream']) or '-'} |"
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "app_map.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# Rewrite the planner snapshot, using real facts in the descriptions.
def write_snapshot(settings, app_map):
    old = json.loads(
        SNAPSHOT_PATH.read_text(encoding="utf-8")
    )
    journeys = {s["id"]: s["journey"] for s in old["services"]}
    descriptions = {s["id"]: s["description"] for s in old["services"]}

    services = []

    for service_id, deployment in settings["targets"].items():
        info = app_map["services"].get(deployment, {})
        work = info.get("workload") or {}
        callers = ", ".join(info.get("upstream", [])) or "none observed"
        callees = ", ".join(info.get("downstream", [])) or "none observed"

        description = (
            descriptions.get(service_id, f"Service {deployment}.").split(
                " Called by"
            )[0]
            + f" Called by: {callers}. Calls: {callees}."
        )

        services.append({
            "id": service_id,
            "description": description,
            "replicas": work.get("replicas", 1),
            "journey": journeys.get(service_id, "unknown"),
        })

    snapshot = {
        "data_source": "cluster_snapshot",
        "services": services,
        "allowed_faults": old["allowed_faults"],
    }

    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, indent=2) + "\n", encoding="utf-8"
    )

    return snapshot


def summarize(app_map):
    return {
        "services": len(app_map["services"]),
        "edges": len(app_map["edges"]),
        "generated_at": app_map["generated_at"],
    }


def main():
    settings = load_settings()
    app_map = build_map(settings)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "app_map.json").write_text(
        json.dumps(app_map, indent=2) + "\n", encoding="utf-8"
    )
    write_markdown(app_map)
    snapshot = write_snapshot(settings, app_map)

    print(f"Services: {len(app_map['services'])}, "
          f"edges: {len(app_map['edges'])}")

    for edge in app_map["edges"]:
        print(f"  {edge['parent']} -> {edge['child']} ({edge['call_count']})")

    print("\nSnapshot for planner:")
    print(json.dumps(snapshot, indent=2))
    print("\nSaved data/chaos/app_map.json, data/chaos/app_map.md, config/service_snapshot.json.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        main()
    except Exception as error:
        raise SystemExit(f"Mapper failed: {error}")
