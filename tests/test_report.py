"""Tests for src/report.py. Run: python -m unittest tests.test_report -v
The LLM is always mocked (patching src.report.structured_call); nothing touches the network."""
import json
import unittest
from unittest.mock import patch

from src import report as R

CFG = {
    "weights": {"A": 0.45, "L": 0.2, "R": 0.2, "C": 0.15},
    "journey_weights": {"checkout": 0.4, "cart": 0.3, "browse": 0.2, "ads-recs": 0.1},
    "llm": {"model": "m", "effort": "medium", "max_tokens": 100, "explain_top": 2,
            "min_text_chars": 40, "max_text_chars": 900},
}
GRAPH = {"generated_at": "g", "edges": [{"from": "frontend", "to": "cart"}, {"from": "cart", "to": "valkey"}]}
RANKED = [{"service": "cart", "blast_radius": 4, "gateway_share": 0.5, "on_checkout_path": True}]


def finding(rank, status="predicted", **kw):
    f = {"rank": rank, "risk_id": f"R-cart-c{rank}", "service": "cart", "check": "single_replica_no_pdb",
         "status": status, "basis": "predicted" if status == "predicted" else "tested",
         "severity": "High", "severity_value": 20.0, "evidence": "replicas=1", "experiment_id": None,
         "S": None, "criticality": 0.3, "fix": None}
    f.update(kw)
    return f


def scores_not_scored():
    return {"generated_at": "t", "graph_generated_at": "g",
            "coverage": {"planned": 8, "run": 0, "scored": 0, "inconclusive": 0, "text": "0 of 8 run"},
            "resilience_score": None, "score_status": "not_scored", "score_note": "Run experiments first.",
            "weights": {"weights": CFG["weights"], "journey_weights": CFG["journey_weights"]},
            "categories": {}, "experiments": [], "findings": [finding(i) for i in (1, 2, 3)],
            "counts": {"confirmed": 0, "refuted": 0, "inconclusive": 0, "predicted": 3}}


def scores_scored():
    fix = {"summary": "Add a PodDisruptionBudget", "files": [{"path": "a.yaml", "apply": "kubectl apply -f a.yaml"}],
           "manual": None}
    exp = {"experiment_id": "exp-001", "target": "cart", "fault_type": "pod_kill", "category": "Redundancy",
           "risk_id": "R-cart-c1", "repeats_run": 2, "outcome": "confirmed", "S": 72.4, "R": 1, "C": 1,
           "recovery_s": 31, "journeys": {"cart": {"A": 0.5, "L": 1, "S": 60.0}, "browse": {"A": 1, "L": 1, "S": None}},
           "uncovered_journeys": [], "weight": 1, "severity": "High", "severity_value": 20.0}
    s = scores_not_scored()
    s.update({"resilience_score": 72.4, "score_status": "scored", "score_note": "",
              "coverage": {"planned": 8, "run": 1, "scored": 1, "inconclusive": 0, "text": "1 of 8 run"},
              "categories": {"Redundancy": {"score": 72.4, "experiments": 1},
                             "Dependency": {"score": None, "experiments": 0}},
              "experiments": [exp],
              "findings": [finding(1, "confirmed", experiment_id="exp-001", S=72.4, fix=fix), finding(2), finding(3)],
              "counts": {"confirmed": 1, "refuted": 0, "inconclusive": 0, "predicted": 2}})
    return s


EXECUTOR = {"probe_path_services": ["frontend-proxy"],
            "journey_probes": {"browse": {"url": "u"}, "cart": {"url": "u"},
                               "shipping-quote": {"url": "u", "for_targets": ["shipping"]}}}
PLAN = [{"experiment_id": "exp-001", "expected_impact": {"journeys": ["browse", "cart"]}},
        {"experiment_id": "exp-002", "expected_impact": {"journeys": ["browse", "checkout"]}},
        {"experiment_id": "exp-003", "expected_impact": {"journeys": ["checkout"]}},
        {"experiment_id": "exp-004", "expected_impact": {"journeys": ["cart", "checkout"]}}]


def exp_row(exp_id, target):
    return {"experiment_id": exp_id, "target": target, "fault_type": "pod_kill", "outcome": "not_run"}


def good_text(extra=""):
    return "The cart service runs a single replica, so a restart drops cart for users. " + extra


class TestValidation(unittest.TestCase):
    def test_number_guard(self):
        facts = json.dumps({"S": 72.4, "n": 3})
        self.assertEqual(R.validate_text(good_text("Score 72.4 across 3 runs."), facts, CFG["llm"]), [])
        bad = R.validate_text(good_text("Score 99 across 3 runs."), facts, CFG["llm"])
        self.assertTrue(any("99" in p for p in bad))

    def test_length_bounds(self):
        self.assertTrue(R.validate_text("too short", "{}", CFG["llm"]))
        self.assertTrue(R.validate_text("x" * 901, "{}", CFG["llm"]))
        self.assertEqual(R.validate_text("x" * 100, "{}", CFG["llm"]), [])

    def test_unknown_and_duplicate_risk_id(self):
        facts = {"R-cart-c1": {"risk_id": "R-cart-c1"}}
        item = R.Explained(risk_id="R-nope", explanation=good_text(), why_fix_helps=good_text())
        self.assertTrue(R.validate_item(item, facts, set(), CFG["llm"]))
        ok = R.Explained(risk_id="R-cart-c1", explanation=good_text(), why_fix_helps=good_text())
        self.assertEqual(R.validate_item(ok, facts, set(), CFG["llm"]), [])
        self.assertTrue(R.validate_item(ok, facts, {"R-cart-c1"}, CFG["llm"]))


