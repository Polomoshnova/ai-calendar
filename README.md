# AI Calendar API

Product foundation for an AI-assisted task scheduler. This milestone contains task
management, PostgreSQL persistence, and a pure deterministic scheduling core.
Calendar providers, AI, authentication, background jobs, plan persistence, and
calendar writes are deliberately out of scope.

## Prerequisites

- Python 3.12
- Docker with Docker Compose

## Setup

```bash
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
docker compose up -d postgres
alembic upgrade head
```

The PostgreSQL container listens on `localhost:5432`. Wait for it to become
healthy before applying migrations:

```bash
docker compose ps
```

Authentication is intentionally not implemented. To exercise Task CRUD manually,
create a development user first:

```bash
docker compose exec postgres psql -U ai_calendar -d ai_calendar -c \
  "INSERT INTO users (id, email, timezone) VALUES ('11111111-1111-1111-1111-111111111111', 'owner@example.com', 'Europe/Warsaw');"
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs> for the API documentation. Task endpoints are
available under `/api/v1/tasks`.

## Internal scheduling lab

The scheduling lab is a development-only product-validation interface. It has no
authentication and is disabled by default. Enable it explicitly:

```bash
ENABLE_INTERNAL_TOOLS=true uvicorn app.main:app --reload
```

Then open <http://127.0.0.1:8000/internal/scheduling-lab>. When
`ENABLE_INTERNAL_TOOLS` is false, the page, assets, and internal API return 404.
Do not enable this interface in a publicly accessible environment.

The lab has two deliberately separate modes:

- **Existing user** loads persisted tasks and preferences. Task actions use the
  existing Task API, and preferences are persisted only when **Save preferences**
  is clicked.
- **Product scenario** loads files from `tests/product/examples/` into temporary
  browser state. Edits, previews, busy intervals, and preferences remain
  stateless and create no users, tasks, preferences, plans, or review records.

Product-validation workflow:

1. Start the application with internal tools enabled.
2. Open `/internal/scheduling-lab`.
3. Select an existing user or load a product scenario.
4. Generate the schedule.
5. Review the local-time day-by-day result and detailed scheduler output.
6. Assign a score and verdict and record any observed problems.
7. Download the normalized scenario inputs, generated result, and review as JSON.
8. Store reviewed exports under `local/scheduling-reviews/`, which Git ignores,
   unless a result is deliberately adapted into `tests/product/`.

The internal preview calls the same pure availability and scheduling
orchestration as the public stateless preview. Exported JSON contains the exact
normalized inputs used for the displayed generation and contains no application
configuration or environment values.

### Internal AI intake

With internal tools enabled, analyze natural-language task text without creating
database records:

```bash
export OPENAI_API_KEY="your-key"
curl -X POST http://127.0.0.1:8000/internal/api/task-drafts/analyze \
  -H 'Content-Type: application/json' \
  -d '{"text":"Prepare release notes by tomorrow afternoon"}'
```

The endpoint uses a versioned prompt and OpenAI Structured Outputs, validates the
provider response against the `TaskDraft` JSON Schema and domain rules, and
returns a temporary draft. It does not create users, tasks, preferences, or
calendar records, and it never invokes availability or the scheduler.

Configuration:

```text
OPENAI_MODEL=gpt-5.6
AI_INTAKE_PROMPT_VERSION=ai-intake.task-draft.v2
AI_INTAKE_DEFAULT_TIMEZONE=UTC
AI_INTAKE_TIMEOUT_SECONDS=30
```

The endpoint returns 404 when internal tools are disabled, 503 when the API key
is missing, and 502 for provider or invalid-output failures.

The response uses `task-draft.schema.v2`. Each interpreted field contains
`value`, `source`, `confidence`, `explanation`, and
`requires_confirmation`. For example:

```json
{
  "title": {
    "value": "Подготовить презентацию",
    "source": "user",
    "confidence": 1.0,
    "explanation": null,
    "requires_confirmation": false
  },
  "duration": {
    "value": 120,
    "source": "estimated",
    "confidence": 0.65,
    "explanation": "Оценка времени на подготовку презентации.",
    "requires_confirmation": true
  },
  "schema_version": "task-draft.schema.v2"
}
```

The real response also contains every field in the strict contract. A draft is
an application-layer AI interpretation object, not a persisted domain entity.
The endpoint does not invoke the database or scheduler.

### Internal task confirmation

Review an AI draft without persistence or scheduler calls:

```bash
curl -X POST http://127.0.0.1:8000/internal/api/task-drafts/confirm \
  -H 'Content-Type: application/json' \
  -d @confirmation-request.json
