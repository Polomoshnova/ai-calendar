# Product architecture

Status: current product direction

Last verified against code: 2026-07-28

Latest verified Alembic revision: `20260728_07`

The canonical technical architecture is
[System overview](architecture/system-overview.md). This document records the
product-level boundaries and delivery sequence.

## Product intent

AI Calendar turns an unstructured work intention into a plan that a user can
inspect before it affects a calendar. The product should:

- preserve the user's original intent while making ambiguity visible;
- require explicit review of inferred or estimated fields;
- produce deterministic, explainable time placement;
- show work that could not be scheduled;
- preserve versioned planning history;
- avoid external calendar writes without explicit confirmation and a future
  apply action.

AI is an interpretation aid, not a scheduling authority. Once a
`ConfirmedTask` exists, availability and scheduling use ordinary deterministic
code.

## Current product flow

Implemented:

```text
natural language
  → structured TaskDraftV2
  → user review
  → ConfirmedTask
  → deterministic preview
  → persisted SchedulePlan
  → confirmation
  → fresh read-only revalidation
```

The steps are intentionally separable. The composed internal workflow currently
ends at preview; plan persistence and confirmation are explicit internal API
operations. Confirmation reserves the stored intervals but does not create
Google events.

## Current calendar position

Google Calendar is an availability source today. Runtime behavior includes
OAuth, encrypted credentials, calendar selection, FreeBusy, calendar-backed
preview, plan revalidation, and explicit plan application. OAuth requests
read-only discovery/availability scope plus the minimal event creation scope.

The synchronization foundation defines the later ownership model:

- after apply, Google will own actual event time, calendar placement, and event
  existence;
- the application will continue to own Task meaning, preferences, and planning
  history;
- `ScheduledSession` will be synchronized through `CalendarEventMapping`;
- pull reconciliation will be authoritative;
- external deletion will be recorded and will not automatically recreate
  backlog work.

These are accepted domain decisions, not current Google write behavior.

## Product surfaces

Current public API:

- Task CRUD
- stateless scheduling preview

Current development-only surfaces:

- AI intake and confirmation
- composed task-to-preview workflow
- scheduling lab and product scenarios
- development-user helper
- Google Calendar read integration
- SchedulePlan persistence, confirmation, and revalidation

There is no production authentication or user-facing product UI yet.

## Near-term sequence

1. Implement `ApplySchedulePlan` with explicit readiness and idempotency rules.
2. Create Google events and persist per-session mappings.
3. Reconcile mapped events through a pull-first workflow.
4. Handle external move, resize, calendar change, and deletion explicitly.
5. Define backlog semantics.
6. Add authenticated APIs and a basic product UI.
7. Evaluate push-assisted triggers only after pull reconciliation is reliable.

Outlook, Apple Calendar, and webhooks are not committed MVP deliverables in the
current codebase.
