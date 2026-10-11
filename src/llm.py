"""llm.py - one structured Claude call, shared by plan.py and report.py.

The LLM never acts (Decisions.md D-005): callers give it facts, it returns text in a pydantic
schema, and callers validate and decide what to keep. Every failure - no `anthropic` package,
no credentials, API error, refusal, truncated or off-schema output - returns None with the
reason printed, so callers always have a deterministic fallback (Architecture.md section 9.4).
"""
import json
import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MAX_TOKENS = 8192
GROQ_TIMEOUT_S = 60
REPO_ROOT = Path(__file__).resolve().parent.parent


def read_env_file(path: Path, key: str) -> str | None:
    """Value of `key` from a simple KEY=VALUE .env file (comments/blank lines ignored, quotes stripped)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip().removeprefix("export ").strip() == key:
            return value.strip().strip("\"'") or None
    return None


def groq_key() -> str | None:
    """GROQ_API_KEY from the environment, else from the repo-root .env."""
    return os.environ.get("GROQ_API_KEY") or read_env_file(REPO_ROOT / ".env", "GROQ_API_KEY")


def structured_call(system: str, user: str, schema: type[BaseModel], cfg: dict, label: str) -> BaseModel | None:
    """cfg needs model, max_tokens (+ effort for anthropic); cfg["provider"] is anthropic (default) or groq.
    label prefixes the printed skip reason."""
    if cfg.get("provider", "anthropic") == "groq":
        return groq_call(system, user, schema, cfg, label)
    return anthropic_call(system, user, schema, cfg, label)


def groq_call(system: str, user: str, schema: type[BaseModel], cfg: dict, label: str) -> BaseModel | None:
    """Groq's OpenAI-compatible chat endpoint over plain requests (no extra dependency)."""
    def skip(reason: str) -> None:
        print(f"{label} skipped: {reason} - keeping deterministic text")

    key = groq_key()
    if not key:
        skip("no Groq key (set GROQ_API_KEY or add it to .env)")
        return None
    try:
        import requests
    except ImportError:
        skip("`requests` not installed (pip install requests)")
        return None

    model = os.environ.get("GROQ_MODEL") or cfg["model"]
    max_tokens = min(cfg.get("max_tokens", GROQ_MAX_TOKENS), GROQ_MAX_TOKENS)
    schema_json = schema.model_json_schema()
    fallback_system = (f"{system}\n\nReply with ONLY a JSON object matching this JSON schema:\n"
                       f"{json.dumps(schema_json)}")
    attempts = [
        (system, {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema_json}}),
        (fallback_system, {"type": "json_object"}),
    ]
    headers = {"Authorization": f"Bearer {key}"}

    for i, (sys_prompt, response_format) in enumerate(attempts):
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "response_format": response_format,
                "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]}
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=GROQ_TIMEOUT_S)
        except requests.RequestException as e:
            skip(f"can't reach Groq ({type(e).__name__})")
            return None
        code = resp.status_code
        if code == 400 and i == 0:
            continue  # schema / response_format not supported by this model - retry with json_object
        if code in (401, 403):
            skip("Groq key rejected")
            return None
        if code == 429:
            skip("rate limited")
            return None
        if code != 200:
            skip(f"Groq API error {code}")
            return None
        break

    try:
        choices = resp.json().get("choices") or []
        if not choices:
            skip("empty response from Groq")
            return None
        if choices[0].get("finish_reason") == "length":
            skip("incomplete output (finish_reason=length)")
            return None
        return schema.model_validate_json(choices[0]["message"]["content"])
    except (ValidationError, ValueError, KeyError, TypeError, AttributeError) as e:
        skip(f"output failed validation ({type(e).__name__})")
        return None


def anthropic_call(system: str, user: str, schema: type[BaseModel], cfg: dict, label: str) -> BaseModel | None:
    def skip(reason: str) -> None:
        print(f"{label} skipped: {reason} - keeping deterministic text")

    try:
        import anthropic
    except ImportError:
        skip("`anthropic` not installed (pip install anthropic)")
        return None

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.parse(
            model=cfg["model"],
            max_tokens=cfg["max_tokens"],
            thinking={"type": "adaptive"},
            output_config={"effort": cfg["effort"]},
            # On a safety-classifier decline, re-run server-side on Anthropic's recommended model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
    except anthropic.AuthenticationError:
        skip("credentials rejected")
        return None
    except anthropic.RateLimitError:
        skip("rate limited")
        return None
    except anthropic.APIStatusError as e:
        skip(f"API error {e.status_code}")
        return None
    except anthropic.APIConnectionError:
        skip("can't reach the API")
        return None
    except anthropic.AnthropicError as e:
        skip(f"{type(e).__name__}: {str(e)[:120]}")
        return None
    except (ValidationError, ValueError) as e:
        # off-schema or unparseable model output (ValueError covers JSON decode errors)
        skip(f"output failed validation ({type(e).__name__})")
        return None
    except TypeError as e:
        # The SDK raises a plain TypeError when no credentials are configured at all.
        # Anything else is a real bug and must surface.
        if "authentication method" not in str(e):
            raise
        skip("no credentials (set ANTHROPIC_API_KEY or run `ant auth login`)")
        return None

    if response.stop_reason == "refusal":
        skip("request was declined")
        return None
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        skip(f"incomplete output (stop_reason={response.stop_reason})")
        return None
    return response.parsed_output

