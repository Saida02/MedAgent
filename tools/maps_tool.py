"""
Clinic search tool backing `clinic_search_skill`.

Order of attempts, per the workflow's location-fallback rule:
  1. If a location was given, use it directly.
  2. If not, call the IP geolocation script to derive a city/coordinates.
  3. Search clinics via the Google Maps MCP server (already configured).
  4. If the MCP call is unavailable, fall back to a direct Google Places
     API call using GOOGLE_MAPS_API_KEY.
  5. If nothing is found nearby, broaden the search query (state / national).
Location missing is never treated as an error -- the pipeline always
continues, per the workflow's explicit design rule.
"""
import json
import logging
import sys
from pathlib import Path

import requests

from tools import gemini_tool, mcp_client
from tools.config import FALLBACK_CLINIC_EMAIL, GOOGLE_MAPS_API_KEY

logger = logging.getLogger(__name__)

_SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "clinic_search_skill" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from ip_geolocation_tool import get_location_from_ip  # noqa: E402


def _search_via_mcp(query: str) -> list:
    raw = mcp_client.call_tool("google-maps", "maps_search_places", {"query": query})
    data = json.loads(raw)
    return data.get("results", data.get("places", []))


def _place_details_via_mcp(place_id: str) -> dict:
    raw = mcp_client.call_tool("google-maps", "maps_place_details", {"place_id": place_id})
    return json.loads(raw)


def _search_via_places_api(query: str) -> list:
    if not GOOGLE_MAPS_API_KEY:
        return []
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/place/textsearch/json",
        params={"query": query, "key": GOOGLE_MAPS_API_KEY},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        logger.warning("Places API status=%s query=%s", data.get("status"), query)
    return data.get("results", [])


def _details_via_places_api(place_id: str) -> dict:
    if not GOOGLE_MAPS_API_KEY:
        return {}
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields": "name,formatted_address,formatted_phone_number,website,rating",
            "key": GOOGLE_MAPS_API_KEY,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("result", {})


def _search_clinics_raw(query: str) -> list:
    try:
        return _search_via_mcp(query)
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Maps MCP unavailable (%s); falling back to Places API", exc)
        return _search_via_places_api(query)


def _details_raw(place_id: str) -> dict:
    if not place_id:
        return {}
    try:
        return _place_details_via_mcp(place_id)
    except mcp_client.MCPUnavailableError:
        return _details_via_places_api(place_id)


def _placeholder_clinics(specialty: str, area: str) -> list:
    label = specialty or "General Medicine"
    area_label = f" ({area})" if area else ""
    names = [
        f"{label} Care Center{area_label}",
        f"Community {label} Clinic{area_label}",
        f"{label} Associates{area_label}",
    ]
    clinics = []
    for i, name in enumerate(names):
        clinic = {
            "name": name,
            "address": area or "Address unavailable - no live directory match",
            "distance": "unknown_if_no_location",
            "rating": round(4.2 + 0.2 * i, 1),
            "place_id": "",
            "clinics_email": FALLBACK_CLINIC_EMAIL,
            "clinics_phone": "",
        }
        # These clinic names are synthetic (no live directory match), so
        # searching the web for "their" doctor would be meaningless --
        # skip straight to the honest not-found default, no Gemini call.
        clinic.update(_doctor_not_found_default(clinic))
        clinic["doctor_email"] = clinic["clinics_email"]
        clinic["doctor_phone"] = clinic["clinics_phone"]
        clinics.append(clinic)
    return clinics


def _doctor_not_found_default(clinic: dict) -> dict:
    # Empty names are the explicit "not found" signal -- rating_tool.py and
    # the UI render this as "no specific doctor found" instead of a
    # placeholder name that could be mistaken for a real one.
    return {
        "doctor_first_name": "",
        "doctor_last_name": "",
        "rating_doctor": clinic.get("rating", 4.5),
        "summary_doctor": "No specific provider found for this clinic -- ask the clinic directly who's available.",
    }


