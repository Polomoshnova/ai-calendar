from copy import deepcopy
from datetime import datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.ai_intake.gateway import AIGateway, AIProviderError
from app.ai_intake.types import TaskDraftV2
from app.availability import TimeInterval
from app.schemas.scheduling import SchedulePreviewResponse
from app.services.scheduling import generate_schedule_preview
from app.task_confirmation.models import ConfirmedTask, ConfirmedTaskStep
from app.workflows.errors import (
    WorkflowAIError,
    WorkflowConfirmationError,
    WorkflowValidationError,
)
from app.workflows.models import (
    WORKFLOW_VERSION,
    SchedulerTaskSnapshot,
    SchedulerTaskValueSources,
    SchedulerValueSource,
    TaskToSchedulePreviewRequest,
    WorkflowSchedulingContext,
)
from app.workflows.task_to_schedule_preview import (
    execute_task_to_schedule_preview,
    map_confirmed_task_to_scheduler_task,
)


def value(
    item: object = None,
    *,
    source: str | None = None,
    confirmation: bool = False,
) -> dict[str, object]:
    return {
        "value": item,
        "source": source,
        "confidence": 1.0 if source == "user" else (0.8 if source else None),
        "explanation": (
            "Derived or estimated value." if source not in {None, "user"} else None
        ),
        "requires_confirmation": confirmation,
    }


