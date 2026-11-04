from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models import User


@pytest.fixture(autouse=True)
def calendar_ui_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    monkeypatch.setenv("CALENDAR_DAY_DEV_USER_EMAIL", "owner@example.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_calendar_day_page_and_assets_are_available(
    client: TestClient, user: User
) -> None:
    response = client.get("/internal/calendar-day")

    assert response.status_code == 200
    assert "AI Calendar" in response.text
    assert "Calendar day navigation" in response.text
    assert 'data-timezone="Europe/Warsaw"' in response.text
    assert "/internal/static/calendar_day.js" in response.text
    for asset in [
        "calendar_day.css",
        "calendar_day.js",
        "calendar_day_api.js",
        "calendar_day_layout.js",
        "calendar_day_view.js",
    ]:
        assert client.get(f"/internal/static/{asset}").status_code == 200


def test_calendar_day_ui_is_unavailable_when_internal_tools_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()

    assert client.get("/internal/calendar-day").status_code == 404
    assert client.get("/internal/static/calendar_day.js").status_code == 404


def test_calendar_day_ui_requires_configured_dev_identity(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CALENDAR_DAY_DEV_USER_EMAIL")
    get_settings.cache_clear()

    assert client.get("/internal/calendar-day").status_code == 404


def test_calendar_client_does_not_send_user_identity() -> None:
    source = (
        __import__("pathlib").Path(__file__).parents[2]
        / "app"
        / "static"
        / "calendar_day_api.js"
    ).read_text()

    assert "user_id" not in source
    assert "X-User-Id" not in source
