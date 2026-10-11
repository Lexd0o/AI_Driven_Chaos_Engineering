"""Deterministic analysis for the Recovery & Solutions agent.

Everything numeric in the final report is computed here. No LLM, no network.
The LLM (recovery_agent.py) only explains these results and picks catalog actions.
"""
import math
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from common import now

DEFAULTS = {
    "min_baseline_samples": 5,
    "min_fault_samples": 3,          # Guardian stops early on breach, so fault windows can be short
    "degrade_factor": 1.5,           # sample is degraded if latency > baseline p95 * factor ...
    "degrade_min_delta_ms": 50,      # ... and at least this much above baseline p95
    "error_rate_threshold": 0.10,
    "recovery_consecutive_samples": 3,
    "recovery_limit_seconds": 30,
    # Weights are the maximum points each finding can subtract from 100.
    "weights": {"error_rate": 35, "slo_breach": 20, "slow_recovery": 20,
                "latency_inflation": 15, "single_replica": 10},
    "severity_points": {"critical": 25, "high": 15, "medium": 7},
}

ACTIONS = {
    "set_timeouts": "Set explicit client timeouts/deadlines on calls to the target so slow dependencies fail fast.",
    "circuit_breaker": "Add a circuit breaker around calls to the target so repeated failures stop hitting it.",
    "bounded_retries": "Use bounded retries with exponential backoff and jitter, for idempotent calls only.",
    "cache_fallback": "Serve cached or degraded responses when the target is slow or unavailable.",
    "add_replicas": "Run at least 2 replicas of the target with pod anti-affinity and a PodDisruptionBudget.",
    "tune_readiness": "Tune readiness/startup probes and resource requests so replacement pods become Ready quickly.",
    "cpu_headroom": "Set CPU requests and limits and add an autoscaler on the target so a CPU-saturated pod cannot starve its own requests.",
}
FINDING_ACTIONS = {
    "error_rate": ["set_timeouts", "circuit_breaker", "bounded_retries", "cache_fallback", "cpu_headroom"],
    "slo_breach": ["set_timeouts", "cache_fallback", "circuit_breaker", "cpu_headroom"],
    "latency_inflation": ["set_timeouts", "cache_fallback", "circuit_breaker", "cpu_headroom"],
    "slow_recovery": ["tune_readiness", "add_replicas", "cpu_headroom"],
    "single_replica": ["add_replicas", "tune_readiness"],
}
TITLES = {
    "error_rate": "Probe failures during the fault",
    "slo_breach": "Successful but too-slow responses during the fault",
    "latency_inflation": "Latency inflated versus baseline",
    "slow_recovery": "Slow or unconfirmed recovery",
    "single_replica": "Single-replica target lost under pod kill",
}
LIMITATIONS = [
    "Evidence is one HTTP business probe (sequential, ~poll interval); no per-service metrics or traces.",
    "Latency percentiles use successful probes only; with few samples p95 is close to the maximum.",
    "Network faults hit one whole pod, not a single dependency edge.",
    "Single run: the score is heuristic and applies to this experiment only.",
    "Recommendations are proposals; none were applied or verified.",
]


def settings_cfg(settings):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    override = settings.get("recovery", {})
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def seconds_between(a, b):
    return round((ts(b) - ts(a)).total_seconds(), 2)


