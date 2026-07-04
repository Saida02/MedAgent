---
name: symptom_analysis_skill
description: Step 2/7 of the healthcare appointment pipeline. Determines medical specialty and urgency from patient symptoms.
---

# symptom_analysis_skill

**Pipeline position:** 2 of 7
**Input:** intake JSON from `intake_skill`
**Output:** JSON merged with intake data, passed to `clinic_search_skill`

## Purpose

Analyze the patient's symptom text and determine:

- **Medical specialty** (e.g. cardiology, dermatology, general medicine).
  If the patient already named a concrete specialty, use it as-is; only
  infer one from symptoms if they didn't.
- **Urgency level:** `LOW` / `MEDIUM` / `HIGH` (HIGH = needs urgent care / ER)

## Output JSON

```json
{
  "specialty": "",
  "urgency": "",
  "symptom_summary": ""
}
```

## Implementation

[`tools/symptom_analysis_tool.py`](../../tools/symptom_analysis_tool.py)
(`analyze_symptoms`) — Gemini-based classification with a keyword-heuristic
fallback (specialty keyword map + severity/keyword-based urgency rules) so
this step always returns a valid, non-empty result.

➡️ Merge with intake data and pass to `clinic_search_skill`.
