from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_intake.factory import get_ai_gateway
from app.ai_intake.gateway import AIProviderError, InvalidAIOutputError
from app.ai_intake.types import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    DraftDatetimeValue,
    DraftIntegerValue,
    DraftPriorityValue,
    DraftStringValue,
    DraftTimeOfDayValue,
    TaskDraft,
)
from app.core.config import get_settings
from app.core.database import Base
from app.internal.ai_intake_router import require_ai_gateway
from app.main import app


def empty_value() -> dict[str, object]:
    return {
        "value": None,
        "source": None,
        "confidence": None,
        "explanation": None,
        "requires_confirmation": False,
    }


class StubGateway:
    def __init__(self, result: TaskDraft | Exception) -> None:
        self.result = result
        self.received_text: str | None = None

    def analyze(self, text: str) -> TaskDraft:
        self.received_text = text
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def draft() -> TaskDraft:
    empty = empty_value()
    return TaskDraft(
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        title=DraftStringValue(
            value="Prepare release notes",
            source="user",
            confidence=1.0,
            explanation=None,
            requires_confirmation=False,
        ),
        description=DraftStringValue.model_validate(empty),
        duration=DraftIntegerValue(
            value=90,
            source="estimated",
            confidence=0.7,
            explanation="Estimated for a short set of release notes.",
            requires_confirmation=True,
        ),
        priority=DraftPriorityValue.model_validate(empty),
        earliest_start=DraftDatetimeValue.model_validate(empty),
        deadline=DraftDatetimeValue.model_validate(empty),
        preferred_time_of_day=DraftTimeOfDayValue.model_validate(empty),
        is_splittable=empty,
        minimum_session_minutes=DraftIntegerValue.model_validate(empty),
        maximum_sessions_per_day=DraftIntegerValue.model_validate(empty),
        proposed_steps=[],
        clarification_questions=[],
    )


def table_row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table)) or 0
        for table in Base.metadata.sorted_tables
    }


@pytest.fixture(autouse=True)
def configure_internal_ai(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StubGateway]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    gateway = StubGateway(draft())
    app.dependency_overrides[require_ai_gateway] = lambda: gateway
    yield gateway
    app.dependency_overrides.pop(require_ai_gateway, None)
    get_ai_gateway.cache_clear()
    get_settings.cache_clear()


def test_analyze_returns_v2_without_database_writes(
    client: TestClient,
    db_session: Session,
    configure_internal_ai: StubGateway,
) -> None:
    before = table_row_counts(db_session)

    response = client.post(
        "/internal/api/task-drafts/analyze",
        json={"text": "Prepare release notes by tomorrow afternoon"},
    )
    db_session.expire_all()
    payload = response.json()

    assert response.status_code == 200
    assert payload["title"]["value"] == "Prepare release notes"
    assert payload["prompt_version"] == PROMPT_VERSION
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "assumptions" not in payload
    assert "uncertainties" not in payload
    assert payload["clarification_questions"] == []
    assert configure_internal_ai.received_text == (
        "Prepare release notes by tomorrow afternoon"
    )
    assert table_row_counts(db_session) == before


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        ({}, 422),
        ({"text": ""}, 422),
        ({"text": "x", "unexpected": True}, 422),
        ({"text": "x" * 10_001}, 422),
    ],
)
def test_analyze_rejects_invalid_input(
    client: TestClient, payload: dict[str, Any], status_code: int
) -> None:
    response = client.post("/internal/api/task-drafts/analyze", json=payload)

    assert response.status_code == status_code


@pytest.mark.parametrize(
    "error",
    [
        AIProviderError("OpenAI is temporarily unavailable"),
        InvalidAIOutputError("bad output"),
    ],
)
def test_analyze_maps_ai_failures(
    client: TestClient,
    configure_internal_ai: StubGateway,
    error: Exception,
) -> None:
    configure_internal_ai.result = error

    response = client.post(
        "/internal/api/task-drafts/analyze", json={"text": "Create a task"}
    )

    assert response.status_code == 502


def test_analyze_is_unavailable_when_internal_tools_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    response = client.post(
        "/internal/api/task-drafts/analyze", json={"text": "Create a task"}
    )

    assert response.status_code == 404


def test_missing_openai_configuration_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides.pop(require_ai_gateway, None)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    get_ai_gateway.cache_clear()

    response = client.post(
        "/internal/api/task-drafts/analyze", json={"text": "Create a task"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "OPENAI_API_KEY is not configured"


def test_endpoint_dependency_is_not_database_or_scheduler() -> None:
    from inspect import getsource, signature

    from app.internal.ai_intake_router import analyze_task_draft

    assert "session" not in signature(analyze_task_draft).parameters
    source = getsource(analyze_task_draft)
    assert "scheduler" not in source
    assert "database" not in source
