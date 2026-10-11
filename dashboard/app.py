"""ChaosPilot dashboard - P2 (Observability).   Run from the repo root:  streamlit run dashboard/app.py

Reads data/runs.db (written by src.observe). Tabs: Live / Map / Runs / Findings / Control. STOP button runs `stop_command`
from config/journeys.yaml (no LLM involved). Every tab degrades gracefully when its data does not exist yet.
Thresholds below are DISPLAY defaults only - the guardrail thresholds are owned by P4.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dashboard import control  # noqa: E402
from src.observe import find_breach, find_healthy  # noqa: E402
from src.telemetry import edges_from_traces, load_config  # noqa: E402

CFG = load_config()
JOURNEYS = list(CFG["journeys"])
TZ = os.getenv("DASHBOARD_TZ", "Asia/Kolkata")
MIN_SUCCESS = 0.90          # display threshold: below this a journey is "Degraded"
P95_MULT = 3.0              # display threshold: p95 above N x baseline p95 is "Degraded"
ROLL = 5                    # samples per rolling window (5 x 2 s = 10 s)

# Categorical palette, fixed order, validated (dataviz reference palette slots 1-4).
SERIES = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"], "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"]}

st.set_page_config(page_title="ChaosPilot", page_icon="🧪", layout="wide")


# ----------------------------------------------------------------- helpers
def is_dark() -> bool:
    try:
        return st.context.theme.type == "dark"
    except Exception:  # noqa: BLE001
        return st.get_option("theme.base") == "dark"


def colors() -> dict[str, str]:
    pal = SERIES["dark" if is_dark() else "light"]
    return {j: pal[i % len(pal)] for i, j in enumerate(JOURNEYS)}


@st.cache_data(ttl=60)
def load_baseline() -> dict | None:
    p = ROOT / "data" / "baseline.json"
    try:
        b = json.loads(p.read_text(encoding="utf-8"))
        return b if b.get("valid") else None
    except Exception:  # noqa: BLE001
        return None


def read_db(db: Path, window_s: int | None, experiment: str | None) -> tuple[pd.DataFrame, list[str]]:
    """Returns (samples dataframe, experiment ids). Empty frame if the DB doesn't exist yet."""
    cols = ["ts", "experiment_id", "journey", "ok", "latency_ms", "status", "service", "metric", "value"]
    if not db.exists():
        return pd.DataFrame(columns=cols), []
    con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        exps = [r[0] for r in con.execute("SELECT DISTINCT experiment_id FROM samples WHERE experiment_id IS NOT NULL ORDER BY 1")]
        if experiment:
            df = pd.read_sql("SELECT * FROM samples WHERE experiment_id=? ORDER BY ts", con, params=(experiment,))
        else:
            mx = con.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
            if mx is None:
                return pd.DataFrame(columns=cols), exps
            since = mx - window_s if window_s else 0
            df = pd.read_sql("SELECT * FROM samples WHERE ts>=? ORDER BY ts", con, params=(since,))
    finally:
        con.close()
    return df, exps


def to_time(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, unit="s", utc=True).dt.tz_convert(TZ)


def style(fig: go.Figure, title: str, ytitle: str, yrange=None) -> go.Figure:
    dark = is_dark()
    ink = "#c3c2b7" if dark else "#52514e"
    grid = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.07)"
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15)), height=330, margin=dict(l=10, r=10, t=50, b=10),
        template="plotly_dark" if dark else "plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified", legend=dict(orientation="h", y=1.12, x=1, xanchor="right"), font=dict(color=ink),
    )
    fig.update_xaxes(showgrid=False, linecolor=grid)
    fig.update_yaxes(title=ytitle, gridcolor=grid, zeroline=False, range=yrange)
    return fig


