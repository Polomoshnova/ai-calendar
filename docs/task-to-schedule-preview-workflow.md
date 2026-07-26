# Internal task-to-schedule-preview workflow

`POST /internal/api/workflows/task-to-schedule-preview` executes the complete
stateless validation path:

```text
Natural-language text
  → AI Intake / TaskDraftV2
  → DraftReview / ConfirmationResult
  → ConfirmedTask mapping
  → shared stateless scheduling preview
```

The endpoint is intended for internal evaluation, product experiments,
transformation debugging, and deterministic replay. It is available only when
`ENABLE_INTERNAL_TOOLS=true` and returns 404 otherwise.

No stage persists tasks, drafts, reviews, audits, or plans. The workflow has no
database dependency, does not create calendar events, and does not import a
calendar provider. It calls the same `generate_schedule_preview` orchestration
used by the Scheduling Lab product-scenario mode.

## Request

`TaskToSchedulePreviewRequest` contains:

- `text`: trimmed non-empty user text;
- `review`: the existing `DraftReview`; no automatic hidden review mode exists;
- `ai_context`: optional timezone-aware reference datetime and IANA timezone for
  deterministic relative-date interpretation;
- `scheduling_context`: explicit preview window, timezone, busy intervals,
  existing `TemporaryPreferences`, and existing `TemporaryTask` inputs;
- `include_trace`: whether completed trace entries are returned.

The workflow reuses `DateTimeInterval`, `TemporaryPreferences`, and
`TemporaryTask`; it does not define a second preference, interval, or pending
task contract. The planning horizon remains limited to 31 days. The scheduling
timezone must match `preferences.timezone`.

Complete request:

```json
{
  "text": "Подготовить презентацию для инвесторов к пятнице",
  "review": {
    "mode": "explicit",
    "duration": {"decision": "edited", "value": 360},
    "deadline": {"decision": "accepted"},
    "is_splittable": {"decision": "accepted"},
    "minimum_session_minutes": {"decision": "edited", "value": 45},
    "proposed_steps": [],
    "confirmation_note": "Тест полного workflow."
  },
  "ai_context": {
    "current_datetime": "2026-07-26T15:00:00+02:00",
    "timezone": "Europe/Warsaw"
  },
  "scheduling_context": {
    "window_start": "2026-07-27T08:00:00+02:00",
    "window_end": "2026-08-02T20:00:00+02:00",
    "timezone": "Europe/Warsaw",
    "busy_intervals": [
      {
        "start": "2026-07-27T10:00:00+02:00",
        "end": "2026-07-27T11:30:00+02:00"
      }
    ],
    "preferences": {
      "timezone": "Europe/Warsaw",
      "working_hours": {
        "monday": [{"start": "09:00", "end": "18:00"}],
        "tuesday": [{"start": "09:00", "end": "18:00"}],
        "wednesday": [{"start": "09:00", "end": "18:00"}],
        "thursday": [{"start": "09:00", "end": "18:00"}],
        "friday": [{"start": "09:00", "end": "18:00"}],
        "saturday": [],
        "sunday": []
      },
      "preferred_task_time": "any",
      "minimum_break_minutes": 15,
      "no_deep_work_after": "17:00",
      "default_minimum_session_minutes": 15
    },
    "existing_pending_tasks": []
  },
  "include_trace": true
}
```

`maximum_sessions_per_day` is an existing per-task constraint, not a global
preference. For the generated workflow task it comes from `ConfirmedTask`; for
existing tasks it is part of each `TemporaryTask`.

## Response

`TaskToSchedulePreviewResponse` exposes every material boundary:

- exact validated `TaskDraftV2`;
- existing `ConfirmationResult`;
- normalized `SchedulerInputSnapshot`;
- existing `SchedulePreviewResponse`;
- structured workflow trace;
- `workflow_version = "task-to-schedule-preview.v1"`.

Abbreviated response:

```json
{
  "draft": {
    "schema_version": "task-draft.schema.v2",
    "prompt_version": "ai-intake.task-draft.v2"
  },
  "confirmation": {
    "task": {
      "title": "Подготовить презентацию для инвесторов",
      "duration_minutes": 360,
      "is_splittable": true,
      "minimum_session_minutes": 45,
      "steps": []
    },
    "audit": {
      "accepted_fields": ["title", "deadline", "is_splittable"],
      "edited_fields": ["duration", "minimum_session_minutes"],
      "rejected_fields": []
    }
  },
  "scheduler_input": {
    "task": {
      "id": "workflow-task-1",
      "duration_minutes": 360,
      "minimum_session_minutes": 45,
      "value_sources": {
        "priority": "scheduler_default",
        "preferred_time_of_day": "scheduler_default",
        "minimum_session_minutes": "confirmed",
        "maximum_sessions_per_day": "scheduler_default",
        "is_splittable": "confirmed"
      }
    },
    "timezone": "Europe/Warsaw",
    "busy_interval_count": 1,
    "pending_task_count": 1
  },
  "schedule_preview": {
    "scheduler_version": "2a.1",
    "scheduled_blocks": [],
    "unscheduled_tasks": [],
    "warnings": []
  },
  "trace": [
    {
      "stage": "ai_intake",
      "status": "completed",
      "summary": "AI task draft created.",
      "warnings": [],
      "metadata": {
        "schema_version": "task-draft.schema.v2"
      }
    }
  ],
  "workflow_version": "task-to-schedule-preview.v1"
}
```

