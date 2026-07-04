"""
Symptom -> specialty/urgency analysis tool backing `symptom_analysis_skill`.

Per the workflow: if the patient already named a concrete medical specialty,
use it directly; otherwise infer one from the symptom text.

Done by Gemini only -- no keyword/regex guessing. If Gemini can't be
reached, that failure is surfaced to the user as-is (GeminiError) rather
than guessing a specialty/urgency from a keyword list.
"""
from tools import gemini_tool


def analyze_symptoms(symptoms: str, severity: str | None) -> dict:
    prompt = f"""
Analyze this patient's symptoms for a healthcare appointment routing system.
Symptoms: "{symptoms}"
Self-rated severity (1-10, may be missing): {severity or "not provided"}

If the patient explicitly named a medical specialty, use that specialty
as-is. Otherwise infer the single most appropriate specialty
(e.g. cardiology, dermatology, general medicine, urgent care).
Determine urgency as exactly one of: LOW, MEDIUM, HIGH (HIGH = needs urgent
care / ER).

Return strict JSON: {{"specialty": "", "urgency": "", "symptom_summary": ""}}.
symptom_summary should be a concise one-sentence summary.
"""
    default = {"specialty": None, "urgency": None, "symptom_summary": None}
    result = gemini_tool.generate_json(prompt, default, raise_on_failure=True)
    if result.get("urgency") not in ("LOW", "MEDIUM", "HIGH"):
        raise gemini_tool.GeminiError(
            f"Gemini returned an invalid urgency value: {result.get('urgency')!r}"
        )
    return result
