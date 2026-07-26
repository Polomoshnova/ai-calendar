from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base


def value(
    item: object = None,
    *,
    source: str | None = None,
    confirmation: bool = False,
) -> dict[str, object]:
    return {
        "value": item,
        "source": source,
        "confidence": 1.0 if source == "user" else (0.7 if source else None),
        "explanation": (
            "AI estimate." if source in {"estimated", "inferred", "default"} else None
        ),
        "requires_confirmation": confirmation,
    }


def draft() -> dict[str, Any]:
    return {
        "title": value("Prepare presentation", source="user"),
        "description": value(),
        "duration": value(240, source="estimated", confirmation=True),
        "priority": value(),
        "earliest_start": value(),
        "deadline": value(
            "2026-07-31T23:59:59+02:00",
            source="inferred",
            confirmation=True,
        ),
        "preferred_time_of_day": value(),
        "is_splittable": value(True, source="estimated", confirmation=True),
        "minimum_session_minutes": value(),
        "maximum_sessions_per_day": value(),
        "proposed_steps": [],
        "clarification_questions": [],
        "prompt_version": "ai-intake.task-draft.v2",
        "schema_version": "task-draft.schema.v2",
    }


def table_row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table)) or 0
        for table in Base.metadata.sorted_tables
    }


@pytest.fixture(autouse=True)
def enable_internal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_valid_confirmation_returns_200_without_database_writes(
    client: TestClient, db_session: Session
) -> None:
    before = table_row_counts(db_session)

    response = client.post(
        "/internal/api/task-drafts/confirm",
        json={
            "draft": draft(),
            "review": {
                "mode": "explicit",
                "duration": {"decision": "edited", "value": 360},
                "deadline": {"decision": "accepted"},
                "is_splittable": {"decision": "accepted"},
                "proposed_steps": [],
                "confirmation_note": "User increased the estimate.",
            },
        },
    )
    db_session.expire_all()

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["title"] == "Prepare presentation"
    assert payload["task"]["duration_minutes"] == 360
    assert payload["task"]["deadline"] == "2026-07-31T23:59:59+02:00"
    assert "duration" in payload["audit"]["edited_fields"]
    assert "confidence" not in payload["task"]
    assert table_row_counts(db_session) == before


def test_incomplete_explicit_review_returns_422(client: TestClient) -> None:
    response = client.post(
        "/internal/api/task-drafts/confirm",
        json={"draft": draft(), "review": {"mode": "explicit"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Missing confirmation for field: duration"


def test_invalid_edit_returns_422(client: TestClient) -> None:
    response = client.post(
        "/internal/api/task-drafts/confirm",
        json={
            "draft": draft(),
            "review": {
                "duration": {"decision": "edited", "value": 0},
            },
        },
    )

    assert response.status_code == 422


def test_confirmation_is_unavailable_when_internal_tools_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    response = client.post(
        "/internal/api/task-drafts/confirm",
        json={
            "draft": draft(),
            "review": {"mode": "accept_unreviewed"},
        },
    )

    assert response.status_code == 404


def test_confirmation_endpoint_has_no_external_dependencies() -> None:
    from inspect import getsource, signature

    from app.internal.task_confirmation_router import confirm_task_draft

    assert set(signature(confirm_task_draft).parameters) == {"data", "_enabled"}
    source = getsource(confirm_task_draft)
    for forbidden in ("database", "session", "scheduler", "gateway", "provider"):
        assert forbidden not in source
