"""
ADK-based reasoning layer -- the Google ADK equivalent of the original
project's tools/gemini_tool.py. Every LLM call in this project goes through
an ADK LlmAgent (run via InMemoryRunner) instead of a raw google-genai
client call, so the agent framework itself is genuinely doing the
reasoning, not just wrapping it.

Same public contract as the original gemini_tool.py on purpose: generate_json
/ generate_text with the same grounded / raise_on_failure semantics, and the
same GeminiError -- so no regex/keyword guessing is needed anywhere, and a
failure surfaces the real error instead of a fabricated value, exactly like
before.
"""
import asyncio
import functools
import json
import logging
import threading
import uuid
from pathlib import Path

from config import GEMINI_MODEL
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.adk.skills import load_skill_from_dir
from google.adk.tools import google_search
from google.genai import types

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@functools.lru_cache(maxsize=None)
def _load_skill_instructions(skill_name: str) -> str:
    """Loads skills/<skill_name>/SKILL.md via ADK's own skill loader and
    returns its markdown instructions -- cached since the file never changes
    at runtime. This is what makes each pipeline step's SKILL.md genuinely
    govern that step's agent, instead of sitting unread next to the code
    that's supposed to follow it."""
    skill = load_skill_from_dir(str(_SKILLS_DIR / skill_name))
    return skill.instructions


class GeminiError(Exception):
    """Raised (instead of silently falling back) when raise_on_failure=True
    and the ADK agent call fails or returns something unusable -- carries
    the real error text."""


def _extract_json_object(text: str) -> dict | None:
    """Gemini often wraps JSON in a ```json ... ``` fence, and grounded
    responses may add a sentence around it; slice out the outermost {...}
    span (no regex -- just the first '{' and the last '}') and parse that."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _run_agent_sync(instruction: str, message: str, grounded: bool, skill: str | None, timeout: float = 30.0) -> str:
    """Runs a one-shot ADK LlmAgent turn synchronously (Flask's request
    handlers are sync) by isolating the asyncio event loop in its own
    thread, the same pattern mcp_client.call_tool uses for MCP calls.
    Returns the agent's raw text response."""
    result_box = {}

    def _runner():
        try:
            result_box["value"] = asyncio.run(_run_agent_async(instruction, message, grounded, skill))
        except Exception as exc:  # noqa: BLE001
            result_box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise GeminiError(f"ADK agent call timed out after {timeout}s")
    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("value", "")


async def _run_agent_async(instruction: str, message: str, grounded: bool, skill: str | None) -> str:
    if skill:
        # The pipeline step's own SKILL.md becomes this call's system
        # instruction, with the specific task prompt appended -- so the
        # documented purpose/rules/output-schema in SKILL.md are what the
        # model actually reasons from, not just adjacent documentation.
        instruction = _load_skill_instructions(skill) + "\n\n---\n\n" + instruction
    agent = LlmAgent(
        name=f"medagent_turn_{uuid.uuid4().hex[:8]}",
        model=GEMINI_MODEL,
        instruction=instruction,
        tools=[google_search] if grounded else [],
    )
    app_name = "medagent"
    user_id = "patient"
    runner = InMemoryRunner(agent=agent, app_name=app_name)
    session = await runner.session_service.create_session(app_name=app_name, user_id=user_id)
    content = types.Content(role="user", parts=[types.Part(text=message or "Proceed.")])

    texts = []
    async for event in runner.run_async(user_id=user_id, session_id=session.id, new_message=content):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    texts.append(part.text)
    await runner.close()
    return "\n".join(texts)


def generate_json(
    prompt: str, default: dict, grounded: bool = False, raise_on_failure: bool = False, skill: str | None = None,
) -> dict:
    """ADK-agent equivalent of gemini_tool.generate_json. `prompt` becomes
    the one-shot agent's task instruction; grounded=True attaches ADK's
    built-in google_search tool for real-world facts instead of parametric
    guesses. `skill` names a directory under skills/ (kebab-case, e.g.
    "intake-skill") whose SKILL.md is loaded via ADK's native skill loader
    and prepended as the agent's governing instruction. raise_on_failure=True
    raises GeminiError instead of returning `default` -- use this wherever
    `default` would otherwise be a guess rather than an honest "unknown"
    value."""
    try:
        text = _run_agent_sync(
            prompt + "\n\nRespond with a single JSON object only, no other text.",
            "Proceed.",
            grounded,
            skill,
        )
        parsed = _extract_json_object(text)
        if parsed is not None:
            return parsed
        if raise_on_failure:
            raise GeminiError("ADK agent's response did not contain valid JSON.")
        return default
    except GeminiError:
        raise
    except Exception as exc:  # noqa: BLE001
        if raise_on_failure:
            raise GeminiError(str(exc)) from exc
        logger.warning("ADK agent generate_json failed, using default: %s", exc)
        return default


def generate_text(
    prompt: str, default: str = "", grounded: bool = False, raise_on_failure: bool = False, skill: str | None = None,
) -> str:
    try:
        text = _run_agent_sync(prompt, "Proceed.", grounded, skill).strip()
        if text:
            return text
        if raise_on_failure:
            raise GeminiError("ADK agent returned an empty response.")
        return default
    except GeminiError:
        raise
    except Exception as exc:  # noqa: BLE001
        if raise_on_failure:
            raise GeminiError(str(exc)) from exc
        logger.warning("ADK agent generate_text failed, using default: %s", exc)
        return default
