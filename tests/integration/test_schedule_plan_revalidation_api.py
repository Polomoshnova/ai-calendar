import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.calendar_integration.errors import (
    CalendarReconnectRequiredError,
    CalendarUnavailableError,
)
from app.calendar_integration.models import (
    CalendarBusyInterval,
    CalendarBusyResult,
    CalendarQueryError,
)
from app.calendar_integration.security import FernetTokenCipher
from app.core.config import get_settings
from app.internal.calendar_router import calendar_runtime
from app.main import app
from app.models import (
    CalendarConnection,
    CalendarConnectionStatus,
    CalendarProviderName,
    CalendarSelection,
    User,
)
from app.schedule_plans.models import (
    SchedulePlan,
    SchedulePlanSource,
    SchedulePlanStatus,
)
from app.schedule_plans.repository import list_reserved_intervals
from app.schedule_plans.revalidation import (
    PlanChangedDuringRevalidationError,
    revalidate_schedule_plan,
)
from app.schedule_plans.revalidation_models import SchedulePlanRevalidation
from app.schedule_plans.schemas import SchedulePlanContext
from app.schedule_plans.service import (
    confirm_schedule_plan,
    create_schedule_plan_from_preview,
    obsolete_schedule_plan,
)
from app.schemas.scheduling import SchedulePreviewResponse
from app.task_confirmation.models import ConfirmedTask


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 28, hour, minute, tzinfo=UTC)


def snapshot_dt(hour: int) -> datetime:
    return datetime(2026, 7, 20, hour, tzinfo=UTC)


class FakeProvider:
    def __init__(self) -> None:
        self.intervals: list[CalendarBusyInterval] = []
        self.errors: list[CalendarQueryError] = []
        self.failure: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    async def query_busy_intervals(
        self,
        _connection: object,
        *,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> CalendarBusyResult:
        self.calls.append(
            {
                "calendar_ids": calendar_ids,
                "time_min": time_min,
                "time_max": time_max,
                "timezone": timezone,
            }
        )
        if self.failure is not None:
            raise self.failure
        return CalendarBusyResult(
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
            intervals=self.intervals,
            errors=self.errors,
        )


@pytest.fixture(autouse=True)
def enable_internal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "true")
    get_settings.cache_clear()
    yield
    app.dependency_overrides.pop(calendar_runtime, None)
    get_settings.cache_clear()


