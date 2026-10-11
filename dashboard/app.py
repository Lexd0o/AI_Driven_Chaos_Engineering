"""ChaosPilot dashboard. Run: streamlit run dashboard/app.py (from the repo root).

Read-only except the STOP button (python -m src.run --stop-all). It never starts experiments.
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dash_data as dd  # noqa: E402

ROOT = dd.REPO_ROOT
P = {k: ROOT / v for k, v in {
    "graph": "data/graph.json", "ranked": "data/ranked_candidates.json",
    "plan": "data/plan_validated.json", "scores": "data/scores.json",
    "report": "reports/findings_report.json", "events": "data/events.log",
    "bench": "data/benchmark.json", "exec": "config/executor.yaml", "runs": "data/runs"}.items()}

st.set_page_config(page_title="ChaosPilot", layout="wide")
st.title("ChaosPilot - resilience dashboard")


def need(path, cmd):
    st.info("`%s` not found yet. Create it with: `%s`" % (path, cmd))


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Data snapshots")
    for k in ("graph", "ranked", "plan", "scores", "report", "bench", "events"):
        st.caption("%s: %s" % (P[k].name, dd.file_mtime(P[k]) or "missing"))
    if st.button("Refresh"):
        st.rerun()
    st.divider()
    st.header("STOP")
    st.caption("Removes any running chaos and writes the stop flag.")
    sure = st.checkbox("I confirm: stop all chaos now")
    if st.button("STOP ALL CHAOS", type="primary", disabled=not sure, width="stretch"):
        try:
            res = subprocess.run([sys.executable, "-m", "src.run", "--stop-all"], cwd=str(ROOT),
                                 capture_output=True, text=True, timeout=60)
            st.code(((res.stdout or "") + (res.stderr or "")) or "(no output)")
            st.write("exit code", res.returncode)
        except Exception as exc:  # noqa: BLE001
            st.error("stop-all failed: %s" % exc)

scores = dd.load_json(P["scores"])
report = dd.load_json(P["report"])
graph = dd.load_json(P["graph"])
ranked = dd.load_json(P["ranked"], [])
plan = dd.load_json(P["plan"], [])
exec_cfg = dd.load_yaml(P["exec"])
runs = dd.load_runs(P["runs"])
frows = dd.finding_rows(report) if report else dd.finding_rows({"findings": scores.get("findings")})
confirmed = {r["service"] for r in frows if r["status"] == "confirmed"}

tabs = st.tabs(["Overview", "Dependency graph", "Risk ranking", "Experiments", "Findings & fixes", "Live health"])

# ----------------------------------------------------------------- overview
with tabs[0]:
    if not scores:
        need("data/scores.json", "python -m src.score")
    else:
        cov = scores.get("coverage", {})
        if scores.get("resilience_score") is not None and scores.get("score_status") == "scored":
            after = (scores.get("comparisons") or {}).get("after_fixes_score")
            if after is not None:
                m = st.columns(2)
                m[0].metric("Resilience score (current)", "%.0f / 100" % scores["resilience_score"])
                m[1].metric("After fixes", "%.0f / 100" % after,
                            "%+.1f" % (after - scores["resilience_score"]))
            else:
                st.metric("Resilience score", "%.0f / 100" % scores["resilience_score"])
        else:
            st.subheader("Not scored yet")
        st.caption(cov.get("text", ""))
        st.caption(scores.get("score_note", ""))
        cats = scores.get("categories", {})
        if cats:
            cols = st.columns(len(cats))
            for c, (name, v) in zip(cols, cats.items()):
                c.metric(name, "n/a" if v.get("score") is None else "%.1f" % v["score"],
                         "%s experiment(s)" % v.get("experiments"), delta_color="off")
        counts = scores.get("counts", {})
        cols = st.columns(4)
        for c, k in zip(cols, ("confirmed", "refuted", "inconclusive", "predicted")):
            c.metric(k.capitalize(), counts.get(k, 0))
        summ = (report.get("executive_summary") or {}) if report else {}
        if summ.get("text"):
            st.write(summ["text"])
            st.caption("source: %s" % ("AI-written" if summ.get("text_source") == "ai" else "template"))
    st.subheader("Benchmark")
    b = dd.load_json(P["bench"])
    if not b:
        need("data/benchmark.json", "python -m src.benchmark")
    else:
        c = st.columns(4)
        c[0].metric("Experiments run", b.get("n_run"))
        c[1].metric("Weaknesses hit", b.get("n_hits"))
        c[2].metric("Our first hit at", b.get("our_first_hit_position"))
        c[3].metric("Random mean first hit", b.get("random_mean_first_hit_position"))
        st.write("Share of random orderings as good or better:", b.get("share_random_as_good_or_better"))
        st.caption("%s - %s" % (b.get("status", ""), b.get("caveat", "")))

# -------------------------------------------------------------------- graph
with tabs[1]:
    if not graph:
        need("data/graph.json", "python -m src.graph")
    else:
        st.graphviz_chart(dd.build_dot(graph, ranked, confirmed), width="stretch")
        st.caption("Cylinder = stateful store. Solid = Jaeger-observed (call count); dashed = override. "
                   "Fill = risk score (green low, red high). Red thick border = confirmed weakness.")

# ------------------------------------------------------------------ ranking
with tabs[2]:
    if not ranked:
        need("data/ranked_candidates.json", "python -m src.rank")
    else:
        st.dataframe(pd.DataFrame(dd.risk_rows(ranked)), hide_index=True, width="stretch")
        with st.expander("Risk flags per service"):
            for r in ranked:
                st.write("**%s**: %s" % (r["service"], ", ".join(r.get("top_risks", [])) or "-"))

# -------------------------------------------------------------- experiments
with tabs[3]:
    if not plan:
        need("data/plan_validated.json", "python -m src.plan")
    else:
        st.dataframe(pd.DataFrame(dd.experiment_rows(plan, exec_cfg, runs)), hide_index=True,
                     width="stretch")
        st.caption("Experiments are started only from the CLI (python -m src.run). "
                   "'refused' = target is on the probe path or only checkout is impacted.")
    if not runs:
        need("data/runs/exp-*.json", "python -m src.run --execute <exp-id>")
    else:
        st.subheader("Runs by experiment and label")
        st.dataframe(pd.DataFrame(dd.run_rows(runs)), hide_index=True, width="stretch")
    comp_rows = dd.comparison_rows(scores)
    if comp_rows:
        st.subheader("Before / after fix")
        st.dataframe(pd.DataFrame(comp_rows), hide_index=True, width="stretch")
    sc_by = {e["experiment_id"]: e for e in scores.get("experiments", [])} if scores else {}
    for run in runs:
        title = "%s r%s - %s [%s]" % (run["experiment_id"], run.get("repeat"), run.get("outcome"),
                                      run.get("label") or "current")
        with st.expander(title):
            s = (sc_by.get(run["experiment_id"]) or {}).get("S") if not run.get("label") else None
            st.write("S:", s, "| peak error rate:", run.get("peak_error_rate"),
                     "| peak p95 ms:", run.get("peak_p95_ms"))
            st.write({k: run.get(k) for k in ("t_inject", "t_breach", "t_clear", "t_healthy")})
            rep_path = (run.get("evidence") or {}).get("agent_report")
            rep = dd.load_json(ROOT / rep_path) if rep_path else {}
            rows = dd.timeline_rows(rep)
            if not rows:
                st.info("No agent report samples found for this run.")
                continue
            df = pd.DataFrame(rows)
            st.caption("Latency (ms) by journey, seconds since first sample")
            st.line_chart(df.pivot_table(index="t_s", columns="journey", values="latency_ms"))
            st.caption("OK (1) / failed (0) by journey")
            st.line_chart(df.pivot_table(index="t_s", columns="journey", values="ok"))
            st.write("Markers (s since first sample):", dd.run_markers(run, rep))

# ----------------------------------------------------------------- findings
with tabs[4]:
    if not frows:
        need("reports/findings_report.json", "python -m src.report")
    else:
        opts = sorted({r["status"] for r in frows if r["status"]})
        only = st.multiselect("Status", opts,
                              default=[s for s in ("confirmed", "refuted", "inconclusive") if s in opts])
        for r in frows:
            if only and r["status"] not in only:
                continue
            tag = " - fix verified by %s" % r["fix_verified_by"] if r.get("fix_verified") else ""
            with st.expander("[%s] %s - %s (%s)%s" % (r["severity"], r["service"], r["check"], r["status"], tag)):
                st.write(r["explanation"])
                st.caption("text source: %s" % r["text_source"])
                if r["why_fix_helps"]:
                    st.write("Why the fix helps:", r["why_fix_helps"])
                st.write("Fix:", r["fix_summary"])
                for cmd in r["apply_commands"]:
                    st.code(cmd, language="bash")
                if r["manual"]:
                    st.warning(r["manual"])

# -------------------------------------------------------------- live health
with tabs[5]:
    auto = st.checkbox("Auto-refresh every 10 s")
    go = st.button("Probe now")
    if go or auto:
        probes = exec_cfg.get("journey_probes") or {}
        if not probes:
            need("config/executor.yaml", "(see Plan.md T1.3)")
        out = []
        for name, cfg in probes.items():
            t0 = time.time()
            try:
                with urllib.request.urlopen(cfg["url"], timeout=3) as resp:
                    out.append({"journey": name, "status": resp.status,
                                "latency_ms": round((time.time() - t0) * 1000)})
            except Exception as exc:  # noqa: BLE001
                out.append({"journey": name, "status": "ERR %s" % type(exc).__name__, "latency_ms": None})
        st.dataframe(pd.DataFrame(out), hide_index=True)
    else:
        st.caption("Press 'Probe now' (needs `kubectl port-forward svc/frontend-proxy 8080:8080`).")
    st.subheader("Recent events")
    ev = dd.tail_lines(P["events"], 30)
    if ev:
        st.code("\n".join(ev))
    else:
        need("data/events.log", "python -m src.run --preview <exp-id>")
    if auto:
        time.sleep(10)
        st.rerun()
