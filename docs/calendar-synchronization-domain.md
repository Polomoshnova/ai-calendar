# Calendar synchronization domain

Status: foundation implemented; runtime orchestration pending

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260730_08`

This document describes the synchronization types and persistence introduced by
Alembic revision `20260728_07`. For the source-of-truth decision, see
[ADR: Calendar synchronization domain foundation](adr-calendar-synchronization-domain.md).

## Runtime boundary

The current Google Calendar adapter is read-only. No endpoint or service creates
Google events, populates mappings from provider responses, polls changes,
records external changes, invokes consistency checking, or applies deadline
updates. The components below are tested building blocks for those future use
cases.

## Persistence model

### CalendarEventMapping

`CalendarEventMapping` is the synchronization record for one
`ScheduledSession`. It stores:

- `scheduled_session_id` and `calendar_connection_id`;
- provider and optional provider account identity;
- calendar ID and external event ID;
- etag and provider update timestamp;
- sync status;
- last attempt, last success, safe error code, and diagnostic message;
- creation and update timestamps.

The database enforces one mapping per session and unique external event identity
within a connection and calendar. `ScheduledSession` does not persist duplicate
provider identity or sync status; compatibility response properties read through
the mapping relationship.

### ExternalCalendarChange

`ExternalCalendarChange` belongs to a mapping and stores:

- change type;
- optional provider timestamp;
- detection and processing timestamps;
- optional `old_values`, `new_values`, and `raw_payload` JSONB.

The model can represent `created`, `updated`, `moved`, and `deleted` changes.
No detector or processor currently writes these rows.

## SchedulePlan calendar context

`SchedulePlan` has nullable, migration-safe fields:

- `busy_sources_snapshot`;
- `write_targets_snapshot`;
- `calendar_selection_hash`;
- `calendar_context_captured_at`.

They are nullable so existing plans remain valid. New plan creation populates
them from the exact FreeBusy calendar selection and the deterministic default
write target.

`BusySourceSnapshot` identifies one connection/provider/account/calendar used
as an availability source and may include a timezone-aware capture time.
`SessionWriteTargetSnapshot` identifies the connection/provider/account/calendar
target for one session. The write-target type can represent different
connections for different sessions. Multiple Google accounts may belong to one
user, although no apply workflow consumes these snapshots.

`calendar_context_hash()` recursively canonicalizes mappings and unordered
collections, serializes compact JSON, and returns a SHA-256 digest. Reordering
busy sources or write targets does not change the hash.

## Consistency policy

`DefaultConsistencyChecker` is pure and provider-independent. It accepts typed
session snapshots and detects:

- overlapping active sessions;
- gaps shorter than the configured minimum break;
- mismatch between session order and chronological order;
- the latest active session ending after the task deadline;
- externally deleted sessions.

Intervals use half-open semantics `[start, end)`, so adjacent sessions do not
overlap. The checker returns `consistent` or `inconsistent` plus typed issue
codes. It does not call Google, mutate Tasks, move sessions, or invoke the
scheduler.

## Deadline policy

`deadline_after_external_move()` implements:

```text
max(current deadline, every non-deleted session end)
```

It accepts only timezone-aware datetimes. With no current deadline, the latest
non-deleted session end becomes the result; with no values, the result is
`None`. No runtime workflow applies this result to `Task.deadline`.

## Reserved intervals

`list_reserved_intervals()` returns stored sessions that overlap a requested
half-open window. Plans reserve time in these states:

- `confirmed`;
- `revalidation_required`;
- `applying`;
- `applied`;
- `partially_applied`.

Plans in `proposed`, `failed`, or `obsolete` do not reserve time.
`exclude_plan_id` supports self-exclusion. The repository query exists and is
tested. Database-backed preview merges its results with request busy intervals.
Revalidation merges other-plan reservations with provider FreeBusy and excludes
the current plan.

## Migration

The file is:

```text
migrations/versions/20260728_05_calendar_sync_domain_foundation.py
```

Its Alembic metadata is:

```text
revision:      20260728_07
down_revision: 20260728_06
```

The migration adds the mapping and change tables, synchronization enums, and
nullable SchedulePlan fields. It removes the earlier external-event columns
from `scheduled_sessions`, making `CalendarEventMapping` the only persistence
source for external synchronization.
