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

- backlog domain (foundation implemented);
- backlog reasons;
- rescheduling entry points;
- task state transitions;
- planner read models.

The current foundation keeps backlog separate from `TaskStatus.pending`,
supports partial unscheduled duration and explicit lifecycle transitions, and
records origin separately from reason. `other` requires an explanatory note;
temporary calendar failures do not enter backlog automatically. The foundation
does not add API, UI, automatic scheduling, or background review behavior.

See the [architecture roadmap](architecture/roadmap.md), [state
models](architecture/state-models.md), and [product
decisions](architecture/product-decisions.md).

## Epic 3 — User Interface

Status: In progress

Purpose: provide the end-to-end user workflow for task input, review,
scheduling, backlog, calendar application, and synchronization states.

The initial foundation is a read-only internal Planner screen over the Planner
API. It establishes the frontend/API boundary, development-user selection, and
loading, error, empty, and section rendering states without product mutations.
Later PRs will add the workflows listed above incrementally.

See the [Planner UI](planner-ui.md), [system overview](architecture/system-overview.md),
and [product architecture](product-architecture.md).

## Epic 4 — Production Readiness

Status: Planned

Purpose: authentication hardening, CI, observability, deployment, background
synchronization, and operational safeguards.

See the [system overview](architecture/system-overview.md), [architecture
roadmap](architecture/roadmap.md), and [ADR index](architecture/adr/index.md).
