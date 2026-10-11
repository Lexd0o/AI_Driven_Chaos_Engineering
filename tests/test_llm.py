"""Tests for the Groq path of src/llm.py. Run: python -m unittest tests.test_llm -v
requests.post is always patched; nothing touches the network."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from src import llm

KEY = "gsk_FAKE_TEST_KEY_123"
CFG = {"provider": "groq", "model": "openai/gpt-oss-20b", "max_tokens": 16000}


class Out(BaseModel):
    text: str


def reply(code=200, content='{"text": "hi"}', finish="stop", choices=True):
    r = MagicMock()
    r.status_code = code
    r.json.return_value = {"choices": [{"finish_reason": finish, "message": {"content": content}}] if choices else []}
    return r


def run(responses, env=None):
    """Call structured_call with patched post; returns (result, printed, post mock)."""
    env = {"GROQ_API_KEY": KEY} if env is None else env
    buf = io.StringIO()
    with patch.dict(os.environ, env, clear=True), patch("requests.post", side_effect=responses) as post, \
            patch.object(llm, "REPO_ROOT", Path(tempfile.gettempdir()) / "no_such_repo_dir"), \
            contextlib.redirect_stdout(buf):
        result = llm.structured_call("sys", "user", Out, CFG, "Lbl")
    return result, buf.getvalue(), post


class GroqTests(unittest.TestCase):
    def test_success_json_schema(self):
        result, out, post = run([reply()])
        self.assertEqual(result, Out(text="hi"))
        self.assertEqual(post.call_count, 1)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 8192)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], f"Bearer {KEY}")
        self.assertNotIn(KEY, out)

    def test_400_falls_back_to_json_object(self):
        result, _, post = run([reply(400), reply()])
        self.assertEqual(result, Out(text="hi"))
        self.assertEqual(post.call_count, 2)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn("ONLY a JSON object", body["messages"][0]["content"])

    def test_no_key(self):
        result, out, post = run([], env={})
        self.assertIsNone(result)
        self.assertIn("Lbl skipped: no Groq key", out)
        post.assert_not_called()

    def test_401(self):
        result, out, _ = run([reply(401)])
        self.assertIsNone(result)
        self.assertIn("Groq key rejected", out)
        self.assertNotIn(KEY, out)

    def test_429_and_500(self):
        self.assertIn("rate limited", run([reply(429)])[1])
        self.assertIn("Groq API error 500", run([reply(500)])[1])

    def test_empty_choices(self):
        result, out, _ = run([reply(choices=False)])
        self.assertIsNone(result)
        self.assertIn("skipped", out)

    def test_finish_length(self):
        result, out, _ = run([reply(finish="length")])
        self.assertIsNone(result)
        self.assertIn("incomplete output", out)

    def test_malformed_json(self):
        result, out, _ = run([reply(content="not json")])
        self.assertIsNone(result)
        self.assertIn("output failed validation", out)

    def test_schema_invalid_json(self):
        result, out, _ = run([reply(content='{"text": 5}')])
        self.assertIsNone(result)
        self.assertIn("output failed validation", out)
        self.assertNotIn(KEY, out)

    def test_env_file_key(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".env").write_text(f'# c\n\nOTHER=1\nGROQ_API_KEY="{KEY}"\n', encoding="utf-8")
            buf = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch.object(llm, "REPO_ROOT", Path(d)), \
                    patch("requests.post", return_value=reply()) as post, contextlib.redirect_stdout(buf):
                result = llm.structured_call("s", "u", Out, CFG, "Lbl")
            self.assertEqual(result, Out(text="hi"))
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], f"Bearer {KEY}")
            self.assertNotIn(KEY, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
