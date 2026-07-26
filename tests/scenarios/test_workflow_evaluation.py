from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import pytest

from app.ai_intake.gateway import AIGateway
from app.workflows.errors import (
    WorkflowConfirmationError,
    WorkflowValidationError,
)
from app.workflows.models import TaskToSchedulePreviewRequest
from app.workflows.task_to_schedule_preview import (
    execute_task_to_schedule_preview,
)


def _value(
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


def make_draft(**kwargs: object) -> object:
    duration = kwargs.get("duration", 120)
    deadline = kwargs.get("deadline", "2026-07-31T17:00:00+02:00")
    earliest_start = kwargs.get("earliest_start")
    from app.ai_intake.types import TaskDraftV2

    return TaskDraftV2.model_validate(
        {
            "title": _value("Task", source="user"),
            "description": _value(),
            "duration": (
                _value(duration, source="estimated", confirmation=True)
                if duration is not None
                else _value()
            ),
            "priority": _value(),
            "earliest_start": (
                _value(earliest_start, source="inferred")
                if earliest_start
                else _value()
            ),
            "deadline": (
                _value(deadline, source="inferred", confirmation=True)
                if deadline
                else _value()
            ),
            "preferred_time_of_day": _value(),
            "is_splittable": _value(True, source="estimated", confirmation=True),
            "minimum_session_minutes": _value(
                30, source="estimated", confirmation=True
            ),
            "maximum_sessions_per_day": _value(),
            "proposed_steps": [],
            "clarification_questions": [],
            "prompt_version": "ai-intake.task-draft.v2",
            "schema_version": "task-draft.schema.v2",
        }
    )


def request_payload(**kwargs: object) -> dict[str, Any]:
    weekdays = bool(kwargs.get("weekdays", True))
    window = [{"start": "09:00", "end": "18:00"}] if weekdays else []
    review = kwargs.get("review") or {
        "mode": "explicit",
        "duration": {"decision": "accepted"},
        "deadline": {"decision": "accepted"},
        "is_splittable": {"decision": "accepted"},
        "minimum_session_minutes": {"decision": "accepted"},
    }
    return {
        "text": "Task",
        "review": review,
        "ai_context": {
            "current_datetime": "2026-07-26T15:00:00+02:00",
            "timezone": "Europe/Warsaw",
        },
        "scheduling_context": {
            "window_start": kwargs.get("window_start", "2026-07-27T08:00:00+02:00"),
            "window_end": kwargs.get("window_end", "2026-07-31T20:00:00+02:00"),
            "timezone": "Europe/Warsaw",
            "busy_intervals": [],
            "preferences": {
                "timezone": "Europe/Warsaw",
                "working_hours": {
                    "monday": window,
                    "tuesday": window,
                    "wednesday": window,
                    "thursday": window,
                    "friday": window,
                    "saturday": [],
                    "sunday": [],
                },
                "minimum_break_minutes": 15,
                "default_minimum_session_minutes": 15,
            },
            "existing_pending_tasks": kwargs.get("existing_tasks", []),
        },
        "include_trace": True,
    }


class FakeGateway:
    def __init__(self, result: object) -> None:
        self.result = result

    def analyze(
        self,
        text: str,
        *,
        current_time: datetime | None = None,
        user_timezone: str | None = None,
    ) -> object:
        return self.result


@dataclass(frozen=True)
class EvaluationScenario:
    name: str
    text: str
    draft_kwargs: dict[str, object]
    request_overrides: dict[str, object]
    review: dict[str, object] | None
    expected: str


SCENARIOS = (
    EvaluationScenario(
        "dentist-tomorrow-morning",
        "Завтра утром подготовиться к визиту к стоматологу",
        {"duration": 30, "deadline": None},
        {},
        None,
        "scheduled",
    ),
    EvaluationScenario(
        "presentation-friday-edited-duration",
        "Подготовить презентацию к пятнице",
        {},
        {},
        {
            "mode": "explicit",
            "duration": {"decision": "edited", "value": 180},
            "deadline": {"decision": "accepted"},
            "is_splittable": {"decision": "accepted"},
            "minimum_session_minutes": {"decision": "accepted"},
        },
        "duration_180",
    ),
    EvaluationScenario(
        "course-eight-lessons",
        "Пройти восемь уроков по 45 минут",
        {"duration": 360},
        {},
        None,
        "scheduled",
    ),
    EvaluationScenario(
        "interview-preparation-two-weeks",
        "Подготовиться к интервью за две недели",
        {"duration": 240, "deadline": "2026-08-07T17:00:00+02:00"},
        {"window_end": "2026-08-07T20:00:00+02:00"},
        None,
        "scheduled",
    ),
    EvaluationScenario(
        "ambiguous-no-confirmed-duration",
        "Когда-нибудь сделать задачу",
        {},
        {},
        {
            "mode": "explicit",
            "duration": {"decision": "rejected"},
            "deadline": {"decision": "accepted"},
            "is_splittable": {"decision": "accepted"},
            "minimum_session_minutes": {"decision": "accepted"},
        },
        "missing_duration",
    ),
    EvaluationScenario(
        "task-too-large",
        "Сделать большой отчёт",
        {"duration": 3000, "deadline": None},
        {},
        None,
        "unscheduled",
    ),
    EvaluationScenario(
        "no-weekday-availability",
        "Подготовить документ",
        {},
        {"weekdays": False},
        None,
        "unscheduled",
    ),
    EvaluationScenario(
        "weekend-disabled",
        "Сделать задачу в выходные",
        {"deadline": None},
        {
            "window_start": "2026-08-01T08:00:00+02:00",
            "window_end": "2026-08-02T20:00:00+02:00",
        },
        None,
        "unscheduled",
    ),
    EvaluationScenario(
        "earliest-after-window",
        "Начать задачу позже окна",
        {
            "earliest_start": "2026-08-01T09:00:00+02:00",
            "deadline": None,
        },
        {},
        None,
        "earliest_after_window",
    ),
    EvaluationScenario(
        "dst-boundary-warsaw",
        "Подготовить план после перехода на зимнее время",
        {"deadline": "2026-10-30T17:00:00+01:00"},
        {
            "window_start": "2026-10-24T08:00:00+02:00",
            "window_end": "2026-10-30T20:00:00+01:00",
        },
        None,
        "scheduled",
    ),
    EvaluationScenario(
        "user-rejects-splitting",
        "Выполнить задачу одним блоком",
        {},
        {},
        {
            "mode": "explicit",
            "duration": {"decision": "accepted"},
            "deadline": {"decision": "accepted"},
            "is_splittable": {"decision": "edited", "value": False},
            "minimum_session_minutes": {"decision": "accepted"},
        },
        "not_split",
    ),
    EvaluationScenario(
        "existing-tasks-compete",
        "Добавить конкурирующую задачу",
        {},
        {
            "existing_tasks": [
                {
                    "id": "existing-1",
                    "title": "Existing task",
                    "duration_minutes": 240,
                    "priority": "urgent",
                    "deadline": "2026-07-27T17:00:00+02:00",
                }
            ]
        },
        None,
        "two_tasks",
    ),
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_deterministic_workflow_evaluation_scenario(
    scenario: EvaluationScenario,
) -> None:
    request_args = dict(scenario.request_overrides)
    if scenario.review is not None:
        request_args["review"] = scenario.review
    payload = request_payload(**request_args)
    payload["text"] = scenario.text
    request = TaskToSchedulePreviewRequest.model_validate(payload)
    gateway = cast(AIGateway, FakeGateway(make_draft(**scenario.draft_kwargs)))

    if scenario.expected == "missing_duration":
        with pytest.raises(WorkflowValidationError) as error:
            execute_task_to_schedule_preview(request, ai_gateway=gateway)
        assert error.value.code == "missing_confirmed_duration"
        return
    if scenario.expected == "earliest_after_window":
        with pytest.raises(WorkflowValidationError) as error:
            execute_task_to_schedule_preview(request, ai_gateway=gateway)
        assert error.value.code == "earliest_start_after_preview_window"
        return

    try:
        result = execute_task_to_schedule_preview(request, ai_gateway=gateway)
    except WorkflowConfirmationError as exc:
        pytest.fail(f"unexpected confirmation failure: {exc}")

    if scenario.expected == "duration_180":
        assert result.scheduler_input.task.duration_minutes == 180
    elif scenario.expected == "unscheduled":
        assert result.schedule_preview.unscheduled_tasks
    elif scenario.expected == "not_split":
        assert result.scheduler_input.task.is_splittable is False
    elif scenario.expected == "two_tasks":
        assert result.scheduler_input.pending_task_count == 2
    else:
        assert (
            result.schedule_preview.scheduled_blocks
            or result.schedule_preview.unscheduled_tasks
        )
