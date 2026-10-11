"""recover.py - run the Recovery & Solutions agent on one finished experiment.

    python -m src.recover --latest            # newest executed run
    python -m src.recover --run run-1a2b3c    # a specific run
    python -m src.recover --latest --no-llm   # deterministic text only (labelled as a fallback)

What the agent does for that run (agents/recovery_agent, LangGraph + Groq):
  VERIFY    was a fault really injected? Five independent checks (live run, controller confirmation,
            consistent timeline, manifest matches the plan, measurable effect on the shop probes).
            If the injection cannot be confirmed the analysis stops there.
  EXPLAIN   what the probes showed: error rate, slow responses, latency vs baseline, recovery time.
  FIND      ranked findings with a resilience score for this experiment.
  SOLVE     recommended actions for the failure that was injected, plus the concrete patches already
            generated for that service (reports/fixes/index.json). Nothing is applied.

It is decoupled from the chaos agent on purpose: it never imports src.chaos, never calls it and never
touches the cluster. Its only inputs are files the chaos agent already left behind:
  data/chaos/reports/<run>.json   the run report (plan, manifest, events, timestamps)
  data/runs.db                    the probe samples recorded during that run
  data/graph.json                 replica counts (for the single-replica finding)
  reports/fixes/index.json        generated patches
Output: reports/recovery/<run>.recovery.json and .md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "data" / "chaos" / "reports"
DB_PATH = ROOT / "data" / "runs.db"
GRAPH_PATH = ROOT / "data" / "graph.json"
PLAN_PATH = ROOT / "data" / "plan_validated.json"
FIXES_INDEX = ROOT / "reports" / "fixes" / "index.json"
OUT_DIR = ROOT / "reports" / "recovery"
AGENT_DIR = ROOT / "agents" / "recovery_agent"

# which generated patches answer which recommended action
ACTION_CHECKS = {
    "add_replicas": ("single_replica_no_pdb",),
    "tune_readiness": ("no_readiness_probe", "no_liveness_probe", "no_resource_limits"),
    "cpu_headroom": ("no_resource_limits", "no_hpa_shared_dependency"),
}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _load(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------ input contract
def read_samples(run_id: str, journeys, db_path: Path = DB_PATH) -> list[dict]:
    """Probe rows recorded for this run (read-only)."""
    if not Path(db_path).exists():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT ts, journey, ok, latency_ms, status FROM samples "
                           "WHERE experiment_id = ? AND journey IS NOT NULL ORDER BY ts", (run_id,)).fetchall()
    finally:
        con.close()
    keep = set(journeys or [])
    return [{"ts": r[0], "journey": r[1], "ok": bool(r[2]), "latency_ms": r[3], "status": r[4]}
            for r in rows if not keep or r[1] in keep]


def stage_samples(rows: list[dict], t_start: float, t_inject: float, t_clear: float) -> list[dict]:
    """Label each probe baseline / during_fault / after_cleanup by its time relative to the run's own timestamps."""
    out = []
    for r in rows:
        if r["ts"] < t_start:
            continue
        stage = "baseline" if r["ts"] < t_inject else "during_fault" if r["ts"] <= t_clear else "after_cleanup"
        error = None if r["ok"] else (f"http {r['status']}" if r.get("status") else "timeout, no response")
        out.append({"observed_at": _iso(r["ts"]), "ok": r["ok"], "latency_ms": float(r["latency_ms"] or 0.0),
                    "error": error, "stage": stage})
    return out


def build_contract(report: dict, samples: list[dict], graph: dict | None, plan_item: dict | None) -> dict:
    """Map the chaos agent's run report to the recovery agent's input contract (pure, no I/O)."""
    plan = dict(report.get("plan") or {})
    if plan_item and plan_item.get("hypothesis"):
        plan.setdefault("hypothesis", plan_item["hypothesis"])
    guard = report.get("guard") or {}
    outcome = guard.get("outcome")
    stop_reason = "probe_threshold_breach" if outcome == "breach" else outcome
    contract = {
        "run_id": report.get("run_id"), "mode": report.get("mode"), "plan": plan,
        "manifest": report.get("manifest"), "resource": {"name": report.get("resource_name")},
        "events": report.get("events", []), "started_at": report.get("started_at"),
        "fault_injected": report.get("fault_injected"), "fault_confirmed_at": report.get("fault_confirmed_at"),
        "cleanup_confirmed": report.get("cleanup_confirmed"), "cleanup_observed_at": report.get("cleanup_observed_at"),
        "stop_reason": stop_reason,
        "post_cleanup_probe_recovery_seconds": (guard.get("recovery_seconds")),
        "samples": [],
    }
    t_start, t_inject, t_clear = report.get("t_start_epoch"), report.get("t_inject_epoch"), report.get("t_clear_epoch")
    if None not in (t_start, t_inject, t_clear):
        contract["samples"] = stage_samples(samples, t_start, t_inject, t_clear)
    if graph:
        contract["snapshot"] = {"data_source": "data/graph.json",
                                "services": [{"id": n["name"], "replicas": n.get("replicas")} for n in graph.get("nodes", [])]}
    return contract


