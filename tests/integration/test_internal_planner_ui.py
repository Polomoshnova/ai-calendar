from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def enable_internal_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_planner_page_and_assets_are_available(client: TestClient) -> None:
    response = client.get("/internal/planner")

    assert response.status_code == 200
    assert "<h1>Planner</h1>" in response.text
    assert "Loading Planner" in response.text
    assert "Development-only · read-only" in response.text
    assert "/internal/static/planner.js" in response.text
    assert client.get("/internal/static/planner.css").status_code == 200
    assert client.get("/internal/static/planner_api.js").status_code == 200
    assert client.get("/internal/static/planner_view.js").status_code == 200


def test_planner_ui_is_unavailable_when_internal_tools_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    assert client.get("/internal/planner").status_code == 404
    assert client.get("/internal/static/planner.js").status_code == 404
