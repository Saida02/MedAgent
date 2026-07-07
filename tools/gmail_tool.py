"""
Email tool backing `booking_email_skill` and the email-reply half of
`confirmation_skill`. Goes through the Gmail MCP server (@shinzolabs/gmail-mcp)
only -- no SMTP/IMAP or local-simulation fallback. If the MCP server is
unreachable, sending/reading fails visibly (email_status="failed" / not
found) rather than silently substituting a different transport.
"""
import logging
import uuid
from datetime import datetime

import mcp_client
import adk_llm

logger = logging.getLogger(__name__)


def _send_via_mcp(to: str, subject: str, body: str, cc: str | None = None) -> str:
    import json

    payload = {"to": [to], "subject": subject, "body": body}
    if cc:
        payload["cc"] = [cc]
    raw = mcp_client.call_tool("gmail-mcp", "send_message", payload)
    try:
        data = json.loads(raw)
        return data.get("id") or data.get("message_id") or str(uuid.uuid4())
    except Exception:  # noqa: BLE001
        return str(uuid.uuid4())


def send_booking_request_email(to: str, subject: str, body: str, cc: str | None = None) -> dict:
    """Returns {"email_status": "sent"|"failed", "message_id": str|None}.
    `cc` is the patient's own email (a required intake field) so they
    receive a copy of everything sent on their behalf."""
    try:
        message_id = _send_via_mcp(to, subject, body, cc)
        logger.info("Booking email sent via Gmail MCP to %s", to)
        return {"email_status": "sent", "message_id": message_id}
    except mcp_client.MCPUnavailableError as exc:
        logger.warning("Gmail MCP unavailable (%s); email not sent", exc)
        return {"email_status": "failed", "message_id": None}


