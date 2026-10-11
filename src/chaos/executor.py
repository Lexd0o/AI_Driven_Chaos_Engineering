"""ExperimentPlan validation + Chaos Mesh manifest + kubectl submit. Ported from P1's chaos_executor.py.

Run from the repo folder:
  python -m src.chaos.executor              validate config/experiment.json only (no cluster)
  python -m src.chaos.executor --mock       build the manifest from sample data (no cluster)
  python -m src.chaos.executor --preview    server-side dry run (nothing created)
  python -m src.chaos.executor --execute    really injects (no guardian here; use the controller)
"""
import argparse
import json
import subprocess
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


from src.chaos import EXPERIMENT_PATH, SETTINGS_PATH

# Every Chaos Mesh kind this project can create (used by the "nothing else running" checks and --abort).
CHAOS_KINDS = "podchaos,networkchaos,stresschaos"


# fault_type -> (manifest kind, kubectl resource type)
def chaos_kind(fault_type):
    if fault_type == "pod_kill":
        return "PodChaos", "podchaos"
    if fault_type == "cpu_stress":
        return "StressChaos", "stresschaos"
    return "NetworkChaos", "networkchaos"


# Validate the experiment request and its parameters.
class ExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_service: str

    fault_type: Literal[
        "pod_kill",
        "network_delay",
        "packet_loss",
        "cpu_stress",          # from P4's agent (StressChaos), used by P3's fault catalog
    ]

    hypothesis: str = Field(min_length=10, max_length=500)

    duration_seconds: int = Field(default=0, ge=0, le=60)
    latency_ms: int = Field(default=0, ge=0, le=1000)
    loss_percent: int = Field(default=0, ge=0, le=30)
    cpu_workers: int = Field(default=0, ge=0, le=4)   # policy.yaml max_cpu_workers = 4

    @model_validator(mode="after")
    def check_fault_parameters(self):
        if self.fault_type != "cpu_stress" and self.cpu_workers:
            raise ValueError("cpu_workers is only for cpu_stress.")

        if self.fault_type == "pod_kill":
            if any([
                self.duration_seconds,
                self.latency_ms,
                self.loss_percent,
            ]):
                raise ValueError(
                    "pod_kill requires all numeric parameters to be 0."
                )

        elif self.fault_type == "network_delay":
            if self.duration_seconds < 1 or self.latency_ms < 1:
                raise ValueError(
                    "network_delay requires a positive duration and latency."
                )

            if self.loss_percent != 0:
                raise ValueError(
                    "network_delay requires loss_percent=0."
                )

        elif self.fault_type == "packet_loss":
            if self.duration_seconds < 1 or self.loss_percent < 1:
                raise ValueError(
                    "packet_loss requires a positive duration and loss."
                )

            if self.latency_ms != 0:
                raise ValueError(
                    "packet_loss requires latency_ms=0."
                )

        elif self.fault_type == "cpu_stress":
            if self.duration_seconds < 1 or self.cpu_workers < 1:
                raise ValueError(
                    "cpu_stress requires a positive duration and cpu_workers."
                )

            if self.latency_ms or self.loss_percent:
                raise ValueError(
                    "cpu_stress requires latency_ms=0 and loss_percent=0."
                )

        return self


# Send a fixed command to Kubernetes.
def kubectl(settings, arguments, body=None):
    command = [
        "kubectl",
        "--context", settings["context"],
        "--namespace", settings["namespace"],
        *arguments,
    ]

    result = subprocess.run(
        command,
        input=json.dumps(body) if body is not None else None,
        text=True,
        capture_output=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "kubectl command failed."
        )

    return result.stdout


