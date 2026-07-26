from functools import lru_cache

from openai import OpenAI

from app.ai_intake.gateway import AIGateway, AIIntakeError
from app.ai_intake.openai_provider import OpenAIProvider
from app.ai_intake.prompts import load_prompt
from app.core.config import get_settings


class AIIntakeConfigurationError(AIIntakeError):
    pass


@lru_cache
def get_ai_gateway() -> AIGateway:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise AIIntakeConfigurationError("OPENAI_API_KEY is not configured")
    client = OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.ai_intake_timeout_seconds,
        max_retries=0,
    )
    provider = OpenAIProvider(client=client, model=settings.openai_model)
    return AIGateway(
        provider=provider,
        prompt=load_prompt(settings.ai_intake_prompt_version),
        default_timezone=settings.ai_intake_default_timezone,
    )
