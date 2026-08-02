# Domain model

Last verified against code: 2026-08-02

Latest verified Alembic revision: `20260802_11`

## Overview

The model separates temporary AI interpretation, durable task intent,
provider-neutral planning, read-only calendar access, and future external event
synchronization. SQLAlchemy entities are durable unless noted otherwise;
Pydantic models are typed application contracts.

```mermaid
erDiagram
    USER ||--o{ TASK : owns
    USER ||--o{ BACKLOG_ENTRY : owns
    TASK ||--o{ BACKLOG_ENTRY : has_history
    USER ||--o{ CALENDAR_CONNECTION : connects
    CALENDAR_CONNECTION ||--o{ CALENDAR_SELECTION : contains
    USER ||--o{ SCHEDULE_PLAN : owns
    TASK o|--o{ SCHEDULE_PLAN : versions
    SCHEDULE_PLAN ||--|{ SCHEDULED_SESSION : contains
    TASK o|--o{ SCHEDULED_SESSION : schedules
    SCHEDULE_PLAN ||--o{ SCHEDULE_PLAN_REVALIDATION : audits
    CALENDAR_CONNECTION o|--o{ SCHEDULE_PLAN_REVALIDATION : checks_with
    SCHEDULED_SESSION ||--o| CALENDAR_EVENT_MAPPING : maps_to
    CALENDAR_CONNECTION ||--o{ CALENDAR_EVENT_MAPPING : owns
    CALENDAR_EVENT_MAPPING ||--o{ EXTERNAL_CALENDAR_CHANGE : records
    TASK ||--o{ TASK_DEADLINE_HISTORY : audits
    EXTERNAL_CALENDAR_CHANGE ||--o| TASK_DEADLINE_HISTORY : causes
    EXTERNAL_CALENDAR_CHANGE ||--o{ EXTERNAL_CALENDAR_CONSISTENCY_FINDING : reports
    SCHEDULE_PLAN ||--o{ EXTERNAL_CALENDAR_CONSISTENCY_FINDING : scopes
    SCHEDULED_SESSION o|--o{ EXTERNAL_CALENDAR_CONSISTENCY_FINDING : concerns
```

`TaskDraftV2` and `ConfirmedTask` are intentionally absent from the ER diagram
because they are not database entities.

### External Calendar Policy Engine

Purpose: transform an immutable `ExternalCalendarAggregate` and normalized
external change into typed policy decisions.

Ownership and mutability: pure domain values; aggregate, event state, sessions,
conflict details, and decisions are immutable. The engine does not accept ORM
entities or provider payloads.

Decisions: no action, update session time, extend deadline, mark event missing,
record a conflict, or flag an unsupported change. The engine identifies
outside-window, overlap, and missing-event conflicts but does not reconcile or
persist them.

Status: domain evaluation and application orchestration are implemented. The
processing service invokes the engine once and applies validated decisions
atomically. See
[External Calendar Policy Engine](../external-calendar-policy-engine.md).

## Entities and contracts

### User

Purpose: owns Tasks, preferences, calendar connections, and SchedulePlans.

Ownership and mutability: persistent and mutable. Email is unique; timezone is
an IANA identifier and is the scheduling timezone authority.

Relations: one user has many Tasks and SchedulePlans. Calendar connections are
unique per `(user_id, provider, provider_account_id)`.

Status: implemented.

### Task

Purpose: stores durable work intent used by database-backed preview.

Ownership and mutability: application-owned and mutable through CRUD. It is not
versioned. `TaskStatus` is `pending`, `completed`, or `cancelled`; the API can
patch status directly and does not enforce a transition graph.

Relations: belongs to User. A SchedulePlan may reference a Task through nullable
`task_id`; deleting a Task sets that reference to null.

Key invariants: positive duration, minimum session, and maximum sessions per
day; timezone-aware optional dates; earliest start before deadline; a
splittable task cannot have a minimum session longer than its duration.

Status: implemented.

### BacklogEntry

