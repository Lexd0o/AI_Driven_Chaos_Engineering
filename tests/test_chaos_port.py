"""Checks that P1's chaos agent still works after moving it into src/chaos. No kubectl, no cluster, no Groq.
Run from the repo folder:  python -m tests.test_chaos_port
"""
import json
import sys
from datetime import datetime

from src.chaos import EXPERIMENT_PATH, OVERRIDES_PATH, SETTINGS_PATH, SNAPSHOT_PATH
from src.chaos import controller
from src.chaos.executor import ExperimentPlan, build_manifest

LABELS = {"opentelemetry.io/name": "product-catalog"}


def plan(**kw):
    base = {"target_service": "catalog", "hypothesis": "A test hypothesis for the port checks."}
    return ExperimentPlan.model_validate({**base, **kw})


def rejected(**kw):
    try:
        plan(**kw)
    except ValueError:
        return True
    return False


# ------------------------------------------------------------------ the bug that crashed every run
def test_utc_now_works():
    stamp = controller.utc_now()
    assert datetime.fromisoformat(stamp).tzinfo is not None


# ------------------------------------------------------------------ config files moved to config/
def test_config_files_exist_and_load():
    for path in (SETTINGS_PATH, EXPERIMENT_PATH, SNAPSHOT_PATH, OVERRIDES_PATH):
        assert path.is_file(), f"missing {path}"
        json.loads(path.read_text(encoding="utf-8"))


def test_settings_target_and_saved_plan_agree():
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    saved = ExperimentPlan.model_validate_json(EXPERIMENT_PATH.read_text(encoding="utf-8"))
    assert settings["namespace"] == "otel-demo"
    assert saved.target_service in settings["targets"]


# ------------------------------------------------------------------ validation unchanged
def test_plan_validation_rules():
    assert plan(fault_type="pod_kill").fault_type == "pod_kill"
    assert rejected(fault_type="pod_kill", duration_seconds=10)
    assert rejected(fault_type="network_delay", duration_seconds=30)            # no latency
    assert rejected(fault_type="packet_loss", duration_seconds=30, loss_percent=50)  # above 30 %
    assert rejected(fault_type="network_delay", duration_seconds=90, latency_ms=100)  # above 60 s
    assert rejected(fault_type="disk_fill")                                     # unknown fault


# ------------------------------------------------------------------ manifests unchanged
def test_manifest_pod_kill():
    m = build_manifest(plan(fault_type="pod_kill"), "otel-demo", LABELS)
    assert m["kind"] == "PodChaos" and m["spec"]["action"] == "pod-kill"
    assert m["spec"]["selector"] == {"namespaces": ["otel-demo"], "labelSelectors": LABELS}
    assert "duration" not in m["spec"]


def test_manifest_network_delay_has_duration():
    m = build_manifest(plan(fault_type="network_delay", duration_seconds=30, latency_ms=500), "otel-demo", LABELS)
    assert m["kind"] == "NetworkChaos" and m["spec"]["action"] == "delay"
    assert m["spec"]["duration"] == "30s" and m["spec"]["delay"]["latency"] == "500ms"


def test_manifest_packet_loss_has_duration():
    m = build_manifest(plan(fault_type="packet_loss", duration_seconds=30, loss_percent=10), "otel-demo", LABELS)
    assert m["spec"]["action"] == "loss"
    assert m["spec"]["duration"] == "30s" and m["spec"]["loss"]["loss"] == "10"


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