```

The request contains a complete `TaskDraftV2` and a typed `DraftReview`. The
response contains a clean `ConfirmedTask` and an in-memory
`ConfirmationAudit`. Review mode defaults to `explicit`; invalid or incomplete
reviews return 422. See [Task Confirmation Layer](docs/task-confirmation.md) for
decision semantics, modes, and examples.

### Internal end-to-end workflow

`POST /internal/api/workflows/task-to-schedule-preview` runs AI Intake,
confirmation, deterministic mapping, and the shared stateless scheduling preview
in one internal request. It returns the draft, confirmation audit, normalized
scheduler input, schedule response, and a safe structured trace. Nothing is
persisted and no calendar API is called.

See [Internal task-to-schedule-preview workflow](docs/task-to-schedule-preview-workflow.md)
for the complete contract, deterministic AI context, error codes, replay
fixtures, mapping rules, and limitations.

## Working hours

Working hours are stored as IANA-local wall-clock times with all seven weekdays
present. An empty list means that weekday is unavailable:

```json
{
  "monday": [{"start": "09:00", "end": "18:00"}],
  "tuesday": [{"start": "09:00", "end": "18:00"}],
  "wednesday": [{"start": "09:00", "end": "18:00"}],
  "thursday": [{"start": "09:00", "end": "18:00"}],
  "friday": [{"start": "09:00", "end": "18:00"}],
  "saturday": [],
  "sunday": []
}
```

`User.timezone` is the only timezone authority. Working-hour values do not carry
offsets; the availability engine resolves them in that IANA timezone for each
date, including DST transitions.

## Deterministic scheduler demonstration

Run a fixed, realistic example without a database or API:

```bash
python -m app.scheduling.demo
```

The command prints normalized free intervals, scheduled blocks, structured reason
codes and scores, unscheduled tasks, warnings, and the scheduler version.

## Stateless schedule preview

`POST /api/v1/scheduling/preview` loads pending tasks and preferences for a user,
combines them with temporary busy intervals, and returns an in-memory scheduling
proposal. The endpoint never creates plans, blocks, audit events, or cache rows.

The planning window is limited to 31 days. All input datetimes must include a UTC
offset. If a user has no `UserPreferences` row, the service uses Monday–Friday
09:00–18:00 working hours, no minimum break, no cutoff, and a 15-minute default
minimum session without writing those defaults to the database.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scheduling/preview \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "11111111-1111-1111-1111-111111111111",
    "planning_window": {
      "start": "2026-07-20T08:00:00Z",
      "end": "2026-07-20T18:00:00Z"
    },
    "busy_intervals": [
      {
        "start": "2026-07-20T12:00:00Z",
        "end": "2026-07-20T13:00:00Z"
      }
    ]
  }'
```

## Quality checks

Create and migrate a separate test database. This command is needed once for a
new PostgreSQL volume:

```bash
docker compose exec postgres createdb -U ai_calendar ai_calendar_test
```

Export the dedicated test URL and apply migrations to that database:

```bash
export TEST_DATABASE_URL="postgresql+psycopg://ai_calendar:ai_calendar@localhost:5432/ai_calendar_test"
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
```

Then run all checks:

```bash
source .venv/bin/activate
pytest
ruff check .
ruff format --check .
mypy app tests
```

Integration tests require `TEST_DATABASE_URL` and never fall back to
`DATABASE_URL`. They refuse to start unless the URL uses PostgreSQL and its
database name starts with `test_` or ends with `_test`. Test cleanup therefore
cannot target the normal `ai_calendar` development database.

## Migrations

```bash
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "describe change"
```

## Stop local services

```bash
docker compose down
```
