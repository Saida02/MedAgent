# MedAgent — AI Healthcare Appointment Agent

A conversational agent that takes a patient from "I don't feel well" to a
confirmed doctor's appointment: it collects symptoms, figures out which
medical specialty fits, finds and ranks real nearby clinics, checks
insurance coverage, emails the clinics, negotiates a time by reading their
replies, and confirms the booking by email and SMS — all through a single
chat box.

## Problem

Booking a doctor's appointment is disproportionately hard for the people
who need it most: symptoms are confusing, "which specialty do I even need"
is unclear, finding a clinic that takes your insurance means several phone
calls, and coordinating a time with a clinic's back-and-forth emails is
tedious. This agent automates the entire chain from intake to a confirmed
appointment, acting as a personal concierge rather than a search tool.

## Solution: a 7-step agent pipeline

The agent follows a strict, sequential pipeline where each step's output feeds the next:

1. **Intake** — collects name, date of birth, symptoms, insurance, location
   through natural conversation (only step allowed to ask the user
   anything).
2. **Symptom analysis** — infers medical specialty and urgency (LOW/MEDIUM/
   HIGH) from the symptom description.
3. **Clinic search** — finds real nearby clinics via the Google Maps MCP
   server. If location is missing, falls back to IP-based geolocation, then
   a broader/national search — a missing location never stops the
   pipeline.
4. **Rating** — re-ranks clinics/doctors using Google rating, distance,
   specialty match, and an LLM sentiment read of doctor bios. Doctor
   identity itself is found via a **grounded Google Search** call (the
   model actually searches the web instead of guessing a plausible-sounding
   but fake name); if no real doctor is found, that's stated honestly
   instead of inventing one.
5. **Insurance check** — flags each clinic as covered / not covered /
   uncertain for the patient's provider.
6. **Booking email** — once the patient selects one or more clinics (multi-
   select is supported), sends each one a real booking-request email with a
   clinic-tagged subject line so replies can be matched back to the right
   thread.
7. **Confirmation** — polls Gmail for each clinic's reply (manually or
   automatically every 5 minutes), extracts proposed time slots, and once
   the patient accepts one, emails that clinic to confirm and sends the
   patient a final SMS.

## Architecture

```
static/index.html   Chat UI
        │  fetch('/api/chat' | '/api/select_clinic' | '/api/send_email' |
        │        '/api/check_reply' | '/api/confirm_booking')
        ▼
server.py            Flask routes -- stateless: the browser holds the full
        │             session JSON and sends it back on every call
        ▼
agent.py              HealthcareAppointmentAgent -- one method per pipeline
        │             step, each a pure function of (session, input)
        ▼
tools/                One module per external capability:
                       gemini_tool     - Gemini reasoning + Google Search
                                         grounding (raises GeminiError
                                         instead of guessing on failure)
                       maps_tool       - Google Maps MCP -> Places API
                                         fallback -> synthetic placeholder
                       gmail_tool      - Gmail MCP only (send + read replies)
                       sms_tool        - Email-to-SMS carrier gateway
                                         (broadcasts to major US carriers)
                       sheets_tool     - Google Sheets MCP -> local JSON log
                       intake_tool, symptom_analysis_tool, insurance_tool,
                       rating_tool, assistant_chat_tool

config.py / mcp_client.py   Project-root-level shared infrastructure (not
                             capability wrappers themselves): config.py loads
                             .env; mcp_client.py is the generic stdio MCP
                             client every MCP-backed tool above calls into
                             (reuses .agents/mcp_config.json).

skills/                One folder per pipeline step, each with a SKILL.md
                       (purpose, inputs/outputs, when to hand off) and, for
                       clinic_search_skill, scripts/ip_geolocation_tool.py
```

**Design principle — no guessing, no getting stuck.** Every external
dependency (MCP servers, Gemini, SMS delivery) has a graceful fallback so the
pipeline always reaches a terminal state instead of stalling. Where a
result would otherwise have to be *guessed* (e.g. patient intent, a
doctor's identity), the agent either does a real lookup (grounded search)
or honestly reports "not found" / surfaces the real error — it never
fabricates data to keep going.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env            # then fill in your own API keys
cp .agents/mcp_config.json.example .agents/mcp_config.json  # optional, for MCP
python server.py
```

Open http://localhost:5000 in a browser.

### Required keys (`.env`)

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Reasoning + grounded search (free tier at [aistudio.google.com](https://aistudio.google.com/apikey)) |
| `GOOGLE_MAPS_API_KEY` | Clinic search fallback if the Maps MCP server is unavailable |
| `SMS_GATEWAY_DOMAIN` | Final SMS confirmation, sent via a free email-to-SMS carrier gateway (through the Gmail MCP server) |
| `TEST_PATIENT_EMAIL` / `TEST_PATIENT_PHONE` | The demo patient's contact info, for verifying real delivery |

None of these are required individually for the agent to run end-to-end —
each one that's missing/unavailable degrades to a fallback (or, for the
core reasoning steps, a clear error message) rather than crashing.

## MCP usage

Gmail, Google Maps, and Google Sheets are all wired up as **MCP servers**
(`.agents/mcp_config.json.example`), invoked through a generic stdio MCP
client (`mcp_client.py`). Google Maps and Google Sheets transparently
fall back to a direct API call (Places API, a local JSON log) if their MCP
server can't be reached; Gmail is MCP-only -- if the Gmail MCP server is
unreachable, sending/reading fails visibly instead of silently switching
transport.
