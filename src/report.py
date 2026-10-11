"""report.py - P5: the findings report, written by code from data/scores.json (no LLM, nothing invented).

    python -m src.report            # scores the runs (src.score) and writes reports/findings_report.md

Sections: summary table with coverage, how the score works, findings (impact seen, worst first),
tolerated faults (prediction not confirmed), not scored (inconclusive), notes and limits.
Each finding links the matching fix from reports/fixes/index.json (suggestions only: nothing is applied).
"""
import argparse
import os
from datetime import datetime, timezone

from src import benchmark, score

FIXES_INDEX_PATH = "reports/fixes/index.json"
REPORT_PATH = "reports/findings_report.md"


def load_fixes(path=FIXES_INDEX_PATH):
    if not os.path.exists(path):
        return {}
    return {entry["risk_id"]: entry for entry in score.load_json(path)}


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M:%S UTC") if epoch else "n/a"


def run_line(run):
    """One measured run as a bullet: only numbers that exist."""
    parts = [f"r{run['repeat']} `{run['run_id']}`: **{run['outcome']}**"]
    if run["status"] == "scored":
        parts.append(f"S {score.fmt(run['S'], 1)} (A {score.fmt(run['A'])}, L {score.fmt(run['L'])}, "
                     f"R {score.fmt(run['R'])}, C {score.fmt(run['C'])})")
        if run["impact_recovery_s"]:
            parts.append(f"impact recovery {run['impact_recovery_s']:.0f} s")
        parts.append("journeys scored: " + ", ".join(run["journeys_used"]))
    else:
        parts.append("not scored: " + "; ".join(run["reasons"]))
        if run["impact_recovery_s"]:
            parts.append(f"observed impact recovery {run['impact_recovery_s']:.0f} s (a measured time, not a score)")
    if run["journeys_too_thin"] and run["status"] == "scored":
        parts.append("left out (too few samples): " + ", ".join(run["journeys_too_thin"]))
    if run["c_weights_rescaled"]:
        parts.append("C not applicable, weights rescaled")
    if run["aborted"]:
        parts.append(f"stopped early by the guardian ({run['abort_reason']})")
    if run["late_breach_ts"]:
        parts.append(f"unrelated blip at {iso(run['late_breach_ts'])} after the fault ended, not scored")
    parts.append(f"numbers {run['source']}")
    return "- " + " - ".join(parts)


def evidence_line(run, show_ids=3):
    """What backs a measured run: the traces and queries the collector recorded. Says so when there are none."""
    evidence = run.get("evidence")
    if not evidence:
        return f"  - Evidence for r{run['repeat']}: none recorded."
    ids = evidence.get("trace_ids") or []
    queries = evidence.get("prom_queries") or []
    shown = ", ".join(f"`{i}`" for i in ids[:show_ids]) if ids else "no failed or slow traces in the window"
    more = f" (+{len(ids) - show_ids} more in the run file)" if len(ids) > show_ids else ""
    return (f"  - Evidence for r{run['repeat']} (collector quality: {evidence.get('quality') or 'unknown'}): "
            f"{len(ids)} trace(s): {shown}{more}; {len(queries)} Prometheus queries recorded.")


def fix_lines(risk_id, fixes):
    entry = fixes.get(risk_id)
    if not entry:
        return [f"- No fix suggestion is linked to `{risk_id}` in `{FIXES_INDEX_PATH}`."]
    lines = [f"- Suggested fix (`{risk_id}`, predicted severity {entry['severity']}): {entry['summary']}"]
    for f in entry.get("files") or []:
        lines.append(f"  - `{f['apply']}`")
    if entry.get("manual"):
        lines.append(f"  - Manual: {entry['manual']}")
    return lines


def summary_table(scores):
    lines = ["| Exp | Fault | Target | Runs scored | Outcome(s) | S | Severity | Prediction |",
             "|---|---|---|---|---|---|---|---|"]
    for e in scores["experiments"]:
        outcomes = ", ".join(r["outcome"] for r in e["runs"]) or "not run"
        lines.append(f"| {e['experiment_id']} | {e['fault_type']} | {e['target']} | {e['n_scored']}/{e['n_runs']} "
                     f"| {outcomes} | {score.fmt(e['S'], 1)} | {e['severity'] or e['status']} | {e['prediction']} |")
    return lines


