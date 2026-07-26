from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from openai import OpenAI
from pydantic import ValidationError

from app.ai_intake.gateway import AIGateway, InvalidAIOutputError
from app.ai_intake.openai_provider import OpenAIProvider
from app.ai_intake.prompts import UnknownPromptVersionError, load_prompt
from app.ai_intake.provider import AIProvider, ProviderRequest, ProviderResponse
from app.ai_intake.types import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    TASK_DRAFT_JSON_SCHEMA,
    DraftIntegerValue,
    TaskDraftV2,
)


def draft_value(
    value: object = None,
    *,
    source: str | None = None,
    confidence: float | None = None,
    explanation: str | None = None,
    requires_confirmation: bool = False,
) -> dict[str, object]:
    return {
        "value": value,
        "source": source,
        "confidence": confidence,
        "explanation": explanation,
        "requires_confirmation": requires_confirmation,
    }


def valid_payload() -> dict[str, object]:
    return {
        "title": draft_value("Prepare release notes", source="user", confidence=1.0),
        "description": draft_value(),
        "duration": draft_value(
            90,
            source="estimated",
            confidence=0.7,
            explanation="Estimated for a short set of release notes.",
            requires_confirmation=True,
        ),
        "priority": draft_value(),
        "earliest_start": draft_value(),
        "deadline": draft_value(
            "2026-07-24T15:00:00+02:00",
            source="inferred",
            confidence=0.9,
            explanation="Normalized from the stated relative deadline.",
        ),
        "preferred_time_of_day": draft_value(),
        "is_splittable": draft_value(),
        "minimum_session_minutes": draft_value(),
        "maximum_sessions_per_day": draft_value(),
        "proposed_steps": [],
        "clarification_questions": [],
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


class StubProvider:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.request: ProviderRequest | None = None

    def generate_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.request = request
        return ProviderResponse(
            payload=self.payload,
            provider="stub",
            model="stub-model",
        )


def gateway_for(provider: AIProvider) -> AIGateway:
    return AIGateway(
        provider=provider,
        prompt=load_prompt(PROMPT_VERSION),
        default_timezone="Europe/Warsaw",
    )


def test_user_source_with_confidence_one_is_valid() -> None:
    value = DraftIntegerValue(
        value=15,
        source="user",
        confidence=1.0,
        explanation=None,
        requires_confirmation=False,
    )

    assert value.value == 15


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "value": 15,
            "source": "user",
            "confidence": 0.6,
            "explanation": None,
            "requires_confirmation": False,
        },
        {
            "value": None,
            "source": "estimated",
            "confidence": None,
            "explanation": None,
            "requires_confirmation": False,
        },
        {
            "value": 15,
            "source": "estimated",
            "confidence": 0.6,
            "explanation": None,
            "requires_confirmation": True,
        },
        {
            "value": 15,
            "source": "estimated",
            "confidence": -0.1,
            "explanation": "Estimate.",
            "requires_confirmation": True,
        },
        {
            "value": 15,
            "source": "estimated",
            "confidence": 1.1,
            "explanation": "Estimate.",
            "requires_confirmation": True,
        },
    ],
)
def test_invalid_draft_values_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DraftIntegerValue.model_validate(kwargs)


def test_minimal_task_draft_v2_is_valid() -> None:
    payload = valid_payload()
    payload["duration"] = draft_value()
    payload["deadline"] = draft_value()

    draft = TaskDraftV2.model_validate(payload)

    assert draft.title.value == "Prepare release notes"
    assert draft.schema_version == SCHEMA_VERSION


def test_explicit_user_duration_is_preserved() -> None:
    payload = valid_payload()
    payload["duration"] = draft_value(15, source="user", confidence=1.0)

    draft = TaskDraftV2.model_validate(payload)

    assert draft.duration.value == 15
    assert draft.duration.source == "user"


def test_derived_duration_from_explicit_numbers_is_inferred() -> None:
    payload = valid_payload()
    payload["duration"] = draft_value(
        360,
        source="inferred",
        confidence=0.99,
        explanation="Calculated as 8 lessons × 45 minutes = 360 minutes.",
    )

    draft = TaskDraftV2.model_validate(payload)

    assert draft.duration.value == 360
    assert draft.duration.source == "inferred"


def test_empty_title_is_rejected() -> None:
    payload = valid_payload()
    payload["title"] = draft_value("", source="user", confidence=1.0)

    with pytest.raises(ValidationError, match="title"):
        TaskDraftV2.model_validate(payload)


def test_earliest_start_after_deadline_is_rejected() -> None:
    payload = valid_payload()
    payload["earliest_start"] = draft_value(
        "2026-07-25T10:00:00+02:00",
        source="inferred",
        confidence=0.9,
        explanation="Normalized date.",
    )

    with pytest.raises(ValidationError, match="earliest_start"):
        TaskDraftV2.model_validate(payload)


