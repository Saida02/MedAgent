"""
General follow-up conversation tool. Once the 7-step pipeline has produced
clinic results, the patient may keep chatting (asking about insurance,
clinics, next steps, or anything else) -- these turns should be answered
naturally using the session's context, not by re-running intake/search or
repeating the same "here are your clinics" message every time.
"""
import adk_llm

_FALLBACK_REPLY = (
    "I'm here to help! You can select one or more clinics above using the "
    "Select buttons, or ask me about your symptoms, insurance coverage, or "
    "the clinics found so far."
)


def answer_followup(session: dict, message: str) -> str:
    intake = session.get("intake", {})
    analysis = session.get("analysis", {})
    clinics = session.get("search", {}).get("clinics", [])
    selections = session.get("selected_bookings", [])
    confirmation = session.get("confirmation", {})

    clinic_lines = "\n".join(
        f"- {c.get('name')} (doctor: {c.get('doctor_first_name', '')} {c.get('doctor_last_name', '')}, "
        f"rating {c.get('rating')}, address {c.get('address')})"
        for c in clinics
    ) or "None found yet."

    selected_lines = ", ".join(b["clinic"] for b in selections) or "None selected yet."

    prompt = f"""
You are MedAgent, a friendly AI healthcare appointment assistant currently
chatting with a patient. Here is everything you already know about this
session -- use it to answer naturally, don't dump it all back unless asked:

Patient name: {intake.get('name')}
Symptoms: {intake.get('symptoms')}
Recommended specialty: {analysis.get('specialty')} (urgency: {analysis.get('urgency')})
Insurance provider: {intake.get('insurance')}
Clinics found:
{clinic_lines}
Clinics selected so far: {selected_lines}
Booking status: {confirmation.get('final_status', 'pending')}

The patient just said: "{message}"

Reply conversationally and helpfully in 1-3 sentences, directly addressing
what they asked using the info above. If they want to pick a clinic, remind
them to use the Select buttons above rather than typing a choice. Don't
re-list every clinic unless they specifically ask for the list again.
"""
    return adk_llm.generate_text(prompt, _FALLBACK_REPLY)
