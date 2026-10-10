"""Shared validation, manifest rendering, and bounded Kubernetes calls.

Vendored from chaos_agent_expanded. ChaosPilot changes: adds cpu_stress
(StressChaos, used by our fault_catalog), and the duration cap comes from
config/policy.yaml via the caller instead of a hard-coded 45 s.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FAULTS = ["pod_kill", "pod_failure", "network_delay", "packet_loss", "packet_duplicate", "cpu_stress"]
CHAOS_KINDS = "podchaos,networkchaos,stresschaos"


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_service: str = Field(min_length=1)
    fault_type: Literal["pod_kill", "pod_failure", "network_delay", "packet_loss", "packet_duplicate", "cpu_stress"]
    hypothesis: str = Field(min_length=10, max_length=500)
    duration_seconds: int = Field(default=0, ge=0, le=120)
    latency_ms: int = Field(default=0, ge=0, le=1000)
    loss_percent: int = Field(default=0, ge=0, le=30)
    duplicate_percent: int = Field(default=0, ge=0, le=10)
    cpu_workers: int = Field(default=0, ge=0, le=4)
    pod_count: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def parameters(self):
        numbers = (self.latency_ms, self.loss_percent, self.duplicate_percent, self.cpu_workers)
        if self.fault_type == "pod_kill":
            if self.duration_seconds or any(numbers):
                raise ValueError("pod_kill requires all fault numeric parameters to be zero")
            if self.pod_count != 1:
                raise ValueError("The lost-pod scenario must kill exactly one application pod")
        else:
            if not self.duration_seconds:
                raise ValueError("Timed faults require a duration")
            expected = {"pod_failure": (False, False, False, False),
                        "network_delay": (True, False, False, False),
                        "packet_loss": (False, True, False, False),
                        "packet_duplicate": (False, False, True, False),
                        "cpu_stress": (False, False, False, True)}[self.fault_type]
            if tuple(bool(n) for n in numbers) != expected:
                raise ValueError("Parameters do not match the selected fault")
        return self


def validate_target(plan, settings):
    if plan.target_service not in settings["targets"]:
        raise ValueError("Target is not approved")
    cap = settings.get("target_limits", {}).get(plan.target_service, {}).get("max_pods", 1)
    if plan.pod_count > cap:
        raise ValueError(f"Target permits at most {cap} affected pod(s)")
    limit = settings.get("max_duration_s")
    if limit is not None and plan.duration_seconds > limit:
        raise ValueError(f"duration_seconds={plan.duration_seconds} exceeds max_duration_s={limit}")


def kubectl(settings, arguments, body=None):
    command = [settings.get("kubectl_path", "kubectl"), "--context", settings["context"],
               "--namespace", settings["namespace"], *arguments]
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "kubectl failed")
    return result.stdout


def get_resource(settings, kind, name):
    output = kubectl(settings, ["get", kind, name, "--ignore-not-found", "-o", "json"])
    return json.loads(output) if output.strip() else None


def ready(deployment):
    if not deployment:
        return False
    desired = deployment["spec"].get("replicas", 1)
    status = deployment.get("status", {})
    return (desired > 0 and status.get("readyReplicas", 0) >= desired
            and status.get("updatedReplicas", 0) == desired
            and status.get("observedGeneration", 0) >= deployment["metadata"].get("generation", 0)
            and status.get("unavailableReplicas", 0) == 0)


def deployment_labels(deployment):
    if not ready(deployment):
        raise RuntimeError("Deployment is not fully ready")
    selector = deployment["spec"]["selector"]
    if selector.get("matchExpressions") or not selector.get("matchLabels"):
        raise ValueError("This version requires a nonempty matchLabels selector")
    return selector["matchLabels"]


def condition(obj, name):
    return any(c.get("type") == name and c.get("status") == "True"
               for c in obj.get("status", {}).get("conditions", []))


def chaos_kind(fault_type):
    if fault_type in {"pod_kill", "pod_failure"}:
        return "podchaos"
    return "stresschaos" if fault_type == "cpu_stress" else "networkchaos"


def build_manifest(plan, namespace, labels, name="reslab-experiment", run_id=None):
    if not labels:
        raise ValueError("An empty selector is not allowed")
    spec = {"mode": "one", "selector": {"namespaces": [namespace], "labelSelectors": labels}}
    if plan.pod_count > 1:
        spec.update(mode="fixed", value=str(plan.pod_count))
    if plan.fault_type in {"pod_kill", "pod_failure"}:
        kind = "PodChaos"
        spec["action"] = "pod-kill" if plan.fault_type == "pod_kill" else "pod-failure"
        if plan.fault_type == "pod_failure":
            spec["duration"] = f"{plan.duration_seconds}s"
    elif plan.fault_type == "cpu_stress":
        kind = "StressChaos"
        spec["duration"] = f"{plan.duration_seconds}s"
        spec["stressors"] = {"cpu": {"workers": plan.cpu_workers, "load": 100}}
    else:
        kind = "NetworkChaos"
        spec["duration"] = f"{plan.duration_seconds}s"
        if plan.fault_type == "network_delay":
            spec.update(action="delay", delay={"latency": f"{plan.latency_ms}ms", "jitter": "0ms", "correlation": "0"})
        elif plan.fault_type == "packet_loss":
            spec.update(action="loss", loss={"loss": str(plan.loss_percent), "correlation": "0"})
        else:
            spec.update(action="duplicate", duplicate={"duplicate": str(plan.duplicate_percent), "correlation": "0"})
    metadata = {"name": name, "namespace": namespace}
    if run_id:
        metadata["labels"] = {"reslab-run-id": run_id}
    return {"apiVersion": "chaos-mesh.org/v1alpha1", "kind": kind, "metadata": metadata, "spec": spec}


def cleanup(settings, kind, name, run_id):
    """Delete only our owned resource; wait for normal finalizer cleanup."""
    obj = get_resource(settings, kind, name)
    if obj:
        if obj["metadata"].get("labels", {}).get("reslab-run-id") != run_id:
            raise RuntimeError("Ownership mismatch: refusing deletion")
        kubectl(settings, ["delete", kind, name, "--wait=true", "--timeout=20s"])
    if get_resource(settings, kind, name) is not None:
        raise RuntimeError("Resource remains after cleanup")
