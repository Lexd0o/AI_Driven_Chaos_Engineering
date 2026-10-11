import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import recovery_agent
import recovery_core as ac
import common

T0 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
SETTINGS = common.read_json(common.BASE_DIR / "settings.json")
CFG = ac.settings_cfg(SETTINGS)


def iso(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat()


def make_report(fault="network_delay", baseline=None, pending=(90,), fault_samples=(), recovery_samples=(),
                replicas=1, stop_reason="duration_finished", injected=True, mode="execute", cleanup=True):
    """Synthetic controller report. Sample spec: number = ok latency ms, string = error text."""
    baseline = baseline if baseline is not None else [80 + (i % 3) * 5 for i in range(30)]
    t, samples = 0, []

    def add(stage, specs):
        nonlocal t
        for spec in specs:
            t += 2
            ok = not isinstance(spec, str)
            samples.append({"observed_at": iso(t), "ok": ok, "latency_ms": float(spec) if ok else 3000.0,
                            "error": None if ok else spec, "stage": stage})

    add("baseline", baseline)
    submit_t = t + 1
    add("during_fault", pending)
    confirmed_t = t + 1
    t = confirmed_t
    add("during_fault", fault_samples)
    cleanup_t = t + 1
    t = cleanup_t
    add("after_cleanup", recovery_samples)
    plan = {"target_service": "catalog", "fault_type": fault, "hypothesis": "This may affect catalog latency.",
            "duration_seconds": 0 if fault == "pod_kill" else 30,
            "latency_ms": 200 if fault == "network_delay" else 0,
            "loss_percent": 10 if fault == "packet_loss" else 0}
    spec = {"mode": "one"}
    if fault == "pod_kill":
        kind, spec["action"] = "PodChaos", "pod-kill"
    else:
        kind = "NetworkChaos"
        spec["duration"] = "30s"
        if fault == "network_delay":
            spec.update(action="delay", delay={"latency": "200ms"})
        else:
            spec.update(action="loss", loss={"loss": "10"})
    events = [{"stage": "submitting", "observed_at": iso(submit_t)}]
    if injected:
        events.append({"stage": "injection_confirmed", "observed_at": iso(confirmed_t)})
    events.append({"stage": "cleanup", "observed_at": iso(cleanup_t)})
    return {
        "run_id": "run-test000001", "mode": mode, "status": "execute_completed", "started_at": iso(0),
        "fault_injected": injected, "fault_confirmed_at": iso(confirmed_t) if injected else None,
        "cleanup_confirmed": cleanup, "cleanup_observed_at": iso(cleanup_t) if cleanup else None,
        "stop_reason": stop_reason, "samples": samples, "events": events, "plan": plan,
        "manifest": {"kind": kind, "metadata": {"name": "reslab-run-test000001",
                                                "labels": {"reslab-run-id": "run-test000001"}}, "spec": spec},
        "resource": {"kind": kind.lower(), "name": "reslab-run-test000001"},
        "snapshot": {"data_source": "live_cluster", "services": [{"id": "catalog", "replicas": replicas, "ready": True}]},
        "post_cleanup_probe_recovery_seconds": 4.0, "synthetic_test_data": True,
        "_times": {"cleanup_t": cleanup_t, "confirmed_t": confirmed_t},
    }


def stats_for(report):
    raw = ac.slice_windows(report)
    limit = SETTINGS["probe"]["max_latency_ms"]
    stats = {k: ac.window_stats(raw[k], limit) for k in ("baseline", "fault", "recovery")}
    stats["pending"], stats["_raw"] = raw["pending"], raw
    return stats, ac.degrade_threshold(stats["baseline"]["p95_ms"], CFG, limit)


def run_analysis(report):
    stats, thr = stats_for(report)
    verification = ac.verify_injection(report, stats, thr, CFG)
    recovery = ac.compute_recovery(report, stats["_raw"], thr, CFG)
    findings, components = ac.detect_findings(report, stats, recovery, thr, SETTINGS, CFG)
    quality = ac.data_quality(report, stats, CFG)
    return stats, verification, recovery, findings, components, quality


DELAY = dict(fault_samples=[300] * 12, recovery_samples=[250, 180, 90, 85, 88, 90, 86])
LOSS = dict(fault="packet_loss", fault_samples=[100] * 6 + ["<urlopen error timed out>"] * 6,
            recovery_samples=[90] * 5)


class MathTests(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        self.assertEqual(ac.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10)
        self.assertEqual(ac.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50), 5)
        self.assertIsNone(ac.percentile([], 50))

    def test_error_classes(self):
        self.assertEqual(ac.classify_error("<urlopen error timed out>"), "timeout")
        self.assertEqual(ac.classify_error("HTTP Error 503: Service Unavailable"), "http_5xx")
        self.assertEqual(ac.classify_error("Expected a nonempty product list; verify"), "bad_payload")

    def test_pending_samples_excluded_from_fault_window(self):
        windows = ac.slice_windows(make_report(pending=(90, 95), **DELAY))
        self.assertEqual(windows["pending"], 2)
        self.assertEqual(len(windows["fault"]), 12)
        self.assertEqual(len(windows["baseline"]), 30)

    def test_failed_samples_excluded_from_latency_percentiles(self):
        stats, _ = stats_for(make_report(**LOSS))
        self.assertEqual(stats["fault"]["failed"], 6)
        self.assertEqual(stats["fault"]["p95_ms"], 100)
        self.assertEqual(stats["fault"]["errors"], {"timeout": 6})


class VerificationTests(unittest.TestCase):
    def verdict(self, report):
        stats, thr = stats_for(report)
        return ac.verify_injection(report, stats, thr, CFG)

    def test_confirmed(self):
        self.assertEqual(self.verdict(make_report(**DELAY))["verdict"], "CONFIRMED")

    def test_partial_when_injected_but_no_effect(self):
        report = make_report(fault_samples=[82] * 10, recovery_samples=[85] * 5)
        self.assertEqual(self.verdict(report)["verdict"], "PARTIAL")

    def test_unconfirmed_when_controller_never_confirmed(self):
        report = make_report(injected=False, fault_samples=[300] * 5, recovery_samples=[85] * 5)
        self.assertEqual(self.verdict(report)["verdict"], "UNCONFIRMED")

    def test_unconfirmed_for_mock_mode(self):
        self.assertEqual(self.verdict(make_report(mode="mock", **DELAY))["verdict"], "UNCONFIRMED")

    def test_unconfirmed_when_manifest_differs_from_plan(self):
        report = make_report(**DELAY)
        report["manifest"]["spec"]["delay"]["latency"] = "900ms"
        result = self.verdict(report)
        self.assertEqual(result["verdict"], "UNCONFIRMED")
        self.assertFalse(next(c for c in result["checks"] if c["name"] == "manifest_matches_plan")["passed"])


class RecoveryTests(unittest.TestCase):
    def test_recovery_seconds_from_cleanup(self):
        report = make_report(**DELAY)
        stats, thr = stats_for(report)
        rec = ac.compute_recovery(report, stats["_raw"], thr, CFG)
        self.assertTrue(rec["applicable"] and rec["recovered"])
        # cleanup at T; samples at T+2 (250), T+4 (180), T+6 first healthy
        self.assertEqual(rec["seconds"], 6.0)
        self.assertEqual(rec["reference"], "cleanup_observed_at")
        self.assertEqual(rec["controller_reported_seconds"], 4.0)

    def test_not_recovered_without_enough_healthy_samples(self):
        report = make_report(fault_samples=[300] * 12, recovery_samples=[250, 180, 90, 85])
        stats, thr = stats_for(report)
        rec = ac.compute_recovery(report, stats["_raw"], thr, CFG)
        self.assertIs(rec["recovered"], False)

    def test_no_degradation_means_not_applicable(self):
        report = make_report(fault_samples=[82] * 10, recovery_samples=[85] * 5)
        stats, thr = stats_for(report)
        rec = ac.compute_recovery(report, stats["_raw"], thr, CFG)
        self.assertFalse(rec["applicable"])
        self.assertIsNone(rec["seconds"])

    def test_pod_kill_measured_from_injection(self):
        report = make_report("pod_kill", fault_samples=["HTTP Error 503: x"] * 3 + [90] * 5,
                             recovery_samples=[88] * 4, stop_reason="observation_finished")
        stats, thr = stats_for(report)
        rec = ac.compute_recovery(report, stats["_raw"], thr, CFG)
        self.assertEqual(rec["reference"], "fault_confirmed_at")
        # confirmed at C; failures at C+2,4,6; first healthy at C+8
        self.assertEqual(rec["seconds"], 8.0)

    def test_time_to_first_impact(self):
        report = make_report(fault_samples=[82, 82, 300, 300, 300], recovery_samples=[85] * 5)
        stats, thr = stats_for(report)
        rec = ac.compute_recovery(report, stats["_raw"], thr, CFG)
        self.assertEqual(rec["time_to_first_impact_seconds"], 6.0)


class FindingTests(unittest.TestCase):
    def test_latency_inflation_for_delay(self):
        *_, findings, components, _ = run_analysis(make_report(**DELAY))
        self.assertEqual([f["id"] for f in findings], ["latency_inflation"])
        evidence = findings[0]["evidence"]
        self.assertEqual(evidence["injected_latency_ms"], 200)
        self.assertGreater(evidence["amplification"], 1.0)

    def test_error_rate_ranked_first_and_score_matches_penalties(self):
        _, _, _, findings, components, quality = run_analysis(make_report(**LOSS))
        self.assertEqual(findings[0]["id"], "error_rate")
        self.assertEqual(findings[0]["severity"], "critical")
        scored = ac.score(components, quality)
        self.assertEqual(scored["total"], round(100 - sum(c["penalty"] for c in components), 1))
        self.assertLess(scored["total"], 70)

    def test_single_replica_only_for_pod_kill_with_impact(self):
        report = make_report("pod_kill", fault_samples=["HTTP Error 503: x"] * 4 + [90] * 4,
                             recovery_samples=[88] * 4, stop_reason="observation_finished")
        *_, findings, _, _ = run_analysis(report)
        self.assertIn("single_replica", [f["id"] for f in findings])
        report = make_report("pod_kill", fault_samples=[85] * 6, recovery_samples=[88] * 4, replicas=1,
                             stop_reason="observation_finished")
        *_, findings, _, _ = run_analysis(report)
        self.assertNotIn("single_replica", [f["id"] for f in findings])

    def test_slow_recovery_finding(self):
        report = make_report(fault_samples=[300] * 12, recovery_samples=[300] * 20 + [85] * 5)
        *_, findings, _, _ = run_analysis(report)
        slow = next(f for f in findings if f["id"] == "slow_recovery")
        self.assertGreater(slow["evidence"]["recovery_seconds"], 30)

    def test_tiny_fault_window_gives_no_score(self):
        _, _, _, _, components, quality = run_analysis(make_report(fault_samples=[300], recovery_samples=[85] * 5))
        self.assertFalse(quality["sufficient"])
        self.assertIsNone(ac.score(components, quality)["total"])

    def test_healthy_run_scores_100_with_no_findings(self):
        _, _, _, findings, components, quality = run_analysis(
            make_report(fault_samples=[82] * 8, recovery_samples=[85] * 5))
        self.assertEqual(findings, [])
        self.assertEqual(ac.score(components, quality)["total"], 100.0)


LOSS_DATA = {k: v for k, v in LOSS.items() if k != "fault"}


class ExpandedFaultTests(unittest.TestCase):
    def report(self, fault, **kw):
        report = make_report(fault="packet_loss", **kw)
        plan, manifest = report["plan"], report["manifest"]
        plan.update(fault_type=fault, loss_percent=0)
        spec = {"mode": "one", "duration": "30s"}
        if fault == "pod_failure":
            manifest["kind"] = "PodChaos"
            spec["action"] = "pod-failure"
        else:
            plan["duplicate_percent"] = 5
            spec.update(action="duplicate", duplicate={"duplicate": "5", "correlation": "0"})
        manifest["spec"] = spec
        return report

    def verdict(self, report):
        stats, thr = stats_for(report)
        return ac.verify_injection(report, stats, thr, CFG)

    def test_pod_failure_confirmed(self):
        self.assertEqual(self.verdict(self.report("pod_failure", **LOSS_DATA))["verdict"], "CONFIRMED")

    def test_packet_duplicate_confirmed(self):
        self.assertEqual(self.verdict(self.report("packet_duplicate", **LOSS_DATA))["verdict"], "CONFIRMED")

    def test_pod_failure_duration_mismatch(self):
        report = self.report("pod_failure", **LOSS_DATA)
        report["manifest"]["spec"]["duration"] = "10s"
        self.assertEqual(self.verdict(report)["verdict"], "UNCONFIRMED")

    def test_packet_duplicate_value_mismatch(self):
        report = self.report("packet_duplicate", **LOSS_DATA)
        report["manifest"]["spec"]["duplicate"]["duplicate"] = "9"
        self.assertEqual(self.verdict(report)["verdict"], "UNCONFIRMED")

    def test_fixed_mode_manifest_accepted_and_checked(self):
        report = self.report("pod_failure", **LOSS_DATA)
        report["plan"]["pod_count"] = 2
        report["manifest"]["spec"].update(mode="fixed", value="2")
        self.assertEqual(self.verdict(report)["verdict"], "CONFIRMED")
        report["manifest"]["spec"]["value"] = "3"
        self.assertEqual(self.verdict(report)["verdict"], "UNCONFIRMED")

    def test_pod_failure_recovery_measured_from_cleanup(self):
        report = self.report("pod_failure", **LOSS_DATA)
        stats, thr = stats_for(report)
        recovery = ac.compute_recovery(report, stats["_raw"], thr, CFG)
        self.assertEqual(recovery["reference"], "cleanup_observed_at")
        self.assertTrue(recovery["recovered"])

    def test_new_parameters_reach_analysis(self):
        report = self.report("packet_duplicate", **LOSS_DATA)
        path = Path(tempfile.mkdtemp()) / "run-test000001.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        analysis, _ = recovery_agent.analyze_report(path, use_llm=False, out_dir=path.parent)
        self.assertEqual(analysis["experiment"]["parameters"]["duplicate_percent"], 5)


class StubLLM:
    """Mimics ChatGroq.with_structured_output(...).invoke(messages)."""

    def __init__(self, *modes):
        self.modes, self.calls, self.last_prompt = list(modes), 0, ""

    def with_structured_output(self, schema, method=None, **kwargs):
        outer = self

        class Runnable:
            def invoke(self, messages):
                outer.calls += 1
                outer.last_prompt = messages[1].content
                mode = outer.modes.pop(0) if outer.modes else "valid"
                if mode == "raise":
                    raise RuntimeError("401 invalid api key")
                facts = json.loads(messages[1].content.split("FACTS:\n", 1)[1].split("\n\nYour previous", 1)[0])
                narrative = ac.fallback_narrative(facts)
                if mode == "bad_action" and narrative.recommendations:
                    narrative.recommendations[0].action_id = "rewrite_in_rust"
                if mode == "bad_number":
                    narrative.summary += " Latency rose by 98765 ms."
                return narrative

        return Runnable()


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def run_graph(self, report, llm=None, use_llm=True):
        path = self.dir / "run-test000001.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return recovery_agent.analyze_report(path, use_llm=use_llm, llm=llm, out_dir=self.dir)

    def test_llm_path_end_to_end(self):
        llm = StubLLM("valid")
        analysis, paths = self.run_graph(make_report(**LOSS), llm)
        self.assertEqual(analysis["narrative"]["source"], "llm")
        self.assertEqual(analysis["verification"]["verdict"], "CONFIRMED")
        self.assertEqual(analysis["findings"][0]["id"], "error_rate")
        self.assertTrue(analysis["findings"][0]["explanation"])
        self.assertEqual(analysis["recommendations"][0]["status"], "proposed - not applied or verified")
        self.assertTrue(Path(paths["json"]).exists() and Path(paths["markdown"]).exists())
        self.assertTrue(analysis["data_provenance"]["synthetic"])

    def test_retry_with_errors_then_success(self):
        llm = StubLLM("bad_action", "valid")
        analysis, _ = self.run_graph(make_report(**LOSS), llm)
        self.assertEqual(llm.calls, 2)
        self.assertIn("rewrite_in_rust", llm.last_prompt)
        self.assertEqual(analysis["narrative"]["source"], "llm")
        self.assertEqual(analysis["narrative"]["attempts"], 2)

    def test_ungrounded_number_rejected_then_fallback(self):
        llm = StubLLM("bad_number", "bad_number", "bad_number")
        analysis, _ = self.run_graph(make_report(**LOSS), llm)
        self.assertEqual(llm.calls, 3)
        self.assertEqual(analysis["narrative"]["source"], "fallback_template")
        self.assertIn("98765", analysis["narrative"]["fallback_reason"])

    def test_llm_exception_goes_straight_to_fallback(self):
        llm = StubLLM("raise")
        analysis, _ = self.run_graph(make_report(**LOSS), llm)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(analysis["narrative"]["source"], "fallback_template")
        self.assertIn("401", analysis["narrative"]["fallback_reason"])

    def test_no_llm_flag(self):
        llm = StubLLM()
        analysis, _ = self.run_graph(make_report(**DELAY), llm, use_llm=False)
        self.assertEqual(llm.calls, 0)
        self.assertEqual(analysis["narrative"]["source"], "fallback_template")

    def test_unconfirmed_stops_before_llm(self):
        llm = StubLLM()
        analysis, paths = self.run_graph(make_report(injected=False, fault_samples=[300] * 5), llm)
        self.assertEqual(llm.calls, 0)
        self.assertEqual(analysis["verification"]["verdict"], "UNCONFIRMED")
        self.assertEqual(analysis["findings"], [])
        self.assertIsNone(analysis["resilience_score"]["total"])
        self.assertTrue(Path(paths["markdown"]).exists())

    def test_healthy_partial_run_has_empty_findings(self):
        llm = StubLLM("valid")
        analysis, _ = self.run_graph(make_report(fault_samples=[82] * 8, recovery_samples=[85] * 5), llm)
        self.assertEqual(analysis["verification"]["verdict"], "PARTIAL")
        self.assertEqual(analysis["findings"], [])
        self.assertEqual(analysis["narrative"]["source"], "llm")


if __name__ == "__main__":
    unittest.main()
