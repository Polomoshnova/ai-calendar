import uuid
from datetime import UTC, datetime, time
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.domain.tasks import TaskPriority, TaskStatus
from app.models import Task, User
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlan,
    SchedulePlanSource,
    SchedulePlanStatus,
)


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


def preview_payload(user_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "planning_window": {
            "start": dt(20, 8).isoformat(),
            "end": dt(20, 18).isoformat(),
        },
        "busy_intervals": [],
    }
    payload.update(overrides)
    return payload


def add_task(
    session: Session,
    user: User,
    task_id: str,
    *,
    duration: int = 60,
    status: TaskStatus = TaskStatus.pending,
    earliest_start: datetime | None = None,
    deadline: datetime | None = None,
    is_splittable: bool = False,
) -> Task:
    task = Task(
        id=uuid.uuid5(uuid.NAMESPACE_URL, task_id),
        user_id=user.id,
        title=task_id,
        description=None,
        duration_minutes=duration,
        earliest_start=earliest_start,
        deadline=deadline,
        priority=TaskPriority.medium,
        status=status,
        is_splittable=is_splittable,
        minimum_session_minutes=15,
        maximum_sessions_per_day=2,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def use_utc_preferences(session: Session, user: User) -> None:
    user.timezone = "UTC"
    session.commit()


def add_plan_reservation(
    session: Session,
    user: User,
    *,
    status: SchedulePlanStatus,
    start: datetime,
    end: datetime,
) -> SchedulePlan:
    plan = SchedulePlan(
        user_id=user.id,
        plan_group_id=uuid.uuid4(),
        source=SchedulePlanSource.manual_preview,
        version=1,
        status=status,
        timezone="UTC",
        planning_window_start=dt(20, 8),
        planning_window_end=dt(20, 18),
        scheduler_version="test",
        idempotency_key=str(uuid.uuid4()),
        confirmed_task_snapshot={},
        scheduling_preferences_snapshot={},
        busy_context_summary={},
        preview_metadata={},
    )
    plan.sessions.append(
        ScheduledSession(
            title="Reserved session",
            start=start,
            end=end,
            duration_minutes=int((end - start).total_seconds() // 60),
            order=1,
            status=ScheduledSessionStatus.confirmed,
        )
    )
    session.add(plan)
    session.commit()
    return plan


def scheduled_start(response: Response) -> str:
    body = response.json()
    assert len(body["scheduled_blocks"]) == 1
    return str(body["scheduled_blocks"][0]["start"])


def test_confirmed_plan_slot_is_excluded_from_preview(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "after-confirmed-reservation")
    add_plan_reservation(
        db_session,
        user,
        status=SchedulePlanStatus.confirmed,
        start=dt(20, 9),
        end=dt(20, 10),
    )

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    assert scheduled_start(response) == "2026-07-20T10:00:00Z"


def test_proposed_plan_slot_remains_available(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "proposed-does-not-reserve")
    add_plan_reservation(
        db_session,
        user,
        status=SchedulePlanStatus.proposed,
        start=dt(20, 9),
        end=dt(20, 10),
    )

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    assert scheduled_start(response) == "2026-07-20T09:00:00Z"


def test_obsolete_plan_slot_remains_available(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "obsolete-does-not-reserve")
    add_plan_reservation(
        db_session,
        user,
        status=SchedulePlanStatus.obsolete,
        start=dt(20, 9),
        end=dt(20, 10),
    )

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    assert scheduled_start(response) == "2026-07-20T09:00:00Z"


def test_another_users_plan_does_not_affect_preview(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "other-user-does-not-reserve")
    other_user = User(
        id=uuid.uuid4(),
        email="other-preview-owner@example.com",
        timezone="UTC",
    )
    db_session.add(other_user)
    db_session.commit()
    add_plan_reservation(
        db_session,
        other_user,
        status=SchedulePlanStatus.confirmed,
        start=dt(20, 9),
        end=dt(20, 10),
    )

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    assert scheduled_start(response) == "2026-07-20T09:00:00Z"


def test_reservation_touching_preview_window_does_not_overlap(
    client: TestClient,
    db_session: Session,
    user: User,
) -> None:
    use_utc_preferences(db_session, user)
    add_task(
        db_session,
        user,
        "touching-reservation",
        earliest_start=dt(20, 10),
    )
    add_plan_reservation(
        db_session,
        user,
        status=SchedulePlanStatus.confirmed,
        start=dt(20, 9),
        end=dt(20, 10),
    )

    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            planning_window={
                "start": dt(20, 10).isoformat(),
                "end": dt(20, 18).isoformat(),
            },
        ),
    )

    assert response.status_code == 200
    assert scheduled_start(response) == "2026-07-20T10:00:00Z"


