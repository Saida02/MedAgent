"""
Scoring/ranking tool backing `rating_engineer_skill`.

Scores each clinic+doctor pair on a 0-100 scale using: Google rating,
distance, specialty match, and an LLM-based review-sentiment proxy (via the
doctor summary text, since no live review feed is wired up). Falls back to
a pure-heuristic score (no sentiment term) if Gemini is unavailable.
"""
from tools import gemini_tool


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _distance_score(distance: str) -> float:
    if not distance or distance == "unknown_if_no_location":
        return 5.0  # neutral score when distance can't be computed
    try:
        km = float("".join(ch for ch in distance if ch.isdigit() or ch == "."))
        return max(0.0, 10.0 - km)  # closer = higher, floors at 0
    except ValueError:
        return 5.0


def _sentiment_scores_batch(summaries: list) -> list:
    """One Gemini call scoring every clinic's summary at once, instead of
    one call per clinic -- see maps_tool._enrich_with_doctors_batch for why
    that matters under the free tier's 5-requests/minute limit."""
    if not summaries:
        return []
    items = "\n".join(f'{i + 1}. "{s}"' for i, s in enumerate(summaries))
    prompt = f"""
Rate the sentiment of EACH of these doctor/clinic summaries on a 0-10 scale,
where 10 is extremely positive and 0 is extremely negative or absent.
Summaries:
{items}
Return a single JSON object: {{"scores": [<number>, ...]}} with exactly
{len(summaries)} entries, in the same order as the numbered summaries above.
"""
    default = {"scores": [7.0] * len(summaries)}
    result = gemini_tool.generate_json(prompt, default)
    scores = result.get("scores")
    if not isinstance(scores, list) or len(scores) != len(summaries):
        scores = default["scores"]
    return [max(0.0, min(10.0, _safe_float(s, 7.0))) for s in scores]


def score_clinic(clinic: dict, specialty: str, sentiment: float) -> dict:
    """
    Returns {"clinic_final_score", "clinic_reason", "doctor_name",
    "doctor_final_score", "doctor_reason"} for one clinic entry.
    """
    rating = _safe_float(clinic.get("rating"), 4.0)
    doctor_rating = _safe_float(clinic.get("rating_doctor"), rating)
    dist_score = _distance_score(clinic.get("distance", ""))
    specialty_match = 10.0 if specialty and specialty.lower() in (clinic.get("summary_doctor", "") + clinic.get("name", "")).lower() else 6.0

    clinic_score = round((rating * 10 * 0.4) + (dist_score * 3) + (specialty_match * 3), 1)
    doctor_score = round((doctor_rating * 10 * 0.4) + (specialty_match * 3) + (sentiment * 3), 1)

    # Empty first/last name is maps_tool's explicit "no real doctor found"
    # signal -- show that honestly instead of a "Dr. " prefix that implies
    # a specific (but unverified) person.
    first = (clinic.get("doctor_first_name") or "").strip()
    last = (clinic.get("doctor_last_name") or "").strip()
    doctor_name = f"Dr. {first} {last}".strip() if (first or last) else "No specific doctor found"

    return {
        "clinic_name": clinic.get("name", ""),
        "clinic_final_score": min(100.0, clinic_score),
        "clinic_reason": f"Google rating {rating}, distance factor {dist_score}/10, specialty match {specialty_match}/10.",
        "doctor_name": doctor_name,
        "doctor_final_score": min(100.0, doctor_score),
        "doctor_reason": f"Doctor rating {doctor_rating}, sentiment {sentiment}/10, specialty match {specialty_match}/10.",
    }


def rank_clinics(clinics: list, specialty: str) -> list:
    sentiments = _sentiment_scores_batch([c.get("summary_doctor", "") for c in clinics])
    scored = [score_clinic(c, specialty, sentiment) for c, sentiment in zip(clinics, sentiments)]
    scored.sort(key=lambda x: x["clinic_final_score"], reverse=True)
    return scored
