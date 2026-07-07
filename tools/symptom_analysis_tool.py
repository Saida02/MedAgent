"""
Symptom -> specialty/urgency analysis tool backing `symptom_analysis_skill`.

Per the workflow: if the patient already named a concrete medical specialty,
use it directly; otherwise infer one from the symptom text.

Done by an ADK LlmAgent only -- no keyword/regex guessing. If the agent
can't be reached, that failure is surfaced to the user as-is (GeminiError)
rather than guessing a specialty/urgency from a keyword list.
"""
import adk_llm


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
    result = adk_llm.generate_json(prompt, default, raise_on_failure=True, skill="symptom-analysis-skill")
    if result.get("urgency") not in ("LOW", "MEDIUM", "HIGH"):
        raise adk_llm.GeminiError(
            f"ADK agent returned an invalid urgency value: {result.get('urgency')!r}"
        )
    return result
