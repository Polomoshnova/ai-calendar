import json
from datetime import datetime
from pathlib import Path
from typing import cast

from app.ai_intake.gateway import AIGateway
from app.ai_intake.types import TaskDraftV2
from app.workflows.models import WorkflowReplayCase
from app.workflows.task_to_schedule_preview import (
    execute_task_to_schedule_preview,
)


class FakeGateway:
    def __init__(self, draft: TaskDraftV2) -> None:
        self.draft = draft

    def analyze(
        self,
        text: str,
        *,
        current_time: datetime | None = None,
        user_timezone: str | None = None,
    ) -> TaskDraftV2:
        return self.draft


def test_serialized_workflow_replay_fixture() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "product"
        / "workflows"
        / "presentation_by_friday.json"
    )
    case = WorkflowReplayCase.model_validate(json.loads(fixture_path.read_text()))

    result = execute_task_to_schedule_preview(
        case.request,
        ai_gateway=cast(AIGateway, FakeGateway(case.fake_ai_response)),
    )

    assert case.expected.status == "completed"
    assert (
        result.scheduler_input.task.duration_minutes
        == case.expected.expected_duration_minutes
    )
    assert result.workflow_version == "task-to-schedule-preview.v1"
