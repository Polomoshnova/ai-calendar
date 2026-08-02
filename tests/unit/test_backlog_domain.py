import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.backlog.domain import (
    BacklogDomainError,
    BacklogOrigin,
    BacklogReason,
    BacklogStatus,
    InvalidBacklogTransitionError,
    calculate_remaining_unscheduled_duration,
    validate_backlog_values,
    validate_transition,
)
from app.schedule_plans.models import (
    ScheduledSession,
    ScheduledSessionStatus,
    SchedulePlanStatus,
)
from app.schedule_plans.repository import RESERVING_PLAN_STATUSES

NOW = datetime(2026, 8, 2, 10, tzinfo=UTC)


def validate(**overrides: object) -> None:
    values: dict[str, object] = {
        "origin": BacklogOrigin.scheduler,
        "reason": BacklogReason.no_available_slot,
        "note": None,
        "status": BacklogStatus.active,
        "remaining_duration_minutes": 60,
        "task_duration_minutes": 120,
        "entered_at": NOW,
        "next_review_at": None,
        "deferred_until": None,
        "resolved_at": None,
        "scheduling_attempt_count": 0,
        "last_scheduling_attempt_at": None,
    }
    values.update(overrides)
    validate_backlog_values(**values)  # type: ignore[arg-type]


def test_statuses_and_reasons_are_typed() -> None:
    assert {item.value for item in BacklogStatus} == {
        "active",
        "deferred",
        "resolved",
        "cancelled",
    }
    assert BacklogReason.partially_scheduled.value == "partially_scheduled"
    assert {item.value for item in BacklogOrigin} == {
        "user",
        "scheduler",
        "system",
        "calendar_sync",
    }
    assert "calendar_unavailable" not in {item.value for item in BacklogReason}


def test_origin_reason_rules_and_other_note() -> None:
    validate(origin=BacklogOrigin.user, reason=BacklogReason.manual_defer)
    validate(origin=BacklogOrigin.scheduler, reason=BacklogReason.no_available_slot)
    with pytest.raises(BacklogDomainError, match="require origin=user"):
        validate(origin=BacklogOrigin.system, reason=BacklogReason.manual_defer)
    with pytest.raises(BacklogDomainError, match="meaningful note"):
        validate(reason=BacklogReason.other)
    with pytest.raises(BacklogDomainError, match="meaningful note"):
        validate(reason=BacklogReason.other, note="   ")
    validate(reason=BacklogReason.other, note="Legacy workflow needs review")


def test_valid_active_and_deferred_values() -> None:
    validate()
    validate(
        status=BacklogStatus.deferred,
        deferred_until=NOW + timedelta(days=1),
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": BacklogStatus.deferred}, "require deferred_until"),
        ({"status": BacklogStatus.resolved}, "require zero remaining"),
        ({"remaining_duration_minutes": -1}, "cannot be negative"),
        ({"remaining_duration_minutes": 121}, "cannot exceed"),
        ({"entered_at": datetime(2026, 8, 2)}, "timezone-aware"),
    ],
)
def test_invalid_values_are_rejected(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(BacklogDomainError, match=message):
        validate(**overrides)


def test_resolved_requires_resolved_at() -> None:
    with pytest.raises(BacklogDomainError, match="require resolved_at"):
        validate(status=BacklogStatus.resolved, remaining_duration_minutes=0)


def test_lifecycle_transition_table() -> None:
    for source, target in [
        (BacklogStatus.active, BacklogStatus.deferred),
        (BacklogStatus.active, BacklogStatus.resolved),
        (BacklogStatus.active, BacklogStatus.cancelled),
        (BacklogStatus.deferred, BacklogStatus.active),
        (BacklogStatus.deferred, BacklogStatus.resolved),
        (BacklogStatus.deferred, BacklogStatus.cancelled),
    ]:
        validate_transition(source, target)
    with pytest.raises(InvalidBacklogTransitionError):
        validate_transition(BacklogStatus.resolved, BacklogStatus.active)
    with pytest.raises(InvalidBacklogTransitionError):
        validate_transition(BacklogStatus.cancelled, BacklogStatus.active)


def session(
    minutes: int, plan_status: SchedulePlanStatus, status: ScheduledSessionStatus
) -> ScheduledSession:
    value = SimpleNamespace(
        duration_minutes=minutes,
        status=status,
        plan=SimpleNamespace(status=plan_status),
    )
    return cast(ScheduledSession, value)


def test_remaining_duration_no_partial_and_full_scheduling() -> None:
    assert calculate_remaining_unscheduled_duration(240, []) == 240
    active = session(120, SchedulePlanStatus.applied, ScheduledSessionStatus.applied)
    assert calculate_remaining_unscheduled_duration(240, [active]) == 120
    assert calculate_remaining_unscheduled_duration(120, [active]) == 0


@pytest.mark.parametrize("plan_status", sorted(RESERVING_PLAN_STATUSES))
def test_every_shared_reserving_status_reduces_remaining_duration(
    plan_status: SchedulePlanStatus,
) -> None:
    scheduled = session(30, plan_status, ScheduledSessionStatus.confirmed)
    assert calculate_remaining_unscheduled_duration(120, [scheduled]) == 90


@pytest.mark.parametrize(
    "plan_status",
    [
        SchedulePlanStatus.proposed,
        SchedulePlanStatus.failed,
        SchedulePlanStatus.obsolete,
    ],
)
def test_non_reserving_plan_statuses_are_ignored(
    plan_status: SchedulePlanStatus,
) -> None:
    scheduled = session(30, plan_status, ScheduledSessionStatus.proposed)
    assert calculate_remaining_unscheduled_duration(120, [scheduled]) == 120


def test_failed_and_obsolete_session_statuses_are_ignored() -> None:
    sessions = [
        session(30, SchedulePlanStatus.applied, ScheduledSessionStatus.failed),
        session(45, SchedulePlanStatus.confirmed, ScheduledSessionStatus.obsolete),
    ]
    assert calculate_remaining_unscheduled_duration(120, sessions) == 120


def test_backlog_foundation_has_no_provider_or_scheduler_calls() -> None:
    backlog_dir = Path(__file__).parents[2] / "app" / "backlog"
    imported: set[str] = set()
    for source_file in backlog_dir.glob("*.py"):
        tree = ast.parse(source_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(module.startswith("google") for module in imported)
    assert not any(module.startswith("app.calendar_integration") for module in imported)
    assert "app.scheduling.scheduler" not in imported
