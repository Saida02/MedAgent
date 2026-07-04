"""
Central configuration loader. Reads secrets/settings from the project's .env
file (never hardcode credentials in source). Also exposes the path to the
existing MCP server configuration under .agents/mcp_config.json so tools can
reuse the Gmail / Maps / Sheets MCP servers that are already set up.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MCP_CONFIG_PATH = PROJECT_ROOT / ".agents" / "mcp_config.json"

# --- Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# --- Twilio (direct API per workflow spec) ---
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# --- Email-to-SMS carrier gateway (simpler alternative to Twilio A2P/
# toll-free registration -- sends the SMS as an email via the existing
# Gmail SMTP setup to "<phone>@<gateway domain>"). Set to the recipient
# test patient's carrier gateway, e.g. "tmomail.net" for T-Mobile/Mint.
SMS_GATEWAY_DOMAIN = os.getenv("SMS_GATEWAY_DOMAIN", "")

# --- Google Maps (MCP primary, direct Places API fallback) ---
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# --- Google Sheets (MCP primary, service account fallback) ---
GOOGLE_SERVICE_ACCOUNT_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "")
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")

# --- Gmail (MCP primary, SMTP fallback for local dev/demo) ---
GMAIL_SMTP_ADDRESS = os.getenv("GMAIL_SMTP_ADDRESS", "")
GMAIL_SMTP_APP_PASSWORD = os.getenv("GMAIL_SMTP_APP_PASSWORD", "")

# --- IP Geolocation fallback (no key required, ipapi.co free tier) ---
IPGEOLOCATION_API_KEY = os.getenv("IPGEOLOCATION_API_KEY", "")

# --- Test / demo patient contact used by the functional test page ---
# No real contact info defaults here -- set these in your own .env.
TEST_PATIENT_EMAIL = os.getenv("TEST_PATIENT_EMAIL", "")
TEST_PATIENT_PHONE = os.getenv("TEST_PATIENT_PHONE", "")

# When a clinic has no discoverable contact email, booking-request emails are
# routed here instead so the demo pipeline stays end-to-end testable.
FALLBACK_CLINIC_EMAIL = os.getenv("FALLBACK_CLINIC_EMAIL", TEST_PATIENT_EMAIL)

FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
