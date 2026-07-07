---
name: rating-engineer-skill
description: Step 4/7 of the healthcare appointment pipeline. Re-ranks clinics and doctors using an AI scoring model.
---

# rating_engineer_skill

**Pipeline position:** 4 of 7
**Input:** `clinics` array from `clinic_search_skill`
**Output:** JSON passed to `insurance_check_skill`

## Purpose

Re-rank clinics and doctors using a composite scoring model.

## Scoring factors

- Google rating
- Distance
- Specialty match
- Review sentiment (LLM-based, derived from the doctor summary text)

## Output JSON

```json
{
  "ranked_clinics_doctors": [
    {
      "clinic_name": "",
      "clinic_final_score": "",
      "clinic_reason": "",
      "doctor_name": "",
      "doctor_final_score": "",
      "doctor_reason": ""
    }
  ]
}
```

> Note: the workflow's original example reused the key `final_score`/`reason`
> for both clinic and doctor, which collides in a flat JSON object. This
> implementation disambiguates with `clinic_final_score`/`clinic_reason` and
> `doctor_final_score`/`doctor_reason`, matching the working UI in
> `health_agent_v2_updated.html`.

## Implementation

[`tools/rating_tool.py`](../../tools/rating_tool.py) (`rank_clinics`,
`score_clinic`) — weighted composite of rating, distance, specialty match,
and Gemini-scored sentiment, with a pure-heuristic fallback.

➡️ Pass clinics to `insurance_check_skill`.
