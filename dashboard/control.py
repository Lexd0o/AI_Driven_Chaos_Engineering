"""Control tab for the dashboard: show the plan, start an experiment, recall it, show and apply fixes.

Everything that touches the cluster goes through the same commands used on the command line:
  start   python -m src.run --exp <id> --execute   (src.run + controller: policy, pre-checks, guardian, kill switch)
  recall  python -m src.run --stop-all             (STOP file + delete every chaos object)
  fixes   the kubectl command listed in reports/fixes/index.json, nothing else
The only LLM here is the remediation agent (section 5), which suggests and never acts. Two typed words guard the two actions that change the cluster:
  reslab-experiment  before a real fault, apply-fix  before a fix is applied or rolled back.
The pure functions below are tested in tests/test_dashboard_control.py; render() is the Streamlit layer.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLAN_PATH = ROOT / "data" / "plan_validated.json"
FIXES_INDEX = ROOT / "reports" / "fixes" / "index.json"
FIXES_DIR = ROOT / "reports" / "fixes"
SCORES_PATH = ROOT / "data" / "scores.json"
RUN_LOG = ROOT / "data" / "dashboard_run.log"
ACTIONS_LOG = ROOT / "data" / "dashboard_actions.log"
BACKUP_DIR = ROOT / "data" / "fix_backups"
REGISTRY = BACKUP_DIR / "registry.json"
GUARD_BASELINE = ROOT / "data" / "chaos" / "baseline.json"
SETTINGS_PATH = ROOT / "config" / "chaos_settings.json"
POLICY_PATH = ROOT / "config" / "policy.yaml"
EXECUTOR_PATH = ROOT / "config" / "executor.yaml"

CONFIRM_RUN = "reslab-experiment"
CONFIRM_FIX = "apply-fix"
NAMESPACE = "otel-demo"
BASELINE_MAX_AGE_S = 30 * 60


# ------------------------------------------------------------------ small readers
def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or half-written file must not break the page
        return default


def load_plan() -> list[dict]:
    return load_json(PLAN_PATH, []) or []


def load_fixes() -> list[dict]:
    return load_json(FIXES_INDEX, []) or []


def fixes_for(item: dict, fixes: list[dict]) -> list[dict]:
    """The fix linked to the experiment's risk first, then the other findings for the same service."""
    own = [f for f in fixes if f["risk_id"] == item.get("risk_id")]
    rest = [f for f in fixes if f["service"] == item["target"]["service"] and f["risk_id"] != item.get("risk_id")]
    return own + rest


def stop_file() -> Path:
    try:
        path = yaml.safe_load(EXECUTOR_PATH.read_text(encoding="utf-8")).get("stop_path")
    except Exception:  # noqa: BLE001
        path = None
    return ROOT / (path or "data/STOP")


def guard_settings() -> dict:
    """The limits the guardian enforces, from config (display only here; src/chaos owns the enforcement)."""
    safety = (load_json(SETTINGS_PATH, {}) or {}).get("safety", {})
    try:
        max_duration = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")).get("max_duration_s")
    except Exception:  # noqa: BLE001
        max_duration = None
    return {"error_ratio_max": safety.get("error_ratio_max"), "consecutive_breaches": safety.get("consecutive_breaches"),
            "latency_multiplier": safety.get("latency_multiplier"), "latency_floor_ms": safety.get("latency_floor_ms"),
            "max_experiment_seconds": safety.get("max_experiment_seconds"), "policy_max_duration_s": max_duration}


def guard_baseline_age_s(now: float | None = None) -> float | None:
    """Seconds since the guard baseline was captured (python -m src.chaos.k8s_monitor --baseline 60)."""
    captured = (load_json(GUARD_BASELINE, {}) or {}).get("captured_at")
    if not captured:
        return None
    try:
        return (now or time.time()) - datetime.fromisoformat(captured).timestamp()
    except ValueError:
        return None


def latest_run_summary(runs_dir: Path = ROOT / "data" / "runs") -> dict | None:
    """The newest run file: did the guardian stop it, and why."""
    files = sorted(Path(runs_dir).glob("*.json"), key=lambda p: p.stat().st_mtime) if Path(runs_dir).exists() else []
    for path in reversed(files):
        if "-forced-" in path.name:
            continue
        run = load_json(path)
        if run:
            return {"file": path.name, "outcome": run.get("outcome"), "aborted": bool(run.get("aborted")),
                    "abort_reason": run.get("abort_reason"), "run_id": run.get("run_id")}
    return None


