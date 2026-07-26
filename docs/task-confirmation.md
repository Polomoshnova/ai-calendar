# Task Confirmation Layer

The confirmation layer is a stateless application boundary between AI
interpretation and future planning:

```text
User text → TaskDraftV2 → DraftReview → ConfirmedTask → future Task/Scheduler
                                      ↘ ConfirmationAudit
```

- `TaskDraftV2` is an AI interpretation object. It preserves source, confidence,
  explanation, and confirmation requirements.
- `DraftReview` is the user's decision object. It contains decisions and edited
  values, but no AI metadata.
- `ConfirmedTask` is a clean, user-approved planning input. It deliberately has
  no source, confidence, prompt version, clarification questions, or provider
  information.
- `ConfirmationAudit` is an in-memory record suitable for future analytics and
  learning. It is not persisted in this milestone.

The pure implementation lives in `app/task_confirmation`. It imports the v2
draft models but has no FastAPI, SQLAlchemy, database, provider, scheduler,
calendar, HTTP, clock, or environment dependency. `apply_review(draft, review)`
does not mutate either input.

## Decisions

Each reviewed field uses one decision:

- `accepted`: retain the draft value; no override is allowed.
- `edited`: replace the draft value; an override is required. Boolean `false` is
  a valid explicit override.
- `rejected`: remove the value; no override is allowed.

Title cannot resolve to null or an empty string. Rejecting `is_splittable` or
resolving it to null applies the documented product default `false`. Conceptual
steps may remain when `is_splittable=false`; no scheduler mapping exists yet, so
no step is silently discarded on that basis.

Step reviews identify the original step by its positive `original_order`.
Accepted steps inherit all values, edited steps inherit unspecified properties,
and rejected steps are omitted. Requested orders must be unique; surviving
steps are normalized to sequential order starting at 1.

## Review modes

- `explicit` (default): every field with `requires_confirmation=true` requires a
  review. A proposed step with a retained component requiring confirmation also
  requires a step review. Other fields are accepted.
- `accept_unreviewed`: all omitted fields and steps are accepted. This supports
  a future “Accept all” action.
- `reject_unreviewed_estimates`: omitted `estimated` and `default` field values
  are removed; `user` and `inferred` values are retained. For proposed steps,
  user-provided titles remain while estimated/default components are removed.

Clarification questions never enter `ConfirmedTask`. Required scheduling values
are resolved through corresponding field reviews.

## Audit semantics

`field_changes` contains all ten reviewable task fields, providing a complete
before/after snapshot. `accepted_fields`, `edited_fields`, and `rejected_fields`
classify those resolutions. `step_changes` includes every proposed step and its
original and confirmed representation; rejected steps have
`confirmed_value=null`.

The audit is returned to the caller only. It is not stored.

## Internal endpoint

`POST /internal/api/task-drafts/confirm` is available only when
`ENABLE_INTERNAL_TOOLS=true`. It returns `404` when internal tools are disabled
and `422` for invalid or incomplete confirmation. It makes no database, AI, or
scheduler calls.

Request:

```json
{
  "draft": {
    "title": {
      "value": "Подготовить презентацию",
      "source": "user",
      "confidence": 1.0,
      "explanation": null,
      "requires_confirmation": false
    },
    "duration": {
      "value": 240,
      "source": "estimated",
      "confidence": 0.68,
      "explanation": "Оценка на основе трёх этапов.",
      "requires_confirmation": true
    },
    "deadline": {
      "value": "2026-07-26T23:59:59+02:00",
      "source": "inferred",
      "confidence": 0.85,
      "explanation": "Конец текущей недели.",
      "requires_confirmation": true
    }
  },
  "review": {
    "mode": "explicit",
    "duration": {"decision": "edited", "value": 360},
    "deadline": {"decision": "accepted"},
    "proposed_steps": [],
    "confirmation_note": "Пользователь увеличил оценку времени."
  }
}
```

The abbreviated draft above illustrates the relevant fields; an actual request
must include every required TaskDraftV2 container field.

Response fragment:

```json
{
  "task": {
    "title": "Подготовить презентацию",
    "duration_minutes": 360,
    "deadline": "2026-07-26T23:59:59+02:00",
    "is_splittable": false,
    "steps": []
  },
  "audit": {
    "accepted_fields": ["title", "deadline"],
    "edited_fields": ["duration"],
    "rejected_fields": [],
    "field_changes": [
      {
        "field": "duration",
        "decision": "edited",
        "original_value": 240,
        "confirmed_value": 360
      }
    ],
    "step_changes": []
  }
}
```

## Current limitations

- No draft, review, confirmed task, or audit persistence.
- No mapping to the persisted SQLAlchemy `Task`.
- No scheduling-preview or scheduler integration.
- No user-facing review UI.
- No calendar integration.
