import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.ai_intake.gateway import (
    AIGateway,
    AIProviderError,
    InvalidAIOutputError,
)
from app.ai_intake.types import TaskDraftV2
from app.availability import TimeInterval
from app.domain.tasks import PreferredTimeOfDay, TaskPriority
from app.scheduling import SchedulingTask
from app.schemas.scheduling import SchedulePreviewResponse
from app.services.scheduling import SchedulePreview, generate_schedule_preview
from app.task_confirmation.errors import ConfirmationError
from app.task_confirmation.models import ConfirmationResult, ConfirmedTask
from app.task_confirmation.service import apply_review
from app.workflows.errors import (
    WorkflowAIError,
    WorkflowConfirmationError,
    WorkflowSchedulingError,
    WorkflowValidationError,
)
from app.workflows.models import (
    WORKFLOW_VERSION,
    SchedulerInputSnapshot,
    SchedulerResolvedValues,
    SchedulerTaskSnapshot,
    SchedulerValueSource,
    TaskToSchedulePreviewRequest,
    TaskToSchedulePreviewResponse,
    WorkflowStage,
    WorkflowTraceEntry,
    WorkflowTraceStatus,
)

WORKFLOW_TASK_ID = "workflow-task-1"
logger = logging.getLogger(__name__)
SchedulingPreviewCallable = Callable[..., SchedulePreview]


@dataclass(frozen=True)
class SchedulerMapping:
    task: SchedulingTask
    snapshot: SchedulerTaskSnapshot
    warnings: tuple[str, ...]
    step_duration_sum: int | None
    defaulted_fields: tuple[str, ...]
    confirmed_fields: tuple[str, ...]


def _priority(value: str | None) -> TaskPriority:
    if value is None:
        return TaskPriority.medium
    try:
        return TaskPriority(value)
    except ValueError as exc:
        raise WorkflowValidationError(
            "invalid_confirmed_priority",
            WorkflowStage.scheduler_mapping,
            f"Confirmed task has unsupported priority: {value}",
        ) from exc


def _preferred_time(value: str | None) -> PreferredTimeOfDay:
    if value is None:
        return PreferredTimeOfDay.any
    try:
        return PreferredTimeOfDay(value)
    except ValueError as exc:
        raise WorkflowValidationError(
            "invalid_confirmed_preferred_time",
            WorkflowStage.scheduler_mapping,
            f"Confirmed task has unsupported preferred_time_of_day: {value}",
        ) from exc


