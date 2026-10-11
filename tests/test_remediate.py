"""Remediation agent: evidence, validation of the LLM output, fallback and verify. No network, no cluster."""
import unittest

from src import remediate

RISKS = [{"risk_id": "R-cart-a", "service": "cart", "evidence": "replicas=1"},
         {"risk_id": "R-ad-b", "service": "ad", "evidence": "no liveness"}]
FIXES = [{"risk_id": "R-ad-b", "service": "ad", "check": "no_liveness_probe", "severity": "medium",
          "summary": "add a livenessProbe to ad", "files": [{"path": "p", "apply": "a"}], "manual": None},
         {"risk_id": "R-cart-a", "service": "cart", "check": "single_replica_no_pdb", "severity": "high",
          "summary": "scale cart to 2", "files": [{"path": "q", "apply": "b"}], "manual": None}]
SCORES = {"experiments": [{"experiment_id": "exp-004", "risk_id": "R-cart-a", "S": 32.7, "severity": "High", "status": "scored"},
                          {"experiment_id": "exp-002", "risk_id": "R-ad-b", "S": 100, "severity": "Low", "status": "scored"}]}
PLAN = [{"experiment_id": "exp-004", "risk_id": "R-cart-a"}]
TXT = "x" * 30


def dec(rid, prio=1, **kw):
    return remediate.Decision(risk_id=rid, priority=prio, diagnosis=TXT, why_this_fix=TXT, **kw)


class RemediateTests(unittest.TestCase):
    def setUp(self):
        self.rows = remediate.gather(RISKS, FIXES, SCORES, PLAN)

    def test_measured_damage_ranks_first(self):
        self.assertEqual(remediate.deterministic_rank(self.rows)[0]["risk_id"], "R-cart-a")

    def test_unknown_and_duplicate_and_short_text_dropped(self):
        d = remediate.Decisions(decisions=[dec("R-cart-a"), dec("R-cart-a"), dec("R-fake"),
                                           remediate.Decision(risk_id="R-ad-b", priority=2, diagnosis="x", why_this_fix="y")])
        kept, dropped = remediate.validate_decisions(d, self.rows)
        self.assertEqual([k.risk_id for k in kept], ["R-cart-a"])
        self.assertEqual(len(dropped), 3)

    def test_verify_with_cannot_name_another_experiment(self):
        kept, _ = remediate.validate_decisions(remediate.Decisions(decisions=[dec("R-cart-a", verify_with="exp-999")]), self.rows)
        self.assertEqual(kept[0].verify_with, "exp-004")

    def test_llm_order_and_patch_come_from_index(self):
        d = remediate.Decisions(decisions=[dec("R-ad-b", 1), dec("R-cart-a", 2)])
        rep = remediate.build(self.rows, d, "m", FIXES)
        self.assertEqual(rep["agent"], "llm:m")
        self.assertEqual([i["risk_id"] for i in rep["items"]], ["R-ad-b", "R-cart-a"])
        self.assertEqual(rep["items"][0]["files"], FIXES[0]["files"])

    def test_not_real_weakness_is_left_out(self):
        d = remediate.Decisions(decisions=[dec("R-ad-b", real_weakness=False), dec("R-cart-a")])
        self.assertEqual([i["risk_id"] for i in remediate.build(self.rows, d, "m", FIXES)["items"]], ["R-cart-a"])

    def test_fallback_when_no_decisions(self):
        rep = remediate.build(self.rows, None, "m", FIXES)
        self.assertEqual(rep["agent"], "deterministic-fallback")
        self.assertEqual(rep["items"][0]["risk_id"], "R-cart-a")

    def test_all_invalid_decisions_fall_back(self):
        rep = remediate.build(self.rows, remediate.Decisions(decisions=[dec("R-fake")]), "m", FIXES)
        self.assertEqual(rep["agent"], "deterministic-fallback")

    def test_verify_before_after(self):
        rep = remediate.build(self.rows, None, "m", FIXES)
        after = {"experiments": [{"experiment_id": "exp-004", "S": 95.0, "severity": "Low"}]}
        v = remediate.verify(rep, after)
        self.assertEqual((v[0]["before"], v[0]["after"], v[0]["verdict"]), (32.7, 95.0, "improved"))

    def test_no_key_returns_none(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(remediate.call_groq(self.rows, {}))


if __name__ == "__main__":
    unittest.main()
