# ADR: Calendar synchronization domain foundation

Status: Accepted
Date: 2026-07-28

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260728_07`

## Context

Schedule plans can be confirmed and revalidated against Google Calendar, but
the application does not yet apply plans or reconcile later external changes.
Synchronization needs durable identity and audit records without coupling the
domain to Google API calls.

## Decision

- After a plan is applied, Google Calendar is the source of truth for actual
  session time, calendar placement, and event existence.
- The application remains the source of truth for the Task and its planning
  metadata.
- `ScheduledSession` is the synchronization unit. External identity and sync
  state live only in `CalendarEventMapping`.
- A task's sessions may target calendars in multiple Google accounts.
- An externally deleted session is recorded as a consistency issue and does not
  automatically return to backlog.
- Plans in `confirmed`, `revalidation_required`, `applying`, `applied`, and
  `partially_applied` states reserve their half-open session intervals.
- Synchronization is pull-first. Polling and webhook delivery are future
  transport concerns and are not part of this foundation.

The per-session write-target snapshot and mapping model can represent different
connections across a task's sessions. The current Google runtime still enforces
one connection per user/provider and has no write workflow, so multi-account
writing is a future application capability rather than implemented behavior.

## Consequences

The system can persist provider identity, diagnostics, external change history,
and immutable calendar-context snapshots without adding Google write scopes or
making external calls. Apply and reconciliation workflows can be added later
against pure consistency and deadline policies.

Push notifications, if introduced, will be an optional trigger for pull
reconciliation rather than a separate source of truth.
