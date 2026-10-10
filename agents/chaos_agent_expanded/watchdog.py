"""Separate local process requests owned-resource cleanup at the deadline.
Survives parent termination, but not host failure; native Chaos Mesh duration
is an additional recovery path. No forced finalizer removal.

Vendored from chaos_agent_expanded. ChaosPilot change: state files go to a
caller-supplied directory, and the process is launched as a module from the
repo root (python -m agents.chaos_agent_expanded.watchdog).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .core import cleanup, now, read_json, write_json

REPO_ROOT = Path(__file__).resolve().parents[2]


def start_watchdog(settings, kind, name, run_id, deadline_epoch, ops_dir):
    state_path = Path(ops_dir).resolve() / f"{run_id}-watchdog.json"
    write_json(state_path, {"cluster": settings, "kind": kind, "name": name,
                            "run_id": run_id, "deadline_epoch": deadline_epoch})
    log = state_path.with_suffix(".log")
    kwargs = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    with log.open("a", encoding="utf-8") as stream:
        process = subprocess.Popen([sys.executable, "-m", "agents.chaos_agent_expanded.watchdog", str(state_path)],
                                   cwd=REPO_ROOT, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=stream, **kwargs)
    ready = state_path.with_suffix(".ready")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ready.exists():
            return state_path
        if process.poll() is not None:
            break
        time.sleep(.1)
    state_path.with_suffix(".cancel").touch()
    raise RuntimeError("Independent cleanup process could not be confirmed; refusing injection")


def cancel_watchdog(path):
    if path is not None:
        Path(path).with_suffix(".cancel").touch()


def watch(path):
    state = read_json(path)
    Path(path).with_suffix(".ready").touch()
    while time.time() < state["deadline_epoch"]:
        if Path(path).with_suffix(".cancel").exists():
            return
        time.sleep(.2)
    # Repeat during a late-create uncertainty window. Never remove foreign resources.
    errors = []
    for _ in range(12):
        if Path(path).with_suffix(".cancel").exists():
            return
        try:
            cleanup(state["cluster"], state["kind"], state["name"], state["run_id"])
            write_json(Path(path).with_suffix(".result.json"), {"cleanup_requested_at": now(),
                       "resource_absent_at_check": True, "errors": errors})
        except Exception as exc:
            errors.append(str(exc))
            write_json(Path(path).with_suffix(".result.json"), {"cleanup_requested_at": now(),
                       "resource_absent_at_check": False, "errors": errors})
        time.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("state")
    watch(Path(parser.parse_args().state))
