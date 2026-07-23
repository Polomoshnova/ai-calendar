from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import Base
from app.models import User


@pytest.fixture(autouse=True)
def enable_internal_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def scenario_preview_payload(
    client: TestClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = client.get("/internal/api/scenarios/01_single_task.json").json()
    payload = {
        "mode": "product_scenario",
        "timezone": scenario["user_preferences"]["timezone"],
        "planning_window": scenario["planning_window"],
        "busy_intervals": scenario["busy_intervals"],
        "preferences": scenario["user_preferences"],
        "tasks": scenario["tasks"],
    }
    return scenario, payload


def table_row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table)) or 0
        for table in Base.metadata.sorted_tables
    }


def test_internal_page_available_when_enabled(client: TestClient) -> None:
    response = client.get("/internal/scheduling-lab")

    assert response.status_code == 200
    assert "Internal Scheduling Lab" in response.text
    assert "Development-only tool" in response.text
    assert "/internal/static/scheduling_lab.js" in response.text
    assert "A. User and preferences" in response.text
    assert "D. Generated schedule and review" in response.text


def test_internal_page_and_api_unavailable_when_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    assert client.get("/internal/scheduling-lab").status_code == 404
    assert client.get("/internal/static/scheduling_lab.js").status_code == 404
    assert client.get("/internal/api/scenarios").status_code == 404
    assert client.post("/internal/api/scheduling/preview", json={}).status_code == 404


def test_scenario_list_and_valid_load(client: TestClient) -> None:
    scenarios = client.get("/internal/api/scenarios")

    assert scenarios.status_code == 200
    assert len(scenarios.json()) == 10
    assert scenarios.json()[0]["filename"] == "01_single_task.json"

    loaded = client.get("/internal/api/scenarios/01_single_task.json")
    assert loaded.status_code == 200
    assert loaded.json()["name"] == "Single task"


@pytest.mark.parametrize(
    "name",
    ["does-not-exist.json", "..%2F01_single_task.json", "README.md"],
)
def test_invalid_scenario_name_rejected(client: TestClient, name: str) -> None:
    response = client.get(f"/internal/api/scenarios/{name}")

    assert response.status_code in {404, 422}


def test_scenario_preview_is_stateless_and_correlates_titles(
    client: TestClient, db_session: Session
) -> None:
    scenario, payload = scenario_preview_payload(client)
    before = table_row_counts(db_session)

    response = client.post("/internal/api/scheduling/preview", json=payload)
    db_session.expire_all()

    assert response.status_code == 200
    body = response.json()
    assert body["scheduler_version"] == "2a.1"
    assert table_row_counts(db_session) == before
    titles = {task["id"]: task["title"] for task in scenario["tasks"]}
    assert body["task_titles"] == titles
    assert all(
        body["task_titles"][block["task_id"]] == titles[block["task_id"]]
        for block in body["scheduled_blocks"]
    )


def test_existing_user_preview_is_still_stateless(
    client: TestClient, db_session: Session, user: User
) -> None:
    before = table_row_counts(db_session)
    payload = {
        "mode": "existing_user",
        "user_id": str(user.id),
        "planning_window": {
            "start": "2026-07-20T08:00:00Z",
            "end": "2026-07-20T18:00:00Z",
        },
        "busy_intervals": [],
    }

    response = client.post("/internal/api/scheduling/preview", json=payload)
    db_session.expire_all()

    assert response.status_code == 200
    assert table_row_counts(db_session) == before


def test_missing_preferences_are_reported_and_not_created(
    client: TestClient, db_session: Session
) -> None:
    user = User(email="no-preferences@example.com", timezone="UTC")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    before = table_row_counts(db_session)

    response = client.get(f"/internal/api/users/{user.id}/preferences")

    assert response.status_code == 200
    assert response.json()["has_stored_preferences"] is False
    assert response.json()["preferences"]["minimum_break_minutes"] == 0
    assert table_row_counts(db_session) == before


def test_invalid_internal_preview_input_returns_clear_validation(
    client: TestClient,
) -> None:
    response = client.post(
        "/internal/api/scheduling/preview",
        json={
            "mode": "product_scenario",
            "planning_window": {
                "start": "2026-07-20T18:00:00Z",
                "end": "2026-07-20T08:00:00Z",
            },
            "busy_intervals": [],
            "tasks": [],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]


def test_review_export_is_stateless_and_rejects_secrets(
    client: TestClient, db_session: Session
) -> None:
    scenario, preview_payload = scenario_preview_payload(client)
    preview = client.post(
        "/internal/api/scheduling/preview", json=preview_payload
    ).json()
    before = table_row_counts(db_session)
    export_request = {
        "normalized_inputs": {
            "user_timezone": scenario["user_preferences"]["timezone"],
            "planning_window": scenario["planning_window"],
            "preferences_used": scenario["user_preferences"],
            "tasks": scenario["tasks"],
            "busy_intervals": scenario["busy_intervals"],
        },
        "generated_preview_result": preview,
        "review": {
            "score": 5,
            "verdict": "logical",
            "notes": "Manual review",
            "observed_problems": [],
        },
    }

    response = client.post("/internal/api/review-export", json=export_request)

    assert response.status_code == 200
    assert response.json()["tasks"] == scenario["tasks"]
    assert "exported_at" in response.json()
    assert table_row_counts(db_session) == before

    export_request["normalized_inputs"]["tasks"][0]["secret"] = "do-not-export"
    rejected = client.post("/internal/api/review-export", json=export_request)
    assert rejected.status_code == 422