# Translate the selected failure into a Chaos Mesh manifest.
def build_manifest(plan, namespace, labels):
    manifest = {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": chaos_kind(plan.fault_type)[0],
        "metadata": {
            "name": "reslab-experiment",
            "namespace": namespace,
        },
        "spec": {
            "mode": "one",
            "selector": {
                "namespaces": [namespace],
                "labelSelectors": labels,
            },
        },
    }

    spec = manifest["spec"]

    if plan.fault_type == "pod_kill":
        spec["action"] = "pod-kill"

    elif plan.fault_type == "network_delay":
        spec["action"] = "delay"
        spec["duration"] = f"{plan.duration_seconds}s"
        spec["delay"] = {
            "latency": f"{plan.latency_ms}ms",
            "jitter": "0ms",
            "correlation": "0",
        }

    elif plan.fault_type == "packet_loss":
        spec["action"] = "loss"
        spec["duration"] = f"{plan.duration_seconds}s"
        spec["loss"] = {
            "loss": str(plan.loss_percent),
            "correlation": "0",
        }

    elif plan.fault_type == "cpu_stress":
        # Same spec as P4's agent (agents/chaos_agent_expanded/core.py).
        spec["duration"] = f"{plan.duration_seconds}s"
        spec["stressors"] = {
            "cpu": {"workers": plan.cpu_workers, "load": 100},
        }

    return manifest


def main():
    parser = argparse.ArgumentParser()

    # Select only one operating mode.
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--mock", action="store_true")

    args = parser.parse_args()

    settings = json.loads(
        SETTINGS_PATH.read_text(encoding="utf-8")
    )

    plan = ExperimentPlan.model_validate_json(
        EXPERIMENT_PATH.read_text(encoding="utf-8")
    )

    if plan.target_service not in settings["targets"]:
        raise ValueError("Target service is not approved.")

    deployment_name = settings["targets"][plan.target_service]

    print("Validated request:")
    print(plan.model_dump_json(indent=2))

    # Default mode only validates the request locally.
    if not args.preview and not args.execute and not args.mock:
        print("Validation only. No cluster access or failure injection.")
        return

    if args.mock:
        # Sample deployment data, not real application observations.
        deployment = {
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {
                        "app": f"mock-{plan.target_service}"
                    }
                },
            },
            "status": {
                "readyReplicas": 1
            },
        }

        print("\nMOCK MODE: using sample deployment data.")

    else:
        if "REPLACE_WITH" in settings["context"] + deployment_name:
            raise ValueError("Replace the placeholders in config/chaos_settings.json.")

        deployment = json.loads(
            kubectl(
                settings,
                ["get", "deployment", deployment_name, "-o", "json"],
            )
        )

    # Refuse a target deployment that is missing ready replicas.
    desired = deployment["spec"].get("replicas", 1)
    ready = deployment.get("status", {}).get("readyReplicas", 0)

    if desired < 1 or ready < desired:
        raise RuntimeError("Target deployment is not fully ready.")

    selector = deployment["spec"]["selector"]

    if selector.get("matchExpressions"):
        raise ValueError(
            "This starter supports matchLabels selectors only."
        )

    labels = selector.get("matchLabels", {})

    if not labels:
        raise ValueError("Target deployment has no usable pod selector.")

    # Build the manifest for the requested failure type.
    manifest = build_manifest(
        plan,
        settings["namespace"],
        labels,
    )

    print("\nChaos Mesh manifest:")
    print(json.dumps(manifest, indent=2))

    # Mock mode never calls Kubernetes.
    if args.mock:
        print("\nMOCK MODE: experiment generated locally.")
        print("No Kubernetes connection or failure injection occurred.")
        return

    # Validate against Kubernetes without creating the experiment.
    if args.preview:
        kubectl(
            settings,
            ["create", "--dry-run=server", "-f", "-", "-o", "json"],
            manifest,
        )

        print("Server validation passed. No experiment was created.")
        return

    # Check for both pod and network experiments.
    existing = json.loads(
        kubectl(
            settings,
            ["get", CHAOS_KINDS, "-o", "json"],
        )
    )

    if existing["items"]:
        raise RuntimeError(
            "An existing chaos resource is present. Inspect it first."
        )

    # Submit the real experiment.
    output = kubectl(
        settings,
        ["create", "-f", "-", "-o", "json"],
        manifest,
    )

    created = json.loads(output)

    print("Experiment submitted:", created["metadata"]["name"])
    print("Resource type:", created["kind"])
    print("Check Chaos Mesh status to confirm injection occurred.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        main()
    except Exception as error:
        raise SystemExit(f"Error: {error}")