class TestBuild(unittest.TestCase):
    def build(self, scores, llm_result, use_llm=True):
        with patch("src.report.structured_call", return_value=llm_result) as m:
            rep = R.build_report(scores, GRAPH, RANKED, CFG, use_llm)
        return rep, m

    def test_llm_none_all_template(self):
        rep, _ = self.build(scores_not_scored(), None)
        self.assertEqual(len(rep["explained"]), 2)
        self.assertTrue(all(f["text_source"] == "template" for f in rep["explained"]))
        self.assertEqual(rep["executive_summary"]["text_source"], "template")
        self.assertIn("Prediction", rep["explained"][0]["explanation"])

    def test_no_llm_flag_skips_call(self):
        _, m = self.build(scores_not_scored(), None, use_llm=False)
        m.assert_not_called()

    def test_valid_ai_used(self):
        llm = R.ReportText(executive_summary=good_text("Score is 72.4."), items=[
            R.Explained(risk_id="R-cart-c1", explanation=good_text(), why_fix_helps=good_text()),
            R.Explained(risk_id="R-cart-c2", explanation=good_text(), why_fix_helps=good_text())])
        rep, _ = self.build(scores_scored(), llm)
        self.assertEqual([f["text_source"] for f in rep["explained"]], ["ai", "ai"])
        self.assertEqual(rep["executive_summary"]["text_source"], "ai")

    def test_one_bad_item_falls_back(self):
        llm = R.ReportText(executive_summary=good_text(), items=[
            R.Explained(risk_id="R-cart-c1", explanation=good_text(), why_fix_helps=good_text()),
            R.Explained(risk_id="R-cart-c2", explanation=good_text("It affects 777 users."),
                        why_fix_helps=good_text())])
        rep, _ = self.build(scores_scored(), llm)
        self.assertEqual([f["text_source"] for f in rep["explained"]], ["ai", "template"])


class TestMarkdown(unittest.TestCase):
    def test_not_scored(self):
        rep = R.build_report(scores_not_scored(), GRAPH, RANKED, CFG, False)
        md = R.render_markdown(rep)
        self.assertIn("Not scored yet - 0 of 8 planned experiments run", md)
        self.assertIn("python -m src.run --list", md)
        self.assertNotIn("--exp <id>", md)
        self.assertIn("python -m src.score && python -m src.report", md)
        self.assertIn("No experiments have been scored yet", md)

    def test_next_step_lists_only_runnable(self):
        s = scores_not_scored()
        s["experiments"] = [exp_row(i, t) for i, t in
                            [("exp-001", "frontend-proxy"), ("exp-002", "product-catalog"),
                             ("exp-003", "payment"), ("exp-004", "cart")]]
        rep = R.build_report(s, GRAPH, RANKED, CFG, False, plan=PLAN, executor=EXECUTOR)
        self.assertEqual([e["experiment_id"] for e in rep["runnable_experiments"]], ["exp-002", "exp-004"])
        md = R.render_markdown(rep)
        self.assertIn("python -m src.run --exp exp-002 --preview", md)
        self.assertIn("python -m src.run --exp exp-004 --preview", md)
        self.assertNotIn("--exp exp-001", md)  # frontend-proxy: probe path
        self.assertNotIn("--exp exp-003", md)  # payment: checkout only, no GET probe


class TestBadge(unittest.TestCase):
    def test_one_badge_per_status(self):
        cases = {
            "predicted": "Predicted (static audit, not yet tested)",
            "confirmed": "Confirmed by exp-004",
            "refuted": "Refuted by exp-004 (survived)",
            "inconclusive": "Inconclusive (exp-004)",
        }
        for status, text in cases.items():
            b = R.badge(finding(1, status, experiment_id="exp-004"))
            self.assertIn(text, b)
            self.assertIn("High", b)
            self.assertNotIn("`", b)

    def test_markdown_has_no_duplicate_status_basis(self):
        md = R.render_markdown(R.build_report(scores_not_scored(), GRAPH, RANKED, CFG, False))
        self.assertNotIn("`predicted` / `predicted`", md)
        self.assertIn("Predicted (static audit, not yet tested)", md)


class TestTemplateText(unittest.TestCase):
    def test_why_fix_differs_from_fix_summary(self):
        for check in R.WHY_FIX:
            fix = {"summary": "Do the thing", "files": [], "manual": None}
            facts = R.finding_facts(finding(1, check=check, fix=fix), {}, GRAPH, {"cart": RANKED[0]})
            _, why = R.template_explanation(facts)
            self.assertNotEqual(why, fix["summary"])
            self.assertNotIn("Do the thing", why)
            self.assertGreater(len(why), 40)

    def test_why_it_matters_uses_graph_context(self):
        facts = R.finding_facts(finding(1), {}, GRAPH, {"cart": RANKED[0]})
        expl, _ = R.template_explanation(facts)
        self.assertIn("cart is called by frontend", expl)
        self.assertIn("checkout path", expl)

    def test_scored(self):
        rep = R.build_report(scores_scored(), GRAPH, RANKED, CFG, False)
        md = R.render_markdown(rep)
        self.assertIn("72.4 / 100", md)
        self.assertIn("| Redundancy | 72.4 | 1 |", md)
        self.assertIn("kubectl apply -f a.yaml", md)
        self.assertIn("| exp-001 | cart | pod_kill | confirmed | 72.4 |", md)


if __name__ == "__main__":
    unittest.main()
