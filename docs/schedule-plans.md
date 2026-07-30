# Schedule plans

A `SchedulePreview` is a transient scheduler result. A `SchedulePlan` is the
persisted, provider-neutral record of the exact blocks a user may review and
confirm before any external calendar write is attempted.

The current lifecycle is:

```text
SchedulePreview → proposed SchedulePlan → confirmed SchedulePlan
                                      ↘ obsolete
```

`confirmed` means the user approved the stored sessions exactly as shown. It
does **not** mean those sessions were applied to Google Calendar. Future write
integration may use the remaining lifecycle states:

```text
confirmed → revalidation_required → confirmed
confirmed → applying → applied | partially_applied | failed
partially_applied → applying | failed
revalidation_required → obsolete
```

The plan service exposes create, confirm, read/list, and obsolete operations.
The separate revalidation service performs read-only FreeBusy checks for
eligible plans. Neither service invokes the scheduler, OpenAI, or Google event
writes.

## Internal endpoints

All routes require `ENABLE_INTERNAL_TOOLS=true` and return `404` otherwise:

- `POST /internal/api/schedule-plans/from-preview`
- `GET /internal/api/schedule-plans/{plan_id}`
- `GET /internal/api/users/{user_id}/schedule-plans`
- `POST /internal/api/schedule-plans/{plan_id}/confirm`
- `POST /internal/api/schedule-plans/{plan_id}/obsolete`

Production APIs must derive `user_id` from an authenticated principal. Accepting
it in the request is limited to these internal development routes.

The create request combines an existing clean `ConfirmedTask`, an existing
`SchedulePreviewResponse`, and a planning context:

```json
{
  "user_id": "11111111-1111-1111-1111-111111111111",
  "confirmed_task": {
    "title": "Prepare report",
    "description": "Draft and review the report.",
    "duration_minutes": 60,
    "priority": "medium",
    "earliest_start": null,
    "deadline": null,
    "preferred_time_of_day": "morning",
    "is_splittable": true,
    "minimum_session_minutes": 30,
    "maximum_sessions_per_day": 2,
    "steps": []
  },
  "schedule_preview": {
    "scheduler_version": "2a.1",
    "planning_window": {
      "start": "2026-07-27T08:00:00Z",
      "end": "2026-07-27T18:00:00Z"
    },
    "free_intervals": [],
    "scheduled_blocks": [
      {
        "task_id": "confirmed-task",
        "start": "2026-07-27T09:00:00Z",
        "end": "2026-07-27T10:00:00Z",
        "reason_codes": ["only_available_slot"],
        "score_components": []
      }
    ],
    "unscheduled_tasks": [],
    "warnings": []
  },
  "planning_context": {
    "timezone": "Europe/Warsaw",
    "planning_window_start": "2026-07-27T08:00:00Z",
    "planning_window_end": "2026-07-27T18:00:00Z",
    "source_calendar_snapshot_at": null,
    "scheduler_version": "2a.1",
    "workflow_version": "task-to-schedule-preview.v1",
    "calendar_context": null,
    "preferences_snapshot": {
      "timezone": "Europe/Warsaw"
    }
  },
  "source": "ai_workflow",
  "confirmation_note": null,
  "idempotency_key": "client-generated-key"
}
```

## Versions and idempotency

The first plan in a `plan_group_id` has version 1. Passing that group ID with a
revised preview creates the next version and marks the prior active plan and its
sessions obsolete. A confirmed plan is never rewritten to represent a revised
schedule.

Callers may supply an idempotency key. Without one, the service hashes the user,
optional task/group identity, source, scheduler version, and scheduled block
timestamps. The key is unique in PostgreSQL; a duplicate or concurrent create
returns the existing plan without duplicating sessions.

Confirmation and obsolete operations are also idempotent. Once confirmed,
session title, description, start, end, duration, task, step, and order are
immutable. No session update route exists.

## Snapshots

Lifecycle fields are relational columns. JSONB is limited to:

- the clean `ConfirmedTask`, without AI confidence or provider metadata;
- effective scheduling preferences and available provenance;
- a busy-context summary with provider, calendar IDs, counts, query time, and
  planning window, but no raw event details;
- scheduler diagnostics, warnings, score/reason metadata, and unscheduled
  remainder.

OAuth tokens, client secrets, raw Google responses, and Calendar event content
are never stored in a plan.

The synchronization foundation also adds nullable
`busy_sources_snapshot`, `write_targets_snapshot`,
`calendar_selection_hash`, and `calendar_context_captured_at` fields. They
capture the exact FreeBusy sources and deterministic per-session write targets
used when a new plan is created. They remain nullable so legacy plans continue
to load safely.

## Interval reservations

The repository treats sessions as reserved when their plan is in
`confirmed`, `revalidation_required`, `applying`, `applied`, or
`partially_applied`. Proposed, failed, and obsolete plans do not reserve time.
Queries use half-open overlap semantics `[start, end)` and support
`exclude_plan_id`.

The reservation query is implemented and tested. Database-backed scheduling
preview merges these intervals into its busy input. Revalidation checks them
alongside provider FreeBusy while excluding the current plan.

## Apply

`POST /internal/api/schedule-plans/{plan_id}/apply?user_id=...` applies a
confirmed plan. It validates every unapplied session against the immutable
`write_targets_snapshot`, atomically claims the plan as `applying`, creates one
Google event per unmapped session, persists each successful
`CalendarEventMapping`, and finalizes the plan as `applied`,
`partially_applied`, or `failed`.

Existing mappings are the primary idempotency check. Google insert requests use
the stable ScheduledSession UUID as the provider event ID, and an insert
conflict is treated as the same event. Database uniqueness continues to enforce
one mapping per ScheduledSession and unique provider event identity. A crash
after Google accepts an event but before its mapping commits can be recovered by
retrying the same stable event ID. Apply does not claim general exactly-once
delivery, because an unusable provider response can still leave an external
event without a local mapping.

Apply does not regenerate snapshots, run the scheduler, reschedule failures,
delete external events as compensation, or release reservations.

## Current limitations

- Internal endpoints only; no production authentication.
- No Google Calendar event update or deletion.
- No FreeBusy revalidation on confirmation.
- No session editing.
- No two-way synchronization or push notifications.
