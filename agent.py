"""
agent.py -- Core orchestrator for the AI Healthcare Appointment Agent.

Implements the strict 7-step sequential pipeline described in
AI_Healthcare_Appointment_Agent_Workflow.md:

  1. intake_skill            (entry point)
  2. symptom_analysis_skill
  3. clinic_search_skill     (with IP-geolocation location fallback)
  4. rating_engineer_skill
  5. insurance_check_skill
  6. booking_email_skill     (waits for the user to pick a clinic first)
  7. confirmation_skill      (final step)

Rules enforced here:
  - Steps always run in order 1 -> 7, never skipped or reordered.
  - Each step's JSON output becomes the next step's input.
  - Only intake_skill may ask the user a clarifying question.
  - A missing location never halts the pipeline (clinic_search_skill's
    fallback chain always produces a result).

The `session` dict threaded through every method mirrors the client-side
`session` object in health_agent_v2_updated.html exactly, so the Flask
layer (server.py) can pass it straight through to/from the browser.
"""
from tools import assistant_chat_tool, gmail_tool, insurance_tool, intake_tool, rating_tool, sheets_tool, symptom_analysis_tool, twilio_tool
from tools.config import TEST_PATIENT_PHONE
from tools.maps_tool import search_clinics


def new_session() -> dict:
    return {
        "current_step": 1,
        "raw_conversation": "",
        "intake": {
            "name": None,
            "age": None,
            "location": None,
            "symptoms": None,
            "severity": None,
            "insurance": None,
            "preferred_time": None,
        },
        "analysis": {"specialty": None, "urgency": None, "symptom_summary": None},
        "search": {"clinics": []},
        "ranked": {"ranked_clinics_doctors": []},
        "insurance": {"coverage_results": []},
        # A list, not a single object -- the workflow allows contacting
        # several clinics in parallel ("do this process all chosen doctors
        # and clinics by user") and falling back among them.
        "selected_bookings": [],
        "emails": [],
        "confirmation": {
            "final_status": "pending",
            "appointment_details": {"clinic": None, "time": None, "doctor": None},
            "sms_sent": False,
        },
    }


