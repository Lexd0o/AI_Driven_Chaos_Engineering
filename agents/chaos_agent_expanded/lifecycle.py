"""Baseline -> inject -> monitor -> stop the fault -> watch recovery.

Vendored from chaos_agent_expanded's controller.run(). ChaosPilot changes:
- callable with a settings dict + ExperimentPlan (no settings.json / experiment.json,
  no Groq planner: our src.plan + src.policy produce the plan, D-005);
- fault deadline = plan duration + grace (cap enforced by config/policy.yaml) instead of 45 s;
- probes several journeys per sample (guardian.probe_journeys);
- adds a post-fault recovery watch (Architecture.md section 7: t_clear, t_healthy),
  which the upstream agent deliberately left to a separate recovery agent.
"""
import json
import time
from pathlib import Path
from uuid import uuid4

from .core import (CHAOS_KINDS, build_manifest, chaos_kind, cleanup, condition, deployment_labels,
                   get_resource, kubectl, now, ready, validate_target, write_json)
from .guardian import Guardian, healthy, over_latency, probe_journeys
from .watchdog import cancel_watchdog, start_watchdog


def run_plan(settings, plan, mode, paths, observe_seconds=15, slo_latency_ms=None, recovery_budget_s=120):
    """mode: mock | preview | execute. paths: runs_dir, ops_dir, lock, stop. Returns the report dict."""
    cfg = settings["guardian"]
    for key in ("baseline_seconds", "breach_seconds", "poll_seconds", "max_latency_ms"):
        if cfg[key] <= 0:
            raise ValueError(f"guardian.{key} must be positive")
    run_id = "run-" + uuid4().hex[:12]
    name = "reslab-" + run_id
    path = Path(paths["runs_dir"]) / f"{run_id}.json"
    report = {"run_id": run_id, "mode": mode, "status": "starting", "started_at": now(),
              "fault_injected": False, "samples": [], "events": [],
              "cluster": {k: settings[k] for k in ("context", "namespace")}}
    if settings.get("kubectl_path"):
        report["cluster"]["kubectl_path"] = settings["kubectl_path"]
    lock = Path(paths["lock"])
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise RuntimeError(f"Experiment lock {lock} exists; confirm no run is active before removing it")
    with handle:
        handle.write(run_id)
    attempted = False
    failed = False
    kind = chaos_kind(plan.fault_type)
    deployment = None
    watchdog_path = None
    stop_path = Path(paths["stop"])
    probes = cfg["probes"]
    breach_latency = slo_latency_ms or cfg["max_latency_ms"]

    def event(stage):
        report["status"] = stage
        report["events"].append({"stage": stage, "observed_at": now()})
        try:
            write_json(path, report)
        except OSError as exc:
            if not attempted:
                raise
            report["report_write_error"] = str(exc)
            print("Report write failed; continuing to fault cleanup", flush=True)
        print(f"STAGE: {stage}", flush=True)

    def sample(stage):
        value = probe_journeys(probes, cfg.get("timeout_seconds", 3))
        value["stage"] = stage
        report["samples"].append(value)
        if stage == "during_fault" and "t_breach" not in report and (
                not value["ok"] or over_latency(value, breach_latency)):
            report["t_breach"] = value["observed_at"]
        write_json(path, report)
        return value

    try:
        validate_target(plan, settings)
        if plan.fault_type not in settings["allowed_faults"]:
            raise ValueError("Failure is not allowed in settings")
        deployment = settings["targets"][plan.target_service]
        labels = ({"app": f"mock-{plan.target_service}"} if mode == "mock"
                  else deployment_labels(get_resource(settings, "deployment", deployment)))
        manifest = build_manifest(plan, settings["namespace"], labels, name, run_id)
        report.update(plan=plan.model_dump(), manifest=manifest, resource={"kind": kind, "name": name})
        if mode == "mock":
            print(json.dumps(manifest, indent=2))
            event("mock_completed")
            return report
        event("server_validation")
        kubectl(settings, ["create", "--dry-run=server", "-f", "-", "-o", "json"], manifest)
        if mode == "preview":
            event("preview_completed")
            print("Server validation passed. No experiment was created.")
            return report
        if json.loads(kubectl(settings, ["get", CHAOS_KINDS, "-o", "json"]))["items"]:
            raise RuntimeError("Existing chaos resources found; inspect them first")
        # Verify removal permission before causing a failure.
        for verb in ("get", "create", "delete"):
            answer = kubectl(settings, ["auth", "can-i", verb, kind]).strip()
            if answer != "yes":
                raise RuntimeError(f"Required {verb} capability unavailable for {kind}")
        original = get_resource(settings, "deployment", deployment)
        report["original_state"] = original
        if plan.pod_count > original["spec"].get("replicas", 1):
            raise ValueError("Requested pod_count exceeds current replica count")
        if stop_path.exists():
            raise RuntimeError("STOP file exists; no fault injected")
        event("baseline")
        deadline = time.monotonic() + cfg["baseline_seconds"]
        while time.monotonic() < deadline:
            if not healthy(sample("baseline"), cfg):
                raise RuntimeError("Baseline unhealthy: no fault injected. Check probe URLs and thresholds.")
            time.sleep(cfg["poll_seconds"])
        if deployment_labels(get_resource(settings, "deployment", deployment)) != labels:
            raise RuntimeError("Deployment selector changed during baseline; replan")
        if json.loads(kubectl(settings, ["get", CHAOS_KINDS, "-o", "json"]))["items"]:
            raise RuntimeError("Another chaos experiment appeared during baseline")
        if stop_path.exists():
            raise RuntimeError("Manual stop requested before injection")
        fault_window = (plan.duration_seconds or observe_seconds) + cfg.get("grace_seconds", 15)
        # Arm a separate local cleanup process before submitting a resource.
        watchdog_path = start_watchdog(
            {k: settings[k] for k in ("context", "namespace", "kubectl_path") if k in settings},
            kind, name, run_id, time.time() + fault_window, paths["ops_dir"])
        report["action_started_at"] = now()
        event("submitting")
        submission_clock = time.monotonic()
        attempted = True
        kubectl(settings, ["create", "-f", "-", "-o", "json"], manifest)
        guardian = Guardian(cfg)
        deadline = submission_clock + fault_window
        confirmed_clock = None
        event("observing")
        while time.monotonic() < deadline:
            if stop_path.exists():
                report["stop_reason"] = "manual_stop"
                break
            obj = get_resource(settings, kind, name)
            if obj is None:
                report["stop_reason"] = "resource_removed_externally"
                break
            report["injection_status"] = {"Selected": condition(obj, "Selected"),
                                          "AllInjected": condition(obj, "AllInjected")}
            if confirmed_clock is None and condition(obj, "Selected") and condition(obj, "AllInjected"):
                confirmed_clock = time.monotonic()
                report["fault_injected"] = True
                report["fault_confirmed_at"] = now()
                event("injection_confirmed")
            if guardian.observe(sample("during_fault"), time.monotonic()):
                report["stop_reason"] = "probe_threshold_breach"
                report["t_detect"] = now()
                break
            if confirmed_clock is not None:
                if plan.fault_type != "pod_kill" and condition(obj, "AllRecovered"):
                    report["stop_reason"] = "duration_finished"
                    break
                if plan.fault_type == "pod_kill" and time.monotonic() - confirmed_clock >= observe_seconds:
                    report["stop_reason"] = "observation_finished"
                    break
            time.sleep(cfg["poll_seconds"])
        else:
            report["stop_reason"] = "fault_window_limit"
        if not report["fault_injected"]:
            raise RuntimeError("Injection was not confirmed; report is inconclusive")
    except (Exception, KeyboardInterrupt) as exc:
        failed = True
        report["error"] = str(exc) or "Interrupted by user"
        report.setdefault("stop_reason", "manual_stop" if isinstance(exc, KeyboardInterrupt)
                          else "monitoring_or_controller_error")
        print(f"Run failed: {report['error']}", flush=True)
    finally:
        try:
            if attempted:
                print("Stopping the injected fault...", flush=True)
                diagnostic = {"run_id": run_id, "resource": {"kind": kind, "name": name},
                              "removal_requested_at": now()}
                try:
                    cleanup(settings, kind, name, run_id)
                    diagnostic["fault_resource_absent"] = True
                    report["t_clear"] = now()
                    cancel_watchdog(watchdog_path)
                except (Exception, KeyboardInterrupt) as exc:
                    failed = True
                    diagnostic["fault_resource_absent"] = False
                    diagnostic["error"] = str(exc) or "Fault removal interrupted"
                    print(f"Fault removal failed: {diagnostic['error']}", flush=True)
                try:
                    write_json(Path(paths["ops_dir"]) / f"{run_id}-stop.json", diagnostic)
                except OSError as exc:
                    failed = True
                    print(f"Could not save operational diagnostics: {exc}", flush=True)
            if not attempted:
                cancel_watchdog(watchdog_path)
        finally:
            if not (attempted and "t_clear" in report):
                lock.unlink(missing_ok=True)
    if attempted and "t_clear" in report:
        try:
            watch_recovery(settings, deployment, cfg, report, sample, event, recovery_budget_s, stop_path)
        except (Exception, KeyboardInterrupt) as exc:
            report["recovery_error"] = str(exc) or "Interrupted by user"
        finally:
            lock.unlink(missing_ok=True)
    report["status"] = "failed" if failed else f"{mode}_completed"
    report["finished_at"] = now()
    write_json(path, report)
    print(f"Run report saved: {path}", flush=True)
    return report


def watch_recovery(settings, deployment, cfg, report, sample, event, budget_s, stop_path):
    """After the fault is removed: t_healthy = start of the first window of
    healthy_window_s continuous healthy samples (probes healthy AND deployment ready).
    Never healthy within the budget -> no t_healthy (no fake number)."""
    event("recovery_watch")
    window = cfg.get("healthy_window_s", 30)
    deadline = time.monotonic() + budget_s + window
    healthy_since, healthy_since_ts = None, None
    while time.monotonic() < deadline:
        if stop_path.exists():
            report["recovery_stop_reason"] = "manual_stop"
            return
        s = sample("recovery")
        s["deployment_ready"] = ready(get_resource(settings, "deployment", deployment))
        if healthy(s, cfg) and s["deployment_ready"]:
            if healthy_since is None:
                healthy_since, healthy_since_ts = time.monotonic(), s["observed_at"]
            if time.monotonic() - healthy_since >= window:
                report["t_healthy"] = healthy_since_ts
                event("recovered")
                return
        else:
            healthy_since = None
        time.sleep(cfg["poll_seconds"])
    report["recovery_stop_reason"] = "recovery_budget_exceeded"