def add_experiment_shading(fig: go.Figure, df: pd.DataFrame) -> None:
    if df.empty:
        return
    ex = df[df.experiment_id.notna() & ~df.experiment_id.astype(str).str.startswith("baseline")]
    for name, g in list(ex.groupby("experiment_id"))[:6]:
        t = to_time(g.ts)
        fig.add_vrect(x0=t.min(), x1=t.max(), fillcolor="rgba(128,128,128,0.12)", line_width=0,
                      annotation_text=str(name), annotation_position="top left", annotation_font_size=10)


def rolling_frame(jdf: pd.DataFrame) -> pd.DataFrame:
    out = []
    for j, g in jdf.groupby("journey"):
        g = g.sort_values("ts").copy()
        g["time"] = to_time(g.ts)
        g["success"] = g["ok"].astype(float).rolling(ROLL, min_periods=3).mean() * 100
        lat = g["latency_ms"].where(g["ok"] == 1)
        g["p95"] = lat.rolling(ROLL, min_periods=3).quantile(0.95)
        out.append(g)
    return pd.concat(out) if out else pd.DataFrame()


def status_label(success: float | None, p95: float | None, base_p95: float | None) -> str:
    if success is None:
        return "— No data"
    if success < 0.5:
        return "❌ Failing"
    if success < MIN_SUCCESS or (p95 and base_p95 and p95 > P95_MULT * base_p95):
        return "⚠️ Degraded"
    return "✅ Healthy"


DEFAULT_STOP = "python -m src.run --stop-all"
SCORES_PATH = ROOT / "data" / "scores.json"


def stop_command() -> str:
    """The configured STOP command; a leading `python` means the interpreter this dashboard runs under."""
    cmd = CFG.get("stop_command", DEFAULT_STOP).split("#")[0].strip()
    if cmd.startswith("python "):
        cmd = f'"{sys.executable}" ' + cmd[len("python "):]
    return cmd


def stop_file() -> Path:
    """Where src.run --stop-all leaves its STOP file (config/executor.yaml stop_path)."""
    try:
        import yaml
        path = yaml.safe_load((ROOT / "config" / "executor.yaml").read_text(encoding="utf-8")).get("stop_path")
    except Exception:  # noqa: BLE001
        path = None
    return ROOT / (path or "data/STOP")


def load_scores() -> dict | None:
    try:
        return json.loads(SCORES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------- sidebar
st.sidebar.title("🧪 ChaosPilot")
db_path = Path(st.sidebar.text_input("Database", os.getenv("DASHBOARD_DB", str(ROOT / CFG["db_path"]))))
_, all_exps = read_db(db_path, 60, None)
exp_choice = st.sidebar.selectbox("Experiment", ["(latest data)"] + all_exps)
window_label = st.sidebar.selectbox("Time window", ["Last 5 min", "Last 15 min", "Last 60 min", "All"], index=1,
                                    disabled=exp_choice != "(latest data)")
WINDOWS = {"Last 5 min": 300, "Last 15 min": 900, "Last 60 min": 3600, "All": None}
auto = st.sidebar.toggle("Auto-refresh (5 s)", value=True)
auto_score = st.sidebar.toggle("Re-score and re-report every 5 min", value=True,
                               help="Runs python -m src.score and python -m src.report in the background (no cluster access).")
auto_plan = st.sidebar.toggle("Refresh the plan from the cluster every 30 min", value=False,
                              help="Rebuilds the plan only if the cluster's risks changed; never during a run; ids are kept.")
live_monitor = st.sidebar.toggle("Live monitoring (probe the shop every 2 s)", value=True,
                                 help="Keeps the Live tab current when no experiment runs. Needs the shop port-forward and PROBE_BASE_URL.")
st.sidebar.divider()
st.sidebar.subheader("Emergency")
if st.sidebar.button("⛔ STOP ALL FAULTS", type="primary", width="stretch"):
    cmd = stop_command()
    try:
        r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=60)
        (st.sidebar.success if r.returncode == 0 else st.sidebar.error)(f"`{cmd}` exited {r.returncode}")
        if r.stdout or r.stderr:
            st.sidebar.code((r.stdout + r.stderr)[-1500:])
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"Could not run `{cmd}`: {e}")
st.sidebar.caption(f"Runs `{CFG.get('stop_command', DEFAULT_STOP)}` - no LLM involved.")
if stop_file().exists():
    st.sidebar.warning(f"STOP file present: new runs are refused. Delete `{stop_file()}` after checking the shop.")