@pytest.fixture
def calendar_setup(
    db_session: Session,
    user: User,
) -> tuple[CalendarConnection, FakeProvider]:
    cipher = FernetTokenCipher(Fernet.generate_key().decode())
    connection = CalendarConnection(
        user_id=user.id,
        provider=CalendarProviderName.google,
        status=CalendarConnectionStatus.active,
        access_token_encrypted=cipher.encrypt("access-token"),
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    connection.selections = [
        CalendarSelection(
            external_calendar_id="primary",
            display_name="Primary",
            primary=True,
            include_in_availability=True,
        ),
        CalendarSelection(
            external_calendar_id="team",
            display_name="Team",
            primary=False,
            include_in_availability=True,
        ),
    ]
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    provider = FakeProvider()
    runtime = SimpleNamespace(
        provider=provider,
        oauth=SimpleNamespace(),
        cipher=cipher,
    )
    app.dependency_overrides[calendar_runtime] = lambda: runtime
    return connection, provider


def confirmed_task() -> ConfirmedTask:
    return ConfirmedTask(
        title="Prepare report",
        description="Confidential description is not logged.",
        duration_minutes=60,
        priority="medium",
        earliest_start=None,
        deadline=None,
        preferred_time_of_day="morning",
        is_splittable=False,
        minimum_session_minutes=15,
        maximum_sessions_per_day=1,
        steps=[],
    )


def preview(
    start: datetime | None = None,
    end: datetime | None = None,
) -> SchedulePreviewResponse:
    start = start or dt(10)
    end = end or dt(11)
    return SchedulePreviewResponse.model_validate(
        {
            "scheduler_version": "2a.1",
            "planning_window": {"start": dt(8), "end": dt(18)},
            "free_intervals": [],
            "scheduled_blocks": [
                {
                    "task_id": "confirmed-task",
                    "start": start,
                    "end": end,
                    "reason_codes": ["only_available_slot"],
                    "score_components": [],
                }
            ],
            "unscheduled_tasks": [],
            "warnings": [],
        }
    )


def create_confirmed_plan(
    db_session: Session,
    user: User,
    *,
    calendar_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    confirm: bool = True,
) -> SchedulePlan:
    start = start or dt(10)
    end = end or dt(11)
    plan = create_schedule_plan_from_preview(
        db_session,
        user_id=user.id,
        confirmed_task=confirmed_task(),
        schedule_preview=preview(start, end),
        planning_context=SchedulePlanContext(
            timezone="Europe/Warsaw",
            planning_window_start=dt(8),
            planning_window_end=dt(18),
            source_calendar_snapshot_at=snapshot_dt(7),
            scheduler_version="2a.1",
            workflow_version="task-to-schedule-preview.v1",
            calendar_context={
                "provider": "google",
                "calendar_ids": calendar_ids or ["primary", "team"],
                "provider_busy_interval_count": 0,
                "merged_busy_interval_count": 0,
                "queried_at": snapshot_dt(7),
            },
            preferences_snapshot={
                "timezone": "Europe/Warsaw",
                "minimum_break_minutes": 0,
            },
        ),
        source=SchedulePlanSource.calendar_backed_preview,
        idempotency_key=f"revalidation-plan-{uuid.uuid4()}",
    )
    return confirm_schedule_plan(db_session, plan.id) if confirm else plan


def revalidate(
    client: TestClient,
    plan: SchedulePlan,
    connection: CalendarConnection,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "connection_id": str(connection.id),
        "request_id": str(uuid.uuid4()),
        "include_internal_busy": True,
    }
    payload.update(overrides)
    return client.post(
        f"/internal/api/schedule-plans/{plan.id}/revalidate",
        json=payload,
    )


def test_valid_revalidation_persists_hash_ttl_and_fresh_query(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)

    response = revalidate(client, plan, connection, request_id="valid-1")

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "valid"
    assert body["can_apply"] is True
    assert body["plan_status_before"] == "confirmed"
    assert body["plan_status_after"] == "confirmed"
    assert body["valid_until"]
    assert len(body["sessions_hash"]) == 64
    assert body["queried_calendar_ids"] == ["primary", "team"]
    assert len(provider.calls) == 1
    assert provider.calls[0]["time_min"] == dt(9, 45)
    assert provider.calls[0]["time_max"] == dt(11, 15)
    stored = db_session.get(
        SchedulePlanRevalidation, uuid.UUID(body["revalidation_id"])
    )
    assert stored is not None
    assert stored.sessions_hash == body["sessions_hash"]
    assert stored.valid_until is not None
    assert stored.diagnostics["snapshot_age_seconds"] > 0
    assert stored.diagnostics["warnings"] == []
    assert stored.diagnostics["reserved_interval_count"] == 0


def test_current_plan_is_excluded_from_reserved_intervals(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    assert response.json()["result"] == "valid"
    assert response.json()["diagnostics"]["reserved_interval_count"] == 0


def test_another_confirmed_plan_causes_internal_conflict(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    create_confirmed_plan(db_session, user)

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "conflict"
    assert body["plan_status_after"] == "revalidation_required"
    assert body["diagnostics"]["reserved_interval_count"] == 1
    assert body["conflicts"][0]["reason_code"] == "session_overlaps_reserved_plan"
    assert body["conflicts"][0]["conflicting_busy_intervals"][0]["source"] == (
        "internal_busy"
    )


@pytest.mark.parametrize(
    "status",
    [SchedulePlanStatus.proposed, SchedulePlanStatus.obsolete],
)
def test_non_reserving_plan_does_not_cause_internal_conflict(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
    status: SchedulePlanStatus,
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    other = create_confirmed_plan(db_session, user, confirm=False)
    if status is SchedulePlanStatus.obsolete:
        obsolete_schedule_plan(db_session, other.id)

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    assert response.json()["result"] == "valid"
    assert response.json()["diagnostics"]["reserved_interval_count"] == 0


def test_another_users_plan_is_ignored_by_revalidation(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    other_user = User(
        email="other-revalidation-owner@example.com",
        timezone="Europe/Warsaw",
    )
    db_session.add(other_user)
    db_session.commit()
    other_connection = CalendarConnection(
        user_id=other_user.id,
        provider=CalendarProviderName.google,
        provider_account_id="other-account",
        status=CalendarConnectionStatus.active,
    )
    other_connection.selections.extend(
        [
            CalendarSelection(
                external_calendar_id=calendar_id,
                display_name=calendar_id.title(),
                primary=calendar_id == "primary",
                include_in_availability=True,
            )
            for calendar_id in ("primary", "team")
        ]
    )
    db_session.add(other_connection)
    db_session.commit()
    create_confirmed_plan(db_session, other_user)

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    assert response.json()["result"] == "valid"
    assert response.json()["diagnostics"]["reserved_interval_count"] == 0


def test_touching_reserved_interval_does_not_conflict(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    create_confirmed_plan(
        db_session,
        user,
        start=dt(11),
        end=dt(12),
    )

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    assert response.json()["result"] == "valid"
    assert response.json()["diagnostics"]["reserved_interval_count"] == 1


def test_provider_and_internal_busy_are_both_considered(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    create_confirmed_plan(db_session, user)
    provider.intervals = [
        CalendarBusyInterval(
            start=dt(10, 30),
            end=dt(10, 45),
            calendar_id="primary",
        )
    ]

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "conflict"
    assert {item["reason_code"] for item in body["conflicts"]} == {
        "session_overlaps_provider_busy",
        "session_overlaps_reserved_plan",
    }
    assert body["provider_busy_interval_count"] == 1
    assert body["diagnostics"]["reserved_interval_count"] == 1
    assert body["diagnostics"]["combined_busy_interval_count"] == 2


def test_provider_failure_preserves_plan_status_and_reservations(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    other = create_confirmed_plan(db_session, user)
    provider.failure = CalendarUnavailableError("provider unavailable")

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "provider_failure"
    assert body["plan_status_before"] == "confirmed"
    assert body["plan_status_after"] == "confirmed"
    assert body["diagnostics"]["reserved_interval_count"] == 1
    db_session.refresh(plan)
    db_session.refresh(other)
    assert plan.status is SchedulePlanStatus.confirmed
    assert other.status is SchedulePlanStatus.confirmed
    reservations = list_reserved_intervals(
        db_session,
        user_id=user.id,
        start=dt(10),
        end=dt(11),
    )
    assert {item.plan_id for item in reservations} == {plan.id, other.id}


def test_direct_conflict_and_recovery_to_confirmed(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    provider.intervals = [
        CalendarBusyInterval(
            start=dt(10, 30),
            end=dt(11, 30),
            calendar_id="primary",
        )
    ]

    conflict = revalidate(client, plan, connection, request_id="conflict")

    assert conflict.status_code == 200
    body = conflict.json()
    assert body["result"] == "conflict"
    assert body["can_apply"] is False
    assert body["plan_status_after"] == "revalidation_required"
    assert body["conflicting_session_count"] == 1
    assert body["conflicts"][0]["session_id"] == str(plan.sessions[0].id)
    assert body["conflicts"][0]["overlap_minutes"] == 30
    repeated = revalidate(
        client,
        plan,
        connection,
        request_id="conflict-again",
    )
    assert repeated.json()["plan_status_before"] == "revalidation_required"
    assert repeated.json()["plan_status_after"] == "revalidation_required"
    provider.intervals = []

    recovered = revalidate(client, plan, connection, request_id="recovered")

    assert recovered.json()["result"] == "valid"
    assert recovered.json()["plan_status_before"] == "revalidation_required"
    assert recovered.json()["plan_status_after"] == "confirmed"
    db_session.refresh(plan)
    assert plan.status is SchedulePlanStatus.confirmed
    assert plan.sessions[0].start == dt(10)
    assert plan.sessions[0].end == dt(11)


def test_partial_failure_is_safe_and_preserves_successful_counts(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    provider.intervals = [
        CalendarBusyInterval(
            start=dt(12),
            end=dt(13),
            calendar_id="primary",
        )
    ]
    provider.errors = [CalendarQueryError(calendar_id="team", reason="forbidden")]

    response = revalidate(client, plan, connection)

    body = response.json()
    assert body["result"] == "provider_partial_failure"
    assert body["can_apply"] is False
    assert body["provider_busy_interval_count"] == 1
    assert body["diagnostics"]["failed_calendars"] == {"team": "calendar_access_denied"}
    assert body["plan_status_after"] == "revalidation_required"


@pytest.mark.parametrize(
    ("failure", "failure_code"),
    [
        (CalendarUnavailableError("raw provider detail"), "provider_unavailable"),
        (
            CalendarReconnectRequiredError("raw authorization detail"),
            "calendar_reconnect_required",
        ),
    ],
)
def test_provider_failure_is_retryable_safe_and_preserves_plan(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
    failure: Exception,
    failure_code: str,
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    provider.failure = failure

    response = revalidate(client, plan, connection)

    assert response.status_code == 200
    rendered = response.text.lower()
    assert response.json()["result"] == "provider_failure"
    assert response.json()["failure_code"] == failure_code
    assert response.json()["can_apply"] is False
    assert response.json()["plan_status_after"] == "confirmed"
    assert "raw provider detail" not in rendered


def test_request_id_is_transport_idempotent_but_new_request_is_fresh(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)

    first = revalidate(client, plan, connection, request_id="retry-key")
    retry = revalidate(client, plan, connection, request_id="retry-key")
    fresh = revalidate(client, plan, connection, request_id="new-key")

    assert retry.json()["revalidation_id"] == first.json()["revalidation_id"]
    assert fresh.json()["revalidation_id"] != first.json()["revalidation_id"]
    assert len(provider.calls) == 2
    records = list(
        db_session.scalars(
            select(SchedulePlanRevalidation).where(
                SchedulePlanRevalidation.plan_id == plan.id
            )
        )
    )
    assert len(records) == 2


def test_requests_without_request_id_always_query_fresh(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)

    first = revalidate(client, plan, connection, request_id=None)
    second = revalidate(client, plan, connection, request_id=None)

    assert first.json()["revalidation_id"] != second.json()["revalidation_id"]
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "status",
    [
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.obsolete,
        SchedulePlanStatus.applying,
        SchedulePlanStatus.applied,
        SchedulePlanStatus.failed,
    ],
)
def test_ineligible_plan_state_returns_409_without_provider_call(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
    status: SchedulePlanStatus,
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    plan.status = status
    db_session.commit()

    response = revalidate(client, plan, connection)

    assert response.status_code == 409
    assert provider.calls == []


def test_selection_ownership_and_connection_health_are_enforced(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    incompatible = revalidate(
        client,
        plan,
        connection,
        calendar_ids=["primary"],
    )
    assert incompatible.status_code == 409

    connection.status = CalendarConnectionStatus.expired
    db_session.commit()
    disabled = revalidate(client, plan, connection)
    assert disabled.status_code == 409

    other = User(email="other@example.com", timezone="Europe/Warsaw")
    db_session.add(other)
    db_session.commit()
    connection.status = CalendarConnectionStatus.active
    connection.user_id = other.id
    db_session.commit()
    wrong_owner = revalidate(client, plan, connection)
    assert wrong_owner.status_code == 409


def test_missing_original_calendar_selection_returns_409(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    team = next(
        item for item in connection.selections if item.external_calendar_id == "team"
    )
    db_session.delete(team)
    db_session.commit()

    response = revalidate(client, plan, connection)

    assert response.status_code == 409
    assert provider.calls == []


def test_history_is_newest_first_filterable_and_private(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, provider = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    first = revalidate(client, plan, connection, request_id="history-1")
    provider.intervals = [
        CalendarBusyInterval(
            start=dt(10, 15),
            end=dt(10, 30),
            calendar_id="primary",
        )
    ]
    second = revalidate(client, plan, connection, request_id="history-2")

    history = client.get(f"/internal/api/schedule-plans/{plan.id}/revalidations")
    filtered = client.get(
        f"/internal/api/schedule-plans/{plan.id}/revalidations",
        params={"status": "valid"},
    )

    assert [item["revalidation_id"] for item in history.json()["revalidations"]] == [
        second.json()["revalidation_id"],
        first.json()["revalidation_id"],
    ]
    assert [item["result"] for item in filtered.json()["revalidations"]] == ["valid"]
    rendered = history.text.lower()
    assert "access-token" not in rendered
    assert "authorization" not in rendered
    assert "confidential description" not in rendered


def test_plan_change_during_provider_io_rejects_stale_result(
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)

    async def changing_query(
        _connection: CalendarConnection,
        calendar_ids: list[str],
        time_min: datetime,
        time_max: datetime,
        timezone: str,
    ) -> tuple[list[str], CalendarBusyResult]:
        concurrent_session = sessionmaker(
            bind=db_session.get_bind(),
            expire_on_commit=False,
        )
        with concurrent_session() as concurrent:
            obsolete_schedule_plan(concurrent, plan.id)
        return calendar_ids, CalendarBusyResult(
            time_min=time_min,
            time_max=time_max,
            timezone=timezone,
            intervals=[],
            errors=[],
        )

    with pytest.raises(
        PlanChangedDuringRevalidationError,
        match="plan_changed_during_revalidation",
    ):
        asyncio.run(
            revalidate_schedule_plan(
                db_session,
                plan_id=plan.id,
                connection_id=connection.id,
                calendar_ids=None,
                include_internal_busy=False,
                minimum_break_minutes=0,
                request_id="stale",
                query_busy=changing_query,
            )
        )
    assert (
        db_session.scalar(
            select(SchedulePlanRevalidation).where(
                SchedulePlanRevalidation.plan_id == plan.id
            )
        )
        is None
    )


def test_revalidation_routes_are_internal_and_guarded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    plan = create_confirmed_plan(db_session, user)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/internal/api/schedule-plans/{plan_id}/revalidate" in paths
    assert "/api/v1/schedule-plans/{plan_id}/revalidate" not in paths

    monkeypatch.setenv("ENABLE_INTERNAL_TOOLS", "false")
    get_settings.cache_clear()
    response = revalidate(client, plan, connection)
    assert response.status_code == 404


def test_missing_plan_and_connection_return_404(
    client: TestClient,
    db_session: Session,
    user: User,
    calendar_setup: tuple[CalendarConnection, FakeProvider],
) -> None:
    connection, _ = calendar_setup
    missing_plan = client.post(
        "/internal/api/schedule-plans/22222222-2222-2222-2222-222222222222/revalidate",
        json={"connection_id": str(connection.id)},
    )
    plan = create_confirmed_plan(db_session, user)
    missing_connection = client.post(
        f"/internal/api/schedule-plans/{plan.id}/revalidate",
        json={"connection_id": "33333333-3333-3333-3333-333333333333"},
    )

    assert missing_plan.status_code == 404
    assert missing_connection.status_code == 404


def test_no_calendar_write_scope_or_event_endpoint(client: TestClient) -> None:
    from app.core.config import Settings

    assert Settings().google_calendar_scopes == (
        "https://www.googleapis.com/auth/calendar.readonly"
    )
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(
        "event" in path.lower()
        and any(method in paths[path] for method in ("post", "put", "patch", "delete"))
        for path in paths
    )
