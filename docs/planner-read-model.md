# Planner read model

`GET /internal/api/planner?user_id=...&days=14` is the bounded, read-only UI
projection for the current planning state. Like the other internal endpoints,
it is available only when internal tools are enabled. `days` accepts `1`–`31`.

The response contains:

- `today`: reserving ScheduledSessions overlapping the user's current local day;
- `upcoming`: reserving ScheduledSessions beginning after today within the
  requested local-day horizon;
- `backlog`: active and deferred BacklogEntries with their Task titles;
- `proposed_plans`: plans awaiting confirmation;
- `confirmed_not_applied`: confirmed, revalidation-required, applying, and
  partially-applied plans;
- `needs_attention`: persisted consistency findings, unprocessed external
  changes, and revalidation-required or partially-applied plans. Terminal
  failed plans are excluded because the current lifecycle provides no retry or
  recovery action for them.

Sync problems are folded into `needs_attention`; a separate `sync_issues`
collection would represent the same persisted conditions twice.

Planner groups sessions using the configured IANA timezone on `User`. Session
times always come from the current `ScheduledSession` rows. In particular, an
external Google move that has been pulled and processed is shown at its updated
time, not at the immutable preview time stored on SchedulePlan.

Planner is not a domain aggregate or source of truth. It performs a fixed set
of bounded SQL queries and returns typed transport models. It never calls an
HTTP endpoint, scheduler, calendar provider, ApplySchedulePlan, pull sync, or
external-change processing, and it never writes data.

The sources of truth remain:

- Task
- BacklogEntry
- SchedulePlan
- ScheduledSession
- CalendarEventMapping
- ExternalCalendarChange
- ExternalCalendarConsistencyFinding

Consistency findings currently have no resolved/dismissed lifecycle field, so
all persisted findings remain visible in `needs_attention`. Processed external
changes are omitted because their lifecycle already records completion.
