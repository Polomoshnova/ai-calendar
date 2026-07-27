from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.timezones import validate_timezone
from app.models import User


@dataclass(frozen=True)
class DevUserResult:
    user: User
    created: bool


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _find_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(func.lower(User.email) == email))


def get_or_create_dev_user(
    session: Session,
    *,
    email: str,
    timezone: str,
) -> DevUserResult:
    normalized_email = normalize_email(email)
    validated_timezone = validate_timezone(timezone)
    existing = _find_user_by_email(session, normalized_email)
    if existing is not None:
        return DevUserResult(user=existing, created=False)

    user = User(email=normalized_email, timezone=validated_timezone)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _find_user_by_email(session, normalized_email)
        if existing is None:
            raise
        return DevUserResult(user=existing, created=False)

    session.refresh(user)
    return DevUserResult(user=user, created=True)