def build_booking_email(patient_name: str, date_of_birth: str, symptoms_summary: str, clinic_name: str,
                          insurance_provider: str | None, preferred_time: str, doctor_name: str | None = None) -> tuple[str, str]:
    # Clinic name is embedded in the subject so that when the patient
    # contacts several clinics at once, each reply thread ("Re: ...") can be
    # attributed back to the specific clinic it came from.
    subject = f"Appointment Request ({clinic_name}) - {patient_name}"
    # "No specific doctor found" is maps_tool's honest not-found signal, not
    # a real preference -- only mention a doctor if one was actually found.
    doctor_line = f"Preferred doctor: {doctor_name}\n" if doctor_name and doctor_name != "No specific doctor found" else ""
    # Only mention insurance if the patient actually gave a provider --
    # don't state a coverage guess/status the clinic didn't ask for.
    insurance_line = f"Insurance: {insurance_provider}\n" if insurance_provider else ""
    body = (
        f"Hello,\n\n"
        f"I would like to request an appointment.\n\n"
        f"Patient: {patient_name}\n"
        f"Date of birth: {date_of_birth or 'Not provided'}\n"
        f"Symptoms summary: {symptoms_summary}\n"
        f"{insurance_line}"
        f"{doctor_line}"
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


def build_followup_answer_email(patient_name: str, clinic_name: str, clinic_question: str, answer: str) -> tuple[str, str]:
    """Sent when the clinic's reply asked a question instead of proposing a
    time -- answers that specific question and reiterates the original
    appointment request so the clinic can move forward."""
    subject = f"Re: Appointment Request ({clinic_name}) - {patient_name}"
    body = (
        f"Hello,\n\n"
        f"Thank you for your reply. To answer your question ({clinic_question}):\n"
        f"{answer}\n\n"
        f"I'd still like to request an appointment -- could you let me know "
        f"your earliest available times? Thank you.\n\n"
        f"Best,\n{patient_name}\n\n"
        f"Requested via MedAgent (clinic: {clinic_name})"
    )
    return subject, body


def _extract_mcp_message_body(message: dict) -> str:
    """gmail-mcp's get_message returns the Gmail API message resource:
    a simple message has payload.body.data directly, a multipart one
    (e.g. text/plain + text/html) has payload.parts instead -- pick the
    text/plain part."""
    payload = message.get("payload") or {}
    parts = payload.get("parts")
    if not parts:
        return (payload.get("body") or {}).get("data", "")
    for part in parts:
        if part.get("mimeType") == "text/plain":
            return (part.get("body") or {}).get("data", "")
    return ""


_NOT_FOUND = {"found": False, "proposals": [], "clinic_question": None, "known_answer": None}


def check_for_reply(clinic_name: str, known_info: dict | None = None, exclude_message_id: str | None = None) -> dict:
    """
    Polls for THIS SPECIFIC clinic's reply (matched via the clinic-tagged
    subject line). Returns {"found", "proposals", "clinic_question",
    "known_answer"} (see _extract_reply_info). "found" is False when there's
    genuinely no reply yet, or the Gmail MCP server is unreachable -- a
    reply that doesn't contain a proposed time (e.g. the clinic asked a
    question instead) is still "found", so the patient sees the real
    situation instead of a misleading "no reply found" message. Called once
    per contacted clinic when the patient reached out to more than one.

    exclude_message_id is the id of the booking request WE sent -- Gmail's
    "Re:"-prefixed subject search still matches the original message too
    (Gmail's subject index ignores the Re:/Fwd: prefix for threading), so
    with a test/fallback address where the clinic and patient share one
    inbox, the agent's own just-sent request would otherwise come back
    looking like the clinic already replied to it.
    """
    subject_tag = f"Re: Appointment Request ({clinic_name})"

    try:
        import json

        raw = mcp_client.call_tool(
            # Quoted so Gmail's search matches the tag as one literal phrase --
            # unquoted, Gmail treats "(" / ")" as its own OR-grouping syntax and
            # only binds subject: to the single token right after it, so the
            # rest of the tag would silently search the whole message instead
            # of the subject line. maxResults > 1 so there's something left
            # to fall back to once the original message is filtered out below.
            "gmail-mcp", "list_messages", {"q": f'subject:"{subject_tag}"', "maxResults": 5}
        )
        messages = json.loads(raw).get("messages", [])
        messages = [m for m in messages if m["id"] != exclude_message_id]
        if not messages:
            return _NOT_FOUND
        raw_message = mcp_client.call_tool("gmail-mcp", "get_message", {"id": messages[0]["id"]})
        body = _extract_mcp_message_body(json.loads(raw_message))
        if not body:
            return _NOT_FOUND
        return _extract_reply_info(body, known_info)  # GeminiError, if any, propagates to the caller
    except mcp_client.MCPUnavailableError as exc:
        logger.warning("Gmail MCP unavailable (%s); cannot check for replies", exc)
        return _NOT_FOUND


def _normalize_proposal(text: str) -> str:
    """Reformats whatever date/time phrasing the agent produced (inconsistent
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


def _extract_reply_info(body: str, known_info: dict | None = None) -> dict:
    """Reads a clinic's reply and figures out, in one ADK agent call:
    - any proposed appointment time slots
    - if none were proposed, what the clinic is actually asking for
    - whether that question is already answerable from the patient's known
      info (in which case the agent can reply immediately without bothering
      the patient) or genuinely needs to be asked
    Returns {"found": True, "proposals": [...], "clinic_question": str|None,
    "known_answer": str|None}. Never invents a "known_answer" -- only fills
    it when the known_info actually contains that information.
    """
    known_info = known_info or {}
    known_info_text = "\n".join(f"- {k}: {v}" for k, v in known_info.items() if v) or "(none provided)"
    prompt = f"""
This is a clinic's reply to an appointment request email. Ignore any
quoted/forwarded text (e.g. lines starting with ">" or after "On ... wrote:")
-- only use the new reply content.

What the agent already knows about the patient:
{known_info_text}

1. Extract any proposed appointment date/time slots.
2. If NO times were proposed, identify the single specific question or
   piece of information the clinic is asking for (e.g. "insurance member ID",
   "reason for visit", "preferred callback time"). If they proposed times,
   this should be null.
3. If a question was identified AND the "what the agent already knows"
   section above genuinely already answers it, provide that exact answer.
   Otherwise (including if you're not confident), this must be null -- do
   not guess or invent an answer the patient didn't actually give.

Return strict JSON: {{"proposals": ["<human readable slot>", ...],
"clinic_question": "<question>" or null, "known_answer": "<answer>" or null}}
Email body:
---
{body}
---
"""
    default = {"proposals": [], "clinic_question": None, "known_answer": None}
    result = adk_llm.generate_json(prompt, default, raise_on_failure=True, skill="confirmation-skill")
    proposals = [_normalize_proposal(p) for p in result.get("proposals") or []]
    return {
        "found": True,
        "proposals": proposals,
        "clinic_question": result.get("clinic_question"),
        "known_answer": result.get("known_answer"),
    }
