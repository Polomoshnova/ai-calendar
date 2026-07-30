# AI Calendar API

## Project overview

AI Calendar is a FastAPI service for turning natural-language work intentions
into reviewable calendar plans. A user can submit free-form task text, receive a
strict structured `TaskDraftV2`, clarify or edit inferred fields, confirm a clean
`ConfirmedTask`, and pass that task into deterministic availability and
scheduling logic. The scheduler produces an explainable preview; a separate
operation can persist its exact blocks as a versioned `SchedulePlan` made of
`ScheduledSession` records.

The primary implemented flow is:

```text
natural-language input
  → AI Intake
  → TaskDraftV2
  → clarification and review
  → ConfirmedTask
  → deterministic scheduling preview
  → persisted SchedulePlan
  → confirmation
  → read-only revalidation
```

AI interpretation and scheduling are deliberately separate. The model converts
ambiguous language into typed input with provenance and confirmation flags; it
does not select calendar slots. Availability calculation and scheduling are
pure, deterministic components, so the same confirmed inputs produce
inspectable reason codes, scores, warnings, and unscheduled outcomes without an
AI provider making planning decisions.

The current Google Calendar runtime integration is read-only. It supports OAuth,
encrypted credentials, calendar listing and selection, FreeBusy queries,
calendar-backed preview, and fresh SchedulePlan revalidation. It requests only
the `calendar.readonly` scope and does not create, update, delete, poll, or watch
Google events. Persistence and pure policies needed for later apply and
pull-reconciliation workflows exist, but no runtime workflow uses them yet.

## Current capabilities

### Implemented

- [x] Natural-language AI task intake with versioned structured output
- [x] Typed clarification, review, and confirmation into `ConfirmedTask`
- [x] Persistent Task CRUD and scheduling preferences
- [x] Timezone-aware availability and deterministic scheduling
- [x] Stateless scheduling preview with diagnostics
- [x] Versioned `SchedulePlan` and ordered `ScheduledSession` persistence
- [x] Idempotent plan creation, confirmation, obsoletion, read, and listing
- [x] Confirmed-plan revalidation against fresh Google FreeBusy data
- [x] Revalidation against reservations from other active plans
- [x] Interval reservations for plans in `confirmed`,
  `revalidation_required`, `applying`, `applied`, and `partially_applied`
- [x] Database-backed preview treats those reserved sessions as busy time
- [x] Google OAuth with hashed state and encrypted credentials
- [x] Calendar listing, selection, FreeBusy, and calendar-backed preview
- [x] Calendar synchronization domain and persistence foundation

### Foundation implemented, runtime flow pending

- [x] One `CalendarEventMapping` per `ScheduledSession`, with provider event
  identity, etag, provider timestamps, sync status, and diagnostics
- [x] `ExternalCalendarChange` audit records for detected provider changes
- [x] Typed busy-source and per-session write-target snapshots
- [x] Order-independent SHA-256 calendar-context hashing
- [x] Pure consistency checking for overlaps, minimum breaks, order, deadline,
  and externally deleted sessions
- [x] Pure deadline extension policy for externally moved sessions
- [x] Multi-connection/account write-target representation in snapshots
- [x] Half-open reserved-interval repository query with `exclude_plan_id`

These components are available to future application services. They are not
connected to Google event creation, polling, or reconciliation, and the current
Google connection model still permits one connection per user/provider.

### Planned

- [ ] `ApplySchedulePlan`
- [ ] Google Calendar event creation
- [ ] Pull reconciliation of applied sessions
- [ ] Handling externally moved, resized, and deleted Google events
- [ ] Explicit backlog behavior
- [ ] Basic authenticated product UI
- [ ] Optional push-assisted synchronization after pull reconciliation is
  established

## Architecture principles

- AI produces structured, reviewable input; it does not choose calendar slots.
- Availability and scheduling are deterministic and provider-neutral.
- A `SchedulePreview` is transient and distinct from a persisted
  `SchedulePlan`.
- Confirming a plan reserves its half-open session intervals `[start, end)`;
  confirmation does not create Google events.