base = load_baseline()
if base is None:
    st.warning("No valid baseline yet (`python -m src.baseline`). Status labels use fixed display thresholds.")

tab_live, tab_map, tab_runs, tab_find, tab_ctl = st.tabs(["Live", "Map", "Runs", "Findings", "Control"])


# ----------------------------------------------------------------- Live
def render_live() -> None:
    df, _ = read_db(db_path, WINDOWS[window_label], None if exp_choice == "(latest data)" else exp_choice)
    jdf = df[df.journey.notna()] if not df.empty else df
    if jdf.empty:
        st.info("No samples yet. Start the sampler:  `python -m src.observe`")
        return
    col = colors()
    cols = st.columns(len(JOURNEYS))
    for c, j in zip(cols, JOURNEYS):
        g = jdf[jdf.journey == j].tail(15)
        if g.empty:
            c.metric(j, "—")
            continue
        succ, ok_lat = g["ok"].mean(), g.loc[g["ok"] == 1, "latency_ms"]
        p95 = float(ok_lat.quantile(0.95)) if len(ok_lat) else None
        bp95 = base["journeys"].get(j, {}).get("p95_ms") if base else None
        c.metric(f"{j}", f"{succ * 100:.0f}% ok", help=f"Last 15 samples (~30 s). Baseline p95: {bp95 or 'n/a'} ms")
        c.caption(f"{status_label(succ, p95, bp95)} · p95 {p95:.0f} ms" if p95 else f"{status_label(succ, p95, bp95)} · p95 n/a")

    rf = rolling_frame(jdf)
    for metric, title, ytitle, yr in (("success", "Success rate per journey (10 s rolling)", "% successful", [0, 112]),
                                      ("p95", "p95 latency per journey, successful requests (10 s rolling)", "ms", None)):
        fig = go.Figure()
        for idx, j in enumerate(JOURNEYS):
            g = rf[rf.journey == j] if not rf.empty else rf
            if g.empty:
                continue
            fig.add_trace(go.Scatter(x=g["time"], y=g[metric], name=j, mode="lines", line=dict(color=col[j], width=2),
                                     hovertemplate="%{y:.1f}"))
            last = g.dropna(subset=[metric]).tail(1)
            if not last.empty:  # direct label at the line end
                fig.add_annotation(x=last["time"].iloc[0], y=last[metric].iloc[0], text=j, showarrow=False, xanchor="left",
                                   xshift=6, yshift=(idx - (len(JOURNEYS) - 1) / 2) * 14, font=dict(size=11, color=col[j]))
        if metric == "success":
            fig.add_hline(y=MIN_SUCCESS * 100, line_dash="dot", line_width=1, opacity=0.5,
                          annotation_text="display threshold", annotation_position="bottom right")
        add_experiment_shading(fig, jdf)
        st.plotly_chart(style(fig, title, ytitle, yr), width="stretch")

    sdf = df[df.service.notna()] if not df.empty else df
    st.subheader("Services")
    if sdf.empty:
        st.caption("No per-service rows yet (needs Prometheus reachable while the sampler runs).")
    else:
        latest = sdf.sort_values("ts").groupby(["service", "metric"]).tail(1)
        piv = latest.pivot(index="service", columns="metric", values="value").round(3)
        st.dataframe(piv.sort_values(piv.columns[0], ascending=False), width="stretch")
    with st.expander("Table view (journeys, latest 50 samples)"):
        st.dataframe(jdf.tail(50).assign(time=lambda d: to_time(d.ts))[["time", "journey", "ok", "latency_ms", "status", "experiment_id"]],
                     width="stretch", hide_index=True)


with tab_live:
    st.fragment(run_every=5 if auto else None)(render_live)()


