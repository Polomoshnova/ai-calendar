# External Calendar Policy Engine

Status: pure domain policy implemented; application processing not implemented

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

The engine never executes these decisions. A later application service may
translate them into explicitly authorized persistence operations.

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
immutable structured details. Conflict persistence and processing remain out of
scope.

## Layer boundary

The module uses only Python domain values. It has no FastAPI, SQLAlchemy,
repository, provider, Google, HTTP, transaction, logging, or dependency
injection dependency. Pull synchronization continues only to detect and record
external changes; it does not invoke this engine in this PR.
