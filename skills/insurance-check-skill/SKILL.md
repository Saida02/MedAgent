---
name: insurance-check-skill
description: Step 5/7 of the healthcare appointment pipeline. Checks insurance compatibility between the patient's provider and each ranked clinic.
---

# insurance_check_skill

**Pipeline position:** 5 of 7
**Input:** `ranked_clinics_doctors` (+ `insurance` from intake)
**Output:** JSON passed to `booking_email_skill`

## Purpose

Match the patient's insurance provider against each clinic's network.

## Logic

- Match insurance provider with clinic network (if available).
- If unknown → mark as `"unknown"` rather than guessing covered/not_covered.

## Output JSON

```json
{
  "coverage_results": [
    {
      "clinic_name": "",
      "insurance_status": "covered | not_covered | unknown"
    }
  ]
}
```

## Implementation

[`tools/insurance_tool.py`](../../tools/insurance_tool.py)
(`check_coverage`) — heuristic broad-network lookup table plus a Gemini
plausibility check for providers/clinics not in the table.

➡️ Pass to `booking_email_skill`.
