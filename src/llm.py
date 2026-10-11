"""llm.py - one structured Claude call, shared by plan.py and report.py.

The LLM never acts (Decisions.md D-005): callers give it facts, it returns text in a pydantic
schema, and callers validate and decide what to keep. Every failure - no `anthropic` package,
no credentials, API error, refusal, truncated or off-schema output - returns None with the
reason printed, so callers always have a deterministic fallback (Architecture.md section 9.4).
"""
from pydantic import BaseModel, ValidationError


def structured_call(system: str, user: str, schema: type[BaseModel], cfg: dict, label: str) -> BaseModel | None:
    """cfg needs model, effort, max_tokens. label prefixes the printed skip reason."""
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