def percentile(values, q):
    """Nearest-rank percentile; None for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def classify_error(text):
    low = (text or "").lower()
    if "timed out" in low or "timeout" in low:
        return "timeout"
    match = re.search(r"http(?: error)? (\d{3})", low)
    if match:
        return "http_5xx" if match.group(1).startswith("5") else "http_4xx"
    if "nonempty product list" in low or "json" in low:
        return "bad_payload"
    if any(word in low for word in ("refused", "reset", "unreachable", "urlopen", "connection")):
        return "connection"
    return "other"


def window_stats(samples, max_latency_ms):
    n = len(samples)
    ok = [s for s in samples if s.get("ok")]
    lat = [s["latency_ms"] for s in ok]
    errors = {}
    for s in samples:
        if not s.get("ok"):
            kind = classify_error(s.get("error"))
            errors[kind] = errors.get(kind, 0) + 1
    stamps = [s["observed_at"] for s in samples]
    return {
        "n": n, "ok": len(ok), "failed": n - len(ok),
        "success_rate": round(len(ok) / n, 4) if n else None,
        "failure_rate": round((n - len(ok)) / n, 4) if n else None,
        "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95),
        "max_ms": max(lat) if lat else None,
        "slow_ok": sum(1 for v in lat if v > max_latency_ms),
        "errors": errors,
        "first_at": min(stamps) if stamps else None, "last_at": max(stamps) if stamps else None,
        "duration_seconds": seconds_between(min(stamps), max(stamps)) if n >= 2 else None,
    }


def slice_windows(report):
    """Split probe samples into baseline / fault / recovery using the confirmation timestamp."""
    samples = report.get("samples", [])
    confirmed = report.get("fault_confirmed_at")
    baseline = [s for s in samples if s.get("stage") == "baseline"]
    during = [s for s in samples if s.get("stage") == "during_fault"]
    recovery = [s for s in samples if s.get("stage") == "after_cleanup"]
    if confirmed:
        fault = [s for s in during if ts(s["observed_at"]) >= ts(confirmed)]
    else:
        fault = []
    return {"baseline": baseline, "fault": fault, "recovery": recovery,
            "pending": len(during) - len(fault)}


def max_latency(settings):
    return settings["probe"]["max_latency_ms"]


def degrade_threshold(base_p95, cfg, limit_ms):
    if base_p95 is None:
        return limit_ms
    return min(max(base_p95 * cfg["degrade_factor"], base_p95 + cfg["degrade_min_delta_ms"]), limit_ms)


def is_degraded(sample, threshold):
    return (not sample.get("ok")) or sample["latency_ms"] > threshold


# ---------------------------------------------------------------- verification

def _first_event(report, stage):
    for event in report.get("events", []):
        if event.get("stage") == stage:
            return event["observed_at"]
    return None


def _manifest_matches(report):
    plan, manifest = report.get("plan"), report.get("manifest")
    resource = report.get("resource", {})
    if not plan or not manifest:
        return False, "plan or manifest missing"
    spec, meta = manifest.get("spec", {}), manifest.get("metadata", {})
    fault = plan["fault_type"]
    problems = []
    if fault in ("pod_kill", "pod_failure"):
        action = "pod-kill" if fault == "pod_kill" else "pod-failure"
        if manifest.get("kind") != "PodChaos" or spec.get("action") != action:
            problems.append("kind/action mismatch")
        if fault == "pod_failure" and spec.get("duration") != f"{plan['duration_seconds']}s":
            problems.append("duration mismatch")
    elif fault == "cpu_stress":
        if manifest.get("kind") != "StressChaos":
            problems.append("kind mismatch")
        if spec.get("duration") != f"{plan['duration_seconds']}s":
            problems.append("duration mismatch")
        if spec.get("stressors", {}).get("cpu", {}).get("workers") != plan.get("cpu_workers"):
            problems.append("cpu workers mismatch")
    else:
        if manifest.get("kind") != "NetworkChaos":
            problems.append("kind mismatch")
        if spec.get("duration") != f"{plan['duration_seconds']}s":
            problems.append("duration mismatch")
        if fault == "network_delay" and (spec.get("action") != "delay"
                                         or spec.get("delay", {}).get("latency") != f"{plan['latency_ms']}ms"):
            problems.append("delay mismatch")
        if fault == "packet_loss" and (spec.get("action") != "loss"
                                       or spec.get("loss", {}).get("loss") != str(plan["loss_percent"])):
            problems.append("loss mismatch")
        if fault == "packet_duplicate" and (spec.get("action") != "duplicate"
                                            or spec.get("duplicate", {}).get("duplicate") != str(plan["duplicate_percent"])):
            problems.append("duplicate mismatch")
    if plan.get("pod_count", 1) > 1 and (spec.get("mode") != "fixed" or spec.get("value") != str(plan["pod_count"])):
        problems.append("pod count mismatch")
    if meta.get("labels", {}).get("reslab-run-id") != report.get("run_id"):
        problems.append("run-id label mismatch")
    if meta.get("name") != resource.get("name"):
        problems.append("resource name mismatch")
    return (not problems), ("; ".join(problems) or "manifest matches plan")


def observed_effect(base, fault, threshold, cfg):
    if not fault["n"]:
        return {"assessable": False, "latency": None, "errors": None, "any": None}
    latency = fault["p95_ms"] is not None and fault["p95_ms"] > threshold
    errors = (fault["failure_rate"] or 0) >= cfg["error_rate_threshold"]
    return {"assessable": True, "latency": latency, "errors": errors, "any": latency or errors,
            "fault_p95_ms": fault["p95_ms"], "degrade_threshold_ms": round(threshold, 1),
            "fault_failure_rate": fault["failure_rate"]}


def verify_injection(report, stats, threshold, cfg):
    """CONFIRMED | PARTIAL | UNCONFIRMED from controller evidence plus independent probe effect."""
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("live_execute_run", report.get("mode") == "execute", f"mode={report.get('mode')}")
    confirmed_at = report.get("fault_confirmed_at")
    has_event = _first_event(report, "injection_confirmed") is not None
    check("controller_confirmed_injection",
          report.get("fault_injected") is True and bool(confirmed_at) and has_event,
          f"fault_injected={report.get('fault_injected')}, fault_confirmed_at={confirmed_at}, event={has_event}")
    submitted, cleaned = _first_event(report, "submitting"), _first_event(report, "cleanup")
    consistent = bool(confirmed_at and submitted and ts(submitted) <= ts(confirmed_at)
                      and (cleaned is None or ts(confirmed_at) <= ts(cleaned)))
    check("timeline_consistent", consistent,
          f"submitting={submitted}, confirmed={confirmed_at}, cleanup={cleaned}")
    matched, detail = _manifest_matches(report)
    check("manifest_matches_plan", matched, detail)
    effect = observed_effect(stats["baseline"], stats["fault"], threshold, cfg)
    check("probe_effect_observed", effect["any"] is True,
          "no fault-window samples" if not effect["assessable"] else
          f"latency_effect={effect['latency']}, error_effect={effect['errors']}")

    infra_ok = all(c["passed"] for c in checks[:4])
    if not infra_ok:
        verdict = "UNCONFIRMED"
    elif effect["any"] is True:
        verdict = "CONFIRMED"
    else:
        verdict = "PARTIAL"   # injected per controller, but no measurable probe impact
    return {"verdict": verdict, "checks": checks, "effect": effect}


# -------------------------------------------------------------------- recovery

def compute_recovery(report, windows, threshold, cfg):
    plan = report.get("plan", {})
    confirmed = report.get("fault_confirmed_at")
    cleanup_at = report.get("cleanup_observed_at") if report.get("cleanup_confirmed") else None
    out = {"applicable": False, "recovered": None, "seconds": None, "reset_at": cleanup_at,
           "recovered_at": None, "reference": None, "time_to_first_impact_seconds": None,
           "fault_active_seconds": None,
           "controller_reported_seconds": report.get("post_cleanup_probe_recovery_seconds"),
           "reason": None}
    cleanup_event = _first_event(report, "cleanup")
    if confirmed and cleanup_event:
        out["fault_active_seconds"] = seconds_between(confirmed, cleanup_event)
    seq = sorted(windows["fault"] + windows["recovery"], key=lambda s: ts(s["observed_at"]))
    bad = [i for i, s in enumerate(seq) if is_degraded(s, threshold)]
    fault_bad = [s for s in windows["fault"] if is_degraded(s, threshold)]
    if fault_bad and confirmed:
        out["time_to_first_impact_seconds"] = seconds_between(confirmed, fault_bad[0]["observed_at"])
    if not bad:
        out["reason"] = "no degraded samples observed, so there was nothing to recover from"
        return out
    out["applicable"] = True
    # pod_kill removes the pod at injection; timed faults (network, pod_failure) end when the fault is removed.
    at_injection = plan.get("fault_type") == "pod_kill"
    reference = confirmed if at_injection else cleanup_at
    out["reference"] = "fault_confirmed_at" if at_injection else "cleanup_observed_at"
    if reference is None:
        out.update(recovered=None, reason="reference time unavailable (cleanup not confirmed)")
        return out
    tail = seq[bad[-1] + 1:]
    need = cfg["recovery_consecutive_samples"]
    if len(tail) < need:
        out.update(recovered=False,
                   reason=f"fewer than {need} consecutive healthy samples after the last degraded sample")
        return out
    start = max(ts(tail[0]["observed_at"]), ts(reference))
    out.update(recovered=True, recovered_at=start.isoformat(),
               seconds=round((start - ts(reference)).total_seconds(), 2),
               reason=f"first of {len(tail)} consecutive healthy samples after the last degraded sample")
    return out


# -------------------------------------------------------------------- findings

def _severity(points, cfg):
    for name in ("critical", "high", "medium"):
        if points >= cfg["severity_points"][name]:
            return name
    return "low"


def detect_findings(report, stats, recovery, threshold, settings, cfg):
    """Rule-based detectors -> (ranked findings, score components)."""
    base, fault = stats["baseline"], stats["fault"]
    plan = report.get("plan", {})
    limit, weights = max_latency(settings), cfg["weights"]
    candidates = {}

    def norm(finding_id, value, evidence):
        candidates[finding_id] = (max(0.0, min(1.0, value)), evidence)

    if fault["n"]:
        rate = fault["failure_rate"]
        if rate >= cfg["error_rate_threshold"]:
            norm("error_rate", rate / 0.5,
                 {"failure_rate": rate, "failed": fault["failed"], "n": fault["n"], "errors": fault["errors"],
                  "baseline_failure_rate": base["failure_rate"]})
        slow_fraction = fault["slow_ok"] / fault["n"]
        if slow_fraction >= cfg["error_rate_threshold"]:
            norm("slo_breach", slow_fraction / 0.5,
                 {"slow_ok": fault["slow_ok"], "n": fault["n"], "max_latency_ms": limit,
                  "fault_max_ms": fault["max_ms"]})
        if base["p95_ms"] and fault["p95_ms"] is not None and fault["p95_ms"] > threshold:
            ratio = round(fault["p95_ms"] / base["p95_ms"], 2)
            evidence = {"baseline_p95_ms": base["p95_ms"], "fault_p95_ms": fault["p95_ms"], "ratio": ratio,
                        "delta_ms": round(fault["p95_ms"] - base["p95_ms"], 1)}
            if plan.get("fault_type") == "network_delay" and plan.get("latency_ms") and \
                    fault["p50_ms"] is not None and base["p50_ms"] is not None:
                evidence["injected_latency_ms"] = plan["latency_ms"]
                evidence["p50_delta_ms"] = round(fault["p50_ms"] - base["p50_ms"], 1)
                evidence["amplification"] = round((fault["p50_ms"] - base["p50_ms"]) / plan["latency_ms"], 2)
            norm("latency_inflation", (ratio - 1) / 3, evidence)
    if recovery["applicable"]:
        timeout = settings["probe"].get("recovery_timeout_seconds", 120)
        limit_s = cfg["recovery_limit_seconds"]
        if recovery["recovered"] is False:
            norm("slow_recovery", 1.0, {"recovered": False, "reason": recovery["reason"]})
        elif recovery["recovered"] and recovery["seconds"] > limit_s:
            span = max(timeout - limit_s, 1)
            norm("slow_recovery", (recovery["seconds"] - limit_s) / span,
                 {"recovery_seconds": recovery["seconds"], "limit_seconds": limit_s,
                  "controller_reported_seconds": recovery["controller_reported_seconds"]})
    if plan.get("fault_type") == "pod_kill" and fault["n"] and (fault["failed"] or fault["slow_ok"]):
        target = next((s for s in report.get("snapshot", {}).get("services", [])
                       if s.get("id") == plan.get("target_service")), None)
        if target and target.get("replicas") == 1:
            norm("single_replica", 1.0, {"replicas": 1, "target": plan["target_service"],
                                         "snapshot_source": report["snapshot"].get("data_source"),
                                         "failed": fault["failed"], "n": fault["n"]})

    components, findings = [], []
    for finding_id, weight in weights.items():
        normalized, evidence = candidates.get(finding_id, (0.0, None))
        penalty = round(weight * normalized, 1)
        components.append({"id": finding_id, "weight": weight, "normalized": round(normalized, 3),
                           "penalty": penalty, "triggered": finding_id in candidates})
        if finding_id in candidates:
            findings.append({"id": finding_id, "title": TITLES[finding_id],
                             "severity": _severity(penalty, cfg), "penalty": penalty, "evidence": evidence})
    findings.sort(key=lambda f: (-f["penalty"], -weights[f["id"]]))
    return findings, components


def data_quality(report, stats, cfg):
    notes, ok = [], True
    if stats["baseline"]["n"] < cfg["min_baseline_samples"]:
        ok = False
        notes.append(f"baseline has {stats['baseline']['n']} samples (< {cfg['min_baseline_samples']})")
    if stats["fault"]["n"] < cfg["min_fault_samples"]:
        ok = False
        notes.append(f"fault window has {stats['fault']['n']} samples (< {cfg['min_fault_samples']})")
    if report.get("stop_reason") == "probe_threshold_breach":
        notes.append("Guardian stopped the run at breach: fault-window impact is a lower bound")
    if stats["pending"]:
        notes.append(f"{stats['pending']} probe samples between submit and injection confirmation were excluded")
    if report.get("cleanup_confirmed") is not True:
        notes.append("cleanup was not confirmed: treat experiment integrity as unverified")
    if report.get("synthetic_test_data"):
        notes.append("SYNTHETIC TEST DATA: not a measured experiment")
    return {"sufficient": ok, "notes": notes}


def score(components, quality):
    if not quality["sufficient"]:
        return {"label": "Resilience score for this experiment (heuristic)", "total": None,
                "reason": "insufficient samples; empty or tiny windows are not treated as healthy",
                "components": components}
    total = max(0.0, round(100 - sum(c["penalty"] for c in components), 1))
    return {"label": "Resilience score for this experiment (heuristic)", "total": total,
            "components": components}


# ------------------------------------------------------------ LLM output schema

class FindingExplanation(BaseModel):
    finding_id: str
    explanation: str


class Recommendation(BaseModel):
    finding_id: str
    action_id: str
    rationale: str


class RecoveryNarrative(BaseModel):
    summary: str
    finding_explanations: list[FindingExplanation]
    recommendations: list[Recommendation]


def _numbers_in(value, out):
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        for x in (value, round(value), round(value, 1), round(value, 2), value * 100, round(value * 100),
                  round(value * 100, 1)):
            out.add(float(x))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers_in(v, out)
    elif isinstance(value, str):
        for m in re.findall(r"\d+(?:\.\d+)?", value):
            out.add(float(m))


def _prose_numbers(text, strip=()):
    for s in strip:
        text = text.replace(s, "")
    return [float(m) for m in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?!\d|\.\d|xx)", text)]


def validate_narrative(narrative: Optional[RecoveryNarrative], facts):
    """Structural + number-grounding checks. Returns a list of error strings (empty = valid)."""
    if narrative is None:
        return ["no structured output returned"]
    errors, findings = [], {f["id"]: f for f in facts["findings"]}
    if not narrative.summary.strip() or len(narrative.summary) > 900:
        errors.append("summary must be 1..900 characters")
    seen = [e.finding_id for e in narrative.finding_explanations]
    if sorted(seen) != sorted(findings):
        errors.append(f"need exactly one explanation per finding id {sorted(findings)}, got {sorted(seen)}")
    for e in narrative.finding_explanations:
        if not e.explanation.strip() or len(e.explanation) > 700:
            errors.append(f"explanation for {e.finding_id} must be 1..700 characters")
    per = {}
    for r in narrative.recommendations:
        if r.finding_id not in findings:
            errors.append(f"recommendation references unknown finding {r.finding_id}")
        elif r.action_id not in FINDING_ACTIONS[r.finding_id]:
            errors.append(f"action {r.action_id} is not allowed for {r.finding_id}")
        per[r.finding_id] = per.get(r.finding_id, 0) + 1
        if not r.rationale.strip() or len(r.rationale) > 500:
            errors.append(f"rationale for {r.finding_id}/{r.action_id} must be 1..500 characters")
    for finding_id in findings:
        if not 1 <= per.get(finding_id, 0) <= 2:
            errors.append(f"finding {finding_id} needs 1-2 recommendations")
    allowed = {0.0, 1.0, 2.0, 3.0, 50.0, 95.0}   # 50/95 appear in percentile names (p50, p95)
    _numbers_in(facts, allowed)
    prose = [narrative.summary] + [e.explanation for e in narrative.finding_explanations] + \
            [r.rationale for r in narrative.recommendations]
    strip = (facts["experiment"]["id"],)
    stray = sorted({n for text in prose for n in _prose_numbers(text, strip) if n not in allowed})
    if stray:
        errors.append(f"numbers not present in the supplied facts: {stray}; quote supplied values only, no arithmetic")
    return errors


def fallback_narrative(facts):
    """Deterministic text used when no valid LLM output is available. Labeled as fallback in the report."""
    verdict, score_total = facts["verification"]["verdict"], facts["score"]["total"]
    findings = facts["findings"]
    if not findings:
        summary = (f"Injection verdict {verdict}. No weakness crossed a detector threshold in this run; "
                   f"the probe evidence is limited to a single run.")
    else:
        summary = (f"Injection verdict {verdict}. {len(findings)} weakness(es) detected; top: "
                   f"{findings[0]['title']} ({findings[0]['severity']}). Experiment resilience score: "
                   f"{score_total if score_total is not None else 'not computed (insufficient samples)'}.")
    texts = {
        "error_rate": lambda e: f"{e['failed']} of {e['n']} probes failed during the fault; error classes: {e['errors']}.",
        "slo_breach": lambda e: f"{e['slow_ok']} of {e['n']} probes succeeded but exceeded {e['max_latency_ms']} ms.",
        "latency_inflation": lambda e: f"Probe p95 rose from {e['baseline_p95_ms']} ms to {e['fault_p95_ms']} ms "
                                       f"(ratio {e['ratio']}).",
        "slow_recovery": lambda e: ("Recovery was not confirmed." if e.get("recovered") is False else
                                    f"Recovery took {e['recovery_seconds']} s against a {e['limit_seconds']} s limit."),
        "single_replica": lambda e: f"The target ran {e['replicas']} replica, so killing a pod removed all capacity.",
    }
    explanations = [{"finding_id": f["id"], "explanation": texts[f["id"]](f["evidence"])} for f in findings]
    recs = [{"finding_id": f["id"], "action_id": FINDING_ACTIONS[f["id"]][0],
             "rationale": ACTIONS[FINDING_ACTIONS[f["id"]][0]]} for f in findings]
    return RecoveryNarrative.model_validate({"summary": summary, "finding_explanations": explanations,
                                            "recommendations": recs})


def build_facts(report, stats, verification, recovery, findings, scored, quality):
    plan = report.get("plan", {})
    return {
        "experiment": {"id": report.get("run_id"), "target": plan.get("target_service"),
                       "fault_type": plan.get("fault_type"), "duration_seconds": plan.get("duration_seconds"),
                       "latency_ms": plan.get("latency_ms"), "loss_percent": plan.get("loss_percent"),
                       **{k: plan[k] for k in ("duplicate_percent", "pod_count") if k in plan},
                       "hypothesis": plan.get("hypothesis"), "stop_reason": report.get("stop_reason"),
                       "cleanup_confirmed": report.get("cleanup_confirmed")},
        "verification": {"verdict": verification["verdict"], "effect": verification["effect"]},
        "windows": {k: {kk: vv for kk, vv in v.items() if kk not in ("first_at", "last_at")}
                    for k, v in stats.items() if k in ("baseline", "fault", "recovery")},
        "recovery": {k: v for k, v in recovery.items() if k not in ("reset_at", "recovered_at")},
        "findings": [{**f, "allowed_actions": FINDING_ACTIONS[f["id"]]} for f in findings],
        "score": {"total": scored["total"], "components": scored["components"]},
        "data_quality": quality,
    }


def render_markdown(analysis):
    v, rec, sc = analysis["verification"], analysis["recovery"], analysis["resilience_score"]
    e = analysis["experiment"]
    lines = [f"# Recovery & Solutions report: {analysis['experiment_id']}", "",
             f"**Verdict:** {v['verdict']}  |  **Narrative source:** {analysis['narrative']['source']}", ""]
    if analysis["data_provenance"]["notes"]:
        lines += ["> " + n for n in analysis["data_provenance"]["notes"]] + [""]
    lines += [f"**Experiment:** {e['fault_type']} on `{e['target']}`, stop reason `{e['stop_reason']}`", "",
              analysis.get("summary", ""), "", "## Verification", "", "| Check | Passed | Detail |", "|---|---|---|"]
    lines += [f"| {c['name']} | {c['passed']} | {c['detail']} |" for c in v["checks"]]
    if v["verdict"] == "UNCONFIRMED":
        return "\n".join(lines) + "\n\nAnalysis stopped: injection could not be confirmed.\n"
    w = analysis["windows"]
    lines += ["", "## Windows", "", "| Window | n | success | p50 ms | p95 ms | errors |", "|---|---|---|---|---|---|"]
    for name in ("baseline", "fault", "recovery"):
        s = w[name]
        lines.append(f"| {name} | {s['n']} | {s['success_rate']} | {s['p50_ms']} | {s['p95_ms']} | {s['errors']} |")
    lines += ["", f"## Resilience score: {sc['total'] if sc['total'] is not None else 'n/a'}", "",
              "| Component | Weight | Normalized | Penalty |", "|---|---|---|---|"]
    lines += [f"| {c['id']} | {c['weight']} | {c['normalized']} | {c['penalty']} |" for c in sc["components"]]
    lines += ["", "## Findings (ranked)", ""]
    for f in analysis["findings"] or []:
        lines.append(f"- **[{f['severity']}] {f['title']}** (-{f['penalty']}): {f['explanation']}")
    if not analysis["findings"]:
        lines.append("- none above detector thresholds")
    lines += ["", "## Recovery", "",
              f"- applicable: {rec['applicable']}, recovered: {rec['recovered']}, seconds: {rec['seconds']}",
              f"- reset at: {rec['reset_at']}, recovered at: {rec['recovered_at']}",
              f"- controller-reported seconds: {rec['controller_reported_seconds']}",
              f"- time to first impact: {rec['time_to_first_impact_seconds']} s, "
              f"fault active: {rec['fault_active_seconds']} s", "", "## Recommendations (proposed, not applied or verified)", ""]
    lines += [f"- `{r['finding_id']}` -> **{r['action_id']}**: {r['rationale']}" for r in analysis["recommendations"]]
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in analysis["limitations"]]
    return "\n".join(lines) + "\n"


__all__ = ["DEFAULTS", "ACTIONS", "FINDING_ACTIONS", "RecoveryNarrative", "settings_cfg", "slice_windows",
           "window_stats", "degrade_threshold", "verify_injection", "compute_recovery", "detect_findings",
           "data_quality", "score", "build_facts", "validate_narrative", "fallback_narrative",
           "render_markdown", "now"]
