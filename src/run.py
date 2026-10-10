"""run.py - run ONE approved experiment from data/plan_validated.json and write its
run record (Architecture.md section 4.4) to data/runs/.

Usage (from the repo root, after policy.py):
    python -m src.run --list                  # approved experiments + whether each is runnable
    python -m src.run --exp exp-004           # mock: print the Chaos Mesh manifest, no cluster
    python -m src.run --exp exp-004 --preview # server-side dry run, nothing created
    python -m src.run --exp exp-004 --execute # real injection
    python -m src.run --stop-all              # STOP file + delete every chaos CR in the namespace

The injector, guardrail and watchdog are the vendored chaos_agent_expanded agent
(agents/chaos_agent_expanded). This file only translates our plan contract into its
ExperimentPlan, picks journey probes, and turns its report into our run record.
Rules.md rule 2: this is the only module that injects faults; it holds data/run.lock
from before the baseline until the recovery watch ends.

Safety layers (Architecture.md section 5):
  L0 policy.check_item() again here, plus executor.yaml's probe-path refusal
  L1 guardian: abort after breach_seconds of failing/slow probes
  L2 `duration` inside every timed Chaos Mesh spec, and a detached watchdog that deletes
     our CR at duration + grace even if this process dies
  L3 --stop-all (no LLM involved) or `data/STOP`
"""
import argparse
import json
import os
import statistics
from datetime import datetime
from pathlib import Path

import yaml

from agents.chaos_agent_expanded.core import CHAOS_KINDS, ExperimentPlan, kubectl, now
from agents.chaos_agent_expanded.lifecycle import run_plan
from src.policy import check_item

PLAN_PATH = "data/plan_validated.json"
POLICY_PATH = "config/policy.yaml"
EXECUTOR_PATH = "config/executor.yaml"


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def to_agent_plan(item: dict) -> ExperimentPlan:
    """plan.json item (section 4.3) -> the agent's ExperimentPlan."""
    params = item.get("params", {})
    fault = item["fault_type"]
    return ExperimentPlan.model_validate({
        "target_service": item["target"]["service"],
        "fault_type": fault,
        "hypothesis": item["hypothesis"][:500],
        # pod_kill is instantaneous; its duration_s is the observation window instead
        "duration_seconds": 0 if fault == "pod_kill" else params["duration_s"],
        "latency_ms": params.get("latency_ms", 0) if fault == "network_delay" else 0,
        "cpu_workers": params.get("cpu_workers", 0) if fault == "cpu_stress" else 0,
    })


def pick_probes(item: dict, executor: dict) -> tuple[dict, list[str]]:
    """Probes for the item's expected journeys (+ target-specific ones); returns (probes, uncovered)."""
    probes, uncovered = {}, []
    all_probes = executor["journey_probes"]
    for journey in item["expected_impact"]["journeys"]:
        if journey in all_probes and not all_probes[journey].get("for_targets"):
            probes[journey] = all_probes[journey]
        else:
            uncovered.append(journey)
    service = item["target"]["service"]
    for name, cfg in all_probes.items():
        if service in (cfg.get("for_targets") or []):
            probes[name] = cfg
    return {k: {"url": v["url"], "expect_products": v.get("expect_products", False)}
            for k, v in probes.items()}, uncovered


def refusal(item: dict, policy: dict, executor: dict) -> str | None:
    """Why --execute must not run this item (None = runnable)."""
    problems = check_item(item, policy)
    if problems:
        return "policy: " + "; ".join(problems)
    if item["target"]["service"] in (executor.get("probe_path_services") or []):
        return (f"{item['target']['service']} carries the probe port-forward; faulting it breaks "
                "the measurement path (config/executor.yaml probe_path_services)")
    probes, _ = pick_probes(item, executor)
    if not probes:
        return f"no GET probe for any expected journey {item['expected_impact']['journeys']}"
    return None


def agent_settings(item: dict, policy: dict, executor: dict) -> dict:
    service = item["target"]["service"]
    probes, _ = pick_probes(item, executor)
    return {"context": executor["context"], "namespace": policy["allowed_namespace"],
            "kubectl_path": executor.get("kubectl_path", "kubectl"),
            "targets": {service: service},  # deployment names == service names in otel-demo
            "allowed_faults": list(policy["allowed_fault_types"]),
            "max_duration_s": policy["max_duration_s"],
            "guardian": {**executor["guardian"], "probes": probes}}


def _ts(value):
    return datetime.fromisoformat(value) if value else None