# ------------------------------------------------------------------ the plan table
def plan_rows(plan: list[dict], scores: dict | None, refusal_for) -> list[dict]:
    """One row per approved experiment: what it is, whether it can run, and its latest result."""
    by_id = {e["experiment_id"]: e for e in (scores or {}).get("experiments", [])}
    rows = []
    for item in plan:
        why = refusal_for(item)
        score = by_id.get(item["experiment_id"], {})
        rows.append({
            "experiment": item["experiment_id"], "rank": item["rank"], "fault": item["fault_type"],
            "target": item["target"]["service"],
            "can run": "yes" if not why else f"no: {why[:70]}",
            "result": ", ".join(r["outcome"] for r in score.get("runs", [])) or "not run",
            "S": None if score.get("S") is None else round(score["S"], 1),
            "severity": score.get("severity") or score.get("status") or "not run",
        })
    return rows


# ------------------------------------------------------------------ starting and recalling a run
class RunSlot:
    """Holds the one run this dashboard started. Shared by every browser session (st.cache_resource)."""

    def __init__(self):
        self.proc = None
        self.exp = None
        self.started = None
        self.log = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def returncode(self):
        return None if self.proc is None else self.proc.poll()

    def close_log(self) -> None:
        if self.log is not None:
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None


def start_run(slot: RunSlot, exp_id: str, typed: str, plan: list[dict], refusal_for, stop_path: Path | None = None,
              python: str = sys.executable, popen=subprocess.Popen) -> tuple[bool, str]:
    """Launch `python -m src.run --exp <id> --execute` and answer its confirmation prompt with the typed word."""
    stop_path = stop_path if stop_path is not None else stop_file()
    if typed.strip() != CONFIRM_RUN:
        return False, f"Type {CONFIRM_RUN} to confirm. Nothing was started."
    item = next((i for i in plan if i["experiment_id"] == exp_id), None)
    if item is None:
        return False, f"{exp_id} is not an approved experiment. Nothing was started."
    if slot.alive():
        return False, f"{slot.exp} is still running. Wait for it to finish, or recall it."
    if stop_path.exists():
        return False, f"STOP file present ({stop_path.name}): new runs are refused. Re-arm first, after checking the shop."
    why = refusal_for(item)
    if why:
        return False, f"{exp_id} is refused: {why}"

    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    slot.close_log()   # the previous run is finished (checked above); release its log file before reusing it
    log = open(RUN_LOG, "w", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = popen([python, "-u", "-m", "src.run", "--exp", exp_id, "--execute"], cwd=ROOT, stdin=subprocess.PIPE,
                 stdout=log, stderr=subprocess.STDOUT, text=True, env=env, creationflags=flags)
    try:
        proc.stdin.write(CONFIRM_RUN + "\n")
        proc.stdin.close()
    except OSError:
        pass
    slot.proc, slot.exp, slot.started, slot.log = proc, exp_id, time.time(), log
    log_action(f"start {exp_id}")
    return True, f"Started {exp_id}. The guardian and kill switch run as usual; watch the stage below."


BATCH_STATUS = ROOT / "data" / "batch_status.json"


def start_batch(slot: RunSlot, ids: list[str], typed: str, plan: list[dict], refusal_for, stop_path: Path | None = None,
                cooldown_s: int = 60, python: str = sys.executable, popen=subprocess.Popen) -> tuple[bool, str]:
    """Launch `python -m src.run_all --exp-ids ...` and answer its single confirmation prompt."""
    stop_path = stop_path if stop_path is not None else stop_file()
    if typed.strip() != CONFIRM_RUN:
        return False, f"Type {CONFIRM_RUN} to confirm. Nothing was started."
    if not ids:
        return False, "Pick at least one experiment. Nothing was started."
    by_id = {p["experiment_id"]: p for p in plan}
    for exp_id in ids:
        if exp_id not in by_id:
            return False, f"{exp_id} is not an approved experiment. Nothing was started."
        why = refusal_for(by_id[exp_id])
        if why:
            return False, f"{exp_id} is refused: {why}"
    if slot.alive():
        return False, f"{slot.exp} is still running. Wait for it to finish, or recall it."
    if stop_path.exists():
        return False, f"STOP file present ({stop_path.name}): new runs are refused. Re-arm first, after checking the shop."

    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    slot.close_log()
    log = open(RUN_LOG, "w", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = popen([python, "-u", "-m", "src.run_all", "--exp-ids", *ids, "--cooldown", str(cooldown_s)], cwd=ROOT,
                 stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, text=True, env=env, creationflags=flags)
    try:
        proc.stdin.write(CONFIRM_RUN + "\n")
        proc.stdin.close()
    except OSError:
        pass
    slot.proc, slot.exp, slot.started, slot.log = proc, f"batch of {len(ids)}", time.time(), log
    log_action(f"start batch {','.join(ids)}")
    return True, f"Started a batch of {len(ids)}: {', '.join(ids)}. Each runs with its own baseline, guardian and kill switch."


def recall(python: str = sys.executable, runner=subprocess.run) -> tuple[bool, str]:
    """Recall: STOP file + delete every chaos object, exactly what the sidebar STOP button runs."""
    r = runner([python, "-m", "src.run", "--stop-all"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    log_action(f"recall exit={r.returncode}")
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()


def rearm(stop_path: Path | None = None) -> bool:
    """Delete the STOP file so new runs are allowed again."""
    path = stop_path if stop_path is not None else stop_file()
    if path.exists():
        path.unlink()
        log_action("re-armed (STOP file deleted)")
        return True
    return False


def read_log_tail(lines: int = 30, path: Path = RUN_LOG) -> str:
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def current_stage(log_text: str) -> str | None:
    stages = [ln.split("STAGE:", 1)[1].strip() for ln in log_text.splitlines() if "STAGE:" in ln]
    return stages[-1] if stages else None


def guardian_state(log_text: str, finished: bool) -> str:
    """Plain words for the automatic-stop panel, from what the run printed."""
    low = log_text.lower()
    if "guarding" in low and not finished:
        return "ARMED: watching the shop now; it removes the fault itself if a limit is crossed."
    if finished and ("guardian" in low and ("breach" in low or "aborted" in low or "interrupted" in low)):
        return "TRIGGERED: the guardian stopped the run early (see the log)."
    if finished:
        return "Run finished. See the latest run below for whether the guardian stopped it."
    return "Not armed yet: pre-checks are running."


# ------------------------------------------------------------------ fixes: validate, undo, apply
def log_action(message: str) -> None:
    ACTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def parse_apply(apply_cmd: str, fixes_dir: Path = FIXES_DIR) -> dict:
    """Turn a command from reports/fixes/index.json into a checked argv. Only `kubectl patch|apply` on a file
    inside reports/fixes, in the otel-demo namespace, is accepted."""
    argv = shlex.split(apply_cmd, posix=True)
    if not argv or argv[0] != "kubectl":
        raise ValueError("only kubectl commands are accepted")
    if any(ch in apply_cmd for ch in ";|&><`$"):
        raise ValueError("shell operators are not accepted")
    verb = next((a for a in argv[1:] if a in ("patch", "apply")), None)
    if verb is None:
        raise ValueError("only `kubectl patch` and `kubectl apply` are accepted")
    if "-n" in argv and argv[argv.index("-n") + 1] != NAMESPACE:
        raise ValueError(f"only the {NAMESPACE} namespace is accepted")
    files = [a for a in argv if a.endswith((".yaml", ".yml"))]
    if len(files) != 1:
        raise ValueError("exactly one YAML file is expected")
    path = (ROOT / files[0]).resolve()
    if Path(fixes_dir).resolve() not in path.parents or not path.exists():
        raise ValueError("the file must exist inside reports/fixes")
    service = None
    if verb == "patch":
        if "deployment" not in argv or argv.index("deployment") + 1 >= len(argv):
            raise ValueError("only deployments can be patched")
        service = argv[argv.index("deployment") + 1]
    return {"argv": argv, "verb": verb, "file": path, "service": service}


def inverse_patch(original, patch):
    """The strategic-merge patch that puts back what `patch` changes: original values for fields that existed,
    null (delete) for fields that did not. Lists of named objects (containers) are matched by name."""
    if isinstance(patch, dict):
        out = {}
        for key, value in patch.items():
            if isinstance(original, dict) and key in original:
                out[key] = inverse_patch(original[key], value) if isinstance(value, (dict, list)) else original[key]
            else:
                out[key] = None
        return out
    if isinstance(patch, list):
        out = []
        for item in patch:
            match = None
            if isinstance(item, dict) and "name" in item and isinstance(original, list):
                match = next((o for o in original if isinstance(o, dict) and o.get("name") == item["name"]), None)
            if match is not None:
                undo = inverse_patch(match, item)
                undo["name"] = item["name"]
                out.append(undo)
            else:
                out.append(item)
        return out
    return original


def run_kubectl(argv: list[str], runner=subprocess.run) -> tuple[bool, str]:
    r = runner(argv, cwd=ROOT, capture_output=True, text=True, timeout=90)
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()


def dry_run_fix(parsed: dict, runner=subprocess.run) -> tuple[bool, str]:
    """Ask the API server to validate the change without applying it."""
    return run_kubectl(parsed["argv"] + ["--dry-run=server"], runner)


def _registry() -> dict:
    return load_json(REGISTRY, {}) or {}


def _save_registry(reg: dict) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def is_applied(parsed: dict) -> bool:
    return parsed["file"].name in _registry()


def apply_fix(parsed: dict, typed: str, slot: RunSlot | None, runner=subprocess.run) -> tuple[bool, str]:
    """Apply one fix. Saves how to undo it first (the original values for a patch; delete for a new object)."""
    if typed.strip() != CONFIRM_FIX:
        return False, f"Type {CONFIRM_FIX} to confirm. Nothing was applied."
    if slot is not None and slot.alive():
        return False, "An experiment is running. Wait for it to finish or recall it first."
    undo_file = None
    if parsed["verb"] == "patch":
        ok, out = run_kubectl(["kubectl", "-n", NAMESPACE, "get", "deployment", parsed["service"], "-o", "json"], runner)
        if not ok:
            return False, f"Could not read the current deployment, so nothing was applied: {out}"
        original = json.loads(out)
        patch = yaml.safe_load(parsed["file"].read_text(encoding="utf-8"))
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (BACKUP_DIR / f"{parsed['service']}-{stamp}-before.json").write_text(json.dumps(original), encoding="utf-8")
        undo_file = BACKUP_DIR / f"{parsed['file'].stem}-undo.json"
        undo_file.write_text(json.dumps(inverse_patch(original, patch), indent=2), encoding="utf-8")
    ok, out = run_kubectl(parsed["argv"], runner)
    if ok:
        reg = _registry()
        reg[parsed["file"].name] = {"verb": parsed["verb"], "service": parsed["service"], "file": str(parsed["file"]),
                                    "undo": str(undo_file) if undo_file else None,
                                    "applied_at": datetime.now().isoformat(timespec="seconds")}
        _save_registry(reg)
    log_action(f"apply {parsed['file'].name} ok={ok}")
    return ok, out


def rollback_fix(parsed: dict, typed: str, slot: RunSlot | None, runner=subprocess.run) -> tuple[bool, str]:
    """Undo an applied fix: restore the saved original values (patch) or delete the object (new PDB/HPA)."""
    if typed.strip() != CONFIRM_FIX:
        return False, f"Type {CONFIRM_FIX} to confirm. Nothing was changed."
    if slot is not None and slot.alive():
        return False, "An experiment is running. Wait for it to finish or recall it first."
    entry = _registry().get(parsed["file"].name)
    if not entry:
        return False, "This fix was not applied from the dashboard, so there is nothing saved to roll back."
    if parsed["verb"] == "patch":
        argv = ["kubectl", "-n", NAMESPACE, "patch", "deployment", parsed["service"], "--patch-file", entry["undo"]]
    else:
        argv = ["kubectl", "delete", "-f", str(parsed["file"])]
    ok, out = run_kubectl(argv, runner)
    if ok:
        reg = _registry()
        reg.pop(parsed["file"].name, None)
        _save_registry(reg)
    log_action(f"rollback {parsed['file'].name} ok={ok}")
    return ok, out


# ------------------------------------------------------------------ the Streamlit layer
def _refusal_fn():
    """src.run's own refusal rules, so the dashboard never offers what the command would refuse."""
    try:
        from src import run as run_mod
        from src.telemetry import load_config
        policy, executor = run_mod.load_yaml(run_mod.POLICY_PATH), run_mod.load_yaml(run_mod.EXECUTOR_PATH)
        journeys = list(load_config()["journeys"])
        return lambda item: run_mod.refusal(item, policy, executor, journeys)
    except Exception:  # noqa: BLE001
        return lambda item: None


def render(st, slot: RunSlot, auto_refresh: bool = True) -> None:
    import pandas as pd

    plan = load_plan()
    scores = load_json(SCORES_PATH)
    refusal_for = _refusal_fn()
    fixes = load_fixes()
    if not plan:
        st.info("No approved plan yet. Build it with `python -m src.plan` then `python -m src.policy`.")
        return

    st.subheader("1. The plan")
    st.dataframe(pd.DataFrame(plan_rows(plan, scores, refusal_for)), width="stretch", hide_index=True)
    ids = [p["experiment_id"] for p in plan]
    first_runnable = next((n for n, p in enumerate(plan) if refusal_for(p) is None), 0)
    choice = st.selectbox("Experiment", ids, index=first_runnable, format_func=lambda i: next(
        f"{i} · {p['fault_type']} on {p['target']['service']}" for p in plan if p["experiment_id"] == i), key="ctl_exp")
    item = next(p for p in plan if p["experiment_id"] == choice)
    why = refusal_for(item)
    st.markdown(f"**Hypothesis:** {item['hypothesis']}")
    st.markdown(f"**Why this experiment:** {item['reason']}")
    st.caption(f"Parameters: {item['params']} · expected journeys: {', '.join(item['expected_impact']['journeys'])} · "
               f"recovery budget {item.get('recovery_budget_s', '?')} s · planner: {item.get('planner', '?')}")

    st.subheader("2. Run it")
    age = guard_baseline_age_s()
    sf = stop_file()
    checks = [
        ("Approved by policy and runnable", why is None, why or ""),
        ("No STOP file", not sf.exists(), "Re-arm below after checking the shop." if sf.exists() else ""),
        ("No experiment already running", not slot.alive(), f"{slot.exp} is running." if slot.alive() else ""),
        ("Guard baseline under 30 min old", age is not None and age <= BASELINE_MAX_AGE_S,
         "Run `python -m src.chaos.k8s_monitor --baseline 60` first." if age is None or age > BASELINE_MAX_AGE_S else ""),
    ]
    for label, ok, note in checks:
        st.markdown(f"{'✅' if ok else '❌'} {label}" + (f" - {note}" if note and not ok else ""))
    st.caption("The command also checks the shop is healthy and that the fault can be removed, and refuses otherwise.")
    typed = st.text_input(f"Type `{CONFIRM_RUN}` to inject a REAL fault", key="ctl_confirm_run")
    if st.button("▶ Start experiment", type="primary", disabled=not all(c[1] for c in checks) or not typed):
        ok, message = start_run(slot, choice, typed, plan, refusal_for)
        (st.success if ok else st.error)(message)

    def run_panel() -> None:
        render_run_panel(st, slot)

    st.fragment(run_every=3 if auto_refresh else None)(run_panel)()

    st.markdown("**Run all approved experiments in a row**")
    runnable = [p["experiment_id"] for p in plan if refusal_for(p) is None]
    picked = st.multiselect("Experiments in this batch", runnable, default=runnable, key="ctl_batch_ids")
    st.caption(f"About {max(1, len(picked)) * 5} minutes. Each experiment captures its own fresh baseline, runs with the "
               "guardian and kill switch, then the batch waits a minute. It stops at the first failed run or when the "
               "STOP file appears. Scores and the report are written at the end.")
    typed_batch = st.text_input(f"Type `{CONFIRM_RUN}` to inject REAL faults in sequence", key="ctl_confirm_batch")
    batch_ok = bool(picked) and typed_batch and not sf.exists() and not slot.alive()
    if st.button("▶ Run batch", disabled=not batch_ok):
        ok, message = start_batch(slot, picked, typed_batch, plan, refusal_for)
        (st.success if ok else st.error)(message)

    def batch_panel() -> None:
        status = load_json(BATCH_STATUS)
        if status:
            st.caption(f"Batch {status['state']}" + (f" - {status['message']}" if status.get("message") else ""))
            st.dataframe(pd.DataFrame(status["items"]), width="stretch", hide_index=True)

    st.fragment(run_every=3 if auto_refresh else None)(batch_panel)()

    st.subheader("3. Automatic stop and recall")
    g = guard_settings()
    st.markdown(
        f"The **guardian** stops the fault by itself when, for {g['consecutive_breaches']} checks in a row, a journey's "
        f"error ratio is above **{g['error_ratio_max']}** or its p95 latency is above **{g['latency_multiplier']}x the baseline "
        f"(at least {g['latency_floor_ms']} ms)**, or when the run passes **{g['max_experiment_seconds']} s**. "
        f"Policy caps any fault at {g['policy_max_duration_s']} s, and a detached watchdog removes the fault even if the run crashes.")
    c1, c2 = st.columns(2)
    if c1.button("⏪ Recall: stop and remove every fault", type="primary"):
        ok, out = recall()
        (st.success if ok else st.error)("Recall sent. Faults removed; new runs are blocked until you re-arm." if ok
                                           else "Recall failed, check the output.")
        if out:
            st.code(out[-1500:])
    if c2.button("Re-arm (delete the STOP file)", disabled=not sf.exists()):
        rearm()
        st.success("Re-armed. New runs are allowed.")
    last = latest_run_summary()
    if last:
        st.caption(f"Latest run {last['run_id']}: **{last['outcome']}**"
                   + (f" - stopped early by the guardian ({last['abort_reason']})" if last["aborted"] else " - not stopped early"))

    st.subheader("4. Fixes")
    st.caption("Fixes are patches generated from the findings. Nothing is applied until you type "
               f"`{CONFIRM_FIX}`. Applying one changes the system you measured, so earlier scores describe the old system.")
    linked = fixes_for(item, fixes)
    if not linked:
        st.info("No fix is linked to this service.")
    for fix in linked:
        with st.expander(f"{fix['severity'].upper()} · {fix['summary']}", expanded=fix["risk_id"] == item.get("risk_id")):
            if fix.get("manual"):
                st.info(f"Manual: {fix['manual']}")
            for f in fix.get("files") or []:
                render_fix_file(st, slot, fix, f)

    render_agent(st, slot)
    render_recovery(st)


def render_agent(st, slot: RunSlot) -> None:
    """Section 5: the remediation agent suggests; applying still goes through render_fix_file's typed gate."""
    from src import remediate

    st.subheader("5. Remediation agent")
    st.caption("An LLM agent reads the audit findings and the measured experiment scores, decides which weaknesses "
               "are real, picks the fix for each from the generated patches and names the experiment that proves it. "
               "It never touches the cluster: you still dry-run, type `apply-fix`, and can roll back.")
    if st.button("Run the agent", key="ctl_agent_run"):
        with st.spinner("Agent working..."):
            try:
                remediate.run()
            except SystemExit as e:
                st.error(str(e))
    report = load_json(remediate.OUT_PATH, None)
    if not report:
        st.info("The agent has not run yet.")
        return
    st.markdown(f"**Agent:** `{report['agent']}` · {report['generated_at']}"
                + ("" if report["agent"].startswith("llm") else " · no LLM answer, deterministic ranking used"))
    for d in report.get("dropped") or []:
        st.caption(f"Dropped an invalid suggestion: {d}")
    verdicts = {v["risk_id"]: v for v in remediate.verify(report, load_json(SCORES_PATH, {}))}
    for it in report["items"]:
        v = verdicts.get(it["risk_id"])
        tag = f" · re-test: S {v['before']} -> {v['after']} ({v['verdict']})" if v else ""
        with st.expander(f"{it['priority']}. {it['audit_severity'].upper()} · {it['service']} · {it['fix_summary']}{tag}"):
            st.markdown(f"**Diagnosis:** {it['diagnosis']}")
            st.markdown(f"**Why this fix:** {it['why_this_fix']}")
            if it.get("verify_with"):
                st.markdown(f"**Prove it with:** `{it['verify_with']}` (run it again after applying; score before: {it['before']['S']})")
            if it.get("manual"):
                st.info(f"Manual: {it['manual']}")
            for f in it.get("files") or []:
                render_fix_file(st, slot, it, f, scope="agent_")


def latest_recovery(out_dir: Path | None = None) -> dict | None:
    folder = out_dir or ROOT / "reports" / "recovery"
    files = sorted(Path(folder).glob("run-*.recovery.json"), key=lambda p: p.stat().st_mtime)
    return load_json(files[-1], None) if files else None


def render_recovery(st) -> None:
    """Section 6: the Recovery agent explains the failure that was injected. Read-only, separate from the chaos agent."""
    st.subheader("6. Recovery agent: what happened and how to fix it")
    st.caption("After a run, this agent reads only the run's report and recorded probe data (it never talks to the chaos "
               "agent or the cluster). It verifies the fault really was injected, explains what the shop did, lists "
               "findings and proposes fixes for that specific failure.")
    c1, c2 = st.columns(2)
    if c1.button("Analyze the latest run", key="ctl_rec_run"):
        with st.spinner("Recovery agent working..."):
            try:
                from src import recover
                recover.analyze(recover.find_report(None))
            except SystemExit as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"{type(e).__name__}: {e}")
    if c2.button("Analyze latest run without the LLM", key="ctl_rec_run_nollm"):
        try:
            from src import recover
            recover.analyze(recover.find_report(None), use_llm=False)
        except SystemExit as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"{type(e).__name__}: {e}")
    a = latest_recovery()
    if not a:
        st.info("No analysis yet. Run an experiment, then analyze it.")
        return
    v, e = a["verification"], a["experiment"]
    st.markdown(f"**Run:** `{a['experiment_id']}` · {e['fault_type']} on `{e['target']}` · "
                f"**Injection verdict: {v['verdict']}** · narrative: `{a['narrative']['source']}`")
    st.dataframe([{"check": c["name"], "passed": c["passed"], "detail": c["detail"]} for c in v["checks"]],
                 width="stretch", hide_index=True)
    if v["verdict"] == "UNCONFIRMED":
        st.warning("The injection could not be confirmed, so the agent stopped. Nothing below is claimed.")
        return
    st.markdown(f"**Explanation:** {a.get('summary', '')}")
    score = (a.get("resilience_score") or {}).get("total")
    st.markdown(f"**Resilience score for this experiment:** {score if score is not None else 'not computed (too few samples)'}")
    for f in a.get("findings") or []:
        st.markdown(f"- **[{f['severity']}] {f['title']}**: {f.get('explanation', '')}")
    for r in a.get("recommendations") or []:
        st.markdown(f"**Solution - {r['action_id']}:** {r['action']}  \n_{r['rationale']}_")
        for p in r.get("patches") or []:
            st.caption(f"Generated patch: {p['summary']} ({', '.join(p['files']) or p.get('manual') or 'manual'}) - apply it in section 4 or 5.")
    if a["narrative"].get("fallback_reason"):
        st.caption(f"Template text used: {a['narrative']['fallback_reason']}")


def render_run_panel(st, slot: RunSlot) -> None:
    if slot.proc is None:
        st.caption("No experiment started from this dashboard yet.")
        return
    log_text = read_log_tail(40)
    finished = not slot.alive()
    stage = current_stage(log_text)
    st.markdown(f"**{slot.exp}**: " + ("running" if not finished else f"finished (exit {slot.returncode()})")
                + (f" · stage `{stage}`" if stage else "") + f" · {int(time.time() - (slot.started or time.time()))} s since start")
    st.markdown(f"**Automatic stop:** {guardian_state(log_text, finished)}")
    st.code(log_text or "(no output yet)")


def render_fix_file(st, slot: RunSlot, fix: dict, f: dict, scope: str = "") -> None:
    """scope keeps widget keys unique when the same patch is shown in two sections."""
    key = scope + f["path"].replace("/", "_")
    try:
        parsed = parse_apply(f["apply"])
    except ValueError as e:
        st.error(f"`{f['path']}` is not applied from the dashboard: {e}")
        return
    st.code(parsed["file"].read_text(encoding="utf-8"), language="yaml")
    st.caption(f"`{f['apply']}`")
    applied = is_applied(parsed)
    typed = st.text_input(f"Type `{CONFIRM_FIX}` to apply or roll back", key=f"ctl_fix_{key}")
    a, b, c = st.columns(3)
    out = None
    if a.button("Dry-run (server check)", key=f"dry_{key}"):
        out = dry_run_fix(parsed)
    if b.button("Apply", key=f"apply_{key}", type="primary", disabled=applied or not typed):
        out = apply_fix(parsed, typed, slot)
    if c.button("Roll back", key=f"undo_{key}", disabled=not applied or not typed):
        out = rollback_fix(parsed, typed, slot)
    if applied:
        st.caption("Applied from this dashboard; roll back puts the saved original values back.")
    if out is not None:
        (st.success if out[0] else st.error)("Done." if out[0] else "Failed.")
        st.code(out[1] or "(no output)")
