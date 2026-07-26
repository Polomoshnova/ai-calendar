from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.ai_intake.types import (
    DraftValueBase,
    ProposedStepV2,
    TaskDraftV2,
    ValueSource,
)
from app.task_confirmation.errors import (
    DuplicateStepReviewError,
    InvalidConfirmationError,
    MissingConfirmationError,
    StepNotFoundError,
)
from app.task_confirmation.models import (
    BooleanFieldReview,
    ConfirmationAudit,
    ConfirmationResult,
    ConfirmedTask,
    ConfirmedTaskStep,
    DatetimeFieldReview,
    DraftReview,
    FieldChange,
    IntegerFieldReview,
    ProposedStepReview,
    ReviewDecision,
    ReviewMode,
    StepChange,
    StringFieldReview,
)

FIELD_NAMES = (
    "title",
    "description",
    "duration",
    "priority",
    "earliest_start",
    "deadline",
    "preferred_time_of_day",
    "is_splittable",
    "minimum_session_minutes",
    "maximum_sessions_per_day",
)

FieldReview = (
    StringFieldReview | IntegerFieldReview | BooleanFieldReview | DatetimeFieldReview
)


@dataclass(frozen=True)
class ResolvedField:
    value: Any
    decision: ReviewDecision


def _resolve_field(
    name: str,
    draft_value: DraftValueBase,
    original_value: Any,
    field_review: FieldReview | None,
    mode: ReviewMode,
) -> ResolvedField:
    if field_review is not None:
        if field_review.decision is ReviewDecision.accepted:
            return ResolvedField(original_value, ReviewDecision.accepted)
        if field_review.decision is ReviewDecision.edited:
            return ResolvedField(field_review.value, ReviewDecision.edited)
        return ResolvedField(None, ReviewDecision.rejected)

    if mode is ReviewMode.accept_unreviewed:
        return ResolvedField(original_value, ReviewDecision.accepted)
    if mode is ReviewMode.reject_unreviewed_estimates and draft_value.source in {
        ValueSource.estimated,
        ValueSource.default,
    }:
        return ResolvedField(None, ReviewDecision.rejected)
    if mode is ReviewMode.explicit and draft_value.requires_confirmation:
        raise MissingConfirmationError(f"Missing confirmation for field: {name}")
    return ResolvedField(original_value, ReviewDecision.accepted)


def _step_dict(
    *,
    title: str,
    description: str | None,
    duration_minutes: int | None,
    order: int,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "duration_minutes": duration_minutes,
        "order": order,
    }


def _original_step_dict(step: ProposedStepV2) -> dict[str, Any]:
    assert step.title.value is not None
    return _step_dict(
        title=step.title.value,
        description=step.description.value,
        duration_minutes=step.duration.value,
        order=step.order,
    )


def _unreviewed_step_values(
    step: ProposedStepV2, mode: ReviewMode
) -> tuple[str, str | None, int | None]:
    assert step.title.value is not None
    if mode is ReviewMode.explicit and (
        step.title.requires_confirmation
        or step.description.requires_confirmation
        or step.duration.requires_confirmation
    ):
        raise MissingConfirmationError(
            f"Missing confirmation for proposed step: original_order={step.order}"
        )
    if mode is not ReviewMode.reject_unreviewed_estimates:
        return step.title.value, step.description.value, step.duration.value

    def conservative_value(field: Any) -> Any:
        if field.source in {ValueSource.estimated, ValueSource.default}:
            return None
        return field.value

    title = conservative_value(step.title)
    if title is None:
        raise InvalidConfirmationError(
            f"Unreviewed proposed step title cannot be removed: "
            f"original_order={step.order}"
        )
    return (
        title,
        conservative_value(step.description),
        conservative_value(step.duration),
    )