def test_invalid_proposed_step_order_is_rejected() -> None:
    payload = valid_payload()
    payload["duration"] = draft_value(
        90,
        source="estimated",
        confidence=0.7,
        explanation="Sum of estimated work.",
        requires_confirmation=True,
    )
    payload["proposed_steps"] = [
        {
            "title": draft_value("Draft", source="user", confidence=1.0),
            "description": draft_value(),
            "duration": draft_value(
                45,
                source="estimated",
                confidence=0.7,
                explanation="Estimated drafting time.",
                requires_confirmation=True,
            ),
            "order": 2,
        }
    ]

    with pytest.raises(ValidationError, match="order"):
        TaskDraftV2.model_validate(payload)


def test_prompt_loader_returns_v2_prompt() -> None:
    prompt = load_prompt(PROMPT_VERSION)

    assert prompt.version == PROMPT_VERSION
    assert "untrusted user text" in prompt.instructions
    assert "8 lessons × 45 minutes" in prompt.instructions


def test_prompt_loader_rejects_unknown_version() -> None:
    with pytest.raises(UnknownPromptVersionError):
        load_prompt("ai-intake.task-draft.v999")


def test_prompt_loader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt(PROMPT_VERSION, tmp_path)


def test_gateway_returns_validated_versioned_task_draft() -> None:
    provider = StubProvider(valid_payload())
    current_time = datetime(2026, 7, 23, 8, tzinfo=UTC)

    draft = gateway_for(provider).analyze(
        "Prepare release notes by tomorrow afternoon",
        current_time=current_time,
    )

    assert draft.title.value == "Prepare release notes"
    assert draft.prompt_version == PROMPT_VERSION
    assert draft.schema_version == SCHEMA_VERSION
    assert provider.request is not None
    assert "2026-07-23T10:00:00+02:00" in provider.request.user_input
    assert "User timezone: Europe/Warsaw" in provider.request.user_input
    assert provider.request.json_schema["additionalProperties"] is False


def test_gateway_rejects_json_schema_violation() -> None:
    payload = valid_payload()
    del payload["title"]

    with pytest.raises(InvalidAIOutputError, match="JSON Schema"):
        gateway_for(StubProvider(payload)).analyze("Missing title")


def test_gateway_classifies_domain_validation_failure() -> None:
    payload = valid_payload()
    payload["earliest_start"] = draft_value(
        "2026-07-25T10:00:00+02:00",
        source="inferred",
        confidence=0.9,
        explanation="Normalized date.",
    )

    with pytest.raises(InvalidAIOutputError) as error:
        gateway_for(StubProvider(payload)).analyze("Invalid task window")

    assert error.value.category == "domain_validation"


def test_gateway_rejects_naive_reference_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        gateway_for(StubProvider(valid_payload())).analyze(
            "Task", current_time=datetime(2026, 7, 23, 8)
        )


def test_provider_schema_is_strict() -> None:
    assert TASK_DRAFT_JSON_SCHEMA["additionalProperties"] is False
    assert TASK_DRAFT_JSON_SCHEMA["required"] == list(
        cast(dict[str, object], TASK_DRAFT_JSON_SCHEMA["properties"])
    )
    schema_version = cast(
        dict[str, object],
        cast(dict[str, object], TASK_DRAFT_JSON_SCHEMA["properties"])["schema_version"],
    )
    assert schema_version["const"] == SCHEMA_VERSION
    definitions = cast(dict[str, dict[str, Any]], TASK_DRAFT_JSON_SCHEMA["$defs"])
    assert definitions["ValueSource"]["enum"] == [
        "user",
        "inferred",
        "estimated",
        "default",
    ]
    confidence = definitions["DraftIntegerValue"]["properties"]["confidence"]
    number_schema = next(
        branch for branch in confidence["anyOf"] if branch.get("type") == "number"
    )
    assert number_schema["minimum"] == 0
    assert number_schema["maximum"] == 1
    assert definitions["DraftIntegerValue"]["additionalProperties"] is False


class FakeResponses:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    def create(self, **kwargs: object) -> object:
        self.arguments = kwargs
        return type(
            "FakeResponse",
            (),
            {
                "status": "completed",
                "output_text": __import__("json").dumps(valid_payload()),
                "id": "response-123",
            },
        )()


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()


def test_openai_provider_uses_responses_structured_output() -> None:
    client = FakeOpenAI()
    provider = OpenAIProvider(cast(OpenAI, client), model="test-model")
    request = ProviderRequest(
        instructions="Extract a task.",
        user_input="Write release notes.",
        schema_name="task_draft",
        json_schema={"type": "object"},
    )

    response = provider.generate_structured(request)

    assert response.payload == valid_payload()
    assert response.request_id == "response-123"
    assert client.responses.arguments["model"] == "test-model"
    text = cast(dict[str, object], client.responses.arguments["text"])
    response_format = cast(dict[str, object], text["format"])
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
