from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from app.ai_intake.prompts import Prompt
from app.ai_intake.provider import AIProvider, ProviderRequest
from app.ai_intake.types import SCHEMA_VERSION, TASK_DRAFT_JSON_SCHEMA, TaskDraft


class AIIntakeError(Exception):
    pass


class AIProviderError(AIIntakeError):
    def __init__(self, message: str, *, category: str = "provider_failure") -> None:
        super().__init__(message)
        self.category = category


class InvalidAIOutputError(AIIntakeError):
    def __init__(
        self, message: str, *, category: str = "structured_output_validation"
    ) -> None:
        super().__init__(message)
        self.category = category


class AIGateway:
    def __init__(
        self,
        provider: AIProvider,
        prompt: Prompt,
        *,
        default_timezone: str = "UTC",
    ) -> None:
        self._provider = provider
        self._prompt = prompt
        try:
            self._default_timezone = ZoneInfo(default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown default timezone: {default_timezone}") from exc
        try:
            Draft202012Validator.check_schema(TASK_DRAFT_JSON_SCHEMA)
        except SchemaError as exc:
            raise RuntimeError("TaskDraft JSON Schema is invalid") from exc
        self._schema_validator = Draft202012Validator(TASK_DRAFT_JSON_SCHEMA)

    def analyze(
        self,
        text: str,
        *,
        current_time: datetime | None = None,
        user_timezone: str | None = None,
    ) -> TaskDraft:
        try:
            timezone = (
                ZoneInfo(user_timezone)
                if user_timezone is not None
                else self._default_timezone
            )
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown user timezone: {user_timezone}") from exc
        reference_time = current_time or datetime.now(timezone)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise ValueError("current_time must be timezone-aware")
        local_reference_time = reference_time.astimezone(timezone)
        user_input = (
            f"Current datetime: {local_reference_time.isoformat()}\n"
            f"User timezone: {timezone.key}\n"
            "Week ends: Sunday at 23:59:59 in the user timezone.\n"
            f"User text:\n{text}"
        )
        response = self._provider.generate_structured(
            ProviderRequest(
                instructions=self._prompt.instructions,
                user_input=user_input,
                schema_name="task_draft",
                json_schema=TASK_DRAFT_JSON_SCHEMA,
            )
        )
        errors = sorted(
            self._schema_validator.iter_errors(response.payload),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            summary = "; ".join(error.message for error in errors[:3])
            raise InvalidAIOutputError(
                f"provider output failed JSON Schema validation: {summary}",
                category="structured_output_validation",
            )
        try:
            draft = TaskDraft.model_validate(response.payload)
        except ValidationError as exc:
            raise InvalidAIOutputError(
                "provider output failed TaskDraft domain validation",
                category="domain_validation",
            ) from exc
        if draft.prompt_version != self._prompt.version:
            raise InvalidAIOutputError(
                "provider output prompt_version does not match configured prompt",
                category="domain_validation",
            )
        if draft.schema_version != SCHEMA_VERSION:
            raise InvalidAIOutputError(
                "provider output schema_version does not match active schema",
                category="domain_validation",
            )
        return draft
