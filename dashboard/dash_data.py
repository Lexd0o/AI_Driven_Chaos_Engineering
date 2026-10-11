"""Pure data-shaping helpers for the ChaosPilot dashboard (no Streamlit import).

Read-only: this module only reads files under the repo root.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_json(path, default=None):
    """Load JSON; return `default` (None -> {}) when missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def load_yaml(path):
    try:
        import yaml
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def tail_lines(path, n=30):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return [ln.rstrip("\n") for ln in fh.readlines()[-n:]]
    except OSError:
        return []


def file_mtime(path):
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return None


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- graph / DOT
def risk_color(score, max_score):
    """Green -> yellow -> red hex fill by risk_score relative to max."""
    if score is None or not max_score:
        return "#e8e8e8"
    f = max(0.0, min(1.0, float(score) / float(max_score)))
    if f < 0.5:
        r, g = int(120 + 270 * f), 200
    else:
        r, g = 255, int(200 - 330 * (f - 0.5))
    return "#%02x%02x%02x" % (min(r, 255), max(g, 40), 90)


def build_dot(graph, ranked=None, confirmed=None):
    """Build a Graphviz DOT string. Stateful -> cylinder; jaeger edges solid with
    call counts; override edges dashed; fill by risk_score; confirmed = red thick border."""
    graph = graph or {}
    scores = {r["service"]: r.get("risk_score") for r in (ranked or []) if "service" in r}
    mx = max([s for s in scores.values() if s is not None], default=0)
    confirmed = set(confirmed or [])
    lines = ["digraph G {", "  rankdir=LR;", '  node [style=filled, fontname="Helvetica"];']
    for n in graph.get("nodes", []):
        name = n.get("name")
        shape = "cylinder" if n.get("stateful") else "box"
        rs = scores.get(name)
        label = name if rs is None else "%s\\nrisk %.2f" % (name, rs)
        attrs = ['label="%s"' % label, "shape=%s" % shape, 'fillcolor="%s"' % risk_color(rs, mx)]
        if name in confirmed:
            attrs += ['color="#c00000"', "penwidth=4"]
        lines.append('  "%s" [%s];' % (name, ", ".join(attrs)))
    for e in graph.get("edges", []):
        if e.get("source") == "jaeger":
            lines.append('  "%s" -> "%s" [style=solid, label="%s"];' % (e["from"], e["to"], e.get("calls", "")))
        else:
            lines.append('  "%s" -> "%s" [style=dashed, label="%s"];' % (e["from"], e["to"], e.get("protocol", "override")))
    lines.append("}")
    return "\n".join(lines)


# ----------------------------------------------------------------- experiments
def classify_experiment(item, executor_cfg, run_ids):
    """Return 'run', 'refused' or 'not run'. Refused: target is a probe-path service,
    or the only impacted journey is checkout (no GET probe exists for it)."""
    executor_cfg = executor_cfg or {}
    target = (item.get("target") or {}).get("service")
    if item.get("experiment_id") in run_ids:
        return "run"
    if target in (executor_cfg.get("probe_path_services") or []):
        return "refused"
    journeys = (item.get("expected_impact") or {}).get("journeys") or []
    if journeys and set(journeys) == {"checkout"}:
        return "refused"
    return "not run"


def experiment_rows(plan, executor_cfg, runs):
    run_ids = {r.get("experiment_id") for r in runs if not r.get("label")}
    rows = []
    for it in plan or []:
        rows.append({
            "experiment_id": it.get("experiment_id"),
            "rank": it.get("rank"),
            "target": (it.get("target") or {}).get("service"),
            "fault": it.get("fault_type"),
            "status": classify_experiment(it, executor_cfg, run_ids),
        })
    return rows


