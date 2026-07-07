"""
Booking log tool. The workflow lists Sheets as an MCP-backed integration
used to record pipeline activity (today's booked/pending/emails-sent counts
shown in the UI sidebar, plus patient intake and clinic-search data for a
full audit trail). Falls back to a local JSON log file if the Sheets MCP
server can't be reached (no active spreadsheet configured, etc.).
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import mcp_client
from config import GOOGLE_SHEETS_SPREADSHEET_ID

logger = logging.getLogger(__name__)

_LOCAL_LOG = Path(__file__).resolve().parent.parent / "data" / "bookings_log.json"

_SCHEMAS = {
    "Bookings": ["timestamp", "patient", "clinic", "doctor", "status", "confirmed_time", "sms_sent"],
    "Users": ["timestamp", "name", "age", "email", "symptoms", "insurance", "location", "preferred_time"],
    "Clinics": [
        "timestamp", "name", "address", "google_rating", "ai_rating", "ai_summary",
        "doctor", "clinics_email", "doctor_email", "insurance_status", "insurance_detail",
    ],
}


def _sheet_exists(spreadsheet_id: str, sheet_name: str) -> bool:
    raw = mcp_client.call_tool("google-sheets", "list_sheets", {"spreadsheet_id": spreadsheet_id})
    return sheet_name in raw.split("\n")


def _column_letter(index: int) -> str:
    """1-indexed column number -> spreadsheet column letter (1 -> A, 27 -> AA)."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _append_rows_via_mcp(spreadsheet_id: str, sheet_name: str, rows: list[list]) -> None:
    """mcp-google-sheets has no direct "append" tool -- update_cells always
    writes to an exact range, so this reads the current row count first and
    writes the new rows just past it. Creates the tab (with its header row)
    the first time it's ever called for this spreadsheet, and re-writes the
    header row in place if _SCHEMAS was changed since the tab was created
    (existing data rows are left as-is under the new header -- this is a
    demo/audit log, not something that needs a full migration script)."""
    if not rows:
        return
    header = _SCHEMAS[sheet_name]
    last_col = _column_letter(len(header))

    if not _sheet_exists(spreadsheet_id, sheet_name):
        mcp_client.call_tool("google-sheets", "create_sheet", {"spreadsheet_id": spreadsheet_id, "title": sheet_name})
        existing_rows = 0
        header_needs_write = True
    else:
        raw = mcp_client.call_tool(
            "google-sheets", "get_sheet_data", {"spreadsheet_id": spreadsheet_id, "sheet": sheet_name}
        )
        value_ranges = json.loads(raw).get("valueRanges", [])
        values = value_ranges[0].get("values", []) if value_ranges else []
        existing_rows = len(values)
        header_needs_write = not values or values[0] != header

    if header_needs_write:
        mcp_client.call_tool(
            "google-sheets",
            "update_cells",
            {"spreadsheet_id": spreadsheet_id, "sheet": sheet_name, "range": f"A1:{last_col}1", "data": [header]},
        )
        existing_rows = max(existing_rows, 1)

    start_row = existing_rows + 1
    end_row = start_row + len(rows) - 1
    mcp_client.call_tool(
        "google-sheets",
        "update_cells",
        {
            "spreadsheet_id": spreadsheet_id,
            "sheet": sheet_name,
            "range": f"A{start_row}:{last_col}{end_row}",
            "data": rows,
        },
    )


