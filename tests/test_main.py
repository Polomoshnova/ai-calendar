from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello World"}


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_current_time() -> None:
    before_request = datetime.now(UTC)
    response = client.get("/time")
    after_request = datetime.now(UTC)

    assert response.status_code == 200
    returned_time = datetime.fromisoformat(response.json()["utc_time"])
    assert returned_time.tzinfo is not None
    assert before_request <= returned_time <= after_request
