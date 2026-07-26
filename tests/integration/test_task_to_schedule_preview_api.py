from collections.abc import Generator
from datetime import datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_intake.factory import get_ai_gateway
from app.ai_intake.gateway import (
    AIGateway,
    AIProviderError,
    InvalidAIOutputError,
)
from app.ai_intake.types import TaskDraftV2
from app.core.config import get_settings
from app.core.database import Base
from app.internal.ai_intake_router import require_ai_gateway
from app.main import app


def value(
    item: object = None,
    *,
    source: str | None = None,
    confirmation: bool = False,
) -> dict[str, object]:
    return {
        "value": item,
        "source": source,
        "confidence": 1.0 if source == "user" else (0.8 if source else None),
        "explanation": (
            "Derived or estimated value." if source not in {None, "user"} else None
        ),
        "requires_confirmation": confirmation,
    }


def draft() -> TaskDraftV2:
    return TaskDraftV2.model_validate(
        {
            "title": value("Prepare presentation", source="user"),
            "description": value(),
            "duration": value(120, source="estimated", confirmation=True),
            "priority": value(),
            "earliest_start": value(),
            "deadline": value(
                "2026-07-31T17:00:00+02:00",
                source="inferred",
                confirmation=True,
            ),
            "preferred_time_of_day": value(),
            "is_splittable": value(True, source="estimated", confirmation=True),
            "minimum_session_minutes": value(30, source="estimated", confirmation=True),
            "maximum_sessions_per_day": value(),
            "proposed_steps": [],
            "clarification_questions": [],
            "prompt_version": "ai-intake.task-draft.v2",
            "schema_version": "task-draft.schema.v2",
        }
    )


def workflow_request(
    review: dict[str, object] | None = None,
) -> dict[str, Any]:
    weekday = [{"start": "09:00", "end": "18:00"}]
    return {
        "text": "Prepare presentation by Friday",
        "review": review
        or {
            "mode": "explicit",
            "duration": {"decision": "edited", "value": 180},
            "deadline": {"decision": "accepted"},
            "is_splittable": {"decision": "edited", "value": False},
            "minimum_session_minutes": {"decision": "accepted"},
        },
        "ai_context": {
            "current_datetime": "2026-07-26T15:00:00+02:00",
            "timezone": "Europe/Warsaw",
        },
        "scheduling_context": {
            "window_start": "2026-07-27T08:00:00+02:00",
            "window_end": "2026-07-31T20:00:00+02:00",
            "timezone": "Europe/Warsaw",
            "busy_intervals": [],
            "preferences": {
                "timezone": "Europe/Warsaw",
                "working_hours": {
                    "monday": weekday,
                    "tuesday": weekday,
                    "wednesday": weekday,
                    "thursday": weekday,
                    "friday": weekday,
                    "saturday": [],
                    "sunday": [],
                },
                "minimum_break_minutes": 15,
                "default_minimum_session_minutes": 15,
            },
            "existing_pending_tasks": [],
        },
        "include_trace": True,
    }


class StubGateway:
    def __init__(self) -> None:
        self.result: TaskDraftV2 | Exception = draft()
        self.calls = 0

    def analyze(
        self,
        text: str,
        *,
        current_time: datetime | None = None,
        user_timezone: str | None = None,
    ) -> TaskDraftV2:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def table_row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table)) or 0
        for table in Base.metadata.sorted_tables
    }


