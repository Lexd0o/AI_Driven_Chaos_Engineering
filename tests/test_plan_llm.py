import contextlib
import io
import unittest

from src import plan

CFG = {"min_text_chars": 10, "max_text_chars": 400, "journeys": ["browse", "cart", "checkout", "ads-recs"]}


def _plan():
    return [{"experiment_id": "exp-005", "planner": "template", "hypothesis": "t", "reason": "r",
             "expected_impact": {"journeys": ["cart", "checkout"]}}]


class ApplyLLMTextTests(unittest.TestCase):
    def _apply(self, item):
        llm = plan.LLMPlanText(items=[item])
        with contextlib.redirect_stdout(io.StringIO()):
            return plan.apply_llm_text(_plan_ := self.plan, llm, CFG)

    def setUp(self):
        self.plan = _plan()

    def test_wrong_journeys_ignored(self):
        n = self._apply(plan.LLMItem(experiment_id="exp-005", hypothesis="Cart checkout fails while active.",
                                     reason="Cart is called by checkout.", journeys=["ads-recs", "browse"]))
        self.assertEqual(n, 1)
        self.assertEqual(self.plan[0]["expected_impact"]["journeys"], ["cart", "checkout"])
        self.assertEqual(self.plan[0]["planner"], "llm")

    def test_text_ascii(self):
        self._apply(plan.LLMItem(experiment_id="exp-005", hypothesis="Checkout\u2011cart \u201cfails\u201d \u2014 p99\u00a0rises \u2192 ok",
                                 reason="Because\u2019s caller \u00e9 volume is high."))
        for f in ("hypothesis", "reason"):
            self.plan[0][f].encode("ascii")
        self.assertIn('"fails" - p99 rises', self.plan[0]["hypothesis"])


if __name__ == "__main__":
    unittest.main()