def draft_payload(
    *,
    duration: int | None = 120,
    deadline: str | None = "2026-07-31T17:00:00+02:00",
    earliest_start: str | None = None,
    with_steps: bool = False,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if with_steps:
        steps = [
            {
                "title": value("Research", source="user"),
                "description": value(),
                "duration": value(45, source="user"),
                "order": 1,
            },
            {
                "title": value("Write", source="user"),
                "description": value(),
                "duration": value(45, source="user"),
                "order": 2,
            },
        ]
    return {
        "title": value("Prepare presentation", source="user"),
        "description": value("Investor presentation", source="user"),
        "duration": (
            value(duration, source="estimated", confirmation=True)
            if duration is not None
            else value()
        ),
        "priority": value("high", source="user"),
        "earliest_start": (
            value(earliest_start, source="inferred") if earliest_start else value()
        ),
        "deadline": (
            value(deadline, source="inferred", confirmation=True)
            if deadline
            else value()
        ),
        "preferred_time_of_day": value("morning", source="user"),
        "is_splittable": value(True, source="estimated", confirmation=True),
        "minimum_session_minutes": value(30, source="estimated", confirmation=True),
        "maximum_sessions_per_day": value(2, source="user"),
        "proposed_steps": steps,
        "clarification_questions": [],
        "prompt_version": "ai-intake.task-draft.v2",
        "schema_version": "task-draft.schema.v2",
    }


def make_draft(**kwargs: object) -> TaskDraftV2:
    return TaskDraftV2.model_validate(draft_payload(**kwargs))


def working_hours(*, weekdays: bool = True) -> dict[str, list[dict[str, str]]]:
    window = [{"start": "09:00", "end": "18:00"}]
    return {
        "monday": window if weekdays else [],
        "tuesday": window if weekdays else [],
        "wednesday": window if weekdays else [],
        "thursday": window if weekdays else [],
        "friday": window if weekdays else [],
        "saturday": [],
        "sunday": [],
    }


def request_payload(
    *,
    review: dict[str, object] | None = None,
    window_start: str = "2026-07-27T08:00:00+02:00",
    window_end: str = "2026-07-31T20:00:00+02:00",
    timezone: str = "Europe/Warsaw",
    busy_intervals: list[dict[str, str]] | None = None,
    existing_tasks: list[dict[str, object]] | None = None,
    weekdays: bool = True,
) -> dict[str, Any]:
    return {
        "text": "Prepare presentation by Friday",
        "review": review
        or {
            "mode": "explicit",
            "duration": {"decision": "accepted"},
            "deadline": {"decision": "accepted"},
            "is_splittable": {"decision": "accepted"},
            "minimum_session_minutes": {"decision": "accepted"},
        },
        "ai_context": {
            "current_datetime": "2026-07-26T15:00:00+02:00",
            "timezone": timezone,
        },
        "scheduling_context": {
            "window_start": window_start,
            "window_end": window_end,
            "timezone": timezone,
            "busy_intervals": busy_intervals or [],
            "preferences": {
                "timezone": timezone,
                "working_hours": working_hours(weekdays=weekdays),
                "preferred_task_time": "any",
                "minimum_break_minutes": 15,
                "no_deep_work_after": "17:00",
                "default_minimum_session_minutes": 15,
            },
            "existing_pending_tasks": existing_tasks or [],
        },
        "include_trace": True,
    }


class FakeGateway:
    def __init__(self, result: TaskDraftV2 | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def analyze(
        self,
        text: str,
        *,
        current_time: datetime | None = None,
        user_timezone: str | None = None,
    ) -> TaskDraftV2:
        self.calls.append(
            {
                "text": text,
                "current_time": current_time,
                "user_timezone": user_timezone,
            }
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def fake_gateway(result: TaskDraftV2 | Exception) -> AIGateway:
    return cast(AIGateway, FakeGateway(result))


@pytest.mark.parametrize(
    "text",
    ["", "   ", "\n\t"],
)
def test_request_rejects_empty_text(text: str) -> None:
    payload = request_payload()
    payload["text"] = text

    with pytest.raises(ValidationError):
        TaskToSchedulePreviewRequest.model_validate(payload)


def test_request_rejects_naive_current_datetime() -> None:
    payload = request_payload()
    payload["ai_context"]["current_datetime"] = "2026-07-26T15:00:00"

    with pytest.raises(ValidationError, match="timezone-aware"):
        TaskToSchedulePreviewRequest.model_validate(payload)


def test_request_rejects_invalid_timezone() -> None:
    payload = request_payload(timezone="Mars/Olympus")

    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        TaskToSchedulePreviewRequest.model_validate(payload)


def test_request_rejects_reversed_or_long_window() -> None:
    with pytest.raises(ValidationError, match="before"):
        TaskToSchedulePreviewRequest.model_validate(
            request_payload(
                window_start="2026-07-31T20:00:00+02:00",
                window_end="2026-07-27T08:00:00+02:00",
            )
        )
    with pytest.raises(ValidationError, match="31 days"):
        TaskToSchedulePreviewRequest.model_validate(
            request_payload(
                window_end="2026-09-01T20:00:00+02:00",
            )
        )


def test_request_rejects_invalid_busy_interval() -> None:
    payload = request_payload(
        busy_intervals=[
            {
                "start": "2026-07-27T11:00:00+02:00",
                "end": "2026-07-27T10:00:00+02:00",
            }
        ]
    )

    with pytest.raises(ValidationError, match="before"):
        TaskToSchedulePreviewRequest.model_validate(payload)


def test_successful_workflow_calls_each_stage_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = TaskToSchedulePreviewRequest.model_validate(request_payload())
    gateway = FakeGateway(make_draft())
    confirmation_calls = 0
    scheduler_calls = 0

    from app.workflows import task_to_schedule_preview as workflow_module

    original_apply_review = workflow_module.apply_review

    def tracked_confirmation(*args: object, **kwargs: object) -> object:
        nonlocal confirmation_calls
        confirmation_calls += 1
        return original_apply_review(*args, **kwargs)

    def tracked_scheduler(**kwargs: object) -> object:
        nonlocal scheduler_calls
        scheduler_calls += 1
        return generate_schedule_preview(**kwargs)

    monkeypatch.setattr(workflow_module, "apply_review", tracked_confirmation)
    result = execute_task_to_schedule_preview(
        request,
        ai_gateway=cast(AIGateway, gateway),
        scheduling_preview=tracked_scheduler,
    )

    assert len(gateway.calls) == 1
    assert confirmation_calls == 1
    assert scheduler_calls == 1
    assert result.draft.title.value == "Prepare presentation"
    assert result.confirmation.task.duration_minutes == 120
    assert result.scheduler_input.task.duration_minutes == 120
    assert result.schedule_preview.scheduler_version
    assert result.workflow_version == WORKFLOW_VERSION
    assert [entry.stage.value for entry in result.trace] == [
        "ai_intake",
        "confirmation",
        "scheduler_mapping",
        "scheduling_preview",
    ]
    assert gateway.calls[0]["user_timezone"] == "Europe/Warsaw"


def test_edited_values_reach_scheduler_input() -> None:
    payload = request_payload()
    payload["review"]["duration"] = {"decision": "edited", "value": 180}
    payload["review"]["deadline"] = {
        "decision": "edited",
        "value": "2026-07-30T17:00:00+02:00",
    }
    payload["review"]["is_splittable"] = {
        "decision": "edited",
        "value": False,
    }
    request = TaskToSchedulePreviewRequest.model_validate(payload)

    result = execute_task_to_schedule_preview(
        request, ai_gateway=fake_gateway(make_draft())
    )

    assert result.scheduler_input.task.duration_minutes == 180
    assert result.scheduler_input.task.deadline == datetime.fromisoformat(
        "2026-07-30T17:00:00+02:00"
    )
    assert result.scheduler_input.task.is_splittable is False


def test_missing_confirmation_stops_before_scheduler() -> None:
    request = TaskToSchedulePreviewRequest.model_validate(
        request_payload(review={"mode": "explicit"})
    )
    scheduler_called = False

    def scheduler(**kwargs: object) -> object:
        nonlocal scheduler_called
        scheduler_called = True
        raise AssertionError

    with pytest.raises(WorkflowConfirmationError):
        execute_task_to_schedule_preview(
            request,
            ai_gateway=fake_gateway(make_draft()),
            scheduling_preview=scheduler,
        )

    assert scheduler_called is False


def test_rejected_or_missing_duration_stops_before_scheduler() -> None:
    payload = request_payload()
    payload["review"]["duration"] = {"decision": "rejected"}
    request = TaskToSchedulePreviewRequest.model_validate(payload)
    scheduler_called = False

    def scheduler(**kwargs: object) -> object:
        nonlocal scheduler_called
        scheduler_called = True
        raise AssertionError

    with pytest.raises(WorkflowValidationError, match="no duration") as error:
        execute_task_to_schedule_preview(
            request,
            ai_gateway=fake_gateway(make_draft()),
            scheduling_preview=scheduler,
        )

    assert error.value.code == "missing_confirmed_duration"
    assert scheduler_called is False


def test_ai_failure_stops_before_scheduler() -> None:
    request = TaskToSchedulePreviewRequest.model_validate(request_payload())
    scheduler_called = False

    def scheduler(**kwargs: object) -> object:
        nonlocal scheduler_called
        scheduler_called = True
        raise AssertionError

    with pytest.raises(WorkflowAIError):
        execute_task_to_schedule_preview(
            request,
            ai_gateway=fake_gateway(AIProviderError("provider failed")),
            scheduling_preview=scheduler,
        )

    assert scheduler_called is False


def test_mapping_preserves_supported_fields_without_mutation() -> None:
    task = ConfirmedTask(
        title="Task",
        description="Description",
        duration_minutes=90,
        priority="urgent",
        earliest_start="2026-07-27T09:00:00+02:00",
        deadline="2026-07-28T17:00:00+02:00",
        preferred_time_of_day="afternoon",
        is_splittable=True,
        minimum_session_minutes=30,
        maximum_sessions_per_day=2,
        steps=[],
    )
    original = deepcopy(task.model_dump())

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )

    assert mapping.task.id == "stable-id"
    assert mapping.task.duration_minutes == 90
    assert mapping.task.priority.value == "urgent"
    assert mapping.task.earliest_start == task.earliest_start
    assert mapping.task.deadline == task.deadline
    assert mapping.task.preferred_time_of_day.value == "afternoon"
    assert mapping.task.is_splittable is True
    assert mapping.task.minimum_session_minutes == 30
    assert mapping.task.maximum_sessions_per_day == 2
    assert mapping.snapshot.value_sources.priority == "confirmed"
    assert mapping.snapshot.value_sources.preferred_time_of_day == "confirmed"
    assert mapping.snapshot.value_sources.minimum_session_minutes == "confirmed"
    assert mapping.snapshot.value_sources.maximum_sessions_per_day == "confirmed"
    assert mapping.snapshot.value_sources.is_splittable == "confirmed"
    assert mapping.defaulted_fields == ()
    assert mapping.confirmed_fields == (
        "priority",
        "preferred_time_of_day",
        "minimum_session_minutes",
        "maximum_sessions_per_day",
        "is_splittable",
    )
    assert not any("Scheduler defaults applied" in item for item in mapping.warnings)
    assert task.model_dump() == original


def test_mapping_records_scheduler_defaults_explicitly() -> None:
    task = ConfirmedTask(
        title="Defaulted task",
        duration_minutes=90,
        priority=None,
        preferred_time_of_day=None,
        is_splittable=True,
        minimum_session_minutes=None,
        maximum_sessions_per_day=None,
        steps=[],
    )

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=20,
    )

    assert mapping.task.priority.value == "medium"
    assert mapping.task.preferred_time_of_day.value == "any"
    assert mapping.task.minimum_session_minutes == 20
    assert mapping.task.maximum_sessions_per_day == 1
    assert mapping.task.is_splittable is True
    assert mapping.snapshot.value_sources.model_dump(mode="json") == {
        "priority": "scheduler_default",
        "preferred_time_of_day": "scheduler_default",
        "minimum_session_minutes": "scheduler_default",
        "maximum_sessions_per_day": "scheduler_default",
        "is_splittable": "confirmed",
    }
    assert mapping.defaulted_fields == (
        "priority",
        "preferred_time_of_day",
        "minimum_session_minutes",
        "maximum_sessions_per_day",
    )
    assert mapping.confirmed_fields == ("is_splittable",)
    assert mapping.warnings[-1] == (
        "Scheduler defaults applied: priority, preferred_time_of_day, "
        "minimum_session_minutes, maximum_sessions_per_day."
    )


def test_scheduler_snapshot_requires_strict_typed_value_sources() -> None:
    task = ConfirmedTask(
        title="Task",
        duration_minutes=60,
        is_splittable=False,
        steps=[],
    )
    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )
    snapshot = mapping.snapshot.model_dump()
    del snapshot["value_sources"]

    with pytest.raises(ValidationError, match="value_sources"):
        SchedulerTaskSnapshot.model_validate(snapshot)

    invalid_sources = mapping.snapshot.value_sources.model_dump()
    invalid_sources["unexpected"] = "confirmed"
    with pytest.raises(ValidationError, match="unexpected"):
        SchedulerTaskValueSources.model_validate(invalid_sources)


