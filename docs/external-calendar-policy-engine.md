# External Calendar Policy Engine

Status: pure domain policy and application processing implemented

The External Calendar Policy Engine converts normalized synchronization state
into deterministic, typed decisions. It does not load data, persist decisions,
call Google, mutate models, or decide transaction boundaries.

## Aggregate input

`ExternalCalendarAggregate` is an immutable provider- and persistence-neutral
value containing:

- Task and SchedulePlan identity;
- the changed ScheduledSession identity;
- current Task deadline;
- the SchedulePlan planning window;
- immutable `ExternalCalendarSession` values for mapped session placement.

`ExternalCalendarChangeInput` supplies the normalized previous and current
`ExternalEventState`. These values contain existence/cancellation state,
start/end, and calendar identity. ORM entities and provider payloads are not
accepted.

## Decisions

`evaluate_external_calendar_policy()` returns an immutable tuple containing one
or more of:

- `NoAction`;
- `UpdateScheduledSessionTime`;
- `ExtendTaskDeadline`;
- `MarkExternalEventMissing`;
- `RecordConflict`;
- `UnsupportedExternalChange`.

The engine never executes these decisions. `ProcessExternalCalendarChangeService`
translates them into explicitly authorized persistence operations without
adding a second set of policy rules.

## Supported policy

- A meaningful external start/end move requests a session-time update.
- The deadline is extended to the latest externally positioned mapped session
  end when that value exceeds the current deadline. It is never shortened.
- Deleted and cancelled events request missing-event handling. They never
  request backlog placement or provider-event recreation.
- Identical previous/current state returns `NoAction`.
- Changes outside the supported policy return `UnsupportedExternalChange`
  instead of raising a policy error.

## Conflict detection

The engine reports, but does not reconcile:

- a move outside the stored planning window;
- projected overlap between mapped sessions;
- a deleted or cancelled mapped event.

Each `RecordConflict` has a typed code, severity, session identities, and
immutable structured details. Processing persists it as an
`ExternalCalendarConsistencyFinding`; it does not resolve the conflict or alter
the SchedulePlan status. `DefaultConsistencyChecker` remains available for its
broader pure checks, but the processing service does not invoke it or duplicate
its rules.

## Layer boundary

The module uses only Python domain values. It has no FastAPI, SQLAlchemy,
repository, provider, Google, HTTP, transaction, logging, or dependency
injection dependency. Pull synchronization continues only to detect and record
external changes. The explicit processing endpoint is the orchestration
boundary that invokes the engine.

## Processing lifecycle and endpoint

`POST /internal/api/external-calendar-changes/{change_id}/process?user_id=...`
is guarded by the internal-tools gate and validates ownership through the
mapping, connection, session, plan, and task relationships.

Lifecycle behavior is:

- `pending` and `failed` are processable;
- `processing` is rejected as concurrent work;
- `processed` returns the stored idempotent result without evaluating or
  applying decisions again.

The service locks the `ExternalCalendarChange` row, re-reads related state,
constructs immutable aggregate values, calls
`evaluate_external_calendar_policy()` exactly once, validates every returned
decision, and only then applies them. Session time changes, deadline history,
missing-event mapping state, consistency findings, the deterministic result,
and final `processed` status commit in one short transaction. Any validation or
persistence failure rolls the whole attempt back to its previous status.

`UnsupportedExternalChange` is stored as a processed typed no-op with action
`unsupported_external_change`. Moves update only the mapped session and may
extend—but never shorten—the Task deadline. Deletions preserve the mapping,
session, Task status, and SchedulePlan status. No provider call, scheduler run,
snapshot regeneration, replacement event, backlog transition, or automatic
rescheduling occurs in this flow.
