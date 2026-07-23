import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.internal.export import build_export_payload, contains_secret_fields
from app.internal.presentation import to_local_iso
from app.internal.scenario_loader import InvalidScenarioError, load_scenario
from app.internal.schemas import ReviewExportRequest


def test_load_product_scenario() -> None:
    scenario = load_scenario("01_single_task.json")

    assert scenario.name == "Single task"
    assert scenario.user_preferences.timezone == "UTC"
    assert scenario.tasks[0].title == "Prepare release notes"


def test_scenario_loader_rejects_invalid_file(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text('{"name": "incomplete"}')

    with pytest.raises(InvalidScenarioError):
        load_scenario("broken.json", tmp_path)


def test_scenario_loader_rejects_path_traversal() -> None:
    with pytest.raises(InvalidScenarioError, match="filename"):
        load_scenario("../01_single_task.json")


def test_export_payload_uses_normalized_inputs_and_no_secrets() -> None:
    request = ReviewExportRequest.model_validate(
        {
            "normalized_inputs": {
                "user_timezone": "UTC",
                "planning_window": {
                    "start": "2026-07-20T08:00:00Z",
                    "end": "2026-07-20T18:00:00Z",
                },
                "preferences_used": {
                    "timezone": "UTC",
                    "working_hours": {
                        day: (
                            [{"start": "09:00", "end": "18:00"}]
                            if day not in {"saturday", "sunday"}
                            else []
                        )
                        for day in (
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                            "saturday",
                            "sunday",
                        )
                    },
                },
                "tasks": [{"id": "task-1", "title": "Write report"}],
                "busy_intervals": [],
            },
            "generated_preview_result": {
                "scheduler_version": "2a.1",
                "scheduled_blocks": [],
            },
            "review": {
                "score": 4,
                "verdict": "logical",
                "notes": "Looks sensible.",
                "observed_problems": [],
            },
        }
    )
    exported_at = datetime(2026, 7, 23, 10, tzinfo=UTC)

    payload = build_export_payload(request, exported_at=exported_at)

    assert payload.tasks == [{"id": "task-1", "title": "Write report"}]
    assert payload.generated_preview_result["scheduler_version"] == "2a.1"
    assert payload.exported_at == exported_at
    assert not contains_secret_fields(payload.model_dump(mode="json"))


def test_export_request_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewExportRequest.model_validate(
            {
                "normalized_inputs": {
                    "user_timezone": "UTC",
                    "planning_window": {
                        "start": "2026-07-20T08:00:00Z",
                        "end": "2026-07-20T18:00:00Z",
                    },
                    "preferences_used": {
                        "timezone": "UTC",
                        "working_hours": json.loads("{}"),
                    },
                    "tasks": [],
                    "busy_intervals": [],
                    "database_url": "secret",
                },
                "generated_preview_result": {},
                "review": {"score": 3, "verdict": "acceptable"},
            }
        )


def test_local_timezone_conversion_uses_iana_zone() -> None:
    value = datetime(2026, 7, 20, 8, tzinfo=UTC)

    assert to_local_iso(value, "Europe/Warsaw") == "2026-07-20T10:00:00+02:00"


def test_local_timezone_conversion_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        to_local_iso(datetime(2026, 7, 20, 8), "Europe/Warsaw")
