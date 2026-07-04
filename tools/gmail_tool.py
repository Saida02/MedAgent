"""
Email tool backing `booking_email_skill` and the email-reply half of
`confirmation_skill`. Per the workflow, Gmail access goes through the
already-configured Gmail MCP server; if that's unreachable (no OAuth session
in this environment) it falls back to SMTP, and finally to a local
simulation so the demo pipeline always completes end-to-end.
"""
import email
import imaplib
import logging
import smtplib
import time
import uuid
from datetime import datetime
from email.mime.text import MIMEText

from tools import gemini_tool, mcp_client
from tools.config import GMAIL_SMTP_ADDRESS, GMAIL_SMTP_APP_PASSWORD

logger = logging.getLogger(__name__)

# In-memory store simulating "sent emails awaiting a reply", used only when
# neither the Gmail MCP nor SMTP is reachable so /api/check_reply has
# something deterministic to poll.
_SIMULATED_OUTBOX: dict[str, dict] = {}


def _send_via_mcp(to: str, subject: str, body: str) -> str:
    import json

    raw = mcp_client.call_tool(
        "gmail-mcp", "send_email", {"to": [to], "subject": subject, "body": body}
    )
    try:
        data = json.loads(raw)
        return data.get("id") or data.get("message_id") or str(uuid.uuid4())
    except Exception:  # noqa: BLE001
        return str(uuid.uuid4())


def _send_via_smtp(to: str, subject: str, body: str) -> str:
    if not (GMAIL_SMTP_ADDRESS and GMAIL_SMTP_APP_PASSWORD):
        raise RuntimeError("SMTP not configured")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_SMTP_ADDRESS
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
        server.login(GMAIL_SMTP_ADDRESS, GMAIL_SMTP_APP_PASSWORD)
        server.sendmail(GMAIL_SMTP_ADDRESS, [to], msg.as_string())
    return str(uuid.uuid4())


def send_booking_request_email(to: str, subject: str, body: str) -> dict:
    """Returns {"email_status": "sent"|"failed", "message_id": str}."""
    try:
        message_id = _send_via_mcp(to, subject, body)
        logger.info("Booking email sent via Gmail MCP to %s", to)
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Gmail MCP unavailable (%s); trying SMTP fallback", exc)
        try:
            message_id = _send_via_smtp(to, subject, body)
            logger.info("Booking email sent via SMTP to %s", to)
        except Exception as smtp_exc:  # noqa: BLE001
            logger.info("SMTP unavailable (%s); simulating send", smtp_exc)
            message_id = f"simulated-{uuid.uuid4()}"
            _SIMULATED_OUTBOX[message_id] = {
                "to": to,
                "subject": subject,
                "body": body,
                "sent_at": time.time(),
            }
    return {"email_status": "sent", "message_id": message_id}


def build_booking_email(patient_name: str, symptoms_summary: str, clinic_name: str,
                          insurance_status: str, preferred_time: str) -> tuple[str, str]:
    # Clinic name is embedded in the subject so that when the patient
    # contacts several clinics at once, each reply thread ("Re: ...") can be
    # attributed back to the specific clinic it came from.
    subject = f"Appointment Request ({clinic_name}) - {patient_name}"
    body = (
        f"Hello,\n\n"
        f"I would like to request an appointment.\n\n"
        f"Patient: {patient_name}\n"
        f"Symptoms summary: {symptoms_summary}\n"
        f"Insurance status: {insurance_status}\n"
        f"Preferred time/day: {preferred_time or 'Any available slot'}\n\n"
        f"Could you please reply with your earliest available appointment "
        f"times so I can confirm one? Thank you.\n\n"
        f"Requested via MedAgent (clinic: {clinic_name})"
    )
    return subject, body


def build_confirmation_email(patient_name: str, clinic_name: str, selected_time: str, doctor_name: str) -> tuple[str, str]:
    """The workflow's confirmation_skill step: once the patient accepts one
    of the clinic's proposed slots, send a reply confirming it so the
    clinic can finalize the booking on their end."""
    subject = f"Re: Appointment Request ({clinic_name}) - {patient_name} - Confirming {selected_time}"
    body = (
        f"Hello,\n\n"
        f"Thank you for the proposed times. {selected_time} works well for me "
        f"-- please confirm this appointment{f' with {doctor_name}' if doctor_name else ''}.\n\n"
        f"Best,\n{patient_name}\n\n"
        f"Confirmed via MedAgent"
    )
    return subject, body