@pytest.mark.parametrize("is_splittable", [True, False])
def test_values_equal_to_defaults_still_have_confirmed_source(
    is_splittable: bool,
) -> None:
    task = ConfirmedTask(
        title="Explicit defaults",
        duration_minutes=90,
        priority="medium",
        preferred_time_of_day="any",
        is_splittable=is_splittable,
        minimum_session_minutes=15,
        maximum_sessions_per_day=1,
        steps=[],
    )

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )

    for source in mapping.snapshot.value_sources.model_dump().values():
        assert source is SchedulerValueSource.confirmed
    assert mapping.defaulted_fields == ()
    assert not any("Scheduler defaults applied" in item for item in mapping.warnings)


def test_scheduler_mapping_trace_exposes_deterministic_provenance() -> None:
    payload = draft_payload()
    payload["priority"] = value()
    payload["preferred_time_of_day"] = value()
    payload["maximum_sessions_per_day"] = value()
    payload["minimum_session_minutes"] = value(
        45, source="estimated", confirmation=True
    )
    draft = TaskDraftV2.model_validate(payload)
    request = TaskToSchedulePreviewRequest.model_validate(request_payload())

    result = execute_task_to_schedule_preview(request, ai_gateway=fake_gateway(draft))

    mapping_trace = next(
        entry for entry in result.trace if entry.stage.value == "scheduler_mapping"
    )
    assert mapping_trace.metadata["defaulted_fields"] == [
        "priority",
        "preferred_time_of_day",
        "maximum_sessions_per_day",
    ]
    assert mapping_trace.metadata["confirmed_fields"] == [
        "minimum_session_minutes",
        "is_splittable",
    ]
    assert mapping_trace.warnings[-1] == (
        "Scheduler defaults applied: priority, preferred_time_of_day, "
        "maximum_sessions_per_day."
    )


