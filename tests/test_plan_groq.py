"""The Groq planner path: valid JSON is accepted, anything else keeps the deterministic text."""
import json
import os
import sys
import types
import unittest
from unittest import mock

from src import plan

CFG = {"provider": "groq", "groq_model": "test-model", "model": "x", "max_tokens": 1000,
       "journeys": ["browse", "cart", "checkout", "ads-recs"], "min_text_chars": 20, "max_text_chars": 400}


def fake_groq(content, finish_reason="stop"):
    """A stand-in `groq` module whose client returns `content`."""
    message = types.SimpleNamespace(content=content)
    response = types.SimpleNamespace(choices=[types.SimpleNamespace(message=message, finish_reason=finish_reason)])
    client = mock.MagicMock()
    client.chat.completions.create.return_value = response
    module = types.ModuleType("groq")
    module.Groq = mock.MagicMock(return_value=client)
    return module, client


class GroqPlannerTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def call(self, content, finish_reason="stop"):
        module, client = fake_groq(content, finish_reason)
        with mock.patch.dict(sys.modules, {"groq": module}):
            return plan.call_llm("{}", CFG), client

    def test_valid_json_is_parsed_and_sent_with_json_mode(self):
        body = {"items": [{"experiment_id": "exp-001", "hypothesis": "h" * 30, "reason": "r" * 30,
                           "journeys": ["cart"]}]}
        result, client = self.call(json.dumps(body))
        self.assertEqual(result.items[0].journeys, ["cart"])
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "test-model")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def test_bad_json_or_schema_keeps_deterministic_text(self):
        self.assertIsNone(self.call("not json")[0])
        self.assertIsNone(self.call(json.dumps({"items": [{"experiment_id": "exp-001"}]}))[0])
        self.assertIsNone(self.call(json.dumps({"items": [{"experiment_id": "exp-001", "hypothesis": "h" * 30,
                                                           "reason": "r" * 30, "journeys": ["admin"]}]}))[0])

    def test_truncated_output_is_rejected(self):
        self.assertIsNone(self.call("{}", finish_reason="length")[0])

    def test_no_key_skips_the_call(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(plan.call_llm("{}", CFG))

    def test_llm_text_cannot_change_targets(self):
        item = {"experiment_id": "exp-001", "target": {"service": "cart"}, "fault_type": "pod_kill",
                "hypothesis": "old", "reason": "old", "expected_impact": {"journeys": ["cart"]},
                "planner": "deterministic"}
        llm = plan.LLMPlanText.model_validate({"items": [
            {"experiment_id": "exp-001", "hypothesis": "h" * 30, "reason": "r" * 30, "journeys": ["cart"]},
            {"experiment_id": "exp-999", "hypothesis": "h" * 30, "reason": "r" * 30, "journeys": ["cart"]}]})
        self.assertEqual(plan.apply_llm_text([item], llm, CFG), 1)
        self.assertEqual((item["target"], item["fault_type"], item["planner"]),
                         ({"service": "cart"}, "pod_kill", "llm"))


if __name__ == "__main__":
    unittest.main()