# ----------------------------------------------------------------- Map
def render_map() -> None:
    edges, source = [], ""
    gp = ROOT / "data" / "graph.json"
    if gp.exists():
        try:
            g = json.loads(gp.read_text(encoding="utf-8"))
            edges, source = (g.get("edges", []) if isinstance(g, dict) else g), "data/graph.json"
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read graph.json: {e}")
    if st.button("Load edges from Jaeger traces (last 15 min)"):
        try:
            import time as _t
            edges, source = edges_from_traces(_t.time() - 900, _t.time()), "Jaeger (live)"
        except Exception as e:  # noqa: BLE001
            st.error(f"Jaeger not reachable: {e}")
    if not edges:
        st.info("No dependency data yet. P3 writes `data/graph.json`, or load edges from Jaeger once it is reachable.")
        return
    # accept both formats: P3 contract (from/to) and Jaeger helper (source/target)
    edges = [{"source": e.get("source", e.get("from")), "target": e.get("target", e.get("to")),
              **{k: v for k, v in e.items() if k not in ("source", "target", "from", "to")}} for e in edges]
    edges = [e for e in edges if e["source"] and e["target"]]
    st.caption(f"Source: {source}")
    nodes = sorted({e["source"] for e in edges} | {e["target"] for e in edges})
    pos = {n: (math.cos(2 * math.pi * i / len(nodes)), math.sin(2 * math.pi * i / len(nodes))) for i, n in enumerate(nodes)}
    fig = go.Figure()
    for e in edges:
        (x0, y0), (x1, y1) = pos[e["source"]], pos[e["target"]]
        fig.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines", line=dict(width=1, color="rgba(128,128,128,0.5)"),
                                 hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes], mode="markers+text", text=nodes,
                             textposition="top center", marker=dict(size=12, color=colors()[JOURNEYS[0]], line=dict(width=2, color="white")),
                             showlegend=False, hovertemplate="%{text}<extra></extra>"))
    fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
    st.plotly_chart(style(fig, "Service dependencies (caller → callee)", "").update_layout(hovermode="closest", height=520),
                    width="stretch")
    st.dataframe(pd.DataFrame(edges), width="stretch", hide_index=True)
    rp = ROOT / "data" / "risks.json"
    if rp.exists():
        st.subheader("Predicted risks")
        try:
            r = json.loads(rp.read_text(encoding="utf-8"))
            st.dataframe(pd.DataFrame(r if isinstance(r, list) else r.get("risks", [])), width="stretch")
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read risks.json: {e}")


with tab_map:
    render_map()