def _resolve_steps(
    draft: TaskDraftV2, review: DraftReview
) -> tuple[list[ConfirmedTaskStep], list[StepChange]]:
    reviews_by_order: dict[int, ProposedStepReview] = {}
    for candidate_review in review.proposed_steps:
        if candidate_review.original_order in reviews_by_order:
            raise DuplicateStepReviewError(
                "Duplicate review for proposed step "
                f"original_order={candidate_review.original_order}"
            )
        reviews_by_order[candidate_review.original_order] = candidate_review

    draft_by_order = {step.order: step for step in draft.proposed_steps}
    for original_order in reviews_by_order:
        if original_order not in draft_by_order:
            raise StepNotFoundError(
                f"No proposed step exists with original_order={original_order}"
            )

    resolved: list[tuple[int, int, ReviewDecision, dict[str, Any], dict[str, Any]]] = []
    rejected_changes: list[StepChange] = []
    for step in draft.proposed_steps:
        original = _original_step_dict(step)
        step_review = reviews_by_order.get(step.order)
        title: str | None
        description: str | None
        duration: int | None
        if step_review is None:
            title, description, duration = _unreviewed_step_values(step, review.mode)
            decision = (
                ReviewDecision.edited
                if (
                    review.mode is ReviewMode.reject_unreviewed_estimates
                    and duration is None
                    and step.duration.value is not None
                )
                else ReviewDecision.accepted
            )
            requested_order = step.order
        elif step_review.decision is ReviewDecision.rejected:
            rejected_changes.append(
                StepChange(
                    original_order=step.order,
                    decision=ReviewDecision.rejected,
                    original_value=original,
                    confirmed_value=None,
                )
            )
            continue
        elif step_review.decision is ReviewDecision.accepted:
            title = step.title.value
            description = step.description.value
            duration = step.duration.value
            decision = ReviewDecision.accepted
            requested_order = step.order
        else:
            title = step_review.title or step.title.value
            description = (
                step_review.description
                if step_review.description is not None
                else step.description.value
            )
            duration = (
                step_review.duration_minutes
                if step_review.duration_minutes is not None
                else step.duration.value
            )
            decision = ReviewDecision.edited
            requested_order = step_review.new_order or step.order

        if title is None or not title.strip():
            raise InvalidConfirmationError(
                f"Confirmed proposed step title is empty: original_order={step.order}"
            )
        pending = _step_dict(
            title=title,
            description=description,
            duration_minutes=duration,
            order=requested_order,
        )
        resolved.append((requested_order, step.order, decision, original, pending))

    requested_orders = [item[0] for item in resolved]
    if len(requested_orders) != len(set(requested_orders)):
        raise InvalidConfirmationError("Duplicate final proposed step order")

    resolved.sort(key=lambda item: (item[0], item[1]))
    steps: list[ConfirmedTaskStep] = []
    changes: list[StepChange] = []
    for final_order, (_, original_order, decision, original, pending) in enumerate(
        resolved, 1
    ):
        confirmed = {**pending, "order": final_order}
        steps.append(ConfirmedTaskStep.model_validate(confirmed))
        changes.append(
            StepChange(
                original_order=original_order,
                decision=decision,
                original_value=original,
                confirmed_value=confirmed,
            )
        )
    changes.extend(rejected_changes)
    changes.sort(key=lambda change: change.original_order)
    return steps, changes


def apply_review(draft: TaskDraftV2, review: DraftReview) -> ConfirmationResult:
    """Resolve a user review without mutating inputs or touching external state."""
    resolved: dict[str, ResolvedField] = {}
    field_changes: list[FieldChange] = []
    for name in FIELD_NAMES:
        draft_value = getattr(draft, name)
        original_value = draft_value.value
        field_review = getattr(review, name)
        resolution = _resolve_field(
            name, draft_value, original_value, field_review, review.mode
        )
        resolved[name] = resolution

    title = resolved["title"].value
    if title is None:
        raise InvalidConfirmationError("Cannot reject required field: title")
    if not isinstance(title, str) or not title.strip():
        raise InvalidConfirmationError("Confirmed title must not be empty")

    is_splittable = resolved["is_splittable"].value
    if is_splittable is None:
        is_splittable = False

    for name in FIELD_NAMES:
        resolution = resolved[name]
        confirmed_value = is_splittable if name == "is_splittable" else resolution.value
        field_changes.append(
            FieldChange(
                field=name,
                decision=resolution.decision,
                original_value=getattr(draft, name).value,
                confirmed_value=confirmed_value,
            )
        )

    steps, step_changes = _resolve_steps(draft, review)
    try:
        task = ConfirmedTask(
            title=title,
            description=resolved["description"].value,
            duration_minutes=resolved["duration"].value,
            priority=resolved["priority"].value,
            earliest_start=resolved["earliest_start"].value,
            deadline=resolved["deadline"].value,
            preferred_time_of_day=resolved["preferred_time_of_day"].value,
            is_splittable=is_splittable,
            minimum_session_minutes=resolved["minimum_session_minutes"].value,
            maximum_sessions_per_day=resolved["maximum_sessions_per_day"].value,
            steps=steps,
        )
    except ValidationError as exc:
        raise InvalidConfirmationError(
            f"Confirmed task validation failed: {exc.errors()[0]['msg']}"
        ) from exc

    accepted = [
        change.field
        for change in field_changes
        if change.decision is ReviewDecision.accepted
    ]
    edited = [
        change.field
        for change in field_changes
        if change.decision is ReviewDecision.edited
    ]
    rejected = [
        change.field
        for change in field_changes
        if change.decision is ReviewDecision.rejected
    ]
    return ConfirmationResult(
        task=task,
        audit=ConfirmationAudit(
            field_changes=field_changes,
            step_changes=step_changes,
            accepted_fields=accepted,
            edited_fields=edited,
            rejected_fields=rejected,
        ),
    )
