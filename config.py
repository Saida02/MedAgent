"""
Central configuration loader. Reads secrets/settings from the project's .env
file (never hardcode credentials in source). Also exposes the path to the
existing MCP server configuration under .agents/mcp_config.json so tools can
reuse the Gmail / Maps / Sheets MCP servers that are already set up.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

MCP_CONFIG_PATH = PROJECT_ROOT / ".agents" / "mcp_config.json"

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- SMS: email-to-SMS carrier gateway (sends the confirmation SMS as an
# email, via the Gmail MCP server, to "<phone>@<gateway domain>"). Set to
# the recipient test patient's carrier gateway, e.g. "tmomail.net" for
# T-Mobile/Mint. Left blank, it broadcasts to every major US carrier.
SMS_GATEWAY_DOMAIN = os.getenv("SMS_GATEWAY_DOMAIN", "")

# --- Google Maps (MCP primary, direct Places API fallback) ---
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# --- Google Sheets (accessed via the google-sheets MCP server; that
# server's own SERVICE_ACCOUNT_PATH is configured directly in
# .agents/mcp_config.json, not here) ---
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")

# --- IP Geolocation fallback (no key required, ipapi.co free tier) ---
IPGEOLOCATION_API_KEY = os.getenv("IPGEOLOCATION_API_KEY", "")

# --- Test / demo patient contact used by the functional test page ---
# No real contact info defaults here -- set this in your own .env. The SMS
# confirmation goes to the phone number collected during intake, not a
# configured test number.
TEST_PATIENT_EMAIL = os.getenv("TEST_PATIENT_EMAIL", "")

# When a clinic has no discoverable contact email, booking-request emails are
# routed here instead so the demo pipeline stays end-to-end testable.
FALLBACK_CLINIC_EMAIL = os.getenv("FALLBACK_CLINIC_EMAIL", TEST_PATIENT_EMAIL)

FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
