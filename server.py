"""
server.py -- Flask backend for the MedAgent test/demo UI (Google ADK build).

Serves static/index.html and implements the same API contract as the
original project's server.py -- the frontend is unmodified, since agent.py's
session shape is identical regardless of which reasoning layer backs it:

  POST /api/chat            -> intake_skill, then steps 2-5 once complete
  POST /api/select_clinic   -> records the user's clinic/doctor choice
  POST /api/send_email      -> booking_email_skill (step 6)
  POST /api/check_reply     -> confirmation_skill: poll for clinic reply
  POST /api/confirm_booking -> confirmation_skill: finalize (step 7)

The frontend is stateless-server-side by design: it keeps the full
`session` object in the browser and sends it back on every request, so each
endpoint just resumes the pipeline from wherever `session` says it left off
-- this keeps agent.py's step methods pure functions of (session, input).
"""
import logging

from flask import Flask, jsonify, request, send_from_directory

from agent import HealthcareAppointmentAgent, new_session
from config import FLASK_DEBUG, FLASK_PORT
from adk_llm import GeminiError

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, static_folder="static", static_url_path="")
# Flask implicitly sets PROPAGATE_EXCEPTIONS=True whenever DEBUG is on, which
# bypasses registered error handlers (like handle_gemini_error below) in
# favor of the interactive HTML debugger page -- the frontend's fetch then
# fails to parse that as JSON and falls back to a generic "Sorry, I
# encountered an error" instead of the real message. Force it off so
# GeminiError still gets the proper JSON response even with FLASK_DEBUG=true.
app.config["PROPAGATE_EXCEPTIONS"] = False
agent = HealthcareAppointmentAgent()


def _hydrate_session(raw: dict | None) -> dict:
    """Fills in any keys missing from a browser-sent session with
    new_session()'s defaults (one level deep into nested dicts too).
    Without this, a browser tab that's been open since before a new session
    field was added would send a session missing that key and crash every
    endpoint with a KeyError."""
    defaults = new_session()
    if not raw:
        return defaults
    merged = {**defaults, **raw}
    for key, default_value in defaults.items():
        if isinstance(default_value, dict) and isinstance(raw.get(key), dict):
            merged[key] = {**default_value, **raw[key]}
    return merged


@app.errorhandler(GeminiError)
def handle_gemini_error(exc):
    """Steps that used to guess with regex now raise GeminiError instead --
    surface the real failure to the chat as-is rather than a guessed value
    or a generic 500. The session is echoed back unchanged since the step
    that failed never got to mutate it."""
    body = request.get_json(silent=True) or {}
    session = _hydrate_session(body.get("session"))
    return jsonify({
        "session": session,
        "status": "gemini_error",
        "message": f"ADK agent error: {exc}",
    })


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/stats")
def api_stats():
    from tools import sheets_tool

    return jsonify(sheets_tool.get_stats())


@app.post("/api/chat")
def api_chat():
    body = request.get_json(force=True)
    session = _hydrate_session(body.get("session"))
    message = body.get("message", "")

    session, bot_message, status = agent.process_chat_message(session, message)
    return jsonify({"session": session, "message": bot_message, "status": status})


@app.post("/api/select_clinic")
def api_select_clinic():
    body = request.get_json(force=True)
    session = _hydrate_session(body.get("session"))
    session = agent.select_clinic(session, body["clinic_name"], body["doctor_name"])
    return jsonify({"session": session, "status": "clinic_selected"})


@app.post("/api/send_email")
def api_send_email():
    body = request.get_json(force=True)
    session = _hydrate_session(body.get("session"))
    session, message = agent.booking_email_skill(session)
    any_sent = any(e["email_status"] == "sent" for e in session["emails"])
    status = "email_sent" if any_sent else "email_failed"
    return jsonify({"session": session, "status": status, "message": message})


@app.post("/api/check_reply")
def api_check_reply():
    body = request.get_json(force=True)
    session = _hydrate_session(body.get("session"))
    session, proposals, notes = agent.check_reply(session)
    if proposals:
        status = "proposals_found"
    elif session["pending_clinic_questions"]:
        status = "clinic_question"
    elif notes:
        status = "clinic_auto_answered"
    else:
        status = "no_reply"
    return jsonify({"session": session, "status": status, "proposals": proposals, "notes": notes})


@app.post("/api/confirm_booking")
def api_confirm_booking():
    body = request.get_json(force=True)
    session = _hydrate_session(body.get("session"))
    session, message = agent.confirm_booking(
        session, body["clinic_name"], body["doctor_name"], body["selected_time"]
    )
    return jsonify({"session": session, "status": "confirmed", "message": message})


if __name__ == "__main__":
    # The reloader is off, not just narrowed with exclude_patterns: this
    # project lives inside OneDrive, which touches file mtimes during
    # background sync (source files, not just __pycache__/.venv) with no
    # real content change, so the reloader's file watcher kept restarting
    # mid-request and killing whatever pipeline call was in flight (surfaces
    # to the browser as a generic fetch failure). debug=True is kept for
    # error pages/PROPAGATE_EXCEPTIONS behavior; only the auto-restart is
    # disabled. Restart the process manually after editing server code.
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=FLASK_DEBUG, use_reloader=False)