def build_report(scores, fixes, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cov = scores["coverage"]
    exps = scores["experiments"]
    out = ["# ChaosPilot findings report", "",
           f"Generated {generated_at} by `python -m src.report` from the run files and stored samples. "
           "Every number is computed by code; the LLM did not touch the cluster or the scores.", "",
           "## Summary", ""]

    if scores["overall"] is None:
        out.append("No experiment could be scored yet, so there is no overall score.")
    else:
        out.append(f"**Overall resilience score: {scores['overall']:.1f} / 100** "
                   f"(criticality-weighted mean of the scored experiments).")
    out.append(f"**Coverage:** {cov['scored']} of {cov['planned']} approved experiments scored, "
               f"{cov['inconclusive']} inconclusive, {cov['not_run']} not run.")
    out += [""] + summary_table(scores) + [""]

    out += ["## How the score works", "",
            f"`{scores['formula']}` per run, then averaged over repeats, then weighted by service criticality "
            "(`config/criticality.yaml`).", "",
            "- **A** successful requests / offered requests during the fault window; **L** requests that were ok and "
            "within the latency limit. Both are weighted across the affected journeys "
            "(checkout 0.4, cart 0.3, browse 0.2).",
            "- **R** 1 if the shop survived; otherwise 1 minus impact recovery time over the recovery budget; "
            "0 if it did not recover.",
            "- **C** share of the journeys *not* targeted that stayed within SLO. When every probed journey is a target "
            "there is nothing to contain: C is shown as n/a and the other three weights are scaled to add up to 1.",
            "- A journey with fewer samples in the fault window than its minimum is left out. If none remain, the run is "
            "**inconclusive** and gets no score.",
            "- Severity = (100 - S) x service criticality: Critical >= 30, High >= 15, Medium >= 5, otherwise Low.", ""]

    impact = sorted((e for e in exps if e["prediction"].startswith(("confirmed", "mixed"))),
                    key=lambda e: -(e["severity_impact"] or 0))
    out += ["## Findings: impact seen", ""]
    if not impact:
        out += ["No experiment showed user-visible impact in the runs so far.", ""]
    for e in impact:
        out += [f"### {e['target']} - {e['fault_type']} - {e['severity']} (S {score.fmt(e['S'], 1)})", "",
                f"**Predicted:** {e['hypothesis']}", "",
                f"**Prediction status:** {e['prediction']}. Expected journeys: {', '.join(e['expected_journeys'])}.", "",
                "**Measured:**"]
        out += [line for r in e["runs"] for line in (run_line(r), evidence_line(r))]
        out += ["", "**Recommendation:**"] + fix_lines(e["risk_id"], fixes) + [""]

    tolerated = [e for e in exps if e["prediction"].startswith("not confirmed")]
    out += ["## Tolerated faults: prediction not confirmed", "",
            "The static analysis predicted a weakness here, and this fault did not expose it. That is a result of this "
            "one fault and these runs, not proof the service is safe.", ""]
    if not tolerated:
        out += ["None."]
    for e in tolerated:
        out += [f"### {e['target']} - {e['fault_type']} (S {score.fmt(e['S'], 1)})", "", f"**Predicted:** {e['hypothesis']}", ""]
        out += [run_line(r) for r in e["runs"]]
        out += [""] + fix_lines(e["risk_id"], fixes) + [""]

    unscored = [e for e in exps if e["status"] != "scored"]
    other = [e for e in exps if e["status"] == "scored" and any(r["status"] != "scored" for r in e["runs"])]
    out += ["## Not scored", ""]
    if not unscored and not other:
        out += ["Every approved experiment that ran was scored."]
    for e in unscored:
        out += [f"### {e['target']} - {e['fault_type']} ({e['experiment_id']}): {e['status']}", ""]
        out += [run_line(r) for r in e["runs"]] or [
            "- No run recorded: refused or not run yet (`python -m src.run --list` shows which and why)."]
        out += [""]
    for e in other:
        out += [f"- {e['experiment_id']}: repeats not scored: "
                + "; ".join(f"r{r['repeat']} ({'; '.join(r['reasons'])})" for r in e["runs"] if r["status"] != "scored")]
    out += [""]

    out += ["## Ranking benchmark: ranked order vs random order", ""]
    bench = benchmark.build_benchmark(scores)
    out += ["Scored experiments in the planner's order: " + ", ".join(bench["order"]) + ".", ""]
    out += [f"- {line}" for line in benchmark.describe(bench)]
    out += ["", f"_{bench['note']}_", ""]

    out += ["## Notes and limits", "",
            "- Numbers come from the runs listed above on one cluster. Repeats are shown where they exist; a single "
            "run is one observation.",
            "- A pod kill is instant, so its fault window holds few samples; the recovery time is the more reliable "
            "number for those runs.",
            "- For timed faults (network delay, CPU stress) the impact recovery time runs from the first breach to the "
            "start of a healthy window, so it includes the time the fault itself was active. R is therefore capped by "
            "the fault length for those runs; the recovery after the fault ended is in the run files "
            "(`post_fault_recovery_s`).",
            "- A short unrelated failure after a fault ended is listed on the run as a blip and is not scored; the shop "
            "shows a low background error rate even with no fault running (source not yet found).",
            "- Runs that were stopped on purpose to test the kill switch (forced breach) are not part of this report.",
            "- Fix files in `reports/fixes/` are suggestions; nothing was applied to the cluster.",
            "- Trace IDs open at `/jaeger/ui/trace/<id>` on the Jaeger the run used. Jaeger keeps traces in memory, so "
            "an ID stops resolving after Jaeger restarts; the IDs and queries stay in `data/runs/*.json`.", ""]
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write reports/findings_report.md")
    parser.add_argument("--out", default=REPORT_PATH)
    args = parser.parse_args(argv)

    scores = score.build_scores()
    report = build_report(scores, load_fixes())
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    score.print_table(scores)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