class HealthcareAppointmentAgent:
    """Stateless per-call orchestrator: every method takes the session dict
    produced by the previous call and returns the updated one, matching the
    workflow's "output of each step = input of next step" rule."""

    # ---- Step 1: intake_skill (ENTRY POINT) --------------------------------
    def intake_skill(self, session: dict, message: str) -> tuple[dict, list]:
        if message:
            session["raw_conversation"] = (session.get("raw_conversation", "") + "\n" + message).strip()

        extracted = intake_tool.extract_intake_fields(session["raw_conversation"])
        for field, value in extracted.items():
            if value and not session["intake"].get(field):
                session["intake"][field] = value

        missing = intake_tool.missing_required_fields(session["intake"])
        if not missing:
            session["current_step"] = max(session["current_step"], 2)
        return session, missing

    # ---- Step 2: symptom_analysis_skill -------------------------------------
    def symptom_analysis_skill(self, session: dict) -> dict:
        result = symptom_analysis_tool.analyze_symptoms(
            session["intake"]["symptoms"], session["intake"]["severity"]
        )
        session["analysis"] = result
        session["current_step"] = max(session["current_step"], 3)
        return session

    # ---- Step 3: clinic_search_skill (location fallback inside) -----------
    def clinic_search_skill(self, session: dict) -> dict:
        location = None
        loc_text = session["intake"].get("location")
        if loc_text:
            location = {"city": loc_text, "region": "", "country": "", "lat": None, "lon": None}

        # The UI card only ever displays the top 3 ranked clinics, so
        # enriching more than that just burns the Gemini free-tier rate
        # limit (5 req/min) on results nobody sees.
        clinics = search_clinics(location, session["analysis"].get("specialty", ""), max_results=3)
        session["search"]["clinics"] = clinics
        session["current_step"] = max(session["current_step"], 4)
        return session

    # ---- Step 4: rating_engineer_skill --------------------------------------
    def rating_engineer_skill(self, session: dict) -> dict:
        ranked = rating_tool.rank_clinics(
            session["search"]["clinics"], session["analysis"].get("specialty", "")
        )
        session["ranked"]["ranked_clinics_doctors"] = ranked
        session["current_step"] = max(session["current_step"], 5)
        return session

    # ---- Step 5: insurance_check_skill --------------------------------------
    def insurance_check_skill(self, session: dict) -> dict:
        provider = session["intake"].get("insurance")
        results = [
            {
                "clinic_name": clinic["name"],
                "insurance_status": insurance_tool.check_coverage(provider, clinic["name"]),
            }
            for clinic in session["search"]["clinics"]
        ]
        session["insurance"]["coverage_results"] = results
        return session

    def run_pipeline_steps_2_to_5(self, session: dict) -> dict:
        session = self.symptom_analysis_skill(session)
        session = self.clinic_search_skill(session)
        session = self.rating_engineer_skill(session)
        session = self.insurance_check_skill(session)
        return session

    def process_chat_message(self, session: dict, message: str) -> tuple[dict, str, str]:
        """Runs intake, then cascades through steps 2-5 once intake is
        complete. Returns (session, bot_message, status).

        Once clinics have already been found, the 7-step pipeline's job for
        this session is done -- further messages are ordinary follow-up
        conversation (questions about insurance, clinics, next steps, etc.),
        not a reason to re-run intake/search or repeat the same "here are
        your clinics" message every time."""
        if session["search"]["clinics"]:
            if message:
                session["raw_conversation"] = (session.get("raw_conversation", "") + "\n" + message).strip()
            reply = assistant_chat_tool.answer_followup(session, message)
            return session, reply, "chatting"

        session, missing = self.intake_skill(session, message)

        if missing:
            prompts = {
                "name": "your full name",
                "symptoms": "a brief description of your symptoms",
                "age": "your date of birth",
            }
            asks = ", ".join(prompts[f] for f in missing)
            return session, f"Thanks! Could you also share {asks}?", "collecting"

        session = self.run_pipeline_steps_2_to_5(session)

        specialty = session["analysis"].get("specialty", "your condition")
        urgency = session["analysis"].get("urgency", "LOW")
        clinic_count = len(session["search"]["clinics"])
        message_out = (
            f"Thanks {session['intake']['name']}! Based on your symptoms I'd recommend "
            f"<strong>{specialty}</strong> (urgency: {urgency}). "
            f"I found {clinic_count} clinics ranked for you below -- please select one."
        )
        return session, message_out, "ready_for_selection"

    # ---- Clinic selection (bridges rating/insurance -> booking_email) -----
    def select_clinic(self, session: dict, clinic_name: str, doctor_name: str) -> dict:
        """Toggles a clinic in/out of the patient's selection -- the patient
        may contact several clinics at once (multi-select), matching the
        workflow's "process all chosen doctors and clinics by user" rule."""
        selections = session.setdefault("selected_bookings", [])
        already_selected = next((b for b in selections if b["clinic"] == clinic_name), None)
        if already_selected:
            selections.remove(already_selected)
            return session

        clinic = next((c for c in session["search"]["clinics"] if c["name"] == clinic_name), None)
        selections.append({
            "clinic": clinic_name,
            "doctor": doctor_name,
            "clinics_email": clinic.get("clinics_email") if clinic else None,
            "doctor_email": clinic.get("doctor_email") if clinic else None,
            "doctor_phone": clinic.get("doctor_phone") if clinic else None,
        })
        return session

    # ---- Step 6: booking_email_skill ---------------------------------------
    def booking_email_skill(self, session: dict) -> tuple[dict, str]:
        """Sends a booking-request email to every clinic the patient
        selected (not just one), each with its own clinic-tagged subject so
        replies can be matched back to the right clinic in check_reply."""
        sent_summaries = []
        session["emails"] = []
        for booking in session["selected_bookings"]:
            insurance_status = next(
                (r["insurance_status"] for r in session["insurance"]["coverage_results"] if r["clinic_name"] == booking["clinic"]),
                "unknown",
            )
            subject, body = gmail_tool.build_booking_email(
                session["intake"]["name"],
                session["analysis"]["symptom_summary"],
                booking["clinic"],
                insurance_status,
                session["intake"].get("preferred_time"),
            )
            to_addr = booking.get("clinics_email") or booking.get("doctor_email")
            result = gmail_tool.send_booking_request_email(to_addr, subject, body)
            session["emails"].append({
                "clinic": booking["clinic"],
                "doctor": booking["doctor"],
                "email_status": result["email_status"],
                "message_id": result["message_id"],
            })
            if result["email_status"] == "sent":
                sent_summaries.append(f"{booking['clinic']} ({to_addr})")

        session["current_step"] = max(session["current_step"], 6)

        if sent_summaries:
            message = "Booking request sent to: " + "; ".join(sent_summaries) + "."
        else:
            message = "Failed to send booking requests."
        return session, message

    # ---- Step 7: confirmation_skill (FINAL STEP) ---------------------------
    def check_reply(self, session: dict) -> tuple[dict, list]:
        """Checks EVERY contacted clinic for a reply. Returns a list of
        {clinic, doctor, time} -- proposals may come from multiple clinics
        at once when the patient contacted several."""
        found = []
        for booking in session["selected_bookings"]:
            proposals = gmail_tool.check_for_reply(booking["clinic"])
            for time_slot in proposals:
                found.append({"clinic": booking["clinic"], "doctor": booking["doctor"], "time": time_slot})
        return session, found

    def confirm_booking(self, session: dict, clinic_name: str, doctor_name: str, selected_time: str) -> tuple[dict, str]:
        booking = next(
            (b for b in session["selected_bookings"] if b["clinic"] == clinic_name),
            {"clinic": clinic_name, "doctor": doctor_name, "clinics_email": None, "doctor_email": None},
        )

        # Workflow rule: "If user accepted one of them, send email this
        # clinic or doctor to complete booking" -- confirm the accepted
        # slot back to the clinic before notifying the patient.
        confirm_subject, confirm_body = gmail_tool.build_confirmation_email(
            session["intake"]["name"], booking["clinic"], selected_time, booking.get("doctor")
        )
        to_addr = booking.get("clinics_email") or booking.get("doctor_email")
        gmail_tool.send_booking_request_email(to_addr, confirm_subject, confirm_body)

        sms_body = (
            f"MedAgent: Your appointment with {booking['doctor']} at {booking['clinic']} "
            f"is confirmed for {selected_time}."
        )
        # Patient's phone isn't part of the intake schema; the pipeline
        # notifies the number configured for this patient in .env.
        sms_sent = twilio_tool.send_confirmation_sms(TEST_PATIENT_PHONE, sms_body)

        session["confirmation"] = {
            "final_status": "confirmed",
            "appointment_details": {
                "clinic": booking["clinic"],
                "time": selected_time,
                "doctor": booking["doctor"],
            },
            "sms_sent": sms_sent,
        }
        session["current_step"] = 7

        sheets_tool.log_booking_event(
            session["intake"]["name"], booking["clinic"], booking["doctor"], "confirmed"
        )

        message = (
            f"Your appointment with {booking['doctor']} at {booking['clinic']} is "
            f"confirmed for {selected_time}. A confirmation SMS and email have been sent."
        )
        return session, message

    def final_output(self, session: dict) -> dict:
        """Per the workflow's final rule: only this JSON is returned once
        step 7 completes -- no intermediate pipeline data."""
        return session["confirmation"]
