from copy import deepcopy
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.ai_intake.types import TaskDraftV2
from app.task_confirmation.errors import (
    DuplicateStepReviewError,
    InvalidConfirmationError,
    MissingConfirmationError,
    StepNotFoundError,
)
from app.task_confirmation.models import (
    BooleanFieldReview,
    DatetimeFieldReview,
    DraftReview,
    IntegerFieldReview,
    ProposedStepReview,
    StringFieldReview,
)
from app.task_confirmation.service import apply_review


def value(
    item: object = None,
    *,
    source: str | None = None,
    confirmation: bool = False,
    explanation: str | None = None,
) -> dict[str, object]:
    confidence = 1.0 if source == "user" else (0.8 if source else None)
    if source not in {None, "user"} and explanation is None:
        explanation = "Derived or estimated value."
    return {
        "value": item,
        "source": source,
        "confidence": confidence,
        "explanation": explanation,
        "requires_confirmation": confirmation,
    }


def draft_payload(*, with_steps: bool = False) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if with_steps:
        steps = [
            {
                "title": value("Research", source="user"),
                "description": value(),
                "duration": value(60, source="estimated", confirmation=True),
                "order": 1,
            },
            {
                "title": value("Write", source="user"),
                "description": value("First draft", source="user"),
                "duration": value(60, source="user"),
                "order": 2,
            },
        ]
    return {
        "title": value("Write article", source="user"),
        "description": value(),
        "duration": value(120, source="estimated", confirmation=True),
        "priority": value(),
        "earliest_start": value(),
        "deadline": value(
            "2026-07-31T23:59:59+02:00",
            source="inferred",
            confirmation=True,
        ),
        "preferred_time_of_day": value(),
        "is_splittable": value(True, source="estimated", confirmation=True),
        "minimum_session_minutes": value(),
        "maximum_sessions_per_day": value(),
        "proposed_steps": steps,
        "clarification_questions": [],
        "prompt_version": "ai-intake.task-draft.v2",
        "schema_version": "task-draft.schema.v2",
    }


def make_draft(*, with_steps: bool = False) -> TaskDraftV2:
    return TaskDraftV2.model_validate(draft_payload(with_steps=with_steps))


def explicit_review(
    *,
    with_step_review: bool = False,
    **overrides: object,
) -> DraftReview:
    data: dict[str, object] = {
        "mode": "explicit",
        "duration": {"decision": "accepted"},
        "deadline": {"decision": "accepted"},
        "is_splittable": {"decision": "accepted"},
        "proposed_steps": (
            [{"original_order": 1, "decision": "accepted"}] if with_step_review else []
        ),
    }
    data.update(overrides)
    return DraftReview.model_validate(data)


