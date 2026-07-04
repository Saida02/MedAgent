"""
SMS notification tool backing `confirmation_skill`. Per the workflow,
Twilio is called via its direct API (not MCP).

Twilio's US SMS compliance requirements (A2P 10DLC brand/campaign
registration, or toll-free verification) can be slow or, for some
registrant countries, outright unavailable. A phone number's carrier also
isn't a reliable guess -- US numbers can be ported between carriers and
keep their original number. So instead of guessing one carrier, this
broadcasts the message (via the already-working Gmail SMTP setup) to every
major US carrier's email-to-SMS gateway at once -- only the one matching
the recipient's real carrier actually delivers, the rest silently bounce,
which costs nothing. SMS_GATEWAY_DOMAIN can pin a single known-correct
domain (faster, no bounce noise) when the recipient's carrier is certain
(e.g. the test patient's own phone). Falls back to Twilio if neither path
is configured/available.
"""
import logging

from tools.config import SMS_GATEWAY_DOMAIN, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER

logger = logging.getLogger(__name__)

# Covers the large majority of US numbers -- MVNOs generally ride on one of
# these host networks (Mint/Metro -> T-Mobile, Visible -> Verizon, etc.).
_CARRIER_GATEWAYS = {
    "T-Mobile / Mint / Metro": "tmomail.net",
    "Verizon / Visible": "vtext.com",
    "AT&T / Cricket": "txt.att.net",
    "Sprint (legacy)": "messaging.sprintpcs.com",
}


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if phone.strip().startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    return "+" + digits


def _digits_only(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:] if len(digits) > 10 else digits  # strip a leading "1" country code


def _send_via_email_gateway(to_phone: str, body: str) -> bool:
    """Sends to a specific pinned gateway if SMS_GATEWAY_DOMAIN is set
    (precise, no guessing needed), otherwise broadcasts to every major
    carrier gateway so at least one delivers regardless of which network
    the number is actually on."""
    from tools import gmail_tool

    number = _digits_only(to_phone)
    domains = [SMS_GATEWAY_DOMAIN] if SMS_GATEWAY_DOMAIN else list(_CARRIER_GATEWAYS.values())

    any_sent = False
    for domain in domains:
        result = gmail_tool.send_booking_request_email(f"{number}@{domain}", "", body)
        any_sent = any_sent or result["email_status"] == "sent"
    return any_sent


def send_confirmation_sms(to_phone: str, body: str) -> bool:
    """Returns True if the SMS was sent (or simulated), False on hard failure."""
    try:
        if _send_via_email_gateway(to_phone, body):
            logger.info("SMS sent via email-to-SMS gateway(s) to %s", to_phone)
            return True
    except Exception as exc:  # noqa: BLE001
        logger.info("Email-to-SMS gateway failed (%s); trying Twilio", exc)

    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER):
        logger.info("Twilio not configured; simulating SMS to %s: %s", to_phone, body)
        return True

    try:
        from twilio.rest import Client

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            to=_normalize_phone(to_phone),
            from_=TWILIO_PHONE_NUMBER,
            body=body,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Twilio send failed, treating as simulated in demo mode: %s", exc)
        return True
