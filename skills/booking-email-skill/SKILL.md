---
name: booking-email-skill
description: Step 6/7 of the healthcare appointment pipeline. Sends an appointment request email to the patient's selected clinic via Gmail MCP.
---

# booking_email_skill

**Pipeline position:** 6 of 7
**Input:** the clinic/doctor the user selected (from `clinic_search_skill`'s
contact info), plus intake + insurance results
**Output:** JSON passed to `confirmation_skill`

## Purpose

Generate and send an appointment request email via the Gmail MCP server.

## Rules

- **First step is waiting for the user's answer.** This skill does not run
  until the user has chosen a clinic/doctor from the ranked list.
- Use the exact clinic/doctor contact information produced by
  `clinic_search_skill` — do not re-derive it.

## Email must include

- Patient info
- Symptoms summary
- Selected clinic
- Insurance status

## Output JSON

```json
{
  "email_status": "sent | failed",
  "message_id": "",
  "clinic_contacted": ""
}
```

## Implementation

[`tools/gmail_tool.py`](../../tools/gmail_tool.py)
(`build_booking_email`, `send_booking_request_email`) — sends via the Gmail
MCP server first, falls back to SMTP, and finally to a local simulated
outbox so the pipeline always reaches a terminal `sent`/`failed` state.

➡️ Pass to `confirmation_skill`.
