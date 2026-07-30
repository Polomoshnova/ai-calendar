import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.calendar_integration.models import CalendarBusyInterval
from app.schedule_plans.models import ScheduledSession, SchedulePlan
from app.schedule_plans.revalidation import detect_conflicts, sessions_hash
from app.schedule_plans.revalidation_schemas import SchedulePlanConflictType


def dt(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 7, 28, hour, minute, second, tzinfo=UTC)


def plan_with_session(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> SchedulePlan:
    start = start or dt(10)
    end = end or dt(11)
    plan = SchedulePlan(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        version=3,
    )
    plan.sessions = [
        ScheduledSession(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            order=1,
            start=start,
            end=end,
            duration_minutes=int((end - start).total_seconds() // 60),
        )
    ]
    return plan


def busy(start: datetime, end: datetime) -> CalendarBusyInterval:
    return CalendarBusyInterval(start=start, end=end, calendar_id="primary")


@pytest.mark.parametrize(
    ("interval", "expected_overlap"),
    [
        (busy(dt(11), dt(11, 30)), False),
        (busy(dt(9, 30), dt(10)), False),
        (busy(dt(10, 59, 59), dt(11, 30)), True),
        (busy(dt(9), dt(12)), True),
        (busy(dt(10, 15), dt(10, 30)), True),
    ],
)
def test_direct_overlap_uses_half_open_intervals(
    interval: CalendarBusyInterval,
    expected_overlap: bool,
) -> None:
    conflicts = detect_conflicts(
        plan_with_session(),
        [interval],
        provider="google",
        minimum_break_minutes=0,
    )

    assert bool(conflicts) is expected_overlap
    if conflicts:
        assert conflicts[0].conflict_type is SchedulePlanConflictType.direct_overlap
        assert conflicts[0].reason_code == "session_overlaps_provider_busy"
        assert conflicts[0].overlap_minutes >= 1


def test_minimum_break_violation_is_distinct_from_overlap() -> None:
    conflicts = detect_conflicts(
        plan_with_session(),
        [busy(dt(11, 10), dt(12))],
        provider="google",
        minimum_break_minutes=15,
    )

    assert len(conflicts) == 1
    assert conflicts[0].conflict_type is (
        SchedulePlanConflictType.minimum_break_violation
    )
    assert conflicts[0].overlap_minutes == 0
    assert conflicts[0].reason_code == "minimum_break_violation"


def test_exact_minimum_break_and_disabled_break_are_valid() -> None:
    exact = detect_conflicts(
        plan_with_session(),
        [busy(dt(11, 15), dt(12))],
        provider="google",
        minimum_break_minutes=15,
    )
    disabled = detect_conflicts(
        plan_with_session(),
        [busy(dt(11, 1), dt(12))],
        provider="google",
        minimum_break_minutes=0,
    )

    assert exact == []
    assert disabled == []


def test_only_conflicting_sessions_are_returned() -> None:
    plan = plan_with_session()
    plan.sessions.append(
        ScheduledSession(
            id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            order=2,
            start=dt(14),
            end=dt(15),
            duration_minutes=60,
        )
    )

    conflicts = detect_conflicts(
        plan,
        [busy(dt(10, 30), dt(10, 45))],
        provider="google",
        minimum_break_minutes=0,
    )

    assert [item.session_order for item in conflicts] == [1]


def test_sessions_hash_is_stable_and_uses_scheduling_fields() -> None:
    plan = plan_with_session()
    first = sessions_hash(plan)
    plan.status = "confirmed"  # type: ignore[assignment]
    plan.sessions[0].failure_code = "future-sync-failure"

    assert sessions_hash(plan) == first
    plan.sessions[0].start = dt(10, 1)
    assert sessions_hash(plan) != first


def test_revalidation_layer_has_no_google_ai_or_scheduler_imports() -> None:
    package = Path(__file__).parents[2] / "app" / "schedule_plans"
    files = [
        package / "revalidation.py",
        package / "revalidation_models.py",
        package / "revalidation_schemas.py",
    ]
    for source_file in files:
        tree = ast.parse(source_file.read_text())
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            module == root or module.startswith(f"{root}.")
            for module in modules
            for root in {
                "google",
                "openai",
                "app.calendar_integration.google",
                "app.ai_intake",
                "app.scheduling.scheduler",
            }
        )
