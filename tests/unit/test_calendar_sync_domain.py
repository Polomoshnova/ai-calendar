import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.calendar_sync import (
    BusySourceSnapshot,
    ConsistencyIssueCode,
    ConsistencySession,
    ConsistencyStatus,
    DefaultConsistencyChecker,
    SessionWriteTargetSnapshot,
    calendar_context_hash,
    deadline_after_external_move,
)


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 28, hour, minute, tzinfo=UTC)


def consistency_session(
    order: int,
    start: datetime,
    end: datetime,
    *,
    deleted: bool = False,
) -> ConsistencySession:
    return ConsistencySession(
        id=uuid.UUID(int=order),
        order=order,
        start=start,
        end=end,
        externally_deleted=deleted,
    )


def test_calendar_context_hash_is_independent_of_input_order() -> None:
    connection_id = uuid.uuid4()
    session_id = uuid.uuid4()
    busy = [
        BusySourceSnapshot(
            connection_id=connection_id,
            provider="google",
            calendar_id=value,
        )
        for value in ("primary", "team")
    ]
    targets = [
        SessionWriteTargetSnapshot(
            scheduled_session_id=session_id,
            connection_id=connection_id,
            provider="google",
            calendar_id=value,
        )
        for value in ("primary", "focus")
    ]

    first = calendar_context_hash(busy_sources=busy, write_targets=targets)
    second = calendar_context_hash(
        busy_sources=list(reversed(busy)),
        write_targets=list(reversed(targets)),
    )

    assert first == second
    assert len(first) == 64


def test_snapshots_are_strongly_typed_and_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BusySourceSnapshot.model_validate(
            {
                "connection_id": str(uuid.uuid4()),
                "provider": "google",
                "calendar_id": "primary",
                "access_token": "must-not-be-stored",
            }
        )


def test_consistency_checker_detects_all_required_issue_types() -> None:
    checker = DefaultConsistencyChecker()
    sessions = [
        consistency_session(2, dt(9), dt(10)),
        consistency_session(1, dt(9, 30), dt(10, 30)),
        consistency_session(3, dt(10, 35), dt(11, 30)),
        consistency_session(4, dt(12), dt(13), deleted=True),
    ]

    result = checker.check(
        sessions,
        minimum_break_minutes=15,
        deadline=dt(11),
    )

    assert result.status is ConsistencyStatus.inconsistent
    assert {issue.code for issue in result.issues} == {
        ConsistencyIssueCode.invalid_session_order,
        ConsistencyIssueCode.session_overlap,
        ConsistencyIssueCode.minimum_break_violation,
        ConsistencyIssueCode.latest_session_after_deadline,
        ConsistencyIssueCode.externally_deleted_session,
    }


def test_consistency_checker_uses_half_open_interval_semantics() -> None:
    result = DefaultConsistencyChecker().check(
        [
            consistency_session(1, dt(9), dt(10)),
            consistency_session(2, dt(10), dt(11)),
        ]
    )

    assert result.status is ConsistencyStatus.consistent
    assert result.issues == ()


def test_external_move_deadline_policy_only_extends_deadline() -> None:
    assert deadline_after_external_move(dt(12), [dt(10), dt(13)]) == dt(13)
    assert deadline_after_external_move(dt(14), [dt(10), dt(13)]) == dt(14)
    assert deadline_after_external_move(None, [dt(10), dt(13)]) == dt(13)
    assert deadline_after_external_move(None, []) is None