Purpose: records why some or all of a Task's work is not assigned to a concrete
schedule, which actor or subsystem created the entry, how many minutes remain,
and when it should next be reviewed.
Backlog is separate from `TaskStatus.pending`; neither state implies the other.

Ownership and mutability: belongs to the same User as its Task. A Task has at
most one `active` or `deferred` entry, enforced by a partial unique database
index, while `resolved` and `cancelled` entries remain as history.

Relations: belongs to one User and one Task. It does not own or mutate a
SchedulePlan or ScheduledSession.

Key invariants: open entries have positive remaining duration no greater than
`Task.duration_minutes`; deferred entries require `deferred_until` or
`next_review_at`; resolved entries require `resolved_at`; all supplied domain
times are timezone-aware. `origin` is supplied explicitly as `user`,
`scheduler`, `system`, or `calendar_sync`; it is not inferred from the reason.
Manual defer is user-originated, while slot, capacity, horizon, and
partial-scheduling outcomes are scheduler-originated. Reasons are
`no_deadline`, `no_available_slot`,
`insufficient_capacity`, `planning_horizon_exceeded`,
`awaiting_user_confirmation`, `manual_defer`, `partially_scheduled`, and
`other`. The `other` reason requires a meaningful explanatory note.

Partial scheduling is represented by the positive duration left after sessions
from reserving plans are subtracted from total Task duration. It uses the same
central reservation policy as scheduling preview and revalidation: confirmed,
revalidation-required, applying, applied, and partially-applied plans reserve
time; proposed, failed, and obsolete plans do not. Only that remainder has
backlog semantics. Deleted Google events do not create a backlog entry or imply
unscheduled completion.

Temporary provider and OAuth failures remain integration errors rather than a
backlog reason. `calendar_sync` is available as an origin only for an explicit
calendar synchronization decision; no automatic creation exists.

Status: domain model, lifecycle service, persistence, locking, and review-query
foundation are implemented. HTTP API, UI, automatic scheduling, and automatic
review are planned separately.

### TaskDraftV2

Purpose: represents AI interpretation of natural-language input. Each
interpreted field carries value, source, confidence, explanation, and a
confirmation requirement.

Ownership and mutability: temporary application data. It is neither persisted
nor treated as confirmed user intent.

Relations: consumed with `DraftReview` by the confirmation service.

Key invariants: strict schema, versioned contract, validated provider output,
and no direct scheduler or database call.

Status: implemented.

### ConfirmedTask

Purpose: clean, provider-neutral task representation produced after typed
review.

Ownership and mutability: an immutable-style Pydantic handoff value, not a
database entity. A serialized copy is stored in
`SchedulePlan.confirmed_task_snapshot`.

Relations: mapped to deterministic scheduler input or used to create a
SchedulePlan from an existing preview.

Key invariants: no AI confidence or explanation metadata; timezone-aware date
validation; reviewed required values.

Status: implemented.

### SchedulePlan

Purpose: persists an exact provider-neutral scheduling proposal independently
of the transient preview.

Ownership and mutability: user-owned and versioned by `plan_group_id` and
`version`. Content snapshots and scheduling fields become immutable once a plan
leaves `proposed`; lifecycle and audit fields may still change according to the
transition table.

Relations: belongs to User, optionally references Task, contains ordered
ScheduledSessions, and owns revalidation audits.

Key invariants: positive version, valid planning window, unique group/version,
unique task/version, unique idempotency key, and at least one validated session
when created by the service.

Status: persistence, confirmation, obsoletion, listing, revalidation, and Apply
are implemented.

### ScheduledSession

Purpose: represents one exact block within a SchedulePlan and is the
synchronization unit.

Ownership and mutability: owned by its plan. Scheduling content becomes
immutable with the confirmed plan. Status can represent proposed, confirmed,
applying, applied, failed, or obsolete.

Relations: belongs to SchedulePlan, optionally references Task, and has at most
one `CalendarEventMapping`.

