# Backlog API

The internal Backlog API exposes explicit Planner backlog operations while
preserving the domain boundary. All routes require internal tools to be enabled.
Scheduling runs only when the user explicitly requests a preview. The API does
not contact a calendar provider, write Google Calendar, or create backlog
entries automatically. A SchedulePlan is persisted only when the user submits
an explicitly selected preview.

## Endpoints

- `GET /internal/api/backlog?user_id=...` lists active and deferred entries by
  default. Optional `status`, `reason`, `origin`, `due_only`, `limit`, and
  `offset` filters are supported.
- `GET /internal/api/backlog/{entry_id}?user_id=...` returns one owned entry.
- `POST /internal/api/backlog` creates an explicit entry. If
  `remaining_duration_minutes` is omitted, the application subtracts sessions
  in reserving SchedulePlans from `Task.duration_minutes`.
- `POST /internal/api/backlog/{entry_id}/defer?user_id=...` moves an active
  entry to deferred and requires `deferred_until` or `next_review_at`.
- `POST /internal/api/backlog/{entry_id}/reactivate?user_id=...` performs only
  the deferred-to-active transition.
- `POST /internal/api/backlog/{entry_id}/resolve?user_id=...` resolves the
  backlog condition.
- `POST /internal/api/backlog/{entry_id}/cancel?user_id=...` explicitly stops
  backlog tracking.
- `POST /internal/api/backlog/{entry_id}/schedule-preview?user_id=...` creates a
  fresh deterministic preview for one owned active or deferred entry. The body
  contains `planning_window` and optional `busy_intervals`.
- `POST /internal/api/backlog/{entry_id}/schedule-plan?user_id=...` validates an
  explicitly selected preview and persists a proposed SchedulePlan linked to
  the entry. The request includes the preview's `scheduling_attempt_count` as
  its identity, the selected preview, and the existing planning context.

The scheduling preview recalculates current unscheduled duration from the
Task's full duration and sessions in reserving SchedulePlans. It schedules only
that remainder, combines request busy intervals with the shared reservation
query, and returns the normal preview plus backlog and attempt metadata. If no
slot is available, the response remains successful and includes
`unscheduled_reason`. The existing preview flow does not query Google FreeBusy,
so this endpoint accepts already-normalized busy intervals instead.

Each accepted preview request updates `last_scheduling_attempt_at` and
increments `scheduling_attempt_count`, including valid unscheduled outcomes.
Invalid context and ineligible entry or Task states do not record an attempt.
Repeated calls are explicit fresh previews; there is no background retry.

Creating a proposed plan does not change backlog status, reason, or remaining
duration. Repeating the same selected attempt is idempotent; a later preview
attempt may create another independent plan. Only successful confirmation
through the existing SchedulePlan confirm endpoint recalculates backlog work.
Partial coverage preserves active/deferred status, stores the recalculated
remainder, and records `partially_scheduled`. Full coverage resolves the entry
and sets `resolved_at`. The calculation always starts from Task duration and
all sessions selected by the shared reservation policy, rather than subtracting
the latest plan blindly.

List ordering is deterministic: review time first, followed by `entered_at`
and entry ID. Ownership failures return 404, conflicting open entries and
invalid lifecycle transitions return 409, and request or domain validation
failures return 422. Equivalent repeated creation returns the existing open
entry.

`origin` and `reason` use the domain enums. `calendar_unavailable` is not a
reason; temporary provider failures remain integration errors. `reason=other`
requires a meaningful note.

The generated OpenAPI document includes typed request and response schemas,
operation summaries, boundary descriptions, and creation, defer, and scheduling
preview and proposed-plan examples.
