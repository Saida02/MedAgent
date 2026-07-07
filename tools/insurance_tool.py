"""
Insurance network matching tool backing `insurance_check_skill`.

Uses an ADK LlmAgent with the built-in google_search grounding tool to look
up what a specific clinic/doctor's own website actually lists as accepted
insurance, instead of guessing from how generally common a provider is. One
batched call covers every clinic in the search results (see maps_tool's
doctor-lookup batching for the same rationale: fewer LLM calls per turn).
"""
import adk_llm


def _unknown_result(clinics: list) -> list:
    return [{"insurance_status": "unknown", "insurance_detail": "Could not verify with a web search."} for _ in clinics]


def check_coverage_batch(insurance_provider: str, clinics: list) -> list:
    """
    Returns a list (same order as `clinics`) of
    {"insurance_status": "covered"|"not_covered"|"unknown",
     "insurance_detail": "<what was actually found, or why not>"}.
    """
    if not insurance_provider or not insurance_provider.strip():
        return [{"insurance_status": "unknown", "insurance_detail": "No insurance provider given."} for _ in clinics]
    if not clinics:
        return []

    clinic_list = "\n".join(
        f"{i + 1}. {c.get('name')} -- {c.get('address')}" for i, c in enumerate(clinics)
    )
    prompt = f"""
For EACH clinic below, search the web (their own website, or a reputable
listing) for what insurance providers they actually accept, and determine
whether "{insurance_provider}" is among them.
Clinics:
{clinic_list}

Return a single JSON object: {{"results": [ {{"insurance_status": "covered" |
"not_covered" | "unknown", "insurance_detail": "<one sentence: what you
actually found, e.g. their listed accepted insurers, or that no insurance
information was publicly found>"}}, ... ]}}
The "results" array MUST have exactly {len(clinics)} entries, in the SAME
ORDER as the numbered clinics above.
If you cannot find real information for a clinic, use "unknown" -- never
guess "covered" or "not_covered" without something you actually found.
"""
    default = {"results": _unknown_result(clinics)}
    result = adk_llm.generate_json(prompt, default, grounded=True, skill="insurance-check-skill")
    results = result.get("results")
    if not isinstance(results, list) or len(results) != len(clinics):
        return default["results"]

    merged = []
    for entry in results:
        if not isinstance(entry, dict) or entry.get("insurance_status") not in ("covered", "not_covered", "unknown"):
            merged.append({"insurance_status": "unknown", "insurance_detail": "Could not verify with a web search."})
        else:
            entry.setdefault("insurance_detail", "")
            merged.append(entry)
    return merged