def test_trace_reports_unallocated_minutes() -> None:
    payload = draft_payload()
    payload["duration"] = value(360, source="estimated", confirmation=True)
    payload["proposed_steps"] = [
        {
            "title": value("Research", source="user"),
            "description": value(),
            "duration": value(150, source="user"),
            "order": 1,
        },
        {
            "title": value("Slides", source="user"),
            "description": value(),
            "duration": value(150, source="user"),
            "order": 2,
        },
    ]
    draft = TaskDraftV2.model_validate(payload)
    request = TaskToSchedulePreviewRequest.model_validate(request_payload())

    result = execute_task_to_schedule_preview(request, ai_gateway=fake_gateway(draft))

    mapping_trace = next(
        entry for entry in result.trace if entry.stage.value == "scheduler_mapping"
    )
    assert mapping_trace.metadata["duration_minutes"] == 360
    assert mapping_trace.metadata["step_duration_sum"] == 300
    assert mapping_trace.metadata["unallocated_minutes"] == 60


def test_conceptual_steps_are_preserved_and_warned() -> None:
    task = ConfirmedTask(
        title="Task",
        duration_minutes=120,
        is_splittable=True,
        steps=[
            ConfirmedTaskStep(title="One", duration_minutes=45, order=1),
            ConfirmedTaskStep(title="Two", duration_minutes=45, order=2),
        ],
    )

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )

    assert len(mapping.snapshot.steps) == 2
    assert mapping.step_duration_sum == 90
    assert mapping.unallocated_minutes == 30
    assert any("differs" in warning for warning in mapping.warnings)
    assert any("not scheduled independently" in warning for warning in mapping.warnings)
    assert mapping.task.duration_minutes == 120


