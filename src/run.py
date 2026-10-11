"""run.py - run ONE approved experiment from data/plan_validated.json. The single way to inject a fault.

Hybrid run path (integration, 11 Oct):
  P3 plan + policy  ->  this file (P4: policy re-check, refusals, STOP, --stop-all, run record file)
  ->  src/chaos/controller.py (P1: inject, confirm, guardian + kill switch, cleanup; P4's watchdog + can-i)
  ->  P2 sampler + run metrics (A, L, t_breach, t_healthy, containment)  ->  runs row in data/runs.db
  ->  data/runs/<exp>-r<n>.json for P5.

Usage (from the repo root, venv active, after the P3 pipeline wrote data/plan_validated.json):
    python -m src.run --list                    # approved experiments + whether each is runnable
    python -m src.run --exp exp-004             # mock: print the Chaos Mesh manifest, no cluster
    python -m src.run --exp exp-004 --preview   # server-side dry run, nothing created
    python -m src.run --exp exp-004 --execute   # real injection (needs a fresh guard baseline)
    python -m src.run --stop-all                # STOP file + delete every chaos object in the namespace

Before --execute: port-forwards + env vars (PROBE_BASE_URL, PROMETHEUS_URL, JAEGER_URL), and
    python -m src.chaos.k8s_monitor --baseline 60      (guard baseline, < 30 min old)

Safety layers (Architecture.md section 5):
  L0 policy.check_item() again here + refusals (probe path, nothing P2 can measure, STOP file)
  L1 guardian (src/chaos/guardian.py): kill switch on 3 bad probes / error ratio / time limit / Ctrl+C
  L2 `duration` inside every timed Chaos Mesh spec + P4's detached watchdog (deletes our object at the deadline)
  L3 --stop-all (no LLM) or `python -m src.chaos.guardian --abort --all`
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import ValidationError

from src.chaos import SETTINGS_PATH
from src.chaos import controller
from src.chaos.executor import CHAOS_KINDS, ExperimentPlan, kubectl
from src.observe import read_runs
from src.policy import check_item
from src.telemetry import load_config

PLAN_PATH = "data/plan_validated.json"
POLICY_PATH = "config/policy.yaml"
EXECUTOR_PATH = "config/executor.yaml"


def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def to_controller_plan(item: dict) -> ExperimentPlan:
    """plan_validated.json item (section 4.3) -> the controller's ExperimentPlan (validated)."""
    params = item.get("params", {})
    fault = item["fault_type"]
    timed = fault != "pod_kill"   # pod_kill is instantaneous; its duration_s is only an observation window
    return ExperimentPlan.model_validate({
        "target_service": item["target"]["service"],
        "fault_type": fault,
        "hypothesis": item["hypothesis"][:500],
        "duration_seconds": params["duration_s"] if timed else 0,
        "latency_ms": params.get("latency_ms", 0) if fault == "network_delay" else 0,
        "loss_percent": params.get("loss_percent", 0) if fault == "packet_loss" else 0,
        "cpu_workers": params.get("cpu_workers", 0) if fault == "cpu_stress" else 0,
    })


def verified_journeys(service: str) -> list[str]:
    """Journeys checked by hand in Jaeger for a service (chaos_settings.json target_journeys, keyed by service)."""
    settings = json.loads(Path(SETTINGS_PATH).read_text(encoding="utf-8"))
    return list(settings.get("target_journeys", {}).get(service, []))


def measured_journeys(item: dict, p2_journeys: list[str]) -> tuple[list[str], list[str]]:
    """Journeys scored as targets (P3's expected ones that P2 probes, plus any verified in Jaeger),
    and expected journeys P2 does not probe (e.g. ads-recs)."""
    expected = item["expected_impact"]["journeys"]
    wanted = set(expected) | set(verified_journeys(item["target"]["service"]))
    measured = [j for j in p2_journeys if j in wanted]
    return measured, [j for j in expected if j not in p2_journeys]


def refusal(item: dict, policy: dict, executor: dict, p2_journeys: list[str]) -> str | None:
    """Why --execute must not run this item (None = runnable)."""
    problems = check_item(item, policy)
    if problems:
        return "policy: " + "; ".join(problems)
    service = item["target"]["service"]
    if service in (executor.get("probe_path_services") or []):
        return (f"{service} carries the probe port-forward; faulting it breaks the measurement path "
                "(config/executor.yaml probe_path_services)")
    measured, _ = measured_journeys(item, p2_journeys)
    if not measured:
        return f"P2 probes none of the expected journeys {item['expected_impact']['journeys']}"
    try:
        to_controller_plan(item)
    except (ValidationError, KeyError) as error:
        return f"plan item does not fit the controller: {error}"
    return None


def controller_settings(item: dict, policy: dict, executor: dict, journeys: list[str]) -> dict:
    """chaos_settings.json (guard thresholds) + this item's target. Deployment names == service names in otel-demo."""
    settings = json.loads(Path(SETTINGS_PATH).read_text(encoding="utf-8"))
    service = item["target"]["service"]
    settings["context"] = executor["context"]
    settings["namespace"] = policy["allowed_namespace"]
    settings["targets"] = {service: service}
    settings["target_journeys"] = {service: journeys}
    return settings