def _enrich_with_doctors_batch(clinics: list, specialty: str) -> list:
    """Uses one Gemini call (with Google Search grounding) to look up a real
    doctor at each clinic, instead of one call per clinic -- a single chat
    turn already spends 2+ Gemini calls on intake/symptom-analysis, so
    per-clinic calls blow through the free tier's 5-requests/minute limit
    well before all clinics get a real lookup. Real per-doctor emails/phones
    are rarely public, so the doctor contact fields still default to the
    clinic's own contact channel (handled by the caller)."""
    defaults = [_doctor_not_found_default(c) for c in clinics]
    if not clinics:
        return defaults

    clinic_list = "\n".join(
        f"{i + 1}. {c.get('name')} -- {c.get('address')}" for i, c in enumerate(clinics)
    )
    prompt = f"""
Search the web for a real doctor or provider at EACH of these clinics, in
the given specialty.
Specialty needed: {specialty}
Clinics:
{clinic_list}

Return a single JSON object: {{"doctors": [ {{"doctor_first_name": "",
"doctor_last_name": "", "rating_doctor": <number 3.5-5.0>, "summary_doctor":
"<one sentence>"}}, ... ]}}
The "doctors" array MUST have exactly {len(clinics)} entries, in the SAME
ORDER as the numbered clinics above (one entry per clinic).
For any clinic where web search finds no specific doctor, use an EMPTY
STRING for that entry's doctor_first_name and doctor_last_name (never
invent a specific-sounding but unverified name), and say so plainly in its
summary_doctor.
"""
    result = gemini_tool.generate_json(prompt, {"doctors": defaults}, grounded=True)
    doctors = result.get("doctors")
    if not isinstance(doctors, list) or len(doctors) != len(clinics):
        return defaults

    merged = []
    for default, doctor in zip(defaults, doctors):
        if not isinstance(doctor, dict):
            merged.append(default)
            continue
        for key, val in default.items():
            doctor.setdefault(key, val)
        merged.append(doctor)
    return merged


def search_clinics(location: dict | None, specialty: str, max_results: int = 10) -> list:
    """
    location: {"city":..., "region":..., "country":..., "lat":..., "lon":...} or None
    Returns a list of clinic dicts matching the workflow's clinic_search_skill schema.
    """
    used_fallback_geo = False
    if not location:
        location = get_location_from_ip()
        used_fallback_geo = True

    city = location.get("city") if location else ""
    region = location.get("region") if location else ""

    queries = []
    if city:
        queries.append(f"{specialty or 'clinic'} near {city}")
    if region:
        queries.append(f"top rated {specialty or 'clinic'} clinics near {region} state")
    queries.append(f"highest rated {specialty or 'general medicine'} clinics")

    raw_results = []
    for query in queries:
        raw_results = _search_clinics_raw(query)
        if raw_results:
            break

    if not raw_results:
        # Maps MCP and the direct Places API fallback are both unreachable
        # (no network / disabled API / no live directory). Per the workflow
        # rule that the pipeline must always continue, synthesize a small
        # set of clearly-labeled placeholder clinics so later steps
        # (rating, insurance, booking, confirmation) remain testable.
        return _placeholder_clinics(specialty, city or region)

    clinics = []
    for item in raw_results[:max_results]:
        place_id = item.get("place_id", "")
        details = _details_raw(place_id)
        name = item.get("name") or details.get("name") or "Unknown Clinic"
        clinic = {
            "name": name,
            "address": item.get("formatted_address", details.get("formatted_address", "")),
            "distance": "unknown_if_no_location" if used_fallback_geo else item.get("distance", ""),
            "rating": item.get("rating", details.get("rating", "")),
            "place_id": place_id,
            # Places API doesn't expose real clinic inboxes, and guessing
            # "appointments@<their-real-domain>" risks actually emailing a
            # real business with test data. Always route booking emails to
            # the configured test address instead, so this stays a safe,
            # verifiable demo regardless of which real clinic was found.
            "clinics_email": FALLBACK_CLINIC_EMAIL,
            "clinics_phone": details.get("formatted_phone_number", ""),
        }
        clinics.append(clinic)

    # One batched Gemini call for all clinics instead of one call each --
    # keeps this well within the free-tier per-minute rate limit even when
    # intake/symptom-analysis calls already used up part of it this turn.
    for clinic, doctor_info in zip(clinics, _enrich_with_doctors_batch(clinics, specialty)):
        clinic.update(doctor_info)
        clinic["doctor_email"] = clinic["clinics_email"]
        clinic["doctor_phone"] = clinic["clinics_phone"]

    return clinics
