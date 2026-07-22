import uuid

from fastapi.testclient import TestClient

from app.models import User

USER_ID = "11111111-1111-1111-1111-111111111111"


def task_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "user_id": USER_ID,
        "title": "Write architecture review",
        "duration_minutes": 90,
        "earliest_start": "2026-07-23T08:00:00Z",
        "deadline": "2026-07-24T16:00:00Z",
        "priority": "high",
    }
    payload.update(overrides)
    return payload


def test_task_crud(client: TestClient, user: User) -> None:
    create_response = client.post("/api/v1/tasks", json=task_payload())
    assert create_response.status_code == 201
    created = create_response.json()
    task_id = created["id"]
    assert created["user_id"] == str(user.id)
    assert created["priority"] == "high"
    assert created["status"] == "pending"
    assert created["preferred_time_of_day"] == "any"
    assert created["is_splittable"] is False
    assert created["minimum_session_minutes"] == 15
    assert created["maximum_sessions_per_day"] == 1

    list_response = client.get("/api/v1/tasks", params={"user_id": USER_ID})
    assert list_response.status_code == 200
    assert [task["id"] for task in list_response.json()] == [task_id]

    get_response = client.get(f"/api/v1/tasks/{task_id}")
    assert get_response.status_code == 200
    assert get_response.json()["title"] == "Write architecture review"

    update_response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Review architecture", "priority": "urgent"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Review architecture"
    assert update_response.json()["priority"] == "urgent"

    delete_response = client.delete(f"/api/v1/tasks/{task_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404


def test_create_rejects_non_positive_duration(client: TestClient, user: User) -> None:
    response = client.post("/api/v1/tasks", json=task_payload(duration_minutes=0))

    assert response.status_code == 422


def test_create_rejects_invalid_time_window(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/tasks",
        json=task_payload(
            earliest_start="2026-07-25T08:00:00Z",
            deadline="2026-07-24T16:00:00Z",
        ),
    )

    assert response.status_code == 422


def test_create_rejects_invalid_priority(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/tasks", json=task_payload(priority="extremely-important")
    )

    assert response.status_code == 422


def test_create_rejects_naive_task_datetime(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/tasks",
        json=task_payload(earliest_start="2026-07-23T08:00:00"),
    )

    assert response.status_code == 422


def test_create_requires_existing_user(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tasks",
        json=task_payload(
            user_id=str(uuid.UUID("22222222-2222-2222-2222-222222222222"))
        ),
    )

    assert response.status_code == 404


def test_update_revalidates_complete_time_window(
    client: TestClient, user: User
) -> None:
    created = client.post("/api/v1/tasks", json=task_payload()).json()

    response = client.patch(
        f"/api/v1/tasks/{created['id']}",
        json={"earliest_start": "2026-07-25T08:00:00Z"},
    )

    assert response.status_code == 422
