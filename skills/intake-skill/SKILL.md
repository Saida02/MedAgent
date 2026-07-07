---
name: intake-skill
description: Step 1/7 (entry point) of the healthcare appointment pipeline. Collects patient data via chat before any other step may run.
---

# intake_skill

**Pipeline position:** 1 of 7 (ENTRY POINT)
**Input:** raw chat message(s) from the patient
**Output:** JSON passed to `symptom_analysis_skill`

## Purpose

Collect the patient information needed to route and book an appointment:

- Full name
- Age (or date of birth)
- Gender (optional)
- Location — city/ZIP (optional; if given, later steps use it directly)
- Symptoms (free text)
- Severity self-rating (1–10)
- Insurance provider (if any)
- Preferred time/day (optional)

## Rules

- This is the **only** skill allowed to ask the user clarifying questions.
- Only `name` and `symptoms` are strictly required to proceed — everything
  else is optional and must not block the pipeline.
- Once intake is complete, never re-ask for a field unless a previously
  required field is discovered to be missing.
- Re-run extraction over the full accumulated conversation text on each
  turn (the patient may give info across multiple messages) and merge new
  fields into what's already known — never overwrite a known field with
  null.

## Output JSON

```json
{
  "name": "",
  "age": "",
  "location": null,
  "symptoms": "",
  "severity": "",
  "insurance": "",
  "preferred_time": ""
}
```

## Implementation

Extraction logic lives in [`tools/intake_tool.py`](../../tools/intake_tool.py)
(`extract_intake_fields`, `missing_required_fields`). It tries Gemini first
for robust natural-language extraction and falls back to regex heuristics
if Gemini is unavailable, so intake never stalls the pipeline.

➡️ Pass output to `symptom_analysis_skill`.
