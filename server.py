"""
server.py -- Flask backend for the MedAgent test/demo UI.

Serves static/index.html (an exact copy of health_agent_v2_updated.html)
and implements the API contract that page's JavaScript already expects:

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
from tools.config import FLASK_DEBUG, FLASK_PORT
from tools.gemini_tool import GeminiError

logging.basicConfig(level=logging.INFO)

app = Flask(__name__, static_folder="static", static_url_path="")
agent = HealthcareAppointmentAgent()


@app.errorhandler(GeminiError)
def handle_gemini_error(exc):
    """Steps that used to guess with regex now raise GeminiError instead --
    surface the real failure to the chat as-is rather than a guessed value
    or a generic 500. The session is echoed back unchanged since the step
    that failed never got to mutate it."""
    body = request.get_json(silent=True) or {}
    session = body.get("session") or new_session()
    return jsonify({
        "session": session,
        "status": "gemini_error",
        "message": f"Gemini error: {exc}",
    })


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/chat")
def api_chat():
    body = request.get_json(force=True)
    session = body.get("session") or new_session()
    message = body.get("message", "")

    session, bot_message, status = agent.process_chat_message(session, message)
    return jsonify({"session": session, "message": bot_message, "status": status})


@app.post("/api/select_clinic")
def api_select_clinic():
    body = request.get_json(force=True)
    session = body["session"]
    session = agent.select_clinic(session, body["clinic_name"], body["doctor_name"])
    return jsonify({"session": session, "status": "clinic_selected"})


@app.post("/api/send_email")
def api_send_email():
    body = request.get_json(force=True)
    session = body["session"]
    session, message = agent.booking_email_skill(session)
    any_sent = any(e["email_status"] == "sent" for e in session["emails"])
    status = "email_sent" if any_sent else "email_failed"
    return jsonify({"session": session, "status": status, "message": message})


@app.post("/api/check_reply")
def api_check_reply():
    body = request.get_json(force=True)
    session = body["session"]
    session, proposals = agent.check_reply(session)
    status = "proposals_found" if proposals else "no_reply"
    return jsonify({"session": session, "status": status, "proposals": proposals})


@app.post("/api/confirm_booking")
def api_confirm_booking():
    body = request.get_json(force=True)
    session = body["session"]
    session, message = agent.confirm_booking(
        session, body["clinic_name"], body["doctor_name"], body["selected_time"]
    )
    return jsonify({"session": session, "status": "confirmed", "message": message})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=FLASK_DEBUG)
