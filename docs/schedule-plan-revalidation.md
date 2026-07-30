# Schedule plan revalidation

Confirmation records that a user approved exact scheduled sessions. It does not
prove those times are still free. Revalidation performs a fresh provider
FreeBusy query for a confirmed plan, compares the immutable sessions with
current busy intervals, persists an audit attempt, and reports whether the exact
plan is currently safe for a future apply operation.

Revalidation never invokes the scheduler, moves sessions, creates a replacement
plan, or writes Calendar events.

## Internal endpoints

Both endpoints require `ENABLE_INTERNAL_TOOLS=true`:

- `POST /internal/api/schedule-plans/{plan_id}/revalidate`
- `GET /internal/api/schedule-plans/{plan_id}/revalidations`

Example request:

```json
{
  "connection_id": "22222222-2222-2222-2222-222222222222",
  "calendar_ids": null,
  "include_internal_busy": true,
  "minimum_break_minutes": null,
  "request_id": "transport-retry-key"
}
```

The plan supplies `user_id`, timezone, original selection, and sessions. The
request cannot narrow the query window or select a different calendar set.
Production APIs must derive ownership from the authenticated principal.

## Lifecycle and readiness

Eligible states are `confirmed` and `revalidation_required`.

- `confirmed + valid` remains `confirmed`.
- `confirmed + conflict` becomes `revalidation_required`.
- `revalidation_required + valid` becomes `confirmed`.
- `revalidation_required + conflict` remains `revalidation_required`.
- A provider failure preserves the current plan status and is retryable.
- A partial calendar failure becomes `revalidation_required`; incomplete data
  can never produce `can_apply=true`.

Proposed, obsolete, applying, applied, and failed plans return `409`.
`can_apply=true` means only that the exact confirmed sessions passed a recent
complete read-only check. It does not mean Calendar events exist.

## Query window and selection

The service derives its UTC query window from the earliest session start and
latest session end. It adds 15 minutes of safety padding by default, or more
when the effective minimum break requires it. Configuration:

```text
SCHEDULE_PLAN_REVALIDATION_PADDING_MINUTES=15
SCHEDULE_PLAN_REVALIDATION_TTL_SECONDS=120
```

Calendar IDs stored in the plan's `busy_context_summary` are authoritative and
must still exist on the connection. If the snapshot contains no IDs, current
enabled selections are used. Explicit request IDs must exactly match the
effective original selection. Revalidation never silently falls back from
multiple calendars to primary only.

Every intentional request performs a fresh provider query. The original
snapshot timestamp is retained only to calculate audit age.

## Conflict semantics

Direct overlap uses half-open intervals `[start, end)`:

```text
session.start < busy.end AND busy.start < session.end
```

A session ending exactly when busy time begins does not directly overlap, and a
session beginning exactly when busy time ends does not directly overlap.
Minimum-break violations are evaluated separately and use a distinct reason
code. Sessions are never automatically rescheduled.

Busy references contain only interval boundaries, calendar ID, provider, and
source. Event titles, descriptions, attendees, raw event IDs, raw responses,
tokens, authorization headers, and OAuth state are neither persisted nor
returned.

The reserved-interval repository defines canonical reserving states and
supports excluding the current plan. With `include_internal_busy=true`,
revalidation loads reservations for the same user and padded query window,
excludes the plan being checked, and evaluates them alongside provider
FreeBusy. Internal overlaps use the reason
`session_overlaps_reserved_plan`; provider overlaps continue to use
`session_overlaps_provider_busy`.

## Freshness, hashing, and retries

Each audit stores a SHA-256 hash over:

- plan version;
- session ID;
- session order;
- UTC start and end;
- duration.

Lifecycle fields and future external event IDs are excluded. A successful
result includes `valid_until`, currently two minutes after `checked_at`. Future
apply logic must verify the latest valid audit, plan version, session hash,
plan update reference, TTL, connection health, and perform a final readiness
check.

`request_id` is optional. Reusing the same plan/request pair returns the
existing audit without another provider call, protecting HTTP retries.
Different IDs—or no ID—perform a fresh query and create a new audit.

The service records plan version, update timestamp, and session hash before
provider I/O, then locks and verifies the plan afterward. A changed status,
version, timestamp, or hash rejects the stale result with `409` and does not
persist or transition it.

## Current limitations

- Internal endpoints only; no production authentication.
- Internal reservations and provider FreeBusy are both checked; other internal
  busy sources are not implemented.
- No automatic rescheduling or session editing.
- No Google Calendar event creation, update, or deletion.
- No background refresh, push notifications, or two-way synchronization.
- Readiness expires with the short TTL.
- A future apply service must perform a final atomic readiness check.
