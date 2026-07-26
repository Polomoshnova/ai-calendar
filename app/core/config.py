from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+psycopg://ai_calendar:ai_calendar@localhost:5432/ai_calendar"
    )
    enable_internal_tools: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6"
    ai_intake_prompt_version: str = "ai-intake.task-draft.v2"
    ai_intake_default_timezone: str = "UTC"
    ai_intake_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def normalize_empty_api_key(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
