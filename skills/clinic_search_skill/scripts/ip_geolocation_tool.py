"""
ip_geolocation_tool.py

Referenced directly in AI_Healthcare_Appointment_Agent_Workflow.md, step 3
(clinic_search_skill fallback): "When clinic_search_skill detects
location == null, it first calls this tool -> gets the city/coordinates
from the IP address -> then passes those coordinates to the Maps MCP."

Standalone script: importable as a function, or runnable directly
(`python ip_geolocation_tool.py`) for manual testing.
"""
import json
import logging

import requests

from tools.config import IPGEOLOCATION_API_KEY

logger = logging.getLogger(__name__)


def get_location_from_ip() -> dict:
    """
    Returns {"city": str, "region": str, "country": str, "lat": float,
    "lon": float} for the caller's public IP, or a safe national-fallback
    dict if the lookup fails (never raises -- the pipeline must keep going).
    """
    try:
        if IPGEOLOCATION_API_KEY:
            resp = requests.get(
                "https://api.ipgeolocation.io/ipgeo",
                params={"apiKey": IPGEOLOCATION_API_KEY},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "city": data.get("city") or "",
                "region": data.get("state_prov") or "",
                "country": data.get("country_name") or "",
                "lat": float(data.get("latitude") or 0.0),
                "lon": float(data.get("longitude") or 0.0),
                "source": "ipgeolocation.io",
            }

        # No key configured -> free ipapi.co tier (no key required).
        resp = requests.get("https://ipapi.co/json/", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise ValueError(data.get("reason", "ipapi.co error"))
        return {
            "city": data.get("city") or "",
            "region": data.get("region") or "",
            "country": data.get("country_name") or "",
            "lat": float(data.get("latitude") or 0.0),
            "lon": float(data.get("longitude") or 0.0),
            "source": "ipapi.co",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("IP geolocation failed, using national fallback: %s", exc)
        return {
            "city": "",
            "region": "",
            "country": "United States",
            "lat": None,
            "lon": None,
            "source": "fallback",
        }


if __name__ == "__main__":
    print(json.dumps(get_location_from_ip(), indent=2))
