# Roadmap

Last verified against code: 2026-08-02

Latest verified Alembic revision: `20260802_11`

This roadmap records dependency order, not delivery dates.

## Epic 1 — Calendar Engine

Status: Complete

Completed components include Google read-only integration, multi-account
connections, SchedulePlan reservations and revalidation, ApplySchedulePlan,
per-session event mapping, explicit pull synchronization, the pure External
Calendar Policy Engine, and atomic external-change processing.

### Implemented capability detail

| Capability | Purpose | Dependencies | Non-goals |
|---|---|---|---|
| AI Intake and confirmation | Convert natural language into reviewed `ConfirmedTask` data. | OpenAI structured output, Pydantic contracts. | AI does not choose slots or persist a Task automatically. |
| Deterministic availability, scheduling, and preview | Produce explainable placement and unscheduled outcomes. | Tasks, preferences, normalized busy intervals. | No provider calls inside the scheduler. |
| SchedulePlan persistence and confirmation | Preserve exact, versioned proposals and user approval. | PostgreSQL, SQLAlchemy, Alembic. | Confirmation does not create Google events. |
| Google read-only integration | Use real calendar availability. | OAuth, encrypted credentials, calendar selection, FreeBusy. | No event content or writes. |
| SchedulePlan revalidation | Check immutable sessions against fresh provider busy data. | Confirmed plan, active connection, FreeBusy. | No rescheduling or apply. |
| Reserved interval repository | Define which persisted plans block time. | SchedulePlan statuses and sessions. | Integrated with database-backed preview and revalidation. |
| Calendar synchronization | Apply plans, map events, detect external changes, evaluate pure policy, and atomically process decisions. | Revisions `20260728_07` through `20260731_10`. | No polling, webhooks, automatic rescheduling, or backlog transition. |

## Epic 2 — Planner

Status: In progress (current focus)

The Backlog Domain Foundation is implemented: typed reasons and statuses,
partial-work accounting, lifecycle transitions, persistence, concurrency
constraints, explicit origin semantics, and due-for-review repository support.
Temporary calendar failures remain integration errors, and partial-work
accounting reuses the Calendar Engine reservation policy. The internal
[Backlog API](../backlog.md) now exposes listing, creation, and explicit
lifecycle operations. Neither the domain nor API invokes scheduling
automatically.

Next scope, in dependency order:

1. Task lifecycle refinement.
2. Manual retry and replanning actions.
3. Planner-oriented read models.

## Epic 3 — User Interface

Status: Planned

Build the authenticated product workflow after Planner contracts are stable.

## Epic 4 — Production Readiness

Status: Planned

Harden authentication, CI, observability, deployment, background
synchronization, and operational safeguards. No delivery dates are assigned.

See the [epic overview](../epics.md) for navigation across the architecture
documents.