def run_record(item: dict, report: dict, repeat: int, uncovered: list[str]) -> dict:
    """Agent report -> run record (Architecture.md section 4.4). Numbers come only from probe samples."""
    during = [s for s in report["samples"] if s.get("stage") == "during_fault"]
    p95_max = item["slo"]["p95_ms_max"]
    journeys = {}
    for name in (during[0]["journeys"] if during else {}):
        values = [s["journeys"][name] for s in during]
        journeys[name] = {"A": round(sum(v["ok"] for v in values) / len(values), 3),
                          "L": round(sum(v["ok"] and v["latency_ms"] <= p95_max for v in values) / len(values), 3),
                          "samples": len(values)}
    latencies = sorted(s["latency_ms"] for s in during)
    if report.get("status", "").endswith("failed") or not report.get("fault_injected") or not during:
        outcome = "inconclusive"
    elif "t_breach" not in report:
        outcome = "survived"
    elif report.get("t_healthy"):
        outcome = "degraded_recovered"
    else:
        outcome = "not_recovered"
    aborted = report.get("stop_reason") in ("probe_threshold_breach", "manual_stop")
    t_breach, t_clear, t_healthy = _ts(report.get("t_breach")), _ts(report.get("t_clear")), _ts(report.get("t_healthy"))
    return {
        "experiment_id": item["experiment_id"], "repeat": repeat, "run_id": report["run_id"],
        "fault_type": item["fault_type"], "target": item["target"]["service"],
        "t_start": report["started_at"], "t_inject": report.get("fault_confirmed_at"),
        "t_breach": report.get("t_breach"), "t_detect": report.get("t_detect"),
        "t_clear": report.get("t_clear"), "t_healthy": report.get("t_healthy"),
        "aborted": aborted, "abort_reason": report.get("stop_reason") if aborted else None,
        "stop_reason": report.get("stop_reason"), "error": report.get("error"),
        "outcome": outcome,
        # probe-based, not Prometheus: share of failed probe samples, p95 of per-sample slowest-journey latency
        "peak_error_rate": round(sum(not s["ok"] for s in during) / len(during), 3) if during else None,
        "peak_p95_ms": (round(statistics.quantiles(latencies, n=20)[-1], 1) if len(latencies) >= 2
                        else latencies[0] if latencies else None),
        "impact_recovery_s": (t_healthy - t_breach).total_seconds() if t_breach and t_healthy else None,
        "post_fault_recovery_s": max(0.0, (t_healthy - t_clear).total_seconds()) if t_clear and t_healthy else None,
        "journeys": journeys, "uncovered_journeys": uncovered,
        "evidence": {"probe_urls": {k: v["url"] for k, v in report.get("probes", {}).items()},
                     "agent_report": f"data/runs/{report['run_id']}.json", "trace_ids": [], "prom_queries": []},
    }


def log_event(executor: dict, experiment_id: str, message: str) -> None:
    with open(executor["events_log"], "a", encoding="utf-8") as f:
        f.write(f"{now()} {experiment_id} {message}\n")


def stop_all(policy: dict, executor: dict) -> None:
    """L3 manual stop: block new runs and delete every chaos CR in the namespace. No LLM."""
    Path(executor["stop_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(executor["stop_path"]).touch()
    settings = {"context": executor["context"], "namespace": policy["allowed_namespace"],
                "kubectl_path": executor.get("kubectl_path", "kubectl")}
    print(kubectl(settings, ["delete", CHAOS_KINDS, "--all", "--wait=true", "--timeout=30s"]).strip()
          or "no chaos resources found")
    print(f"{executor['stop_path']} created - remove it after checking the app, before the next run. "
          "(No feature flags are injected by run.py, so there are none to reset.)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one approved ChaosPilot experiment")
    parser.add_argument("--exp", help="experiment_id from data/plan_validated.json")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--stop-all", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    policy, executor = load_yaml(POLICY_PATH), load_yaml(EXECUTOR_PATH)
    if args.stop_all:
        return stop_all(policy, executor)
    if not os.path.exists(PLAN_PATH):
        raise SystemExit(f"{PLAN_PATH} not found - run the pipeline through src.policy first")
    plan = json.load(open(PLAN_PATH))

    if args.list or not args.exp:
        for item in plan:
            why = refusal(item, policy, executor)
            probes, uncovered = pick_probes(item, executor)
            print(f"{item['experiment_id']}  {item['fault_type']:<13} {item['target']['service']:<16} "
                  f"{'runnable' if not why else 'REFUSED: ' + why}"
                  + (f"  probes={list(probes)} uncovered={uncovered}" if not why else ""))
        return

    item = next((i for i in plan if i["experiment_id"] == args.exp), None)
    if item is None:
        raise SystemExit(f"{args.exp} is not in {PLAN_PATH} (only policy-approved items are)")
    why = refusal(item, policy, executor)
    if why and args.execute:
        raise SystemExit(f"refusing {args.exp}: {why}")
    if why:
        print(f"note: --execute would be refused: {why}")

    run_mode = "execute" if args.execute else "preview" if args.preview else "mock"
    settings = agent_settings(item, policy, executor)
    _, uncovered = pick_probes(item, executor)
    paths = {"runs_dir": executor["runs_dir"], "ops_dir": os.path.join(executor["runs_dir"], "operations"),
             "lock": executor["lock_path"], "stop": executor["stop_path"]}
    log_event(executor, args.exp, f"{run_mode} start ({item['fault_type']} on {item['target']['service']})")
    report = run_plan(settings, to_agent_plan(item), run_mode, paths,
                      observe_seconds=item["params"]["duration_s"],
                      slo_latency_ms=item["slo"]["p95_ms_max"],
                      recovery_budget_s=item["recovery_budget_s"])
    report["probes"] = settings["guardian"]["probes"]
    log_event(executor, args.exp, f"{run_mode} {report['status']} stop_reason={report.get('stop_reason')}")
    if run_mode != "execute":
        return

    os.makedirs(executor["runs_dir"], exist_ok=True)
    repeat = 1 + sum(1 for f in os.listdir(executor["runs_dir"]) if f.startswith(f"{args.exp}-r"))
    record = run_record(item, report, repeat, uncovered)
    out = os.path.join(executor["runs_dir"], f"{args.exp}-r{repeat}.json")
    with open(out, "w") as f:
        json.dump(record, f, indent=2)
    log_event(executor, args.exp, f"outcome={record['outcome']} record={out}")
    print(f"wrote {out}: outcome={record['outcome']}, t_breach={record['t_breach']}, "
          f"t_healthy={record['t_healthy']}, uncovered journeys={uncovered}")
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