def run_rows(runs):
    """One row per run record with its label ('current' when unlabelled), grouped by experiment/label."""
    rows = [{"experiment_id": r.get("experiment_id"), "label": r.get("label") or "current",
             "repeat": r.get("repeat"), "outcome": r.get("outcome"), "note": r.get("note"),
             "peak_error_rate": r.get("peak_error_rate"), "peak_p95_ms": r.get("peak_p95_ms")}
            for r in runs or []]
    return sorted(rows, key=lambda x: (str(x["experiment_id"]), x["label"] != "current", x["label"],
                                       x["repeat"] or 0))


def comparison_rows(scores):
    """Flat before/after rows from scores['comparisons']['rows']."""
    out = []
    for r in ((scores or {}).get("comparisons") or {}).get("rows") or []:
        b, a = r.get("before") or {}, r.get("after") or {}
        out.append({"experiment_id": r.get("experiment_id"), "label": r.get("label"),
                    "before_S": b.get("S"), "before_outcome": b.get("outcome"),
                    "after_S": a.get("S"), "after_outcome": a.get("outcome"),
                    "delta_S": r.get("delta_S")})
    return out


def load_runs(runs_dir):
    runs = []
    for p in sorted(Path(runs_dir).glob("exp-*-r*.json")):
        d = load_json(p)
        if d.get("experiment_id"):
            d["_file"] = p.name
            runs.append(d)
    return runs


def timeline_rows(report):
    """Rows {t_s, journey, latency_ms, ok, stage} from an agent report's samples."""
    samples = (report or {}).get("samples") or []
    t0 = None
    rows = []
    for s in samples:
        ts = parse_ts(s.get("observed_at"))
        if ts is None:
            continue
        t0 = t0 or ts
        t = round((ts - t0).total_seconds(), 2)
        js = s.get("journeys") or {}
        if not js:
            js = {"all": s}
        for name, j in js.items():
            rows.append({"t_s": t, "journey": name, "latency_ms": j.get("latency_ms"),
                         "ok": 1 if j.get("ok") else 0, "stage": s.get("stage")})
    return rows


def run_markers(run, report):
    """Seconds-from-first-sample for t_inject/t_breach/t_clear/t_healthy."""
    samples = (report or {}).get("samples") or []
    t0 = next((parse_ts(s.get("observed_at")) for s in samples if parse_ts(s.get("observed_at"))), None)
    out = {}
    if t0 is None:
        return out
    for k in ("t_inject", "t_breach", "t_clear", "t_healthy"):
        ts = parse_ts((run or {}).get(k))
        if ts:
            out[k] = round((ts - t0).total_seconds(), 2)
    return out


# -------------------------------------------------------------------- findings
def finding_rows(report):
    """Flatten findings, merging in explanation + text_source from `explained`."""
    report = report or {}
    expl = {e.get("risk_id"): e for e in report.get("explained") or []}
    rows = []
    for f in report.get("findings") or []:
        e = expl.get(f.get("risk_id"), {})
        fix = f.get("fix") or {}
        cmds = [x.get("apply") for x in fix.get("files") or [] if x.get("apply")]
        rows.append({
            "rank": f.get("rank"), "severity": f.get("severity"), "status": f.get("status"),
            "service": f.get("service"), "check": f.get("check"),
            "explanation": e.get("explanation") or f.get("evidence") or "",
            "why_fix_helps": e.get("why_fix_helps") or "",
            "text_source": {"ai": "AI-written", "template": "template"}.get(e.get("text_source"), "evidence only"),
            "fix_summary": fix.get("summary") or "",
            "apply_commands": cmds, "manual": fix.get("manual"),
            "fix_verified": bool(f.get("fix_verified")), "fix_verified_by": f.get("fix_verified_by"),
        })
    return rows


def risk_rows(ranked):
    return [{"rank": r.get("rank"), "service": r.get("service"), "risk_score": r.get("risk_score"),
             "centrality": r.get("centrality"), "fan_in": r.get("fan_in"),
             "gateway_share": r.get("gateway_share"), "blast_radius": r.get("blast_radius"),
             "risk_flags": r.get("risk_flags")} for r in ranked or []]
