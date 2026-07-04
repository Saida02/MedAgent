---
name: confirmation_skill
description: Step 7/7 (FINAL STEP) of the healthcare appointment pipeline. Handles the clinic's email reply and finalizes the appointment via SMS + email.
---

# confirmation_skill

**Pipeline position:** 7 of 7 (FINAL STEP)
**Input:** `email_status`, `clinic_contacted` from `booking_email_skill`
**Output:** final JSON — the only thing the pipeline returns to the caller

## Purpose

Handle the clinic's response and finalize the appointment.

## Actions

1. Listen for an email reply (Gmail MCP).
2. If the clinic replies with proposed date/time options, ask the user to
   confirm one of the eligible slots. Repeat this for every clinic/doctor
   the user chose to contact.
3. Once the user accepts a slot, send a confirmation email to that
   clinic/doctor to complete the booking.
4. Listen again for the clinic's final confirmation reply.
5. If rejected → fall back to the next ranked clinic (re-enter
   `booking_email_skill` for it).

## Output JSON

```json
{
  "final_status": "confirmed | pending | failed",
  "appointment_details": {
    "clinic": "",
    "time": "",
    "doctor": ""
  },
  "sms_sent": true
}
```

## Final rule

At the end of step 7, only this JSON is returned — no intermediate step
data, no extra explanation.

## Implementation

[`tools/gmail_tool.py`](../../tools/gmail_tool.py) (`check_for_reply`) polls
for and parses the clinic's reply into proposed time slots;
[`tools/twilio_tool.py`](../../tools/twilio_tool.py)
(`send_confirmation_sms`) sends the final SMS via Twilio's direct API (not
MCP, per the workflow); [`tools/sheets_tool.py`](../../tools/sheets_tool.py)
logs the outcome.