def test_accepted_field_review_with_null_override_is_valid() -> None:
    review = StringFieldReview(decision="accepted")

    assert review.value is None


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (StringFieldReview, {"decision": "accepted", "value": "override"}),
        (StringFieldReview, {"decision": "edited"}),
        (StringFieldReview, {"decision": "rejected", "value": "override"}),
        (IntegerFieldReview, {"decision": "edited", "value": 0}),
    ],
)
def test_invalid_field_review_is_rejected(
    model: type[StringFieldReview] | type[IntegerFieldReview],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_edited_boolean_false_is_valid() -> None:
    review = BooleanFieldReview(decision="edited", value=False)

    assert review.value is False


def test_explicit_mode_requires_confirmation() -> None:
    with pytest.raises(
        MissingConfirmationError,
        match="Missing confirmation for field: duration",
    ):
        apply_review(make_draft(), DraftReview())


def test_explicit_mode_accepts_non_required_unreviewed_field() -> None:
    result = apply_review(make_draft(), explicit_review())

    assert result.task.title == "Write article"
    assert "title" in result.audit.accepted_fields


def test_accept_unreviewed_retains_estimates() -> None:
    result = apply_review(
        make_draft(with_steps=True),
        DraftReview(mode="accept_unreviewed"),
    )

    assert result.task.duration_minutes == 120
    assert result.task.is_splittable is True
    assert result.task.steps[0].duration_minutes == 60


def test_conservative_mode_retains_user_and_inferred_values() -> None:
    result = apply_review(
        make_draft(with_steps=True),
        DraftReview(mode="reject_unreviewed_estimates"),
    )

    assert result.task.title == "Write article"
    assert result.task.deadline == datetime.fromisoformat("2026-07-31T23:59:59+02:00")
    assert result.task.steps[0].title == "Research"
    assert result.task.steps[1].duration_minutes == 60


def test_conservative_mode_removes_estimated_and_default_values() -> None:
    payload = draft_payload()
    payload["minimum_session_minutes"] = value(30, source="default", confirmation=True)
    draft = TaskDraftV2.model_validate(payload)

    result = apply_review(
        draft,
        DraftReview(mode="reject_unreviewed_estimates"),
    )

    assert result.task.duration_minutes is None
    assert result.task.minimum_session_minutes is None
    assert result.task.is_splittable is False
    assert {"duration", "minimum_session_minutes", "is_splittable"} <= set(
        result.audit.rejected_fields
    )


def test_accepted_title_works() -> None:
    result = apply_review(
        make_draft(),
        explicit_review(title={"decision": "accepted"}),
    )

    assert result.task.title == "Write article"


def test_edited_title_works() -> None:
    result = apply_review(
        make_draft(),
        explicit_review(title={"decision": "edited", "value": "New title"}),
    )

    assert result.task.title == "New title"


def test_rejected_title_fails() -> None:
    with pytest.raises(InvalidConfirmationError, match="required field: title"):
        apply_review(
            make_draft(),
            explicit_review(title={"decision": "rejected"}),
        )


def test_empty_edited_title_fails() -> None:
    with pytest.raises(InvalidConfirmationError, match="must not be empty"):
        apply_review(
            make_draft(),
            explicit_review(title={"decision": "edited", "value": ""}),
        )


@pytest.mark.parametrize(
    ("duration_review", "expected"),
    [
        ({"decision": "accepted"}, 120),
        ({"decision": "edited", "value": 180}, 180),
        ({"decision": "rejected"}, None),
    ],
)
def test_duration_transformations(
    duration_review: dict[str, object], expected: int | None
) -> None:
    result = apply_review(
        make_draft(),
        explicit_review(duration=duration_review),
    )

    assert result.task.duration_minutes == expected


def test_accept_edit_and_reject_datetime_or_string_fields() -> None:
    result = apply_review(
        make_draft(),
        explicit_review(
            deadline={
                "decision": "edited",
                "value": "2026-08-01T12:00:00+02:00",
            },
            preferred_time_of_day={"decision": "rejected"},
        ),
    )

    assert result.task.deadline == datetime.fromisoformat("2026-08-01T12:00:00+02:00")
    assert result.task.preferred_time_of_day is None


def test_earliest_start_after_edited_deadline_fails() -> None:
    payload = draft_payload()
    payload["earliest_start"] = value("2026-07-30T10:00:00+02:00", source="user")
    draft = TaskDraftV2.model_validate(payload)

    with pytest.raises(InvalidConfirmationError, match="earliest_start"):
        apply_review(
            draft,
            explicit_review(
                deadline={
                    "decision": "edited",
                    "value": "2026-07-29T10:00:00+02:00",
                }
            ),
        )


def test_explicit_false_for_is_splittable_is_preserved() -> None:
    result = apply_review(
        make_draft(),
        explicit_review(is_splittable={"decision": "edited", "value": False}),
    )

    assert result.task.is_splittable is False


def test_accept_all_steps() -> None:
    result = apply_review(
        make_draft(with_steps=True),
        explicit_review(with_step_review=True),
    )

    assert [step.title for step in result.task.steps] == ["Research", "Write"]


def test_edit_step_duration_and_title() -> None:
    result = apply_review(
        make_draft(with_steps=True),
        explicit_review(
            proposed_steps=[
                {
                    "original_order": 1,
                    "decision": "edited",
                    "title": "Deep research",
                    "duration_minutes": 45,
                }
            ]
        ),
    )

    assert result.task.steps[0].title == "Deep research"
    assert result.task.steps[0].duration_minutes == 45


def test_reject_step_removes_it_and_normalizes_order() -> None:
    result = apply_review(
        make_draft(with_steps=True),
        explicit_review(proposed_steps=[{"original_order": 1, "decision": "rejected"}]),
    )

    assert [step.title for step in result.task.steps] == ["Write"]
    assert result.task.steps[0].order == 1
    assert result.audit.step_changes[0].confirmed_value is None


def test_reorder_steps() -> None:
    result = apply_review(
        make_draft(with_steps=True),
        explicit_review(
            proposed_steps=[
                {
                    "original_order": 1,
                    "decision": "edited",
                    "new_order": 2,
                },
                {
                    "original_order": 2,
                    "decision": "edited",
                    "new_order": 1,
                    "title": "Write first",
                },
            ]
        ),
    )

    assert [step.title for step in result.task.steps] == [
        "Write first",
        "Research",
    ]
    assert [step.order for step in result.task.steps] == [1, 2]


def test_duplicate_step_reviews_fail() -> None:
    review = explicit_review(
        proposed_steps=[
            {"original_order": 1, "decision": "accepted"},
            {"original_order": 1, "decision": "rejected"},
        ]
    )

    with pytest.raises(DuplicateStepReviewError, match="original_order=1"):
        apply_review(make_draft(with_steps=True), review)


def test_unknown_step_order_fails() -> None:
    review = explicit_review(
        proposed_steps=[{"original_order": 4, "decision": "accepted"}]
    )

    with pytest.raises(StepNotFoundError, match="original_order=4"):
        apply_review(make_draft(with_steps=True), review)


def test_duplicate_final_order_fails() -> None:
    review = explicit_review(
        proposed_steps=[
            {
                "original_order": 1,
                "decision": "edited",
                "new_order": 2,
            }
        ]
    )

    with pytest.raises(InvalidConfirmationError, match="Duplicate final"):
        apply_review(make_draft(with_steps=True), review)


def test_audit_preserves_original_and_confirmed_values() -> None:
    draft = make_draft()
    original = deepcopy(draft.model_dump())

    result = apply_review(
        draft,
        explicit_review(
            duration={"decision": "edited", "value": 180},
            preferred_time_of_day={"decision": "rejected"},
        ),
    )

    duration_change = next(
        change for change in result.audit.field_changes if change.field == "duration"
    )
    assert duration_change.original_value == 120
    assert duration_change.confirmed_value == 180
    assert "duration" in result.audit.edited_fields
    assert "preferred_time_of_day" in result.audit.rejected_fields
    assert draft.model_dump() == original
    dumped = result.task.model_dump()
    assert "confidence" not in str(dumped)
    assert "explanation" not in str(dumped)


def test_datetime_review_rejects_naive_value() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        DatetimeFieldReview(decision="edited", value=datetime(2026, 7, 31, 12))


def test_step_review_rejects_overrides_when_accepted_or_rejected() -> None:
    with pytest.raises(ValidationError, match="must not contain overrides"):
        ProposedStepReview(
            original_order=1,
            decision="accepted",
            duration_minutes=30,
        )
