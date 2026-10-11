"""run_all.py - run every approved, runnable experiment one after another, then score and write the report.

Usage (from the repo root, venv active, port-forwards + env vars set, no other sampler running):
    python -m src.run_all                      # all runnable experiments, asks for the confirmation word once
    python -m src.run_all --exp-ids exp-002 exp-008
    python -m src.run_all --skip exp-007 --cooldown 90
    python -m src.run_all --list               # what it would run, nothing injected

Each experiment goes through the same path as a single run (src.run --execute): policy re-check, refusals, guardian,
kill switch, watchdog, cleanup. Before every experiment a fresh guard baseline is captured. The batch halts at the
first failed or refused run, or as soon as a STOP file appears (the Recall / STOP ALL FAULTS buttons create it).
Progress is written to data/batch_status.json for the dashboard.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src import run as run_mod
from src.telemetry import load_config

CONFIRM = "reslab-experiment"
STATUS_PATH = "data/batch_status.json"


def runnable_ids(plan: list[dict], policy: dict, executor: dict, journeys: list[str], skip=()) -> list[str]:
    return [i["experiment_id"] for i in plan
            if i["experiment_id"] not in skip and run_mod.refusal(i, policy, executor, journeys) is None]


def write_status(path: str, state: str, items: list[dict], message: str = "", started: str | None = None) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "message": message, "started": started, "updated": datetime.now(timezone.utc).isoformat(),
               "items": items}
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_batch(ids: list[str], stop_path: str, status_path: str = STATUS_PATH, cooldown_s: int = 60,
              keep_going: bool = False, runner=subprocess.run, sleep=time.sleep, python: str = sys.executable) -> str:
    """Returns the final state: done, halted or failed."""
    items = [{"id": i, "status": "pending"} for i in ids]
    started = datetime.now(timezone.utc).isoformat()
    state, message = "running", ""
    write_status(status_path, state, items, message, started)
    try:
        for n, item in enumerate(items):
            if os.path.exists(stop_path):
                state, message = "halted", f"STOP file present before {item['id']}"
                for rest in items[n:]:
                    rest["status"] = "skipped"
                break
            item["status"] = "baseline"
            write_status(status_path, state, items, message, started)
            print(f"BATCH: {n + 1}/{len(items)} {item['id']} - capturing guard baseline", flush=True)
            if runner([python, "-m", "src.chaos.k8s_monitor", "--baseline", "60"]).returncode != 0:
                item["status"], state, message = "failed", "failed", f"baseline failed before {item['id']}"
                for rest in items[n + 1:]:
                    rest["status"] = "skipped"
                break
            item["status"] = "running"
            write_status(status_path, state, items, message, started)
            print(f"BATCH: {n + 1}/{len(items)} {item['id']} - running", flush=True)
            code = runner([python, "-u", "-m", "src.run", "--exp", item["id"], "--execute", "--yes"]).returncode
            item["status"] = "ok" if code == 0 else "failed"
            write_status(status_path, state, items, message, started)
            if code != 0 and not keep_going:
                state, message = "failed", f"{item['id']} failed or was refused (exit {code})"
                for rest in items[n + 1:]:
                    rest["status"] = "skipped"
                break
            if n < len(items) - 1:
                sleep(cooldown_s)
        else:
            state = "done"
        if state in ("done", "failed") or any(i["status"] == "ok" for i in items):
            print("BATCH: scoring and writing the report", flush=True)
            for module in ("src.score", "src.report"):
                if runner([python, "-m", module]).returncode != 0:
                    message = (message + f" {module} failed.").strip()
        if state == "running":
            state = "done"
    except KeyboardInterrupt:
        state, message = "halted", "interrupted (Ctrl+C)"
    write_status(status_path, state, items, message, started)
    print(f"BATCH: {state}. {message}".strip(), flush=True)
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run all approved ChaosPilot experiments in sequence")
    parser.add_argument("--exp-ids", nargs="*", help="only these experiment ids (default: every runnable one)")
    parser.add_argument("--skip", nargs="*", default=[], help="experiment ids to leave out")
    parser.add_argument("--cooldown", type=int, default=60, help="seconds between experiments (default 60)")
    parser.add_argument("--keep-going", action="store_true", help="continue after a failed run (default: halt)")
    parser.add_argument("--list", action="store_true", help="show what would run, inject nothing")
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    args = parser.parse_args(argv)

    policy, executor = run_mod.load_yaml(run_mod.POLICY_PATH), run_mod.load_yaml(run_mod.EXECUTOR_PATH)
    with open(run_mod.PLAN_PATH, encoding="utf-8") as f:
        plan = json.load(f)
    ids = runnable_ids(plan, policy, executor, list(load_config()["journeys"]), skip=args.skip)
    if args.exp_ids:
        unknown = [i for i in args.exp_ids if i not in ids]
        if unknown:
            raise SystemExit(f"not approved or not runnable: {', '.join(unknown)}")
        ids = [i for i in ids if i in args.exp_ids]
    if not ids:
        raise SystemExit("nothing to run")
    print("BATCH will run, in order: " + ", ".join(ids), flush=True)
    if args.list:
        return 0
    if not args.yes:
        typed = input(f"Type {CONFIRM} to inject REAL faults in sequence: ").strip()
        if typed != CONFIRM:
            raise SystemExit("not confirmed, nothing was started")
    return 0 if run_batch(ids, executor["stop_path"], cooldown_s=args.cooldown, keep_going=args.keep_going) == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
