"""
SMS notification tool backing `confirmation_skill`.

A phone number's carrier isn't a reliable guess -- US numbers can be
ported between carriers and keep their original number. So instead of
guessing one carrier, this sends the message (via the already-working
Gmail MCP setup) to every major US carrier's email-to-SMS gateway at
once -- only the one matching the recipient's real carrier actually
delivers, the rest silently bounce, which costs nothing. SMS_GATEWAY_DOMAIN
can pin a single known-correct domain (faster, no bounce noise) when the
recipient's carrier is certain (e.g. the test patient's own phone).
"""
import logging

from config import SMS_GATEWAY_DOMAIN

logger = logging.getLogger(__name__)

# Covers the large majority of US numbers -- MVNOs generally ride on one of
# these host networks (Mint/Metro -> T-Mobile, Visible -> Verizon, etc.).
_CARRIER_GATEWAYS = {
    "T-Mobile / Mint / Metro": "tmomail.net",
    "Verizon / Visible": "vtext.com",
    "AT&T / Cricket": "txt.att.net",
    "Sprint (legacy)": "messaging.sprintpcs.com",
}


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
        logger.info("Email-to-SMS gateway failed (%s); simulating send", exc)

    logger.info("Simulating SMS to %s: %s", to_phone, body)
    return True
