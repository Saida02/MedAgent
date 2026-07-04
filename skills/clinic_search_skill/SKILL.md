---
name: clinic_search_skill
description: Step 3/7 of the healthcare appointment pipeline. Finds up to 10 nearest/most-relevant clinics via Google Maps MCP, with a location fallback chain.
---

# clinic_search_skill

**Pipeline position:** 3 of 7
**Input:** `location` (optional), `specialty`
**Output:** JSON passed to `rating_engineer_skill`

## Purpose

Find the 10 nearest clinics using the Google Maps MCP server (already
configured in `.agents/mcp_config.json`), filtered by specialty relevance
and ranked by distance.

## Location handling — critical rule

`location` is **optional**. Missing location is **not an error** — it means
"use a broader search". The pipeline must never stop or re-ask the user.

**If location exists:** use it directly, filter by specialty, rank by distance.

**If location is missing (null):**
1. Call `scripts/ip_geolocation_tool.py` (`get_location_from_ip`) to derive
   a city/coordinates from the caller's IP address, then pass those to the
   Maps MCP server.
2. Ask Maps MCP for "top clinics in user region" within a reasonable radius.
3. If nothing found, broaden to "top rated clinics near major city center
   in state".
4. If still nothing, fall back to "highest rated clinics for specialty"
   (national fallback).
5. Use the LLM (Gemini) to enrich each result with the specific doctor
   (within the needed specialty) and contact info for the appointment.

## Output JSON

```json
{
  "clinics": [
    {
      "name": "",
      "address": "",
      "distance": "unknown_if_no_location",
      "rating": "",
      "place_id": "",
      "clinics_email": "",
      "clinics_phone": "",
      "doctor_first_name": "",
      "doctor_last_name": "",
      "rating_doctor": "",
      "summary_doctor": "",
      "doctor_email": "",
      "doctor_phone": ""
    }
  ]
}
```

## Files

- [`scripts/ip_geolocation_tool.py`](scripts/ip_geolocation_tool.py) — IP-based
  geolocation fallback, called only when `location == null`.
- [`tools/maps_tool.py`](../../tools/maps_tool.py) — orchestrates the full
  fallback chain above: Maps MCP → direct Places API fallback → Gemini
  doctor enrichment.

➡️ Pass to `rating_engineer_skill`.
