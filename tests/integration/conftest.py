import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import Task, User, UserPreferences


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        session.execute(delete(Task))
        session.execute(delete(UserPreferences))
        session.execute(delete(User))
        session.commit()
        yield session
        session.rollback()
        session.execute(delete(Task))
        session.execute(delete(UserPreferences))
        session.execute(delete(User))
        session.commit()


@pytest.fixture
def user(db_session: Session) -> User:
    user = User(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="owner@example.com",
        timezone="Europe/Warsaw",
    )
    user.preferences = UserPreferences()
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