# ----------------------------------------------------------------- Runs
def render_scores() -> None:
    """Scored runs from data/scores.json (python -m src.score). Numbers are computed by code, shown as stored."""
    scores = load_scores()
    if scores is None:
        st.info("No scores yet. After runs finish:  `python -m src.score`  (then `python -m src.report`).")
        return
    cov = scores["coverage"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Overall resilience score", "n/a" if scores["overall"] is None else f"{scores['overall']:.1f} / 100")
    c2.metric("Coverage", f"{cov['scored']} of {cov['planned']} scored")
    c3.metric("Inconclusive / not run", f"{cov['inconclusive']} / {cov['not_run']}")
    rows = []
    for e in scores["experiments"]:
        rows.append({"experiment": e["experiment_id"], "fault": e["fault_type"], "target": e["target"],
                     "runs scored": f"{e['n_scored']}/{e['n_runs']}",
                     "outcome": ", ".join(r["outcome"] for r in e["runs"]) or "not run",
                     "S": None if e["S"] is None else round(e["S"], 1),
                     "severity": e["severity"] or e["status"], "prediction": e["prediction"]})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    with st.expander("Per-run details (A, L, R, C)"):
        detail = []
        for e in scores["experiments"]:
            for r in e["runs"]:
                detail.append({"experiment": e["experiment_id"], "repeat": r["repeat"], "run": r["run_id"],
                               "outcome": r["outcome"], "S": None if r["S"] is None else round(r["S"], 1),
                               "A": r["A"], "L": r["L"], "R": r["R"], "C": r["C"],
                               "impact_recovery_s": r["impact_recovery_s"], "stopped_by_guardian": r["aborted"],
                               "not scored because": "; ".join(r["reasons"])})
        st.dataframe(pd.DataFrame(detail), width="stretch", hide_index=True)
    st.caption(f"Formula {scores['formula']}. C = n/a when every probed journey is a target. "
               f"Scores file updated {datetime.fromtimestamp(SCORES_PATH.stat().st_mtime).strftime('%H:%M:%S')}.")


with tab_runs:
    st.subheader("Scored runs")
    render_scores()
    st.subheader("Raw samples per run")
    df_all, _ = read_db(db_path, None, None)
    jall = df_all[df_all.journey.notna() & df_all.experiment_id.notna()] if not df_all.empty else df_all
    jall = jall[~jall.experiment_id.astype(str).str.startswith("baseline")] if not jall.empty else jall
    if jall.empty:
        st.info("No experiment runs recorded yet. P4's runner tags samples with the experiment id.")
    else:
        rows = []
        for exp, g in jall.groupby("experiment_id"):
            for j in JOURNEYS:
                gj = g[g.journey == j].sort_values("ts")
                if gj.empty:
                    continue
                bp95 = base["journeys"].get(j, {}).get("p95_ms") if base else None
                slo = {"journey": j, "min_success": MIN_SUCCESS, "max_p95_ms": (P95_MULT * bp95) if bp95 else 1e9, "window_s": 10, "min_n": 3}
                recs = gj.to_dict("records")
                br = find_breach(recs, slo)
                rec = find_healthy(recs, slo, after_ts=br) if br else None
                lat = gj.loc[gj.ok == 1, "latency_ms"]
                rows.append({"experiment": exp, "journey": j, "samples": len(gj),
                             "duration_s": round(gj.ts.max() - gj.ts.min(), 1), "success_%": round(gj.ok.mean() * 100, 1),
                             "p95_ms": round(float(lat.quantile(0.95)), 0) if len(lat) else None,
                             "breached": br is not None, "recovery_s": round(rec - br, 1) if (br and rec) else None})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("Breach/recovery use display thresholds, not the guardrail's. recovery_s empty = no 30 s healthy stretch observed.")


# ----------------------------------------------------------------- Findings
with tab_find:
    for p in (ROOT / "reports" / "findings_report.md", ROOT / "data" / "findings_report.md", ROOT / "findings_report.md"):
        if p.exists():
            st.markdown(p.read_text(encoding="utf-8"))
            break
    else:
        st.info("No findings report yet. Write it with `python -m src.report` (it writes `reports/findings_report.md`).")


# ----------------------------------------------------------------- Control
@st.cache_resource
def ambient() -> control.Ambient:
    return control.Ambient()


@st.cache_resource
def rescorer() -> control.Rescorer:
    return control.Rescorer(interval_s=300)


@st.cache_resource
def plan_refresher() -> control.PlanRefresher:
    return control.PlanRefresher(interval_s=1800)


@st.cache_resource
def run_slot() -> control.RunSlot:
    """The one experiment this dashboard started (shared by every browser tab)."""
    return control.RunSlot()


st.session_state["_plan_refresher"] = plan_refresher()
with tab_ctl:
    control.render(st, run_slot(), auto)

if live_monitor:
    ambient().start(run_slot())
    if ambient().last_error:
        st.sidebar.caption(f"Live monitor error: {ambient().last_error}")
    else:
        st.sidebar.caption("Live monitor: probing the shop (no experiment needed).")
else:
    ambient().stop()

if auto_score:
    rescorer().start(run_slot())
    done = rescorer().last_ok
    st.sidebar.caption(rescorer().last_error or (f"Scores refreshed {done:%H:%M:%S}" if done else "Scores: first refresh running..."))
else:
    rescorer().stop()

if auto_plan:
    plan_refresher().start(run_slot())
else:
    plan_refresher().stop()
