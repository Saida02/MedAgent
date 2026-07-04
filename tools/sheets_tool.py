"""
Booking log tool. The workflow lists Sheets as an MCP-backed integration
used to record pipeline activity (today's booked/pending/emails-sent counts
shown in the UI sidebar). Falls back to a local JSON log file if the Sheets
MCP server can't be reached (no active spreadsheet configured, etc.).
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from tools import mcp_client
from tools.config import GOOGLE_SHEETS_SPREADSHEET_ID

logger = logging.getLogger(__name__)

_LOCAL_LOG = Path(__file__).resolve().parent.parent / "data" / "bookings_log.json"


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


def log_booking_event(patient_name: str, clinic: str, doctor: str, status: str) -> None:
    row = {
        "timestamp": datetime.utcnow().isoformat(),
        "patient": patient_name,
        "clinic": clinic,
        "doctor": doctor,
        "status": status,
    }
    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        _append_local(row)
        return
    try:
        mcp_client.call_tool(
            "google-sheets",
            "append_values",
            {
                "spreadsheet_id": GOOGLE_SHEETS_SPREADSHEET_ID,
                "range": "Bookings!A:E",
                "values": [[row["timestamp"], row["patient"], row["clinic"], row["doctor"], row["status"]]],
            },
        )
    except mcp_client.MCPUnavailableError as exc:
        logger.info("Sheets MCP unavailable (%s); logging locally instead", exc)
        _append_local(row)
