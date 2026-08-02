# Product epics

## Epic 1 — Calendar Engine

Status: Complete

Purpose: complete the calendar lifecycle from scheduling through external
change processing.

Major completed capabilities include AI intake and confirmation, deterministic
scheduling preview, persisted SchedulePlans and reservations, Google OAuth and
multi-account connections, revalidation, ApplySchedulePlan, per-session
CalendarEventMapping persistence, pull synchronization, the pure External
Calendar Policy Engine, and atomic ExternalCalendarChange processing.

See the [system overview](architecture/system-overview.md), [domain
model](architecture/domain-model.md), and [implemented
sequences](architecture/sequences.md).

## Epic 2 — Planner

Status: In progress

Purpose: manage tasks that are unscheduled, partially scheduled, deferred, or
need user attention.

Initial scope:

- backlog domain;
- backlog reasons;
- rescheduling entry points;
- task state transitions;
- planner read models.

See the [architecture roadmap](architecture/roadmap.md), [state
models](architecture/state-models.md), and [product
decisions](architecture/product-decisions.md).

## Epic 3 — User Interface

Status: Planned

Purpose: provide the end-to-end user workflow for task input, review,
scheduling, backlog, calendar application, and synchronization states.

See the [system overview](architecture/system-overview.md) and [product
architecture](product-architecture.md).

## Epic 4 — Production Readiness

Status: Planned

Purpose: authentication hardening, CI, observability, deployment, background
synchronization, and operational safeguards.

See the [system overview](architecture/system-overview.md), [architecture
roadmap](architecture/roadmap.md), and [ADR index](architecture/adr/index.md).
