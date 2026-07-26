import json

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.ai_intake.gateway import AIProviderError, InvalidAIOutputError
from app.ai_intake.provider import ProviderRequest, ProviderResponse


class OpenAIProvider:
    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def generate_structured(self, request: ProviderRequest) -> ProviderResponse:
        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=request.instructions,
                input=request.user_input,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": request.schema_name,
                        "schema": request.json_schema,
                        "strict": True,
                    }
                },
            )
        except AuthenticationError as exc:
            raise AIProviderError(
                "OpenAI authentication failed", category="authentication"
            ) from exc
        except APITimeoutError as exc:
            raise AIProviderError(
                "OpenAI request timed out", category="timeout"
            ) from exc
        except RateLimitError as exc:
            message = str(exc).lower()
            category = (
                "insufficient_quota"
                if "quota" in message or "billing" in message
                else "rate_limit"
            )
            raise AIProviderError(
                "OpenAI is temporarily unavailable", category=category
            ) from exc
        except APIConnectionError as exc:
            raise AIProviderError(
                "OpenAI is temporarily unavailable", category="connection"
            ) from exc
        except APIStatusError as exc:
            category = (
                "model_unavailable"
                if exc.status_code in {404, 503}
                else "provider_failure"
            )
            raise AIProviderError(
                f"OpenAI request failed with status {exc.status_code}",
                category=category,
            ) from exc

        if response.status != "completed" or not response.output_text:
            raise AIProviderError(
                f"OpenAI response was not completed: {response.status}"
            )
        try:
            payload = json.loads(response.output_text)
        except json.JSONDecodeError as exc:
            raise InvalidAIOutputError("OpenAI returned invalid JSON") from exc
        return ProviderResponse(
            payload=payload,
            provider="openai",
            model=self._model,
            request_id=response.id,
        )
