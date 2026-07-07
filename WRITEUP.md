# MedAgent — An AI Concierge That Turns "I Don't Feel Well" Into a Confirmed Appointment

### A 7-skill Google ADK agent that triages symptoms, finds and ranks real clinics, negotiates a time by reading their email replies, and confirms by SMS (sent through an email-to-SMS carrier gateway, no separate SMS API) — built to never guess and never get stuck.

**Track:** Concierge Agents
**Code:** https://github.com/Saida02/MedAgent

---

## The Problem

Booking a doctor's appointment is disproportionately hard for the people who need it most. You don't always know which specialty you need. Finding a clinic that actually takes your insurance means several phone calls. And once you've reached out, coordinating a time with a clinic's back-and-forth replies is genuinely tedious — especially if you're not feeling well in the first place.

This is a personal-concierge problem, not a search problem: the value isn't in showing you a list of clinics, it's in *doing the whole chain* for you — from "my throat hurts" to a confirmed slot on your calendar.

## Why an Agent, Not a Form

A form can collect symptoms. It can't decide that "chest pain" plus "can't breathe" means urgent care instead of a routine GP visit, search the web to find out who actually practices at a specific clinic, read an unstructured email reply and pull a proposed time out of it, or keep going when a step it depends on (a location, a clinic's contact info, an API) is missing. That's reasoning and judgment under uncertainty — the actual job of an agent, not a script.

## Architecture — 7 Skills on Google ADK

MedAgent is built on the **Google Agent Development Kit**: every reasoning step runs as an ADK `LlmAgent` through `InMemoryRunner` (`adk_llm.py`), and each pipeline step's own `SKILL.md` is loaded via ADK's native skill loader (`google.adk.skills.load_skill_from_dir`) and injected as that step's governing instruction — so `SKILL.md` isn't just documentation sitting next to the code, it's what the model actually reasons from.

The pipeline is a strict, sequential run of 7 skills, where each skill's structured output becomes the next skill's input:

1. **Intake skill** — collects name, date of birth, email, phone, symptoms, insurance, and location through free-form chat. Name, symptoms, age, email, and phone are required; the rest are optional. This is the only skill allowed to ask the user anything.
2. **Symptom analysis skill** — infers the medical specialty and urgency (LOW/MEDIUM/HIGH) from the symptom description.
3. **Clinic search skill** — finds real nearby clinics via the **Google Maps MCP server**. If location is missing, it falls back to IP-based geolocation and then a broader search — a missing location never halts the pipeline.
4. **Rating skill** — re-ranks clinics using Google rating, distance, specialty match, and an LLM sentiment read of doctor bios. Doctor identity is resolved with a **grounded Google Search** call — the model does a real web search instead of inventing a plausible-sounding name. If no real doctor is found for a clinic, the agent says so honestly rather than fabricating one.
5. **Insurance check skill** — flags each clinic as covered / not covered / uncertain for the patient's provider.
6. **Booking email skill** — once the patient selects one or more clinics (multi-select is supported, so several clinics can be contacted in parallel), sends each one a real email through the **Gmail MCP server**, with a clinic-tagged subject line so replies can be matched back to the right thread later.
7. **Confirmation skill** — polls the **Gmail MCP server** for each clinic's reply, extracts any proposed time slot. If the clinic asks a question instead, it is always routed to the patient to answer — the agent never emails a clinic on the patient's behalf. Once the patient accepts a time, it emails that clinic to confirm and sends a final SMS to the patient's own phone number — not through a carrier SMS API, but through the same Gmail MCP connection, addressed to `<number>@<carrier-gateway-domain>` (e.g. `tmomail.net` for T-Mobile), which the carrier delivers as a text message.

```
static/index.html  (chat UI)
      │ fetch('/api/chat' | 'select_clinic' | 'send_email' | 'check_reply' | 'confirm_booking')
      ▼
server.py           Flask routes -- stateless; the browser holds the full
      │              session JSON and resends it every call
      ▼
agent.py             One method per pipeline step, each a pure function
      │              of (session, input) -> session
      ▼
adk_llm.py           Every LLM call runs as a Google ADK LlmAgent via
      │              InMemoryRunner, with that step's SKILL.md loaded as
      │              its instruction -- not a raw Gemini SDK call
      ▼
tools/               maps_tool · gmail_tool · sms_tool · sheets_tool ·
                     intake_tool · symptom_analysis_tool · insurance_tool ·
                     rating_tool · assistant_chat_tool
config.py / mcp_client.py   Shared infrastructure (env config, generic
                             stdio MCP client) used by the tools above
skills/              One SKILL.md per pipeline step, loaded natively by
                     ADK's own skill loader (kebab-case names, folder
                     name must match frontmatter name exactly)
```

## Key Concepts Demonstrated

**Google ADK.** Reasoning isn't a bare `google-genai` call anywhere in this codebase — every step runs through an ADK `LlmAgent`, and that step's `SKILL.md` is loaded via ADK's own skill loader and prepended as the agent's instruction, so the documented rules genuinely govern the model's behavior at runtime.