def map_confirmed_task_to_scheduler_task(
    confirmed_task: ConfirmedTask,
    *,
    task_id: str,
    default_minimum_session_minutes: int,
) -> SchedulerMapping:
    """Pure deterministic mapping into the scheduler's existing task type."""
    if confirmed_task.duration_minutes is None:
        raise WorkflowValidationError(
            "missing_confirmed_duration",
            WorkflowStage.scheduler_mapping,
            "Confirmed task has no duration and cannot be scheduled.",
        )
    if confirmed_task.duration_minutes <= 0:
        raise WorkflowValidationError(
            "invalid_confirmed_duration",
            WorkflowStage.scheduler_mapping,
            "Confirmed task duration must be positive.",
        )

    warnings: list[str] = []
    known_step_durations = [
        step.duration_minutes
        for step in confirmed_task.steps
        if step.duration_minutes is not None
    ]
    all_steps_have_duration = len(known_step_durations) == len(confirmed_task.steps)
    step_sum = (
        sum(known_step_durations)
        if confirmed_task.steps and all_steps_have_duration
        else None
    )
    if step_sum is not None and step_sum != confirmed_task.duration_minutes:
        warnings.append(
            "Conceptual step duration sum differs from confirmed total duration; "
            "the confirmed total was retained."
        )
    if confirmed_task.steps:
        warnings.append(
            "Conceptual steps are preserved in the snapshot but are not scheduled "
            "independently."
        )

    priority = _priority(confirmed_task.priority)
    preferred_time = _preferred_time(confirmed_task.preferred_time_of_day)
    minimum_session = (
        confirmed_task.minimum_session_minutes or default_minimum_session_minutes
    )
    maximum_sessions = confirmed_task.maximum_sessions_per_day or 1
    value_sources = SchedulerResolvedValues(
        priority=(
            SchedulerValueSource.confirmed
            if confirmed_task.priority is not None
            else SchedulerValueSource.scheduler_default
        ),
        preferred_time_of_day=(
            SchedulerValueSource.confirmed
            if confirmed_task.preferred_time_of_day is not None
            else SchedulerValueSource.scheduler_default
        ),
        minimum_session_minutes=(
            SchedulerValueSource.confirmed
            if confirmed_task.minimum_session_minutes is not None
            else SchedulerValueSource.scheduler_default
        ),
        maximum_sessions_per_day=(
            SchedulerValueSource.confirmed
            if confirmed_task.maximum_sessions_per_day is not None
            else SchedulerValueSource.scheduler_default
        ),
        is_splittable=SchedulerValueSource.confirmed,
    )
    source_values = value_sources.model_dump()
    defaulted_fields = tuple(
        field
        for field, source in source_values.items()
        if source == SchedulerValueSource.scheduler_default
    )
    confirmed_fields = tuple(
        field
        for field, source in source_values.items()
        if source == SchedulerValueSource.confirmed
    )
    if defaulted_fields:
        warnings.append(f"Scheduler defaults applied: {', '.join(defaulted_fields)}.")
    task = SchedulingTask(
        id=task_id,
        duration_minutes=confirmed_task.duration_minutes,
        priority=priority,
        earliest_start=confirmed_task.earliest_start,
        deadline=confirmed_task.deadline,
        preferred_time_of_day=preferred_time,
        is_splittable=confirmed_task.is_splittable,
        minimum_session_minutes=minimum_session,
        maximum_sessions_per_day=maximum_sessions,
    )
    snapshot = SchedulerTaskSnapshot(
        id=task.id,
        title=confirmed_task.title,
        description=confirmed_task.description,
        duration_minutes=task.duration_minutes,
        priority=task.priority.value,
        earliest_start=task.earliest_start,
        deadline=task.deadline,
        preferred_time_of_day=task.preferred_time_of_day.value,
        is_splittable=task.is_splittable,
        minimum_session_minutes=task.minimum_session_minutes,
        maximum_sessions_per_day=task.maximum_sessions_per_day,
        steps=confirmed_task.steps,
        value_sources=value_sources,
    )
    return SchedulerMapping(
        task=task,
        snapshot=snapshot,
        warnings=tuple(warnings),
        step_duration_sum=step_sum,
        defaulted_fields=defaulted_fields,
        confirmed_fields=confirmed_fields,
    )


