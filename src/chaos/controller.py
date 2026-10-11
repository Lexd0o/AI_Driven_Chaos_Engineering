"""One chaos run: plan -> validate -> inject -> guard -> clean up -> measure -> report.
Ported from P1's controller.py; Stage 2 (D-025) wires in the guardian and P2's sampler and run metrics.

Run from the repo folder:
  python -m src.chaos.controller --mock --skip-plan      manifest only, no cluster
  python -m src.chaos.controller --preview --skip-plan   server-side dry run, nothing created
  python -m src.chaos.controller --execute --skip-plan   REAL fault, watched by the guardian

Options for --execute:
  --pre-seconds N   healthy observation before injecting (default 30)
  --budget N        recovery budget in seconds (default 120, Architecture.md 4.3)
  --force-breach    lower the guardian thresholds so the kill switch fires (G3 test). The report says so.
  --yes             skip the typed confirmation

Before --execute (PowerShell, repo folder, venv active):
  python -m src.chaos.k8s_monitor --baseline 60    guard baseline, must be less than 30 min old
  frontend-proxy and prometheus port-forwards running, env vars set (D-026), so P2's probes can reach the shop

One live run, in order. The experiment id is the run id everywhere (reports, samples, runs row):
  checks (target ready, no chaos objects, guard baseline, no other sampler running) -> confirm -> P2 sampler thread starts
  -> pre-observation + shop health gate -> submit -> t_inject (Chaos Mesh confirms injection)
  -> guardian.guard (kill switch on breach / max time / Ctrl+C / error) -> t_clear (kill switch verified)
  -> cleanup confirmed -> keep sampling budget + 30 s -> collect_run_metrics + collect_evidence
  -> runs row + JSON report.

Recovery in scores = Architecture.md 7, computed by P2 from the samples (D-024). The guardian's own recovery
check only decides when cleanup is done; it is kept under report["guard"] and never enters a score.

Reports: data/chaos/reports/<run_id>.json   Lock: data/chaos/.controller.lock   Samples + runs: data/runs.db
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from src.chaos import DATA_DIR, EXPERIMENT_PATH, ROOT, SETTINGS_PATH
from src.chaos import guardian
from src.chaos.executor import CHAOS_KINDS, ExperimentPlan, build_manifest, chaos_kind, kubectl
from src.evidence import collect_evidence
from src.observe import connect, latest_sli, run_sampler, save_run
from src.runmetrics import collect_run_metrics, make_slos

CONFIRM_WORD = "reslab-experiment"        # typed by hand before a real injection (P1's design)
MAX_BASELINE_AGE_S = 30 * 60              # Experiments.md: baseline fresh < 30 min
MIN_BASELINE_SUCCESS = 0.9                # P1's pre-flight rule
RECOVERY_BUDGET_S = 120                   # Architecture.md 4.3
SUSTAIN_S = 30                            # healthy window length used by P2's recovery detection
GATE_WINDOW_S = 20                        # shop health gate looks at P2's samples of the last 20 s
GATE_RETRIES = 2                          # re-checks after a failed gate, each on a fresh window (so at most 3 checks)
GATE_RETRY_WAIT_S = GATE_WINDOW_S         # wait one full window, so a re-check never reuses the blip's samples
OTHER_SAMPLER_WINDOW_S = 15               # any journey row this recent means another sampler is running
SAMPLER_STOP_WAIT_S = 20                  # a probe tick can take up to ~10 s when requests time out
STOP_PATH = ROOT / "data" / "STOP"         # P4's manual stop file (python -m src.run --stop-all creates it)
WATCHDOG_GRACE_S = 60                     # watchdog deletes our object this long after the guard's own time limit
FORCE_BREACH = {"latency_floor_ms": 1, "latency_multiplier": 0}   # P1's definition: every probe counts as slow
GUARD_NOTE = (
    "guard.recovery_seconds is the guardian's cleanup check (D-024). "
    "Scores use run_metrics (Architecture.md 7), never this number."
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def now():
    return time.time()


def sleep(seconds):
    time.sleep(seconds)


def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8")
    )


def run_planner():
    result = subprocess.run(
        [
            sys.executable,
            "-m", "src.chaos.planner",
        ],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
        },
    )

    if result.stdout:
        print(result.stdout)

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "Planner failed."
        )


def get_resource(settings, resource, name):
    output = kubectl(
        settings,
        [
            "get", resource, name,
            "--ignore-not-found",
            "-o", "json",
        ],
    )

    return json.loads(output) if output.strip() else None


def deployment_ready(deployment):
    if not deployment:
        return False

    spec = deployment["spec"]
    status = deployment.get("status", {})
    desired = spec.get("replicas", 1)
    generation = deployment["metadata"].get("generation", 0)

    return (
        desired > 0
        and status.get("observedGeneration", 0) >= generation
        and status.get("updatedReplicas", 0) == desired
        and status.get("readyReplicas", 0) >= desired
        and status.get("unavailableReplicas", 0) == 0
    )


def condition_true(resource, condition_name):
    conditions = resource.get("status", {}).get("conditions", [])

    return any(
        condition.get("type") == condition_name
        and condition.get("status") == "True"
        for condition in conditions
    )


def wait_for(settings, resource, name, predicate, timeout):
    deadline = time.monotonic() + timeout
    latest = None

    while time.monotonic() < deadline:
        latest = get_resource(settings, resource, name)

        if latest is not None and predicate(latest):
            return latest

        time.sleep(1)

    status = latest.get("status", {}) if latest else "not found"

    raise TimeoutError(
        f"Timed out waiting for {resource}/{name}. "
        f"Last status: {json.dumps(status)}"
    )


# ------------------------------------------------------------------ Stage 2: pre-flight
# Which of P2's journeys depend on the target. Set by hand in config/chaos_settings.json (target_journeys).
def journeys_for(settings, target_service):
    journeys = (settings.get("target_journeys") or {}).get(target_service)

    if not journeys:
        raise ValueError(
            f"No target_journeys entry for '{target_service}' in config/chaos_settings.json."
        )

    known = make_slos()
    unknown = [j for j in journeys if j not in known]

    if unknown:
        raise ValueError(
            f"Unknown journeys {unknown}; known journeys: {sorted(known)}."
        )

    return list(journeys)


# Reasons the guardian baseline cannot be used. Empty list = fine.
def baseline_problems(baseline, error_ratio_max, now_dt=None):
    problems = []
    now_dt = now_dt or datetime.now(timezone.utc)

    try:
        age = (
            now_dt - datetime.fromisoformat(baseline["captured_at"])
        ).total_seconds()

        if age > MAX_BASELINE_AGE_S:
            problems.append(
                f"it is {age / 60:.0f} min old (limit {MAX_BASELINE_AGE_S // 60})"
            )

    except (KeyError, TypeError, ValueError):
        problems.append("captured_at is missing or unreadable")

    if not isinstance(baseline.get("probe_p95_ms"), (int, float)):
        problems.append("probe_p95_ms is missing")

    rate = baseline.get("probe_success_rate")

    if rate is None or rate < MIN_BASELINE_SUCCESS:
        problems.append(
            f"probe success rate {rate} is below {MIN_BASELINE_SUCCESS}"
        )

    ratio = (baseline.get("prometheus") or {}).get("error_ratio")

    if ratio is not None and ratio > error_ratio_max:
        problems.append(
            f"error ratio {ratio:.3f} is already above {error_ratio_max}"
        )

    return problems


def load_guard_baseline(error_ratio_max):
    path = DATA_DIR / "baseline.json"

    if not path.is_file():
        raise RuntimeError(
            "No guard baseline. Run first: "
            "python -m src.chaos.k8s_monitor --baseline 60"
        )

    baseline = read_json(path)
    problems = baseline_problems(baseline, error_ratio_max)

    if problems:
        raise RuntimeError(
            "Guard baseline not usable (" + "; ".join(problems) + "). "
            "Re-run: python -m src.chaos.k8s_monitor --baseline 60"
        )

    return baseline


# Is the shop healthy right now, judged by P2's own samples? Checked just before injecting.
def shop_gate():
    journeys = {}
    problems = []

    for name, slo in make_slos().items():
        sli = latest_sli(name, GATE_WINDOW_S)
        journeys[name] = sli

        if sli["n"] < slo["min_n"]:
            problems.append(
                f"{name}: only {sli['n']} samples in the last {GATE_WINDOW_S} s"
            )
        elif sli["success_rate"] < slo["min_success"]:
            problems.append(
                f"{name}: success {sli['success_rate']:.2f} "
                f"is below {slo['min_success']}"
            )
        elif sli["p95_ms"] is not None and sli["p95_ms"] > slo["max_p95_ms"]:
            problems.append(
                f"{name}: p95 {sli['p95_ms']:.0f} ms "
                f"is above {slo['max_p95_ms']} ms"
            )

    return {
        "ok": not problems,
        "window_s": GATE_WINDOW_S,
        "journeys": journeys,
        "problems": problems,
    }


# Journey rows written by ANY sampler in the last window_s seconds (data/runs.db).
def recent_sample_count(window_s):
    con = connect()

    try:
        return con.execute(
            "SELECT COUNT(*) FROM samples WHERE journey IS NOT NULL AND ts >= ?",
            (now() - window_s,),
        ).fetchone()[0]
    finally:
        con.close()


# A second sampler doubles the load on the shop and races our probe users' carts.
# It only sees samplers that write to the same database file (the default data/runs.db).
def check_no_other_sampler():
    rows = recent_sample_count(OTHER_SAMPLER_WINDOW_S)

    if rows:
        raise RuntimeError(
            f"Another sampler is writing to the samples database ({rows} rows in the "
            f"last {OTHER_SAMPLER_WINDOW_S} s). Stop it first (for example a window "
            f"running python -m src.observe). If a run just finished, wait "
            f"{OTHER_SAMPLER_WINDOW_S} s and try again. Nothing was injected."
        )


# ------------------------------------------------------------------ P4's safety pieces (hybrid run path)
def check_stop_file():
    if STOP_PATH.exists():
        raise RuntimeError(
            f"Manual stop is active ({STOP_PATH}). Check the app, delete the file, then retry. "
            "Nothing was injected."
        )


def check_can_delete(settings, resource_type):
    # Never create what we may not be able to delete (P4's agent: can-i delete before injecting).
    try:
        answer = kubectl(settings, ["auth", "can-i", "delete", resource_type]).strip()
    except RuntimeError as error:
        answer = str(error)

    if answer != "yes":
        raise RuntimeError(
            f"kubectl cannot delete {resource_type} in this namespace ({answer}); refusing to inject."
        )


# A separate process that deletes our object at the deadline even if this program dies (P4's watchdog, layer L2).
def arm_watchdog(settings, resource_type, resource_name, run_id, deadline_epoch):
    from agents.chaos_agent_expanded.watchdog import start_watchdog

    ops_dir = DATA_DIR / "operations"
    ops_dir.mkdir(parents=True, exist_ok=True)
    cluster = {"context": settings["context"], "namespace": settings["namespace"]}
    return str(start_watchdog(cluster, resource_type, resource_name, run_id, deadline_epoch, ops_dir))


def disarm_watchdog(path):
    if path:
        from agents.chaos_agent_expanded.watchdog import cancel_watchdog

        cancel_watchdog(path)


def confirm(args):
    if args.yes:
        return

    typed = input(
        f"This injects a REAL fault into the cluster. "
        f"Type {CONFIRM_WORD} to continue: "
    )

    if typed.strip() != CONFIRM_WORD:
        raise RuntimeError("Not confirmed. Nothing was injected.")


# ------------------------------------------------------------------ Stage 2: P2 sampler thread
# Ctrl+C never reaches a background thread, so it is stopped with an event instead.
def start_sampler(run_id):
    # Create the tables first, here, so the thread and this thread never race to set up a fresh database.
    connect().close()

    stop = threading.Event()
    errors = []

    def work():
        try:
            run_sampler(run_id, None, None, stop)
        except Exception as error:  # noqa: BLE001 - reported, never silent
            errors.append(str(error))

    thread = threading.Thread(target=work, name="p2-sampler", daemon=True)
    thread.start()

    return {"thread": thread, "stop": stop, "errors": errors}


def stop_sampler(sampler):
    if sampler is None:
        return

    sampler["stop"].set()
    sampler["thread"].join(timeout=SAMPLER_STOP_WAIT_S)


def check_sampler(sampler):
    if not sampler["thread"].is_alive():
        raise RuntimeError(
            "Sampler stopped unexpectedly: "
            + ("; ".join(sampler["errors"]) or "no error recorded")
        )


def wait_until(epoch, sampler):
    while now() < epoch:
        check_sampler(sampler)
        sleep(1)


# The shop has a low background error rate (a lone fast 503 about every 100 s on any journey, seen
# live on 11 Oct, cause not found). One such blip in the 20 s window gives success 0.89 and used to refuse
# the run 3 times in a row. So: when the check fails, look again on a completely fresh window, up to
# GATE_RETRIES more times. A shop that is really unhealthy fails every window and is still refused.
def shop_gate_with_retry(sampler, retries=GATE_RETRIES, wait_s=GATE_RETRY_WAIT_S):
    gate = shop_gate()
    earlier = []

    for _ in range(retries):
        if gate["ok"]:
            break
        earlier.append(gate["problems"])
        print(f"Shop check failed ({'; '.join(gate['problems'])}); looking again in {wait_s} s.")
        wait_until(now() + wait_s, sampler)
        gate = shop_gate()

    gate["attempts"] = len(earlier) + 1
    gate["earlier_problems"] = earlier
    return gate


# ------------------------------------------------------------------ Stage 2: run record
# One `runs` row (Architecture.md 4.4). Numbers come from P2's metrics; the guardian only adds t_detect and the abort reason.
def build_run_record(report, plan, metrics, guard_result, evidence, report_path, meta=None):
    aborted = guard_result["outcome"] != "completed"
    reason = None

    if aborted:
        reason = guard_result["breach"]["reason"] or guard_result["outcome"]

    evidence = evidence or {}
    outcome = metrics["outcome"]
    quality = metrics["quality"]

    if report["forced_breach"]:
        # The fault was cut short on purpose, so the run cannot be judged (inconclusive stays inconclusive).
        # P2's computed numbers stay untouched in report["run_metrics"].
        outcome = "inconclusive"
        quality = "inconclusive"

    meta = meta or {}

    return {
        "experiment_id": report["run_id"],
        "plan_experiment_id": meta.get("plan_experiment_id"),   # P3's exp-00N when started by src.run
        "repeat": meta.get("repeat", 1),
        "mode": report["mode"],
        "target": plan.target_service,
        "fault_type": plan.fault_type,
        "t_start": report["t_start_epoch"],
        "t_inject": metrics["t_inject"],
        "t_breach": metrics["t_breach"],
        "t_detect": guard_result.get("detected_epoch"),
        "t_clear": metrics["t_clear"],
        "t_healthy": metrics["t_healthy"],
        "aborted": aborted,
        "abort_reason": reason,
        "outcome": outcome,
        "quality": quality,
        "peak_error_rate": metrics["peak_error_rate"],
        "peak_p95_ms": metrics["peak_p95_ms"],
        "impact_recovery_s": metrics["impact_recovery_s"],
        "post_fault_recovery_s": metrics["post_fault_recovery_s"],
        "containment_C": metrics["containment_C"],
        "forced_breach": report["forced_breach"],
        "journeys": metrics["journeys"],
        "evidence": {
            "trace_ids": evidence.get("trace_ids", []),
            "prom_queries": evidence.get("prom_queries", []),
            "quality": evidence.get("quality"),
        },
        "report_path": str(report_path),
    }


def fmt(value):
    return "n/a" if value is None else f"{value:.2f}"


def print_summary(report):
    guard_result = report.get("guard")
    metrics = report.get("run_metrics")

    print("\n=== Run summary ===")
    print(f"run id: {report['run_id']}   status: {report['status']}")

    if report["forced_breach"]:
        print(
            "FORCED BREACH run: guardian thresholds were lowered. "
            "It tests the stop path; it is not a resilience result."
        )

    if guard_result:
        print(
            f"guardian: {guard_result['outcome']} "
            f"(breach reason: {guard_result['breach']['reason']}); "
            f"kill switch verified gone: "
            f"{guard_result['kill_switch']['verified_gone']}"
        )

    if metrics:
        print(
            f"run metrics: outcome={metrics['outcome']} "
            f"quality={metrics['quality']}"
        )

        for name, values in metrics["journeys"].items():
            print(
                f"  {name}: A={fmt(values['A'])} "
                f"L={fmt(values['L'])} n={values['n']}"
            )

        print(
            f"  containment C={fmt(metrics['containment_C'])}  "
            f"impact recovery={fmt(metrics['impact_recovery_s'])} s"
        )

        for issue in metrics["issues"]:
            print(f"  issue: {issue}")

    elif report["fault_injected"]:
        print("run metrics: none (the run stopped before measurement)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plan, validate, and run a bounded, guarded chaos experiment."
    )

    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--preview", action="store_true")
    modes.add_argument(
        "--execute",
        action="store_true",
        help="REAL fault, watched by the guardian.",
    )

    parser.add_argument(
        "--skip-plan",
        action="store_true",
        help="Use config/experiment.json (required: the Groq planner is retired; normal runs use python -m src.run).",
    )

    parser.add_argument(
        "--pre-seconds",
        type=int,
        default=30,
        help="--execute: healthy observation before injecting.",
    )

    parser.add_argument(
        "--budget",
        type=int,
        default=RECOVERY_BUDGET_S,
        help="--execute: recovery budget in seconds.",
    )

    parser.add_argument(
        "--force-breach",
        action="store_true",
        help="--execute: lower guardian thresholds so the kill switch fires.",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="--execute: skip the typed confirmation.",
    )

    args = parser.parse_args(argv)

    if not 10 <= args.pre_seconds <= 300:
        parser.error("--pre-seconds must be between 10 and 300.")

    if not 30 <= args.budget <= 600:
        parser.error("--budget must be between 30 and 600.")

    if args.force_breach and not args.execute:
        parser.error("--force-breach only applies to --execute.")

    report = run_experiment(args)

    if report["status"] == "failed":
        raise SystemExit(1)


# One run. args = parsed CLI options (mock/preview/execute, skip_plan, pre_seconds, budget, force_breach, yes).
# src.run passes plan + settings + meta (P3's plan item); the CLI passes none and uses config/experiment.json.
def run_experiment(args, plan=None, settings=None, meta=None):
    mode = (
        "execute" if args.execute
        else "preview" if args.preview
        else "mock"
    )

    run_id = f"run-{uuid4().hex[:12]}"
    resource_name = f"reslab-{run_id}"

    reports_dir = DATA_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{run_id}.json"

    # Prevent concurrent controllers from sharing experiment.json.
    lock_path = DATA_DIR / ".controller.lock"

    try:
        lock_file = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        raise SystemExit(
            "Another controller may be running. "
            "Inspect .controller.lock before starting another run."
        )

    with lock_file:
        lock_file.write(run_id)

    report = {
        "run_id": run_id,
        "plan_experiment_id": (meta or {}).get("plan_experiment_id"),
        "mode": mode,
        "status": "starting",
        "started_at": utc_now(),
        "events": [],
        "fault_injected": False,
        "cleanup_confirmed": None,
        "deployment_ready_after": None,
        "forced_breach": args.force_breach,
        "t_start_epoch": None,
        "t_inject_epoch": None,
        "t_clear_epoch": None,
        "guard": None,
        "run_metrics": None,
        "evidence": None,
        "run_record": None,
    }

    deployment_name = None
    watchdog_path = None
    resource_type = None
    submission_attempted = False
    failed = False
    sampler = None

    def save_report():
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def event(stage):
        report["status"] = stage
        report["events"].append({
            "stage": stage,
            "observed_at": utc_now(),
        })
        save_report()
        print(f"\nSTAGE: {stage}")

    # Confirms the chaos object is gone and the target is ready again.
    # Safe to call twice: the second call does nothing.
    def cleanup():
        nonlocal failed

        if report["cleanup_confirmed"] is not None:
            return

        event("cleanup")

        try:
            current = get_resource(
                settings, resource_type, resource_name
            )

            if current is not None:
                owner = current["metadata"].get(
                    "labels", {}
                ).get("reslab-run-id")

                if owner != run_id:
                    raise RuntimeError(
                        "Ownership mismatch; refusing cleanup."
                    )

                kubectl(
                    settings,
                    [
                        "delete", resource_type, resource_name,
                        "--wait=true", "--timeout=20s",
                    ],
                )

            remaining = get_resource(
                settings, resource_type, resource_name
            )

            if remaining is not None:
                raise RuntimeError(
                    "Experiment resource still exists."
                )

            report["cleanup_confirmed"] = True
            report["cleanup_observed_at"] = utc_now()
            report["cleanup_observed_epoch"] = now()

        except Exception as error:
            failed = True
            report["cleanup_confirmed"] = False
            report["cleanup_error"] = str(error)
            print(f"Cleanup failed: {error}")
            print(
                "THE FAULT MAY STILL BE ACTIVE. Run: "
                "python -m src.chaos.guardian --abort --all"
            )

        if report["cleanup_confirmed"]:
            disarm_watchdog(watchdog_path)

            try:
                wait_for(
                    settings,
                    "deployment",
                    deployment_name,
                    deployment_ready,
                    timeout=120,
                )

                report["deployment_ready_after"] = True
                report["deployment_ready_observed_at"] = utc_now()

            except Exception as error:
                failed = True
                report["deployment_ready_after"] = False
                report["deployment_check_error"] = str(error)

    try:
        if plan is not None:
            event("using_plan_item")

        else:
            if not args.skip_plan:
                # P1's Groq planner chose targets and faults itself; retired at integration (D-005, D-012).
                raise RuntimeError(
                    "The LLM planner is retired: plans come from P3's pipeline. "
                    "Use python -m src.run --exp <id>, or --skip-plan with config/experiment.json."
                )

            event("using_saved_plan")

            plan = ExperimentPlan.model_validate_json(
                EXPERIMENT_PATH.read_text(
                    encoding="utf-8"
                )
            )

        if settings is None:
            settings = read_json(SETTINGS_PATH)

        if plan.target_service not in settings["targets"]:
            raise ValueError("Target service is not approved.")

        deployment_name = settings["targets"][plan.target_service]
        report["plan"] = plan.model_dump()

        if mode == "mock":
            labels = {"app": f"mock-{plan.target_service}"}

        else:
            if "REPLACE_WITH" in settings["context"] + deployment_name:
                raise ValueError("Replace the settings placeholders.")

            event("checking_deployment")

            deployment = get_resource(
                settings, "deployment", deployment_name
            )

            if not deployment_ready(deployment):
                raise RuntimeError("Target deployment is not ready.")

            selector = deployment["spec"]["selector"]

            if selector.get("matchExpressions"):
                raise ValueError(
                    "This version supports matchLabels selectors only."
                )

            labels = selector.get("matchLabels", {})

            if not labels:
                raise ValueError("Deployment has no usable pod labels.")

        manifest = build_manifest(
            plan, settings["namespace"], labels
        )

        # Each run gets its own resource name and ownership label.
        manifest["metadata"]["name"] = resource_name
        manifest["metadata"]["labels"] = {
            "reslab-run-id": run_id
        }

        kind, resource_type = chaos_kind(plan.fault_type)

        report["manifest"] = manifest
        report["resource_name"] = resource_name
        report["resource_type"] = resource_type

        print("\nManifest:")
        print(json.dumps(manifest, indent=2))

        if mode == "mock":
            event("mock_completed")
            print("No cluster connection or failure injection.")

        else:
            event("server_validation")

            kubectl(
                settings,
                ["create", "--dry-run=server", "-f", "-", "-o", "json"],
                manifest,
            )

            if mode == "preview":
                event("preview_completed")
                print("Server validation passed. No experiment created.")

            else:
                existing = json.loads(
                    kubectl(
                        settings,
                        ["get", CHAOS_KINDS, "-o", "json"],
                    )
                )

                if existing["items"]:
                    raise RuntimeError(
                        "Existing chaos resources found. Inspect them first."
                    )

                # ---- Pre-flight: nothing is injected until all of this passes ----
                check_stop_file()
                check_can_delete(settings, resource_type)

                event("checking_guard_baseline")

                safety = dict(settings.get("safety", {}))
                max_seconds = safety.pop("max_experiment_seconds", 90)
                limits = {**guardian.DEFAULT_THRESHOLDS, **safety}

                guard_baseline = load_guard_baseline(
                    limits["error_ratio_max"]
                )
                report["guard_baseline"] = guard_baseline

                targets = journeys_for(settings, plan.target_service)
                report["target_journeys"] = targets

                thresholds = dict(safety)

                if args.force_breach:
                    thresholds.update(FORCE_BREACH)
                    report["forced_breach_thresholds"] = FORCE_BREACH
                    print(
                        "\nFORCE BREACH: guardian thresholds lowered "
                        f"({FORCE_BREACH}). The kill switch should fire "
                        "within a few probes."
                    )

                event("checking_other_samplers")
                check_no_other_sampler()

                confirm(args)

                event("starting_sampler")
                sampler = start_sampler(run_id)
                report["t_start_epoch"] = now()

                event("pre_observation")
                wait_until(report["t_start_epoch"] + args.pre_seconds, sampler)

                event("checking_shop_health")
                gate = shop_gate_with_retry(sampler)
                report["shop_gate"] = gate

                if not gate["ok"]:
                    raise RuntimeError(
                        "Shop not healthy before injection; nothing injected. "
                        + "; ".join(gate["problems"])
                    )

                check_stop_file()   # someone may have pressed stop during pre-observation

                # ---- Inject ----
                event("arming_watchdog")
                watchdog_path = arm_watchdog(
                    settings, resource_type, resource_name, run_id,
                    now() + max_seconds + WATCHDOG_GRACE_S,
                )
                report["watchdog"] = watchdog_path

                event("submitting")

                # Set before the API call, so cleanup is attempted
                # even if the response fails after resource creation.
                submission_attempted = True

                created = json.loads(
                    kubectl(
                        settings,
                        ["create", "-f", "-", "-o", "json"],
                        manifest,
                    )
                )

                report["resource_uid"] = created["metadata"]["uid"]
                event("waiting_for_injection")

                injected = wait_for(
                    settings,
                    resource_type,
                    resource_name,
                    lambda obj: (
                        condition_true(obj, "Selected")
                        and condition_true(obj, "AllInjected")
                    ),
                    timeout=60,
                )

                t_inject = now()
                report["fault_injected"] = True
                report["t_inject_epoch"] = t_inject
                report["fault_confirmed_at"] = utc_now()
                report["injection_status"] = injected.get("status", {})
                event("injection_confirmed")

                # ---- Guard: kill switch on breach / max time / Ctrl+C / error ----
                event("guarding")

                guard_result = guardian.guard(
                    resource_name,
                    kind,
                    guard_baseline,
                    thresholds,
                    max_seconds,
                    settings=settings,
                    deployment=deployment_name,
                )

                report["guard"] = guard_result
                report["guard_note"] = GUARD_NOTE
                event("guard_finished")

                print(
                    f"Guardian outcome: {guard_result['outcome']} "
                    f"(breach: {guard_result['breach']['reason']})"
                )

                if guard_result["outcome"] in ("interrupted", "guardian_error"):
                    failed = True
                    report["error"] = (
                        f"Guardian ended with {guard_result['outcome']}."
                    )

                # ---- Cleanup, then t_clear ----
                cleanup()

                if report["cleanup_confirmed"] is not True:
                    raise RuntimeError(
                        "Cleanup not confirmed; stopping before measurement."
                    )

                kill = guard_result["kill_switch"]

                t_clear = (
                    kill["at_epoch"] if kill["verified_gone"]
                    else report["cleanup_observed_epoch"]
                )
                report["t_clear_epoch"] = t_clear

                # ---- Recovery observation (P2's samples keep coming) ----
                if guard_result["outcome"] == "interrupted":
                    report["observation_skipped"] = "interrupted"

                else:
                    event("post_observation")

                    try:
                        wait_until(
                            t_clear + args.budget + SUSTAIN_S, sampler
                        )
                    except (KeyboardInterrupt, RuntimeError) as problem:
                        report["observation_problem"] = (
                            str(problem) or "Interrupted by user."
                        )
                        print(
                            "Observation ended early; the run metrics will "
                            "probably be inconclusive."
                        )

                # ---- Measure ----
                stop_sampler(sampler)
                event("collecting_metrics")

                metrics = collect_run_metrics(
                    run_id,
                    targets,
                    t_inject,
                    t_clear,
                    recovery_budget_s=args.budget,
                )

                evidence = collect_evidence(run_id, t_inject - 5, now())

                report["run_metrics"] = metrics
                report["evidence"] = evidence

                record = build_run_record(
                    report, plan, metrics, guard_result, evidence, report_path, meta
                )
                report["run_record"] = record
                save_run(record)
                event("metrics_saved")

                event("observation_completed")

    except (Exception, KeyboardInterrupt) as error:
        failed = True
        report["status"] = "failed"
        report["error"] = str(error) or "Interrupted by user."
        print(f"\nRun failed: {report['error']}")

    finally:
        try:
            if submission_attempted:
                cleanup()

            stop_sampler(sampler)

            if sampler is not None:
                report["sampler_errors"] = sampler["errors"]

            # A failed cleanup keeps the watchdog armed: it is the backstop that deletes the fault.
            if report["cleanup_confirmed"] is not False:
                disarm_watchdog(watchdog_path)

            report["status"] = (
                "failed" if failed
                else f"{mode}_completed"
            )

            report["finished_at"] = utc_now()
            save_report()
            print(f"\nReport saved: {report_path}")

            if mode == "execute":
                print_summary(report)

        finally:
            lock_path.unlink(missing_ok=True)

    return report


if __name__ == "__main__":
    main()