def _append_local(row: dict) -> None:
    _LOCAL_LOG.parent.mkdir(exist_ok=True)
    rows = []
    if _LOCAL_LOG.exists():
        try:
            rows = json.loads(_LOCAL_LOG.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            rows = []
    rows.append(row)
    _LOCAL_LOG.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def log_booking_event(
    patient_name: str, clinic: str, doctor: str, status: str,
    confirmed_time: str = "", sms_sent: bool | str = "",
) -> None:
    """status is "pending" (booking email just sent) or "confirmed" (patient
    accepted a time) -- confirmed_time/sms_sent are only meaningful for the
    latter. Always logged locally too -- even when Sheets is configured --
    so the UI's booked/pending counts can always be computed from one
    reliable source regardless of whether the Sheets MCP call actually
    succeeds."""
    row = {
        "timestamp": datetime.utcnow().isoformat(),
        "patient": patient_name,
        "clinic": clinic,
        "doctor": doctor,
        "status": status,
        "confirmed_time": confirmed_time,
        "sms_sent": sms_sent,
    }
    _append_local(row)
    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        return
    try:
        _append_rows_via_mcp(GOOGLE_SHEETS_SPREADSHEET_ID, "Bookings", [[row[k] for k in _SCHEMAS["Bookings"]]])
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Sheets MCP unavailable (%s); already logged locally", exc)


def log_user_intake(intake: dict) -> None:
    """Called once per patient, right after intake_skill collects all
    required fields -- records who used the agent and what they came in
    with, independent of whether they went on to book anything."""
    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        return
    row = [
        datetime.utcnow().isoformat(),
        intake.get("name") or "",
        intake.get("age") or "",
        intake.get("email") or "",
        intake.get("symptoms") or "",
        intake.get("insurance") or "",
        intake.get("location") or "",
        intake.get("preferred_time") or "",
    ]
    try:
        _append_rows_via_mcp(GOOGLE_SHEETS_SPREADSHEET_ID, "Users", [row])
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Sheets MCP unavailable (%s); skipping Users log", exc)


def log_clinic_data(clinics: list[dict]) -> None:
    """Called once per patient turn, after clinic_search_skill,
    rating_engineer_skill, and insurance_check_skill have all run --
    each clinic dict here is the merge of all three (search result +
    AI ranking + insurance status), so one row captures the full picture
    instead of three separate partial ones."""
    if not GOOGLE_SHEETS_SPREADSHEET_ID or not clinics:
        return
    timestamp = datetime.utcnow().isoformat()
    rows = [
        [
            timestamp,
            clinic.get("name") or "",
            clinic.get("address") or "",
            clinic.get("rating") or "",
            clinic.get("clinic_final_score") or "",
            clinic.get("clinic_reason") or "",
            clinic.get("doctor_name")
            or f"{clinic.get('doctor_first_name', '')} {clinic.get('doctor_last_name', '')}".strip(),
            clinic.get("clinics_email") or "",
            clinic.get("doctor_email") or "",
            clinic.get("insurance_status") or "",
            clinic.get("insurance_detail") or "",
        ]
        for clinic in clinics
    ]
    try:
        _append_rows_via_mcp(GOOGLE_SHEETS_SPREADSHEET_ID, "Clinics", rows)
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Sheets MCP unavailable (%s); skipping Clinics log", exc)


def get_clinic_contact(clinic_name: str) -> dict | None:
    """Looks up `clinic_name`'s current clinics_email/doctor_email straight
    from the Clinics tab, at the moment the patient selects that clinic --
    not from the browser session's copy of the original search result. This
    is what makes a manual correction in the sheet (e.g. swapping in a real
    inbox for testing) actually take effect: whoever edits that row there is
    the one booking_email_skill will send to, not whatever Gemini found
    during the original search. Returns None (never a guess) if Sheets
    isn't configured, the tab doesn't exist yet, or no row matches --
    callers should fall back to the session's own value in that case."""
    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        return None
    try:
        if not _sheet_exists(GOOGLE_SHEETS_SPREADSHEET_ID, "Clinics"):
            return None
        raw = mcp_client.call_tool(
            "google-sheets", "get_sheet_data", {"spreadsheet_id": GOOGLE_SHEETS_SPREADSHEET_ID, "sheet": "Clinics"}
        )
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Sheets MCP unavailable (%s); using session's clinic contact instead", exc)
        return None

    value_ranges = json.loads(raw).get("valueRanges", [])
    rows = value_ranges[0].get("values", []) if value_ranges else []
    header, data_rows = (rows[0], rows[1:]) if rows else ([], [])
    if header != _SCHEMAS["Clinics"]:
        return None  # sheet's been reshaped by hand -- don't misread columns
    name_col = header.index("name")
    email_col = header.index("clinics_email")
    doctor_email_col = header.index("doctor_email")

    # Rows are appended in search order, so the last match is the most
    # recent search result for this clinic (or a manual edit to that row).
    match = None
    for row in data_rows:
        if len(row) > name_col and row[name_col] == clinic_name:
            match = row
    if not match:
        return None

    return {
        "clinics_email": match[email_col] if len(match) > email_col else "",
        "doctor_email": match[doctor_email_col] if len(match) > doctor_email_col else "",
    }


def get_stats() -> dict:
    """Returns {"booked": int, "pending": int} computed from the local log,
    counting each (patient, clinic) pair only once by its most recent
    status -- so a clinic that was "pending" and later got "confirmed"
    counts as booked, not both."""
    if not _LOCAL_LOG.exists():
        return {"booked": 0, "pending": 0}
    try:
        rows = json.loads(_LOCAL_LOG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"booked": 0, "pending": 0}

    latest_status = {}
    for row in rows:
        key = (row.get("patient"), row.get("clinic"))
        # Rows are appended in chronological order, so the last one seen
        # for a given key is the most recent status.
        latest_status[key] = row.get("status")

    booked = sum(1 for status in latest_status.values() if status == "confirmed")
    pending = sum(1 for status in latest_status.values() if status == "pending")
    return {"booked": booked, "pending": pending}