**MCP Servers.** Gmail, Google Maps, and Google Sheets are wired up as real MCP servers, invoked through a generic stdio MCP client (`mcp_client.py`) built on the official `mcp` Python SDK. Maps and Sheets degrade gracefully to a direct API call if their MCP server is unreachable; Gmail is MCP-only, so a send/read failure surfaces visibly instead of silently switching transport.

**Google Sheets as a live record, not just a log.** The Sheets integration keeps three tabs — Users (every intake), Clinics (search results + AI rating + insurance status), and Bookings (status, confirmed time, SMS sent) — always mirrored to a local JSON file too, so nothing is lost if Sheets isn't configured. The Clinics tab is live: correcting a clinic's email there takes effect on the very next email MedAgent sends for that clinic, not just at the moment it was first found.

**Security.** No credentials are hardcoded anywhere in source; everything sensitive lives in `.env` (gitignored), with a checked-in `.env.example` documenting exactly what's needed. `.agents/mcp_config.json` (which holds a real API key locally) is also gitignored, with a placeholder `.example` version tracked instead. Emails to clinics with no discoverable real contact address are routed to a fixed test address rather than a guessed one — deliberately avoiding sending unsolicited automated messages to real businesses during testing.

## The Build: What Actually Happened

The most instructive part of building this wasn't the happy path — it was making the agent survive real infrastructure the way a production system has to.

- **ADK skill loading has its own naming contract.** `google.adk.skills.load_skill_from_dir` requires the frontmatter `name:` to be lowercase kebab-case *and* to exactly match its containing folder name. The pipeline's skills were originally named with underscores (`intake_skill`); both the frontmatter and every folder had to be renamed to kebab-case (`intake-skill`) before ADK would load a single one of them.
- **A clinic replying to itself is a real failure mode, not an edge case.** With a test/fallback contact address, the patient's own inbox often *is* the clinic's inbox. Gmail's `subject:` search also silently ignores the `Re:`/`Fwd:` prefix it's threading on, so an unquoted, unscoped subject query matched the agent's *own just-sent* booking request and misread it as the clinic's reply — auto-answering a question nobody asked, from an email nobody sent. The fix was two-layered: quote the subject as one literal phrase, and explicitly exclude the outgoing message's own ID from the reply search.
- **"Guess vs. fail loudly" extends to auto-replying, not just extraction.** An earlier version let the agent auto-answer a clinic's question if it already knew the answer from intake, without asking the patient first. Combined with the self-reply bug above, that meant the agent could email a clinic on the patient's behalf based on a reply that was never real. The fix: every clinic question, known-answer or not, is now routed to the patient — the agent never decides or replies for them.
- **Flask's debug mode quietly swallows your own error handling.** A custom `@app.errorhandler` for surfacing real Gemini/ADK errors to the chat was correctly written from day one, but never fired: Flask implicitly sets `PROPAGATE_EXCEPTIONS=True` whenever `debug=True`, which bypasses registered handlers in favor of the interactive HTML debugger page. The browser's `fetch` then failed to parse that as JSON and fell back to a generic "Sorry, I encountered an error" — hiding the real, useful error message behind a wall of silence. Fixed by explicitly forcing the config flag off.
- **A OneDrive-synced project folder and a file-watching auto-reloader don't mix.** Running the dev server from inside a OneDrive-synced directory meant background sync periodically touched file mtimes — sometimes deep inside `.venv/site-packages` — with no real content change. Werkzeug's reloader treated every touch as a code change and restarted the process mid-request, killing whatever pipeline call was in flight. Narrowing the watched paths helped but didn't fully solve it; the reliable fix was disabling the auto-reloader outright and restarting manually after edits.
- **Gemini quota is a moving target.** Free-tier limits are per-model and both per-minute and per-day. A batch of doctor-lookup calls (one Gemini call *per clinic*) burned through the per-minute limit before finishing early on; batching all clinics into a single grounded call cut the request count roughly 3x.
- **SMS delivery in the US is a compliance maze.** Commercial SMS APIs generally require carrier-level business registration before they'll deliver free-form text. The fallback: an email-to-SMS carrier gateway (`<number>@tmomail.net`, `@vtext.com`, etc.) through the same Gmail MCP setup, broadcasting to all major carriers when the recipient's carrier isn't known, since US numbers can be ported between carriers and can't be reliably guessed from the number alone.

## Demo

A full walkthrough — symptom intake, clinic ranking with a real grounded doctor search, multi-clinic booking emails, reading a real reply, and final SMS confirmation — is in the attached video.

## What's Next

- Sequential fallback when a clinic explicitly declines a proposed time ("If rejected → fallback to next clinic," per the original spec), rather than only handling acceptance.
- A real carrier-lookup step (rather than a multi-carrier broadcast) for SMS to arbitrary numbers.
- Moving the Flask session store server-side (it currently round-trips through the browser on every request), now that the pipeline's state shape has stabilized.
