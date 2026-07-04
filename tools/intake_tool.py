"""
Free-text -> structured-intake extraction tool backing `intake_skill`.

The UI collects everything through a single chat box (see
health_agent_v2_updated.html), so this tool re-runs extraction over the full
accumulated conversation text on every turn and merges newly-found fields
into the existing intake JSON -- previously-known fields are never
overwritten with a null value, matching the workflow's "never re-ask unless
critical missing field" rule.

Extraction is done by Gemini only -- no regex/keyword guessing. If Gemini
can't be reached, that failure is surfaced to the user as-is (via
GeminiError) rather than guessing at their name/symptoms from a pattern.
"""
from tools import gemini_tool

REQUIRED_FIELDS = ["name", "symptoms", "age"]  # minimum needed to leave intake_skill
_OPTIONAL_FIELDS = ["location", "severity", "insurance", "preferred_time"]
ALL_FIELDS = REQUIRED_FIELDS + _OPTIONAL_FIELDS


def extract_intake_fields(conversation_text: str) -> dict:
    prompt = f"""
Extract patient intake information from this conversation text for a
healthcare appointment booking assistant.
Return strict JSON with exactly these keys: name, age, location, symptoms,
severity, insurance, preferred_time.
- "name" is the patient's full name.
- "age" may hold an age or a date of birth, whichever the patient gave.
- "location" is a city/ZIP or null if not mentioned.
- "severity" is a 1-10 self-rating string, or null if not mentioned.
- Use null for anything not mentioned. Do not invent values.

Conversation:
---
{conversation_text}
---
"""
    empty = {field: None for field in ALL_FIELDS}
    result = gemini_tool.generate_json(prompt, empty, raise_on_failure=True)
    return {field: result.get(field) for field in ALL_FIELDS}


def missing_required_fields(intake: dict) -> list:
    return [f for f in REQUIRED_FIELDS if not intake.get(f)]
