import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.api.dependencies import DatabaseSession
from app.domain.timezones import validate_timezone
from app.internal.dependencies import InternalToolsEnabled
from app.internal.dev_users import get_or_create_dev_user, normalize_email

router = APIRouter(prefix="/internal/api/dev", tags=["internal-dev"])


class DevUserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    timezone: str = "Europe/Warsaw"

    @field_validator("email", mode="before")
    @classmethod
    def trim_email(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email")
    @classmethod
    def normalize_valid_email(cls, value: EmailStr) -> str:
        return normalize_email(str(value))

    @field_validator("timezone")
    @classmethod
    def validate_iana_timezone(cls, value: str) -> str:
        return validate_timezone(value)


class DevUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    timezone: str
    created_at: datetime
    updated_at: datetime
    created: bool


@router.post("/users", response_model=DevUserResponse)
def create_or_get_dev_user(
    data: DevUserCreateRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> DevUserResponse:
    result = get_or_create_dev_user(
        session,
        email=str(data.email),
        timezone=data.timezone,
    )
    user = result.user
    return DevUserResponse(
        id=user.id,
        email=user.email,
        timezone=user.timezone,
        created_at=user.created_at,
        updated_at=user.updated_at,
        created=result.created,
    )
