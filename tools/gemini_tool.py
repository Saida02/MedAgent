"""
Gemini reasoning layer. Used by several skills (symptom analysis, clinic /
doctor enrichment, rating engineering, reply parsing) to turn free text into
the structured JSON each pipeline step requires.

Doctor/clinic enrichment specifically needs real-world facts Gemini's
training data can't reliably provide (which doctor works where), so those
calls enable Gemini's built-in Google Search grounding tool -- the model
issues real web searches and grounds its answer in the results, instead of
guessing from parametric memory alone. This requires the newer `google-genai`
SDK (the legacy `google-generativeai` package doesn't expose the grounding
tool for Gemini 2.x models).

No regex/keyword guessing lives in this project: when a caller passes
raise_on_failure=True (intake, symptom analysis, reply-time parsing), a
Gemini failure raises GeminiError with the real error text instead of
silently substituting a guessed value -- callers surface that message to
the user rather than pretending nothing went wrong. Other callers pass a
plain, honest default (e.g. "unknown"/"not found", never a guess) that
generate_json falls back to instead.
"""
import json
import logging

from tools.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_client = None
_client_ready = False
_REQUEST_TIMEOUT_MS = 15000

if GEMINI_API_KEY:
    try:
        from google import genai

        _client = genai.Client(api_key=GEMINI_API_KEY)
        _client_ready = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini client init failed: %s", exc)


class GeminiError(Exception):
    """Raised (instead of silently falling back) when raise_on_failure=True
    and Gemini is unavailable or errors -- carries the real error text."""


def is_available() -> bool:
    return _client_ready


def _extract_json_object(text: str) -> dict | None:
    """Grounded responses aren't guaranteed to be pure JSON (the model may
    add a sentence around it); slice out the outermost {...} span (no
    regex -- just the first '{' and the last '}') and parse that."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def generate_json(prompt: str, default: dict, grounded: bool = False, raise_on_failure: bool = False) -> dict:
    """Ask Gemini for a JSON object matching `default`'s shape.

    grounded=True enables Google Search grounding for prompts that need
    real-world facts (e.g. "who actually works at this clinic") rather than
    the model's own guess.

    raise_on_failure=True raises GeminiError on any failure instead of
    returning `default` -- use this where `default` would otherwise be a
    guess rather than an honest "unknown" value.
    """
    if not _client_ready:
        if raise_on_failure:
            raise GeminiError("Gemini is not configured (missing/invalid GEMINI_API_KEY).")
        return default

    from google.genai import types

    try:
        if grounded:
            # response_mime_type=json isn't reliably honored together with
            # search grounding, so ask in plain text and parse the JSON
            # block back out.
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
            )
            response = _client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt + "\n\nRespond with a single JSON object only.",
                config=config,
            )
            parsed = _extract_json_object(response.text or "")
            if parsed is not None:
                return parsed
            if raise_on_failure:
                raise GeminiError("Gemini's grounded response did not contain valid JSON.")
            return default

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
        )
        response = _client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=config
        )
        parsed = json.loads((response.text or "").strip())
        if isinstance(parsed, dict):
            return parsed
        if raise_on_failure:
            raise GeminiError("Gemini returned valid JSON but it wasn't an object.")
        return default
    except GeminiError:
        raise
    except Exception as exc:  # noqa: BLE001
        if raise_on_failure:
            raise GeminiError(str(exc)) from exc
        logger.warning("Gemini generate_json failed, using default: %s", exc)
        return default


def generate_text(prompt: str, default: str = "", grounded: bool = False, raise_on_failure: bool = False) -> str:
    if not _client_ready:
        if raise_on_failure:
            raise GeminiError("Gemini is not configured (missing/invalid GEMINI_API_KEY).")
        return default

    from google.genai import types

    try:
        config = types.GenerateContentConfig(http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS))
        if grounded:
            config.tools = [types.Tool(google_search=types.GoogleSearch())]
        response = _client.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=config)
        text = (response.text or "").strip()
        if text:
            return text
        if raise_on_failure:
            raise GeminiError("Gemini returned an empty response.")
        return default
    except GeminiError:
        raise
    except Exception as exc:  # noqa: BLE001
        if raise_on_failure:
            raise GeminiError(str(exc)) from exc
        logger.warning("Gemini generate_text failed, using default: %s", exc)
        return default