def test_matching_step_sum_has_no_discrepancy_warning() -> None:
    task = ConfirmedTask(
        title="Task",
        duration_minutes=90,
        is_splittable=True,
        steps=[
            ConfirmedTaskStep(title="One", duration_minutes=45, order=1),
            ConfirmedTaskStep(title="Two", duration_minutes=45, order=2),
        ],
    )

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )

    assert not any("differs" in warning for warning in mapping.warnings)
    assert mapping.unallocated_minutes == 0


def test_step_total_greater_than_duration_reports_negative_unallocated() -> None:
    task = ConfirmedTask(
        title="Task",
        duration_minutes=90,
        is_splittable=True,
        steps=[
            ConfirmedTaskStep(title="One", duration_minutes=50, order=1),
            ConfirmedTaskStep(title="Two", duration_minutes=50, order=2),
        ],
    )

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )

    assert mapping.step_duration_sum == 100
    assert mapping.unallocated_minutes == -10
    assert mapping.task.duration_minutes == 90


def test_incomplete_step_durations_report_null_unallocated() -> None:
    task = ConfirmedTask(
        title="Task",
        duration_minutes=90,
        is_splittable=True,
        steps=[
            ConfirmedTaskStep(title="One", duration_minutes=45, order=1),
            ConfirmedTaskStep(title="Two", duration_minutes=None, order=2),
        ],
    )

    mapping = map_confirmed_task_to_scheduler_task(
        task,
        task_id="stable-id",
        default_minimum_session_minutes=15,
    )

    assert mapping.step_duration_sum is None
    assert mapping.unallocated_minutes is None


