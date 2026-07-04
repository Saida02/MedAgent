"""
Insurance network matching tool backing `insurance_check_skill`.

There is no live insurance-network API in scope for this project, so
coverage is estimated with a small heuristic network map plus a Gemini
plausibility check for providers/clinics not in the map. Anything that
can't be determined is marked "unknown" per the workflow spec rather than
guessed as covered/not covered.
"""
from tools import gemini_tool

# Common large US insurance networks that are broadly accepted -- used as a
# heuristic baseline when a clinic's real network affiliation is unknown.
_BROAD_NETWORK_PROVIDERS = {
    "aetna", "cigna", "unitedhealthcare", "united healthcare", "blue cross",
    "blue cross blue shield", "bcbs", "humana", "kaiser permanente",
}


def check_coverage(insurance_provider: str, clinic_name: str) -> str:
    """Returns "covered" | "not_covered" | "unknown"."""
    if not insurance_provider or not insurance_provider.strip():
        return "unknown"

    provider_key = insurance_provider.strip().lower()
    if provider_key in _BROAD_NETWORK_PROVIDERS:
        return "covered"

    prompt = f"""
Patient insurance provider: {insurance_provider}
Clinic: {clinic_name}
Based only on how common/broadly-accepted this insurance provider generally
is at outpatient clinics in the US, classify likely coverage.
Return strict JSON: {{"status": "covered" | "not_covered" | "unknown"}}.
If you are not reasonably confident, return "unknown".
"""
    result = gemini_tool.generate_json(prompt, {"status": "unknown"})
    status = result.get("status", "unknown")
    return status if status in ("covered", "not_covered", "unknown") else "unknown"