Key invariants: positive duration and order, start before end, stored duration
matches the interval, and order is unique within a plan.

`ScheduledSession` does not persist `external_event_id`, provider identity,
etag, or sync status. Compatibility properties read external identity through
its mapping. `CalendarEventMapping` is the sole persistence source for external
event identity and synchronization state.

Status: implemented as a plan component and synchronization unit.

### CalendarConnection

Purpose: stores one user's provider connection and encrypted credentials.

Ownership and mutability: user-owned and mutable as OAuth tokens refresh,
connections expire, or the user disconnects.

Relations: belongs to User and owns selections and event mappings.

Key invariants: unique `(user_id, provider, provider_account_id)`; provider is
currently `google`; status is `active`, `expired`, `revoked`, or `error`;
tokens are encrypted and never returned by API schemas.

Status: implemented for Google read access and explicit event creation during
Apply.

### CalendarSelection

Purpose: records known provider calendars and whether each contributes to
availability.

Ownership and mutability: belongs to a connection and is replaceable through
the selection endpoint.

Relations: many selections belong to one CalendarConnection.

Key invariants: external calendar identity is unique within a connection.

Status: implemented for read-only availability. A dedicated write calendar is
not yet a runtime setting.

### CalendarEventMapping

Purpose: links one ScheduledSession to one external provider event and stores
etag, provider update time, sync status, and safe diagnostics.

Ownership and mutability: application persistence mirrors provider identity and
sync observations. Apply creates it and pull synchronization updates it.

Relations: belongs to ScheduledSession and CalendarConnection; owns external
change audit rows.

Key invariants: one mapping per ScheduledSession and unique external identity
within `(calendar_connection_id, calendar_id, external_event_id)`.

One Task may have sessions mapped to different Google accounts and calendars
because write targets and mappings are per session.

Status: created by ApplySchedulePlan and read by pull synchronization.

### ExternalCalendarChange

Purpose: records a detected external `created`, `updated`, `moved`, or `deleted`
change with optional before/after values and raw diagnostic payload.

Ownership and mutability: append-oriented audit data with nullable
`processed_at`.

Relations: belongs to CalendarEventMapping.

Key invariants: mapping reference and change type are required; deletion of the
mapping cascades to its changes.

Pull synchronization detects and persists these rows. The External Calendar
Policy Engine returns typed decisions; `ProcessExternalCalendarChangeService`
validates and applies them atomically with the final processing status.

Status: detection and processing are implemented through explicit internal
endpoints.

### TaskDeadlineHistory

Purpose: audits deadline extensions caused by processed external calendar
changes.

Relations: belongs to one Task and one `ExternalCalendarChange`. At most one
history row may be written for a change.

Status: implemented. Deadline processing extends only and never shortens.

### ExternalCalendarConsistencyFinding

Purpose: persists structured conflict findings returned by the Policy Engine
without resolving them.

Relations: belongs to one `ExternalCalendarChange` and `SchedulePlan`, and may
reference one `ScheduledSession`.

Status: implemented. A deleted Google event preserves the mapping, session,
Task status, and plan; it does not automatically move a Task to backlog.

### SchedulePlanRevalidation

Purpose: persists each fresh read-only readiness check, its session hash,
provider outcome, conflicts, diagnostics, and validity window.

Ownership and mutability: append-oriented audit owned by SchedulePlan. An
optional request ID makes retries idempotent per plan.

Relations: belongs to SchedulePlan and User; optionally references the calendar
connection, which becomes null if disconnected.

Key invariants: captures the plan version, update timestamp, and sessions hash
used for the check. Stale concurrent results are rejected before persistence.

Status: implemented.

## Reservation invariants

Plans in `confirmed`, `revalidation_required`, `applying`, `applied`, and
`partially_applied` reserve their session intervals. Plans in `proposed`,
`failed`, and `obsolete` do not. Overlap uses half-open semantics:

```text
[start, end)
session.start < query.end AND query.start < session.end
```

An interval ending exactly when another begins is not an overlap.