def _trace(
    stage: WorkflowStage,
    summary: str,
    *,
    warnings: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> WorkflowTraceEntry:
    entry = WorkflowTraceEntry(
        stage=stage,
        status=WorkflowTraceStatus.completed,
        summary=summary,
        warnings=list(warnings),
        metadata=metadata or {},
    )
    logger.info(
        "Workflow stage completed",
        extra={
            "workflow_version": WORKFLOW_VERSION,
            "stage": stage.value,
            "status": entry.status.value,
            "warning_count": len(warnings),
        },
    )
    return entry


def _validate_window_constraints(
    task: ConfirmedTask, request: TaskToSchedulePreviewRequest
) -> tuple[str, ...]:
    context = request.scheduling_context
    window_start = context.window_start
    window_end = context.window_end
    if task.deadline is not None and task.deadline < window_start:
        raise WorkflowValidationError(
            "deadline_before_preview_window",
            WorkflowStage.scheduler_mapping,
            "Confirmed task deadline is before the scheduling window.",
        )
    if task.earliest_start is not None and task.earliest_start > window_end:
        raise WorkflowValidationError(
            "earliest_start_after_preview_window",
            WorkflowStage.scheduler_mapping,
            "Confirmed task earliest_start is after the scheduling window.",
        )
    warnings: list[str] = []
    if task.deadline is not None and task.deadline > window_end:
        warnings.append(
            "Confirmed deadline is after the preview window; scheduling is limited "
            "to the requested window."
        )
    return tuple(warnings)


def execute_task_to_schedule_preview(
    request: TaskToSchedulePreviewRequest,
    *,
    ai_gateway: AIGateway,
    scheduling_preview: SchedulingPreviewCallable = generate_schedule_preview,
) -> TaskToSchedulePreviewResponse:
    trace: list[WorkflowTraceEntry] = []
    ai_context = request.ai_context
    try:
        draft: TaskDraftV2 = ai_gateway.analyze(
            request.text,
            current_time=(
                ai_context.current_datetime if ai_context is not None else None
            ),
            user_timezone=ai_context.timezone if ai_context is not None else None,
        )
    except (AIProviderError, InvalidAIOutputError) as exc:
        raise WorkflowAIError(
            "ai_intake_failed",
            WorkflowStage.ai_intake,
            "AI task intake failed.",
        ) from exc
    trace.append(
        _trace(
            WorkflowStage.ai_intake,
            "AI task draft created.",
            metadata={
                "prompt_version": draft.prompt_version,
                "schema_version": draft.schema_version,
                "clarification_question_count": len(draft.clarification_questions),
                "confirmation_required_field_count": sum(
                    getattr(draft, name).requires_confirmation
                    for name in (
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
                ),
            },
        )
    )

    try:
        confirmation: ConfirmationResult = apply_review(draft, request.review)
    except ConfirmationError as exc:
        raise WorkflowConfirmationError(
            "confirmation_failed",
            WorkflowStage.confirmation,
            str(exc),
        ) from exc
    trace.append(
        _trace(
            WorkflowStage.confirmation,
            "Draft review applied.",
            metadata={
                "accepted_field_count": len(confirmation.audit.accepted_fields),
                "edited_field_count": len(confirmation.audit.edited_fields),
                "rejected_field_count": len(confirmation.audit.rejected_fields),
                "confirmed_duration_minutes": (confirmation.task.duration_minutes),
                "confirmed_deadline": confirmation.task.deadline,
                "confirmed_step_count": len(confirmation.task.steps),
            },
        )
    )

    window_warnings = _validate_window_constraints(confirmation.task, request)
    mapping = map_confirmed_task_to_scheduler_task(
        confirmation.task,
        task_id=WORKFLOW_TASK_ID,
        default_minimum_session_minutes=(
            request.scheduling_context.preferences.default_minimum_session_minutes
        ),
    )
    mapping_warnings = window_warnings + mapping.warnings
    scheduling_context = request.scheduling_context
    all_tasks = (
        mapping.task,
        *(task.to_domain() for task in scheduling_context.existing_pending_tasks),
    )
    scheduler_input = SchedulerInputSnapshot(
        task=mapping.snapshot,
        existing_pending_tasks=scheduling_context.existing_pending_tasks,
        window_start=scheduling_context.window_start,
        window_end=scheduling_context.window_end,
        timezone=scheduling_context.timezone,
        busy_interval_count=len(scheduling_context.busy_intervals),
        pending_task_count=len(all_tasks),
    )
    trace.append(
        _trace(
            WorkflowStage.scheduler_mapping,
            "Confirmed task mapped to scheduler input.",
            warnings=mapping_warnings,
            metadata={
                "duration_minutes": mapping.task.duration_minutes,
                "step_count": len(confirmation.task.steps),
                "step_duration_sum": mapping.step_duration_sum,
                "defaulted_fields": list(mapping.defaulted_fields),
                "confirmed_fields": list(mapping.confirmed_fields),
                "window_start": scheduling_context.window_start,
                "window_end": scheduling_context.window_end,
                "timezone": scheduling_context.timezone,
                "busy_interval_count": len(scheduling_context.busy_intervals),
                "pending_task_count": len(all_tasks),
            },
        )
    )

    try:
        preview = scheduling_preview(
            planning_window=TimeInterval(
                scheduling_context.window_start,
                scheduling_context.window_end,
            ),
            busy_intervals=tuple(
                interval.to_domain() for interval in scheduling_context.busy_intervals
            ),
            tasks=all_tasks,
            preferences=scheduling_context.preferences.to_domain(),
        )
    except ValueError as exc:
        raise WorkflowSchedulingError(
            "scheduling_preview_failed",
            WorkflowStage.scheduling_preview,
            "Scheduling preview failed.",
        ) from exc
    preview_response = SchedulePreviewResponse.from_domain(
        preview.planning_window,
        preview.free_intervals,
        preview.result,
    )
    trace.append(
        _trace(
            WorkflowStage.scheduling_preview,
            "Scheduling preview completed.",
            metadata={
                "scheduled_block_count": len(preview_response.scheduled_blocks),
                "unscheduled_task_count": len(preview_response.unscheduled_tasks),
                "warning_count": len(preview_response.warnings),
                "scheduler_version": preview_response.scheduler_version,
            },
        )
    )
    return TaskToSchedulePreviewResponse(
        draft=draft,
        confirmation=confirmation,
        scheduler_input=scheduler_input,
        schedule_preview=preview_response,
        trace=trace if request.include_trace else [],
        workflow_version=WORKFLOW_VERSION,
    )