Scheduling blocks in a real response are produced by the deterministic scheduler
and depend on the request inputs.

## ConfirmedTask mapping

- A positive confirmed `duration_minutes` is mandatory. It is never inferred
  from steps or defaulted by this workflow.
- Priority, earliest start, deadline, preferred time, splitting, minimum session,
  and maximum sessions/day map to existing `SchedulingTask` fields.
- Null priority maps to the scheduler default `medium`.
- Null preferred time maps to `any`.
- Null minimum session maps to
  `preferences.default_minimum_session_minutes`.
- Null maximum sessions/day maps to the existing scheduler default `1`.
- Description and conceptual steps are retained in the snapshot, but the current
  scheduler does not schedule steps independently. A trace warning makes this
  limitation explicit.
- When all step durations are known and their sum differs from the confirmed
  total, the total remains authoritative and a warning records the discrepancy.
- The generated task uses deterministic id `workflow-task-1`.

### Scheduler default provenance

`ConfirmedTask` contains only user-approved planning values. The operational
mapper may still need scheduler fallbacks, so `SchedulerTaskSnapshot` records
their provenance in the typed `SchedulerTaskValueSources` model under
`value_sources`.

`SchedulerValueSource` has two meanings:

- `confirmed`: the value was present in `ConfirmedTask`;
- `scheduler_default`: the mapper supplied the scheduler's operational fallback.

Provenance depends on presence in `ConfirmedTask`, not on the effective value.
Equal values can therefore have different sources:

```text
ConfirmedTask.priority = null
→ SchedulerTaskSnapshot.priority = medium
→ value_sources.priority = scheduler_default

ConfirmedTask.priority = medium
→ SchedulerTaskSnapshot.priority = medium
→ value_sources.priority = confirmed
```

The covered fields and defaults are:

- `priority`: `medium`;
- `preferred_time_of_day`: `any`;
- `minimum_session_minutes`:
  `preferences.default_minimum_session_minutes`;
- `maximum_sessions_per_day`: `1`;
- `is_splittable`: always `confirmed`, because `ConfirmedTask` contains a
  required boolean.

When any fallback is used, the `scheduler_mapping` trace contains one diagnostic
warning, for example:

```text
Scheduler defaults applied: priority, preferred_time_of_day,
maximum_sessions_per_day.
```

The same trace entry exposes deterministic `defaulted_fields` and
`confirmed_fields` arrays. This provenance is not passed to `SchedulingTask` and
does not affect the scheduling heuristic or public scheduler warnings.

When every conceptual step has a known duration, scheduler-mapping trace also
contains:

```text
unallocated_minutes = confirmed duration - step duration sum
```

For example:

```text
ConfirmedTask.duration_minutes = 360
steps total = 300
→ unallocated_minutes = 60
```

The value can be positive, zero, or negative. When any step duration is unknown,
`step_duration_sum` and `unallocated_minutes` are both `null`. This diagnostic
never rewrites the confirmed duration or step durations and never creates an
automatic buffer step.

A deadline before the preview window and earliest start after the preview window
are rejected. A deadline after the window is allowed, but trace warns that the
preview is limited to the requested window. Relative dates are never
reinterpreted after AI Intake.

## Trace and logging

Trace stages are `ai_intake`, `confirmation`, `scheduler_mapping`, and
`scheduling_preview`. Entries include counts, versions, normalized constraints,
and warnings. They never contain chain-of-thought, provider HTTP payloads, API
keys, authorization headers, database URLs, or raw prompts.

Application logs record only workflow version, stage, status, versions/counts,
and warning/error codes. Full user text and private busy-interval content are not
logged.

## Errors

The endpoint returns structured details:

```json
{
  "detail": {
    "code": "missing_confirmed_duration",
    "stage": "scheduler_mapping",
    "message": "Confirmed task has no duration and cannot be scheduled."
  }
}
```

- 400: invalid workflow relationships such as deadline/window conflicts;
- 422: incomplete confirmation or missing/invalid confirmed scheduler values;
- 502: provider or AI structured-output failure;
- 503: provider configuration missing;
- 500: scheduling orchestration failure or unexpected server error;
- 404: internal tools disabled.

## Replay and evaluation

`WorkflowReplayCase` is the serializable replay format containing request, fake
AI response, and expected invariants. Fixtures live under
`tests/product/workflows/`. Standard tests replace only the AI gateway and never
call OpenAI.

Evaluation scenarios cover relative-date tasks, edited duration, course work,
two-week planning, missing duration, insufficient capacity, unavailable
weekdays, disabled weekends, window conflicts, Europe/Warsaw DST, rejected
splitting, and competition with existing pending tasks.

## Current limitations

- No database persistence or ORM mapping.
- No Google Calendar integration, OAuth, or event creation.
- No user-facing confirmation UI.
- Conceptual steps are not independently scheduled.
- Live AI output is nondeterministic outside fake-provider replay tests.
- Callers must explicitly review fields required by the generated draft.