def _extract_plain_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return msg.get_payload(decode=True).decode(charset, errors="replace")


def _search_via_imap(subject_contains: str) -> str:
    """Reads the most recent inbox message whose subject contains
    `subject_contains` (e.g. "Re: Appointment Request..."), using the same
    Gmail app password already configured for SMTP sending. Returns the
    plain-text body, or "" if nothing matches."""
    if not (GMAIL_SMTP_ADDRESS and GMAIL_SMTP_APP_PASSWORD):
        raise RuntimeError("IMAP not configured (no GMAIL_SMTP_* credentials)")

    with imaplib.IMAP4_SSL("imap.gmail.com", timeout=10) as imap:
        imap.login(GMAIL_SMTP_ADDRESS, GMAIL_SMTP_APP_PASSWORD)
        imap.select("INBOX")
        status, data = imap.search(None, f'(SUBJECT "{subject_contains}")')
        if status != "OK" or not data or not data[0]:
            return ""
        latest_uid = data[0].split()[-1]
        status, msg_data = imap.fetch(latest_uid, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            return ""
        msg = email.message_from_bytes(msg_data[0][1])
        return _extract_plain_text(msg)


def check_for_reply(clinic_name: str) -> list:
    """
    Polls for THIS SPECIFIC clinic's reply (matched via the clinic-tagged
    subject line) and, if found, extracts proposed appointment time slots
    via Gemini. Returns a list of human-readable time strings, or an empty
    list if no reply is available yet. Called once per contacted clinic
    when the patient reached out to more than one.
    """
    subject_tag = f"Re: Appointment Request ({clinic_name})"

    try:
        import json

        raw = mcp_client.call_tool(
            "gmail-mcp", "search_emails", {"query": f"subject:{subject_tag}"}
        )
        data = json.loads(raw)
        messages = data.get("messages", [])
        if messages:
            latest_body = messages[0].get("snippet") or messages[0].get("body", "")
            return _extract_time_proposals(latest_body)
        return []
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Gmail MCP unavailable (%s); trying IMAP fallback", exc)

    try:
        body = _search_via_imap(subject_tag)
    except Exception as exc:  # noqa: BLE001 -- IMAP/connection failure only
        logger.info("IMAP polling unavailable (%s); nothing to report", exc)
        return []

    if not body:
        return []
    return _extract_time_proposals(body)  # GeminiError, if any, propagates to the caller


def _normalize_proposal(text: str) -> str:
    """Reformats whatever date/time phrasing Gemini produced (inconsistent
    capitalization, day-before-month, etc.) into one consistent display
    format: "Weekday, Month DD, YYYY at H:MM AM/PM". Falls back to the
    original text untouched if it doesn't parse as a date (fuzzy parsing on
    non-date text is unreliable, so this only replaces the text when
    dateutil is confident enough to succeed)."""
    try:
        from dateutil import parser as date_parser

        # Zero out minute/second in the fallback-default so an omitted
        # minute (e.g. "8 am") becomes ":00", not whatever minute it
        # happens to be right now.
        default = datetime.now().replace(minute=0, second=0, microsecond=0)
        dt = date_parser.parse(text, fuzzy=True, default=default)
        return dt.strftime("%A, %B %d, %Y at %I:%M %p")
    except (ValueError, OverflowError, ImportError):
        return text


def _extract_time_proposals(body: str) -> list:
    prompt = f"""
Extract proposed appointment date/time slots from this clinic reply email.
Ignore any quoted/forwarded text (e.g. lines starting with ">" or after
"On ... wrote:") -- only use the new reply content.
Return strict JSON: {{"proposals": ["<human readable slot>", ...]}}.
Email body:
---
{body}
---
"""
    result = gemini_tool.generate_json(prompt, {"proposals": []}, raise_on_failure=True)
    return [_normalize_proposal(p) for p in result.get("proposals") or []]