def next_repeat(experiment_id: str) -> int:
    """1 + earlier real (not forced-breach) runs of this experiment in the runs table."""
    try:
        rows = read_runs()
    except Exception:  # noqa: BLE001 - a missing/locked db must not block a run
        rows = []
    return 1 + sum(1 for r in rows if r.get("plan_experiment_id") == experiment_id
                   and r.get("mode") == "execute" and not r.get("forced_breach"))


def log_event(executor: dict, experiment_id: str, message: str) -> None:
    os.makedirs(os.path.dirname(executor["events_log"]) or ".", exist_ok=True)
    with open(executor["events_log"], "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {experiment_id} {message}\n")


def stop_all(policy: dict, executor: dict) -> None:
    """L3 manual stop: block new runs and delete every chaos object in the namespace. No LLM."""
    Path(executor["stop_path"]).parent.mkdir(parents=True, exist_ok=True)
    Path(executor["stop_path"]).touch()
    settings = {"context": executor["context"], "namespace": policy["allowed_namespace"]}
    print(kubectl(settings, ["delete", CHAOS_KINDS, "--all", "--wait=true", "--timeout=30s"]).strip()
          or "no chaos resources found")
    print(f"{executor['stop_path']} created - remove it after checking the app, before the next run.")


def write_run_file(executor: dict, item: dict, report: dict, measured: list[str], uncovered: list[str]) -> str:
    """data/runs/<exp>-r<n>.json: the runs row plus the plan context P5 needs."""
    record = report["run_record"]
    os.makedirs(executor["runs_dir"], exist_ok=True)
    name = f"{item['experiment_id']}-r{record['repeat']}"
    if record.get("forced_breach"):
        name += f"-forced-{report['run_id']}"
    out = os.path.join(executor["runs_dir"], name + ".json")
    payload = {**record, "run_id": report["run_id"],
               "plan": {k: item.get(k) for k in ("experiment_id", "rank", "risk_id", "fault_type", "params",
                                                 "hypothesis", "reason", "expected_impact", "slo",
                                                 "recovery_budget_s", "planner")},
               "measured_journeys": measured, "uncovered_journeys": uncovered,
               "controller_report": record["report_path"]}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run one approved ChaosPilot experiment")
    parser.add_argument("--exp", help="experiment_id from data/plan_validated.json")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--stop-all", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--pre-seconds", type=int, default=30, help="--execute: healthy observation first")
    parser.add_argument("--budget", type=int, default=None,
                        help="--execute: recovery budget (default: the plan item's recovery_budget_s)")
    parser.add_argument("--force-breach", action="store_true", help="--execute: G3 kill-switch test")
    parser.add_argument("--yes", action="store_true", help="--execute: skip the typed confirmation")
    args = parser.parse_args(argv)

    policy, executor = load_yaml(POLICY_PATH), load_yaml(EXECUTOR_PATH)
    if args.stop_all:
        return stop_all(policy, executor)
    if not os.path.exists(PLAN_PATH):
        raise SystemExit(f"{PLAN_PATH} not found - run the pipeline through src.policy first")
    with open(PLAN_PATH, encoding="utf-8") as f:
        plan = json.load(f)
    p2_journeys = list(load_config()["journeys"])

    if args.list or not args.exp:
        for item in plan:
            why = refusal(item, policy, executor, p2_journeys)
            measured, uncovered = measured_journeys(item, p2_journeys)
            print(f"{item['experiment_id']}  {item['fault_type']:<13} {item['target']['service']:<16} "
                  + ("runnable" if not why else "REFUSED: " + why)
                  + (f"  measured={measured} not_probed={uncovered}" if not why else ""))
        return

    item = next((i for i in plan if i["experiment_id"] == args.exp), None)
    if item is None:
        raise SystemExit(f"{args.exp} is not in {PLAN_PATH} (only policy-approved items are)")
    why = refusal(item, policy, executor, p2_journeys)
    if why and args.execute:
        raise SystemExit(f"refusing {args.exp}: {why}")
    if why:
        print(f"note: --execute would be refused: {why}")
    if args.force_breach and not args.execute:
        parser.error("--force-breach only applies to --execute.")

    run_mode = "execute" if args.execute else "preview" if args.preview else "mock"
    measured, uncovered = measured_journeys(item, p2_journeys)
    budget = args.budget if args.budget is not None else item.get("recovery_budget_s", 120)
    if not 10 <= args.pre_seconds <= 300 or not 30 <= budget <= 600:
        parser.error("--pre-seconds must be 10-300 and the budget 30-600 seconds.")

    controller_args = argparse.Namespace(
        mock=run_mode == "mock", preview=run_mode == "preview", execute=run_mode == "execute",
        skip_plan=True, pre_seconds=args.pre_seconds, budget=budget,
        force_breach=args.force_breach, yes=args.yes)
    meta = {"plan_experiment_id": item["experiment_id"],
            "repeat": next_repeat(item["experiment_id"]) if run_mode == "execute" else 1}

    log_event(executor, args.exp, f"{run_mode} start ({item['fault_type']} on {item['target']['service']})")
    report = controller.run_experiment(
        controller_args, plan=to_controller_plan(item),
        settings=controller_settings(item, policy, executor, measured), meta=meta)
    log_event(executor, args.exp, f"{run_mode} {report['status']} run_id={report['run_id']}")

    if report.get("run_record"):
        out = write_run_file(executor, item, report, measured, uncovered)
        log_event(executor, args.exp, f"outcome={report['run_record']['outcome']} record={out}")
        print(f"Run file: {out}")
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
