from datetime import time

import pytest
from sqlalchemy.orm import Session

from app.domain.preferences import Weekday, default_working_hours_json
from app.domain.tasks import PreferredTimeOfDay
from app.models import User
from app.services.preferences import load_scheduling_preferences


def test_load_preferences_uses_user_as_timezone_authority(
    db_session: Session, user: User
) -> None:
    assert user.preferences is not None
    user.preferences.preferred_task_time = PreferredTimeOfDay.morning
    user.preferences.minimum_break_minutes = 15
    user.preferences.no_deep_work_after = time(17)
    db_session.commit()

    loaded = load_scheduling_preferences(db_session, user.id)

    assert loaded.timezone == "Europe/Warsaw"
    assert loaded.preferred_task_time is PreferredTimeOfDay.morning
    assert loaded.minimum_break_minutes == 15
    assert loaded.working_hours[Weekday.monday][0].start == time(9)
    assert loaded.working_hours[Weekday.saturday] == ()


def test_invalid_working_hours_are_rejected_at_model_boundary(user: User) -> None:
    assert user.preferences is not None
    invalid = default_working_hours_json()
    del invalid["sunday"]

    with pytest.raises(ValueError, match="exactly seven weekdays"):
        user.preferences.working_hours = invalid
