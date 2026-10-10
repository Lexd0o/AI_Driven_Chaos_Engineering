"""One chaos run: plan -> validate -> inject -> clean up -> report. Ported from P1's controller.py.

Run from the repo folder:
  python -m src.chaos.controller --mock --skip-plan      manifest only, no cluster
  python -m src.chaos.controller --preview --skip-plan   server-side dry run
Reports: data/chaos/reports/<run_id>.json   Lock: data/chaos/.controller.lock

WARNING: --execute in this version injects WITHOUT the guardian watching (same as P1's committed branch).
Only the Chaos Mesh duration limits a network fault. Do not use --execute until the guardian is wired in.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from src.chaos import DATA_DIR, EXPERIMENT_PATH, ROOT, SETTINGS_PATH
from src.chaos.executor import ExperimentPlan, build_manifest, kubectl


def utc_now():
    return datetime.now(timezone.utc).isoformat()


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


def main():
    parser = argparse.ArgumentParser(
        description="Plan, validate, and run a bounded chaos experiment."
    )

    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--execute", action="store_true")

    parser.add_argument(
        "--skip-plan",
        action="store_true",
        help="Use the saved experiment.json without calling Groq.",
    )

    parser.add_argument(
        "--observe-seconds",
        type=int,
        default=15,
        help="Post-injection observation window for pod kill.",
    )

    args = parser.parse_args()

    if not 1 <= args.observe_seconds <= 120:
        parser.error("--observe-seconds must be between 1 and 120.")

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
        "mode": mode,
        "status": "starting",
        "started_at": utc_now(),
        "events": [],
        "fault_injected": False,
        "cleanup_confirmed": None,
        "deployment_ready_after": None,
        "recovery_seconds": None,
    }

    settings = None
    deployment_name = None
    resource_type = None
    submission_attempted = False
    failed = False

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

    try:
        event("planning" if not args.skip_plan else "using_saved_plan")

        if not args.skip_plan:
            run_planner()

        settings = read_json(SETTINGS_PATH)
        plan = ExperimentPlan.model_validate_json(
            EXPERIMENT_PATH.read_text(
                encoding="utf-8"
            )
        )

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

        resource_type = (
            "podchaos"
            if plan.fault_type == "pod_kill"
            else "networkchaos"
        )

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
                        ["get", "podchaos,networkchaos", "-o", "json"],
                    )
                )

                if existing["items"]:
                    raise RuntimeError(
                        "Existing chaos resources found. Inspect them first."
                    )

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

                report["fault_injected"] = True
                report["fault_confirmed_at"] = utc_now()
                report["injection_status"] = injected.get("status", {})
                event("injection_confirmed")

                if plan.fault_type == "pod_kill":
                    # Pod kill is a one-time action.
                    time.sleep(args.observe_seconds)

                else:
                    event("waiting_for_fault_restoration")

                    restored = wait_for(
                        settings,
                        resource_type,
                        resource_name,
                        lambda obj: condition_true(obj, "AllRecovered"),
                        timeout=plan.duration_seconds + 60,
                    )

                    report["restoration_status"] = restored.get(
                        "status", {}
                    )
                    report["fault_restoration_observed_at"] = utc_now()

                event("observation_completed")

    except (Exception, KeyboardInterrupt) as error:
        failed = True
        report["status"] = "failed"
        report["error"] = str(error) or "Interrupted by user."
        print(f"\nRun failed: {report['error']}")

    finally:
        try:
            if submission_attempted:
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

                except Exception as error:
                    failed = True
                    report["cleanup_confirmed"] = False
                    report["cleanup_error"] = str(error)
                    print(f"Cleanup failed: {error}")

                if report["cleanup_confirmed"]:
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

            report["status"] = (
                "failed" if failed
                else f"{mode}_completed"
            )

            report["finished_at"] = utc_now()
            save_report()
            print(f"\nReport saved: {report_path}")

        finally:
            lock_path.unlink(missing_ok=True)

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()  