- Google Calendar is read-only at the current runtime integration stage.
- After a future apply operation, Google Calendar will be the source of truth
  for actual event time, calendar placement, and event existence.
- The application remains the source of truth for Task metadata and planning
  history.
- `ScheduledSession` is the future synchronization unit; external identity and
  sync state live in `CalendarEventMapping`, not on the session.
- Synchronization is pull-first. Push notifications may later reduce latency,
  but are not the reconciliation authority.

## Local development

### Prerequisites

- Python 3.12
- Docker with Docker Compose

### Environment and dependencies

```bash
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### PostgreSQL and migrations

```bash
docker compose up -d postgres
docker compose ps
alembic upgrade head
```

The container exposes PostgreSQL at `localhost:5432`. The latest migration file
is `20260728_05_calendar_sync_domain_foundation.py`; its Alembic revision is
`20260728_07`, following `20260728_06`.

Useful migration commands:

```bash
alembic current
alembic heads
alembic downgrade -1
```

### Application startup

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level debug
```

Swagger UI is available at <http://127.0.0.1:8000/docs> and the generated
OpenAPI document at <http://127.0.0.1:8000/openapi.json>.

Public Task and preview routes are under `/api/v1`. AI intake, confirmation,
workflow, SchedulePlan, revalidation, calendar, development-user, and scheduling
lab routes are internal development tools. Enable them explicitly:

```bash
ENABLE_INTERNAL_TOOLS=true \
  uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level debug
```

Then open <http://127.0.0.1:8000/internal/scheduling-lab>. Internal tooling has
no production authentication and must not be exposed publicly.

### Quality checks

Create and migrate the dedicated test database once:

```bash
docker compose exec postgres createdb -U ai_calendar ai_calendar_test
export TEST_DATABASE_URL="postgresql+psycopg://ai_calendar:ai_calendar@localhost:5432/ai_calendar_test"
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
```

Run the verified checks:

```bash
source .venv/bin/activate
pytest
ruff check .
ruff format --check .
mypy app
```

Integration tests require `TEST_DATABASE_URL`; they reject non-PostgreSQL URLs
and database names that neither start with `test_` nor end with `_test`.
The current baseline is 296 passing tests; Ruff, strict mypy for `app`, and
Alembic upgrade/downgrade validation also pass.

Stop local services with:

```bash
docker compose down
```

## Documentation map

- [System overview](docs/architecture/system-overview.md) — canonical current
  architecture, boundaries, flow, lifecycle, persistence, and roadmap
- [Architecture index](docs/architecture/index.md) — entry point for domain,
  state, sequence, product-decision, roadmap, and OpenAPI documentation
- [Domain model](docs/architecture/domain-model.md)
- [State models](docs/architecture/state-models.md)
- [Sequence diagrams](docs/architecture/sequences.md)
- [Product decisions](docs/architecture/product-decisions.md)
- [Architecture roadmap](docs/architecture/roadmap.md)
- [OpenAPI documentation gaps](docs/architecture/openapi-gaps.md)
- [ADR index](docs/architecture/adr/index.md)
- [Product architecture](docs/product-architecture.md) — product principles,
  current scope, and near-term delivery sequence
- [Historical architecture audit](docs/architecture-audit.md) — point-in-time
  review of the earlier scheduling foundation
- [Calendar synchronization ADR](docs/adr-calendar-synchronization-domain.md) —
  source-of-truth and synchronization decisions
- [Calendar synchronization domain](docs/calendar-synchronization-domain.md) —
  models, snapshots, policies, and runtime boundary
- [AI intake](docs/ai-intake.md)
- [Task confirmation](docs/task-confirmation.md)
- [Task-to-preview workflow](docs/task-to-schedule-preview-workflow.md)
- [Schedule plans](docs/schedule-plans.md)
- [SchedulePlan revalidation](docs/schedule-plan-revalidation.md)
- [Google Calendar read-only integration](docs/google-calendar-readonly.md)
- [Internal development users](docs/internal-dev-users.md)