@pytest.fixture(autouse=True)
def configure_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[StubGateway, None, None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    gateway = StubGateway()
    app.dependency_overrides[require_ai_gateway] = lambda: cast(AIGateway, gateway)
    yield gateway
    app.dependency_overrides.pop(require_ai_gateway, None)
    get_ai_gateway.cache_clear()
    get_settings.cache_clear()


def test_workflow_endpoint_returns_all_stages_without_database_writes(
    client: TestClient,
    db_session: Session,
    configure_workflow: StubGateway,
) -> None:
    before = table_row_counts(db_session)

    response = client.post(
        "/internal/api/workflows/task-to-schedule-preview",
        json=workflow_request(),
    )
    db_session.expire_all()

    assert response.status_code == 200
    payload = response.json()
    assert payload["draft"]["schema_version"] == "task-draft.schema.v2"
    assert payload["confirmation"]["task"]["duration_minutes"] == 180
    assert payload["scheduler_input"]["task"]["duration_minutes"] == 180
    assert payload["scheduler_input"]["task"]["is_splittable"] is False
    assert payload["scheduler_input"]["task"]["value_sources"] == {
        "priority": "scheduler_default",
        "preferred_time_of_day": "scheduler_default",
        "minimum_session_minutes": "confirmed",
        "maximum_sessions_per_day": "scheduler_default",
        "is_splittable": "confirmed",
    }
    mapping_trace = next(
        entry for entry in payload["trace"] if entry["stage"] == "scheduler_mapping"
    )
    assert mapping_trace["metadata"]["defaulted_fields"] == [
        "priority",
        "preferred_time_of_day",
        "maximum_sessions_per_day",
    ]
    assert payload["schedule_preview"]["scheduler_version"]
    assert len(payload["schedule_preview"]["scheduled_blocks"]) == 1
    assert payload["schedule_preview"]["unscheduled_tasks"] == []
    assert payload["workflow_version"] == "task-to-schedule-preview.v1"
    assert len(payload["trace"]) == 4
    assert configure_workflow.calls == 1
    assert table_row_counts(db_session) == before


def test_incomplete_confirmation_returns_structured_422(
    client: TestClient,
) -> None:
    response = client.post(
        "/internal/api/workflows/task-to-schedule-preview",
        json=workflow_request({"mode": "explicit"}),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "confirmation_failed"
    assert detail["stage"] == "confirmation"


def test_rejected_duration_returns_structured_422(client: TestClient) -> None:
    review = cast(dict[str, object], workflow_request()["review"])
    review["duration"] = {"decision": "rejected"}

    response = client.post(
        "/internal/api/workflows/task-to-schedule-preview",
        json=workflow_request(review),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "missing_confirmed_duration"
    assert detail["stage"] == "scheduler_mapping"


@pytest.mark.parametrize(
    "error",
    [
        AIProviderError("sensitive provider detail"),
        InvalidAIOutputError("sensitive invalid payload detail"),
    ],
)
def test_ai_failure_returns_502(
    client: TestClient,
    configure_workflow: StubGateway,
    error: Exception,
) -> None:
    configure_workflow.result = error

    response = client.post(
        "/internal/api/workflows/task-to-schedule-preview",
        json=workflow_request(),
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_intake_failed"
    assert "sensitive" not in response.text


def test_missing_provider_configuration_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides.pop(require_ai_gateway, None)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    get_ai_gateway.cache_clear()

    response = client.post(
        "/internal/api/workflows/task-to-schedule-preview",
        json=workflow_request(),
    )

    assert response.status_code == 503


def test_workflow_is_unavailable_when_internal_tools_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    response = client.post(
        "/internal/api/workflows/task-to-schedule-preview",
        json=workflow_request(),
    )

    assert response.status_code == 404


def test_workflow_endpoint_is_visible_and_has_no_database_dependency() -> None:
    from inspect import signature

    from app.internal.workflow_router import task_to_schedule_preview

    assert "/internal/api/workflows/task-to-schedule-preview" in app.openapi()["paths"]
    components = app.openapi()["components"]["schemas"]
    snapshot_schema = components["SchedulerTaskSnapshot"]
    assert snapshot_schema["properties"]["value_sources"] == {
        "$ref": "#/components/schemas/SchedulerTaskValueSources"
    }
    assert set(components["SchedulerTaskValueSources"]["properties"]) == {
        "priority",
        "preferred_time_of_day",
        "minimum_session_minutes",
        "maximum_sessions_per_day",
        "is_splittable",
    }
    assert set(signature(task_to_schedule_preview).parameters) == {
        "data",
        "_enabled",
        "gateway",
    }