# ------------------------------------------------------------------ solutions
def patches_for(service: str, action_id: str, fixes: list[dict]) -> list[dict]:
    checks = ACTION_CHECKS.get(action_id, ())
    return [{"risk_id": f["risk_id"], "summary": f["summary"], "files": [x["path"] for x in f.get("files") or []],
             "manual": f.get("manual")}
            for f in fixes if f["service"] == service and f["check"] in checks]


def attach_solutions(analysis: dict, fixes: list[dict]) -> dict:
    service = (analysis.get("experiment") or {}).get("target")
    for rec in analysis.get("recommendations") or []:
        rec["patches"] = patches_for(service, rec["action_id"], fixes) if service else []
    return analysis


def markdown_patches(analysis: dict) -> str:
    lines = ["", "## Concrete patches for this service (generated, not applied)", ""]
    seen = False
    for rec in analysis.get("recommendations") or []:
        for p in rec.get("patches") or []:
            seen = True
            where = ", ".join(f"`{f}`" for f in p["files"]) or p.get("manual") or "manual"
            lines.append(f"- **{rec['action_id']}**: {p['summary']} -> {where}")
    if not seen:
        lines.append("- no generated patch matches these actions (the actions are application-level changes)")
    lines.append("- Apply through the dashboard Control tab: dry-run, type `apply-fix`, roll back if needed.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ run
def find_report(run: str | None, reports_dir: Path = REPORTS_DIR) -> Path:
    if run:
        path = Path(reports_dir) / f"{run}.json"
        if not path.exists():
            raise SystemExit(f"{path} not found")
        return path
    files = []
    for p in Path(reports_dir).glob("run-*.json"):
        d = _load(p, {})
        if d.get("mode") == "execute":
            files.append(p)
    if not files:
        raise SystemExit(f"no executed run report in {reports_dir} (mock and preview runs are skipped)")
    return max(files, key=lambda p: p.stat().st_mtime)


def analyze(report_path: Path, use_llm: bool = True) -> tuple[dict, dict]:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    report = _load(report_path, None)
    if not report:
        raise SystemExit(f"cannot read {report_path}")
    plan_item = next((p for p in _load(PLAN_PATH, []) or []
                      if p.get("experiment_id") == report.get("plan_experiment_id")), None)
    samples = read_samples(report.get("run_id", ""), report.get("target_journeys")) \
        or read_samples(report.get("run_id", ""), None)
    contract = build_contract(report, samples, _load(GRAPH_PATH), plan_item)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    contract_path = OUT_DIR / f"{contract['run_id']}.input.json"
    contract_path.write_text(json.dumps(contract, indent=2), encoding="utf-8")

    sys.path.insert(0, str(AGENT_DIR))
    try:
        import recovery_agent
    except ImportError as e:
        raise SystemExit(f"{e} - run: pip install -r agents/recovery_agent/requirements.txt")
    analysis, paths = recovery_agent.analyze_report(contract_path, use_llm=use_llm, out_dir=OUT_DIR)
    fixes = _load(FIXES_INDEX, []) or []
    attach_solutions(analysis, fixes)
    Path(paths["json"]).write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    md = Path(paths["markdown"])
    md.write_text(md.read_text(encoding="utf-8") + markdown_patches(analysis), encoding="utf-8")
    return analysis, paths


def main() -> None:
    ap = argparse.ArgumentParser(description="Recovery & Solutions agent for one finished experiment")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", help="run id, e.g. run-2640f74be99e")
    g.add_argument("--latest", action="store_true", help="newest executed run")
    ap.add_argument("--no-llm", action="store_true", help="deterministic text only")
    args = ap.parse_args()
    path = find_report(args.run)
    analysis, paths = analyze(path, use_llm=not args.no_llm)
    v = analysis["verification"]
    print(f"run {analysis['experiment_id']}: {analysis['experiment']['fault_type']} on {analysis['experiment']['target']}")
    print(f"injection verdict: {v['verdict']}  |  narrative: {analysis['narrative']['source']}")
    for c in v["checks"]:
        print(f"  [{'ok' if c['passed'] else 'FAIL'}] {c['name']}: {c['detail']}")
    print(f"resilience score (this experiment): {analysis['resilience_score']['total']}")
    print(analysis["summary"])
    for r in analysis.get("recommendations") or []:
        print(f"  -> {r['action_id']}: {r['rationale']}")
    if analysis["narrative"].get("fallback_reason"):
        print(f"fallback reason: {analysis['narrative']['fallback_reason']}")
    print(f"saved: {paths['json']}")


if __name__ == "__main__":
    main()
