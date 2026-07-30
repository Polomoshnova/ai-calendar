import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.calendar import (
    CalendarConnection,
    CalendarProviderName,
    CalendarSelection,
)
from app.schedule_plans.errors import SchedulePlanValidationError
from app.schedule_plans.models import SchedulePlan, SchedulePlanSource
from app.schedule_plans.schemas import SchedulePlanContext
from app.schedule_plans.service import (
    _validate_preview,
    create_schedule_plan_from_preview,
)
from app.schemas.scheduling import SchedulePreviewResponse
from app.task_confirmation.models import ConfirmedTask


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 27, hour, minute, tzinfo=UTC)


def confirmed_task(duration: int = 60) -> ConfirmedTask:
    return ConfirmedTask(
        title="Prepare report",
        description="Draft and review the report.",
        duration_minutes=duration,
        priority="medium",
        earliest_start=None,
        deadline=None,
        preferred_time_of_day="morning",
        is_splittable=True,
        minimum_session_minutes=15,
        maximum_sessions_per_day=2,
        steps=[],
    )


def context(**overrides: object) -> SchedulePlanContext:
    values: dict[str, object] = {
        "timezone": "Europe/Warsaw",
        "planning_window_start": dt(8),
        "planning_window_end": dt(18),
        "scheduler_version": "2a.1",
        "preferences_snapshot": {"timezone": "Europe/Warsaw"},
    }
    values.update(overrides)
    return SchedulePlanContext.model_validate(values)


def preview(
    blocks: list[tuple[datetime, datetime]],
    *,
    scheduler_version: str = "2a.1",
) -> SchedulePreviewResponse:
    return SchedulePreviewResponse.model_validate(
        {
            "scheduler_version": scheduler_version,
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
                for start, end in blocks
            ],
            "unscheduled_tasks": [],
            "warnings": [],
        }
    )


def test_context_accepts_valid_iana_timezone_and_aware_window() -> None:
    parsed = context()

    assert parsed.timezone == "Europe/Warsaw"
    assert parsed.planning_window_start == dt(8)


@pytest.mark.parametrize(
    "overrides",
    [
        {"planning_window_start": dt(18), "planning_window_end": dt(8)},
        {"planning_window_start": datetime(2026, 7, 27, 8)},
        {"timezone": "Mars/Olympus_Mons"},
    ],
)
def test_context_rejects_invalid_window_or_timezone(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        context(**overrides)


def test_context_rejects_secret_snapshot_fields() -> None:
    with pytest.raises(ValidationError, match="forbidden secret fields"):
        context(preferences_snapshot={"nested": {"access_token": "secret"}})


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([], "at least one"),
        ([(dt(7), dt(8))], "inside the planning window"),
        ([(dt(8), dt(9)), (dt(8, 30), dt(10))], "must not overlap"),
        (
            [
                (
                    dt(8),
                    dt(
                        8,
                        30,
                    ),
                )
            ],
            "exceeds confirmed task duration",
        ),
    ],
)
def test_preview_validation_rejects_invalid_blocks(
    blocks: list[tuple[datetime, datetime]],
    message: str,
) -> None:
    task = confirmed_task(duration=15 if "exceeds" in message else 120)

    with pytest.raises(SchedulePlanValidationError, match=message):
        _validate_preview(task, preview(blocks), context())


def test_preview_validation_preserves_chronological_order() -> None:
    normalized = _validate_preview(
        confirmed_task(duration=90),
        preview([(dt(10), dt(10, 30)), (dt(8), dt(9))]),
        context(),
    )

    assert normalized == [
        (dt(8), dt(9), 60),
        (dt(10), dt(10, 30), 30),
    ]


def test_schedule_plans_package_has_no_provider_or_ai_dependencies() -> None:
    package = Path(__file__).parents[2] / "app" / "schedule_plans"
    source_files = list(package.glob("*.py"))
    assert source_files

    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            imported == root or imported.startswith(f"{root}.")
            for imported in imports
            for root in {
                "google",
                "openai",
                "app.calendar_integration.google",
                "app.ai_intake",
                "app.scheduling.scheduler",
            }
        ), f"{source_file} has a forbidden dependency"


def test_concurrent_duplicate_create_returns_existing_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    existing = Mock(spec=SchedulePlan)
    existing.user_id = user_id
    session = Mock(spec=Session)
    session.get.return_value = Mock()
    connection = Mock(spec=CalendarConnection)
    connection.id = uuid.uuid4()
    connection.provider = CalendarProviderName.google
    connection.provider_account_id = "account-1"
    session.scalar.return_value = connection
    selection = Mock(spec=CalendarSelection)
    selection.external_calendar_id = "primary"
    selection.primary = True
    selection.include_in_availability = True
    session.scalars.return_value = [selection]
    session.commit.side_effect = IntegrityError(
        "duplicate idempotency key",
        params={},
        orig=Exception("unique violation"),
    )
    lookup = Mock(side_effect=[None, existing])
    monkeypatch.setattr(
        "app.schedule_plans.service.get_schedule_plan_by_idempotency_key",
        lookup,
    )

    result = create_schedule_plan_from_preview(
        session,
        user_id=user_id,
        confirmed_task=confirmed_task(),
        schedule_preview=preview([(dt(8), dt(9))]),
        planning_context=context(
            calendar_context={
                "provider": "google",
                "calendar_ids": ["primary"],
                "provider_busy_interval_count": 0,
                "merged_busy_interval_count": 0,
            }
        ),
        source=SchedulePlanSource.ai_workflow,
        idempotency_key="same-request",
    )

    assert result is existing
    session.rollback.assert_called_once_with()
