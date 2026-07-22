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
