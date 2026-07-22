# AI Calendar API

Product foundation for an AI-assisted task scheduler. This milestone contains task
management and PostgreSQL persistence only; calendar, AI, authentication,
background jobs, and scheduling are deliberately out of scope.

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
