from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderRequest:
    instructions: str
    user_input: str
    schema_name: str
    json_schema: dict[str, object]


@dataclass(frozen=True)
class ProviderResponse:
    payload: object
    provider: str
    model: str
    request_id: str | None = None


class AIProvider(Protocol):
    def generate_structured(self, request: ProviderRequest) -> ProviderResponse: ...