@pytest.mark.parametrize("duration", [0, -1])
def test_mapping_rejects_non_positive_duration(duration: int) -> None:
    task = ConfirmedTask.model_construct(
        title="Invalid task",
        description=None,
        duration_minutes=duration,
        priority=None,
        earliest_start=None,
        deadline=None,
        preferred_time_of_day=None,
        is_splittable=False,
        minimum_session_minutes=None,
        maximum_sessions_per_day=None,
        steps=[],
    )

    with pytest.raises(WorkflowValidationError) as error:
        map_confirmed_task_to_scheduler_task(
            task,
            task_id="stable-id",
            default_minimum_session_minutes=15,
        )

    assert error.value.code == "invalid_confirmed_duration"


def test_deadline_before_window_and_earliest_after_window_fail() -> None:
    request = TaskToSchedulePreviewRequest.model_validate(request_payload())
    with pytest.raises(WorkflowValidationError) as deadline_error:
        execute_task_to_schedule_preview(
            request,
            ai_gateway=fake_gateway(make_draft(deadline="2026-07-26T17:00:00+02:00")),
        )
    assert deadline_error.value.code == "deadline_before_preview_window"

    with pytest.raises(WorkflowValidationError) as earliest_error:
        execute_task_to_schedule_preview(
            request,
            ai_gateway=fake_gateway(
                make_draft(
                    earliest_start="2026-08-01T09:00:00+02:00",
                    deadline=None,
                )
            ),
        )
    assert earliest_error.value.code == "earliest_start_after_preview_window"


def test_deadline_after_window_adds_warning() -> None:
    request = TaskToSchedulePreviewRequest.model_validate(request_payload())

    result = execute_task_to_schedule_preview(
        request,
        ai_gateway=fake_gateway(make_draft(deadline="2026-08-02T17:00:00+02:00")),
    )

    mapping_trace = next(
        entry for entry in result.trace if entry.stage.value == "scheduler_mapping"
    )
    assert any("after the preview window" in item for item in mapping_trace.warnings)


def test_busy_intervals_are_respected() -> None:
    request = TaskToSchedulePreviewRequest.model_validate(
        request_payload(
            window_end="2026-07-27T18:00:00+02:00",
            busy_intervals=[
                {
                    "start": "2026-07-27T09:00:00+02:00",
                    "end": "2026-07-27T17:00:00+02:00",
                }
            ],
        )
    )
    result = execute_task_to_schedule_preview(
        request, ai_gateway=fake_gateway(make_draft(deadline=None))
    )

    for block in result.schedule_preview.scheduled_blocks:
        assert not (
            block.start < datetime.fromisoformat("2026-07-27T15:00:00+00:00")
            and block.end > datetime.fromisoformat("2026-07-27T07:00:00+00:00")
        )


def test_no_availability_returns_normal_unscheduled_preview() -> None:
    request = TaskToSchedulePreviewRequest.model_validate(
        request_payload(weekdays=False)
    )

    result = execute_task_to_schedule_preview(
        request, ai_gateway=fake_gateway(make_draft())
    )

    assert result.schedule_preview.scheduled_blocks == []
    assert len(result.schedule_preview.unscheduled_tasks) == 1


def test_include_trace_false_returns_empty_trace() -> None:
    payload = request_payload()
    payload["include_trace"] = False
    request = TaskToSchedulePreviewRequest.model_validate(payload)

    result = execute_task_to_schedule_preview(
        request, ai_gateway=fake_gateway(make_draft())
    )

    assert result.trace == []


def test_planning_window_domain_is_timezone_aware() -> None:
    context = WorkflowSchedulingContext.model_validate(
        request_payload()["scheduling_context"]
    )

    interval = context.planning_window().to_domain()

    assert isinstance(interval, TimeInterval)


def test_provenance_does_not_change_external_layer_schemas() -> None:
    assert "value_sources" not in TaskDraftV2.model_fields
    assert "value_sources" not in ConfirmedTask.model_fields
    assert "value_sources" not in SchedulePreviewResponse.model_fields