def test_successful_preview(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    task = add_task(db_session, user, "write-report", deadline=dt(20, 17))

    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            busy_intervals=[
                {"start": dt(20, 12).isoformat(), "end": dt(20, 13).isoformat()}
            ],
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scheduler_version"] == "2a.1"
    assert body["planning_window"] == {
        "start": dt(20, 8).isoformat().replace("+00:00", "Z"),
        "end": dt(20, 18).isoformat().replace("+00:00", "Z"),
    }
    assert [block["task_id"] for block in body["scheduled_blocks"]] == [str(task.id)]
    block = body["scheduled_blocks"][0]
    assert block["reason_codes"]
    assert block["score_components"]
    assert body["free_intervals"]
    assert body["unscheduled_tasks"] == []
    assert body["warnings"] == []


def test_user_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(uuid.UUID("22222222-2222-2222-2222-222222222222")),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


def test_invalid_planning_window(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            planning_window={
                "start": dt(20, 18).isoformat(),
                "end": dt(20, 8).isoformat(),
            },
        ),
    )

    assert response.status_code == 422


def test_naive_planning_datetime_is_rejected(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            planning_window={
                "start": "2026-07-20T08:00:00",
                "end": dt(20, 18).isoformat(),
            },
        ),
    )

    assert response.status_code == 422


def test_planning_horizon_over_31_days_is_rejected(
    client: TestClient, user: User
) -> None:
    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            planning_window={
                "start": "2026-07-01T00:00:00Z",
                "end": "2026-08-02T00:00:00Z",
            },
        ),
    )

    assert response.status_code == 422


def test_invalid_busy_interval_is_rejected(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            busy_intervals=[
                {"start": dt(20, 12).isoformat(), "end": dt(20, 11).isoformat()}
            ],
        ),
    )

    assert response.status_code == 422


def test_naive_busy_interval_is_rejected(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            busy_intervals=[
                {"start": "2026-07-20T12:00:00", "end": dt(20, 13).isoformat()}
            ],
        ),
    )

    assert response.status_code == 422


def test_non_pending_and_out_of_window_tasks_are_excluded(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    included = add_task(db_session, user, "included")
    add_task(db_session, user, "completed", status=TaskStatus.completed)
    add_task(db_session, user, "cancelled", status=TaskStatus.cancelled)
    add_task(db_session, user, "starts-later", earliest_start=dt(20, 18))
    add_task(db_session, user, "ended-before", deadline=dt(20, 8))

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    returned_ids = {
        item["task_id"]
        for key in ("scheduled_blocks", "unscheduled_tasks")
        for item in response.json()[key]
    }
    assert returned_ids == {str(included.id)}


def test_unscheduled_tasks_are_returned(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    task = add_task(db_session, user, "too-large", duration=600)

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    assert response.json()["unscheduled_tasks"] == [
        {
            "task_id": str(task.id),
            "remaining_minutes": 600,
            "reason_code": "insufficient_free_time",
        }
    ]


def test_identical_requests_return_identical_response_bytes(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "deterministic")
    payload = preview_payload(user.id)

    first = client.post("/api/v1/scheduling/preview", json=payload)
    second = client.post("/api/v1/scheduling/preview", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.content == second.content


def test_preview_creates_no_database_rows(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "read-only")
    before = table_row_counts(db_session)

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))
    db_session.expire_all()
    after = table_row_counts(db_session)

    assert response.status_code == 200
    assert after == before
    assert set(after) == {
        "users",
        "user_preferences",
        "tasks",
        "calendar_connections",
        "calendar_oauth_states",
        "calendar_selections",
        "calendar_event_mappings",
        "external_calendar_changes",
        "schedule_plans",
        "schedule_plan_revalidations",
        "scheduled_sessions",
    }


def test_missing_preferences_use_defaults_without_persistence(
    client: TestClient, db_session: Session
) -> None:
    user = User(
        id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        email="without-preferences@example.com",
        timezone="UTC",
    )
    db_session.add(user)
    db_session.commit()

    response = client.post("/api/v1/scheduling/preview", json=preview_payload(user.id))

    assert response.status_code == 200
    assert user.preferences is None
    assert (
        db_session.scalar(
            select(func.count()).select_from(Base.metadata.tables["user_preferences"])
        )
        == 0
    )


def test_all_returned_datetimes_are_timezone_aware(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    add_task(db_session, user, "aware")

    body = client.post(
        "/api/v1/scheduling/preview", json=preview_payload(user.id)
    ).json()
    intervals = [
        body["planning_window"],
        *body["free_intervals"],
        *body["scheduled_blocks"],
    ]

    for interval in intervals:
        assert datetime.fromisoformat(interval["start"]).tzinfo is not None
        assert datetime.fromisoformat(interval["end"]).tzinfo is not None


def test_cutoff_behavior_remains_applied_to_preview_tasks(
    client: TestClient, db_session: Session, user: User
) -> None:
    use_utc_preferences(db_session, user)
    assert user.preferences is not None
    user.preferences.no_deep_work_after = time(17)
    db_session.commit()
    task = add_task(db_session, user, "after-cutoff", duration=120)

    response = client.post(
        "/api/v1/scheduling/preview",
        json=preview_payload(
            user.id,
            planning_window={
                "start": dt(20, 16).isoformat(),
                "end": dt(20, 18).isoformat(),
            },
        ),
    )

    assert response.status_code == 200
    assert response.json()["scheduled_blocks"] == []
    assert response.json()["unscheduled_tasks"][0]["task_id"] == str(task.id)


def table_row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table)) or 0
        for table in Base.metadata.sorted_tables
    }
