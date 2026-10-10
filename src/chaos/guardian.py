"""Safety loop, kill switch and recovery check (no LLM). Ported from P1's guardian.py.

Kill switch from the repo folder:
  python -m src.chaos.guardian --abort          delete podchaos/networkchaos reslab-experiment
  python -m src.chaos.guardian --abort --all    delete every podchaos/networkchaos in the namespace
The guard loop itself is called by the controller.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from src.chaos import k8s_monitor as monitor

EXPERIMENT_NAME = "reslab-experiment"
CHAOS_KINDS = "podchaos,networkchaos"

DEFAULT_THRESHOLDS = {
    "latency_multiplier": 3,
    "latency_floor_ms": 1500,
    "error_ratio_max": 0.05,
    "consecutive_breaches": 3,
    "recovery_timeout_seconds": 180,
    "recovery_healthy_probes": 5,
}

LOOP_SECONDS = 2
PROMETHEUS_SECONDS = 30
HEALTH_SECONDS = 10


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def latency_limit(baseline, thresholds):
    p95 = baseline.get("probe_p95_ms") or 0

    return max(
        p95 * thresholds["latency_multiplier"],
        thresholds["latency_floor_ms"],
    )


# The exact kubectl arguments used by the kill switch.
def delete_args(kind, name, namespace):
    return [
        "delete", kind, name,
        "-n", namespace,
        "--wait=false",
        "--ignore-not-found",
    ]


# Real dependencies; tests replace these with fakes.
def make_deps(settings, deployment):
    def exists(kind, name):
        out = monitor.kubectl(
            settings,
            ["get", kind, name, "--ignore-not-found", "-o", "name"],
        )
        return bool(out.strip())

    def state(kind, name):
        out = monitor.kubectl(
            settings,
            ["get", kind, name, "--ignore-not-found", "-o", "json"],
        )

        if not out.strip():
            return "missing"

        status = json.loads(out).get("status", {})
        recovered = any(
            c.get("type") == "AllRecovered" and c.get("status") == "True"
            for c in status.get("conditions", [])
        )
        stopped = status.get("experiment", {}).get("desiredPhase") == "Stop"

        return "finished" if recovered or stopped else "running"

    return {
        "probe": lambda: monitor.probe(settings),
        "prometheus": lambda: monitor.prometheus_signals(
            settings, deployment
        ),
        "health": lambda: monitor.pod_health(settings, deployment),
        "state": state,
        "exists": exists,
        "delete": lambda args: monitor.kubectl(settings, args),
        "now": time.monotonic,
        "sleep": time.sleep,
    }


# Run the kill switch and confirm the object is gone.
def kill_switch(kind, name, namespace, deps, timeline, reason):
    args = delete_args(kind, name, namespace)
    started = deps["now"]()
    error = None

    try:
        deps["delete"](args)
    except Exception as exc:
        error = str(exc)

    gone = False

    for _ in range(10):
        try:
            if not deps["exists"](kind, name):
                gone = True
                break
        except Exception as exc:
            error = str(exc)

        deps["sleep"](1)

    action = {
        "command": "kubectl " + " ".join(args),
        "reason": reason,
        "at": utc_now(),
        "verified_gone": gone,
        "error": error,
    }

    timeline.append({
        "event": "kill_switch", "at": action["at"],
        "reason": reason, "verified_gone": gone,
    })

    return action, started


# Keep probing until the service is healthy again.
def measure_recovery(thresholds, deps, timeline, since):
    healthy = 0
    deadline = since + thresholds["recovery_timeout_seconds"]

    while deps["now"]() < deadline:
        tick = deps["now"]()
        sample = deps["probe"]()
        healthy = healthy + 1 if sample["ok"] else 0

        try:
            fully_ready = deps["health"]()["fully_ready"]
        except Exception:
            fully_ready = False

        timeline.append({
            "event": "recovery_probe", "at": utc_now(),
            "ok": sample["ok"], "latency_ms": sample["latency_ms"],
            "healthy_streak": healthy, "fully_ready": fully_ready,
        })

        if healthy >= thresholds["recovery_healthy_probes"] and fully_ready:
            seconds = round(deps["now"]() - since, 1)
            timeline.append({
                "event": "recovered", "at": utc_now(),
                "recovery_seconds": seconds,
            })
            return {"recovered": True, "recovery_seconds": seconds}

        deps["sleep"](max(0, LOOP_SECONDS - (deps["now"]() - tick)))

    timeline.append({"event": "recovery_timeout", "at": utc_now()})

    return {"recovered": False, "recovery_seconds": None}


def guard(experiment_name, kind, baseline, thresholds, max_seconds,
          deps=None, settings=None, deployment=None):
    settings = settings or monitor.load_settings()
    thresholds = {**DEFAULT_THRESHOLDS, **thresholds}

    if deps is None:
        deps = make_deps(settings, deployment)

    limit = latency_limit(baseline, thresholds)
    timeline = [{
        "event": "guard_start", "at": utc_now(), "latency_limit_ms": limit,
    }]
    start = deps["now"]()

    streak = 0
    outcome = None
    breach = {"breached": False, "reason": None, "values": None}
    last_prom = last_health = -1e9
    saw_unready = False
    interrupted = False

    try:
        while outcome is None:
            tick = deps["now"]()
            sample = deps["probe"]()
            bad = (not sample["ok"]) or sample["latency_ms"] > limit
            streak = streak + 1 if bad else 0

            timeline.append({
                "event": "probe", "at": utc_now(),
                "t": round(tick - start, 1),
                "ok": sample["ok"], "latency_ms": sample["latency_ms"],
                "breach_streak": streak,
            })

            if streak >= thresholds["consecutive_breaches"]:
                outcome = "breach"
                breach = {
                    "breached": True,
                    "reason": (
                        "probe_failure" if not sample["ok"]
                        else "probe_latency_exceeded"
                    ),
                    "values": {
                        "latency_ms": sample["latency_ms"],
                        "latency_limit_ms": limit,
                        "ok": sample["ok"],
                        "consecutive": streak,
                    },
                }

            # Slow cross-check from Prometheus.
            if outcome is None and tick - last_prom >= PROMETHEUS_SECONDS:
                last_prom = tick

                try:
                    prom = deps["prometheus"]()
                    timeline.append({
                        "event": "prometheus", "at": utc_now(), **prom,
                    })
                    ratio = prom.get("error_ratio")

                    if ratio is not None and (
                        ratio > thresholds["error_ratio_max"]
                    ):
                        outcome = "breach"
                        breach = {
                            "breached": True,
                            "reason": "error_ratio_exceeded",
                            "values": {
                                "error_ratio": ratio,
                                "error_ratio_max":
                                    thresholds["error_ratio_max"],
                            },
                        }
                except Exception as exc:
                    timeline.append({
                        "event": "prometheus_error", "at": utc_now(),
                        "error": str(exc),
                    })

            if outcome is None and (
                kind == "PodChaos" or tick - last_health >= HEALTH_SECONDS
            ):
                last_health = tick

                try:
                    health = deps["health"]()
                    timeline.append({
                        "event": "pod_health", "at": utc_now(), **health,
                    })

                    # A killed pod that is replaced ends a one-shot fault.
                    if not health["fully_ready"]:
                        saw_unready = True
                    elif kind == "PodChaos" and saw_unready:
                        outcome = "completed"
                except Exception as exc:
                    timeline.append({
                        "event": "pod_health_error", "at": utc_now(),
                        "error": str(exc),
                    })

            # Timed network faults end by themselves.
            if outcome is None and kind != "PodChaos":
                try:
                    if deps["state"](
                        kind.lower(), experiment_name
                    ) != "running":
                        outcome = "completed"
                except Exception:
                    pass

            if outcome is None and deps["now"]() - start >= max_seconds:
                outcome = "max_seconds_exceeded"

            if outcome is None:
                deps["sleep"](
                    max(0, LOOP_SECONDS - (deps["now"]() - tick))
                )

    except KeyboardInterrupt:
        outcome = "interrupted"
        interrupted = True

    # Fail safe: any guardian error still runs the kill switch.
    except Exception as exc:
        outcome = "guardian_error"
        timeline.append({
            "event": "guardian_error", "at": utc_now(), "error": str(exc),
        })

    action, killed_at = kill_switch(
        kind.lower(), experiment_name, settings["namespace"],
        deps, timeline, breach["reason"] or outcome,
    )

    try:
        recovery = measure_recovery(thresholds, deps, timeline, killed_at)
    except KeyboardInterrupt:
        recovery = {
            "recovered": False, "recovery_seconds": None,
            "interrupted": True,
        }

    return {
        "experiment": experiment_name,
        "kind": kind,
        "thresholds": thresholds,
        "latency_limit_ms": limit,
        "outcome": outcome,
        "interrupted": interrupted,
        "breach": breach,
        "kill_switch": action,
        "recovery": recovery,
        "recovery_seconds": recovery["recovery_seconds"],
        "timeline": timeline,
    }


def abort(settings, delete_all=False):
    existing = json.loads(
        monitor.kubectl(settings, ["get", CHAOS_KINDS, "-o", "json"])
    )["items"]

    targets = [
        item for item in existing
        if delete_all or item["metadata"]["name"] == EXPERIMENT_NAME
    ]

    if not targets:
        print(f"Nothing to delete in namespace {settings['namespace']}.")
        return []

    deleted = []

    for item in targets:
        kind = item["kind"].lower()
        name = item["metadata"]["name"]
        monitor.kubectl(
            settings,
            delete_args(kind, name, settings["namespace"]),
        )
        deleted.append(f"{kind}/{name}")
        print(f"Deleted {kind}/{name}")

    return deleted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--abort", action="store_true",
        help="delete the reslab-experiment chaos object",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="with --abort, delete every chaos object in the namespace",
    )
    args = parser.parse_args()

    if not args.abort:
        parser.error("Use --abort. The guard loop runs from controller.py.")

    abort(monitor.load_settings(), args.all)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        main()
    except Exception as error:
        raise SystemExit(f"Guardian failed: {error}")
