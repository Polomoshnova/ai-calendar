import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.api.dependencies import DatabaseSession
from app.core.config import Settings, get_settings
from app.domain.preferences import serialize_working_hours
from app.internal.export import build_export_payload, contains_secret_fields
from app.internal.scenario_loader import (
    InvalidScenarioError,
    list_scenarios,
    load_scenario,
)
from app.internal.schemas import (
    InternalPreviewRequest,
    InternalPreviewResponse,
    PreferencesEnvelope,
    ReviewExportPayload,
    ReviewExportRequest,
    ScenarioDocument,
    ScenarioSummary,
    TemporaryPreferences,
    UserSummary,
)
from app.models import User
from app.schemas.scheduling import SchedulePreviewResponse
from app.services.preferences import (
    UserNotFoundError,
    load_scheduling_preferences,
    save_scheduling_preferences,
)
from app.services.scheduling import (
    PreviewUserNotFoundError,
    generate_schedule_preview,
    preview_schedule,
)
from app.services.tasks import list_tasks

INTERNAL_DIRECTORY = Path(__file__).resolve().parent
APP_DIRECTORY = INTERNAL_DIRECTORY.parent
templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")

router = APIRouter(prefix="/internal", include_in_schema=False)


def require_internal_tools(
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if not settings.enable_internal_tools:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


InternalToolsEnabled = Annotated[None, Depends(require_internal_tools)]


@router.get("/scheduling-lab")
def scheduling_lab(request: Request, _enabled: InternalToolsEnabled) -> object:
    return templates.TemplateResponse(
        request=request,
        name="scheduling_lab.html",
        context={"title": "Internal Scheduling Lab"},
    )


@router.get("/static/{filename}")
def internal_static(filename: str, _enabled: InternalToolsEnabled) -> FileResponse:
    if filename not in {"scheduling_lab.css", "scheduling_lab.js"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(APP_DIRECTORY / "static" / filename)


@router.get("/api/users", response_model=list[UserSummary])
def users(
    session: DatabaseSession, _enabled: InternalToolsEnabled
) -> list[UserSummary]:
    records = session.scalars(select(User).order_by(User.email, User.id))
    return [
        UserSummary(
            id=user.id,
            email=user.email,
            timezone=user.timezone,
            has_stored_preferences=user.preferences is not None,
        )
        for user in records
    ]


def _preferences_envelope(
    session: DatabaseSession, user_id: uuid.UUID
) -> PreferencesEnvelope:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    loaded = load_scheduling_preferences(session, user_id)
    return PreferencesEnvelope(
        user_id=user.id,
        has_stored_preferences=user.preferences is not None,
        preferences=TemporaryPreferences(
            timezone=loaded.timezone,
            working_hours=serialize_working_hours(loaded.working_hours),
            preferred_task_time=loaded.preferred_task_time,
            minimum_break_minutes=loaded.minimum_break_minutes,
            no_deep_work_after=loaded.no_deep_work_after,
            default_minimum_session_minutes=loaded.default_minimum_session_minutes,
        ),
    )


@router.get("/api/users/{user_id}/preferences", response_model=PreferencesEnvelope)
def get_preferences(
    user_id: uuid.UUID,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> PreferencesEnvelope:
    return _preferences_envelope(session, user_id)


@router.put("/api/users/{user_id}/preferences", response_model=PreferencesEnvelope)
def put_preferences(
    user_id: uuid.UUID,
    data: TemporaryPreferences,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> PreferencesEnvelope:
    try:
        save_scheduling_preferences(session, user_id, data.to_domain())
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _preferences_envelope(session, user_id)


@router.get("/api/scenarios", response_model=list[ScenarioSummary])
def scenarios(_enabled: InternalToolsEnabled) -> list[ScenarioSummary]:
    try:
        return list_scenarios()
    except InvalidScenarioError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/scenarios/{filename}", response_model=ScenarioDocument)
def scenario(filename: str, _enabled: InternalToolsEnabled) -> ScenarioDocument:
    try:
        return load_scenario(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Scenario not found") from exc
    except InvalidScenarioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/scheduling/preview", response_model=InternalPreviewResponse)
def internal_preview(
    data: InternalPreviewRequest,
    session: DatabaseSession,
    _enabled: InternalToolsEnabled,
) -> InternalPreviewResponse:
    busy = tuple(item.to_domain() for item in data.busy_intervals)
    if data.mode == "existing_user":
        assert data.user_id is not None
        try:
            preview = preview_schedule(
                session=session,
                user_id=data.user_id,
                planning_window=data.planning_window.to_domain(),
                busy_intervals=busy,
            )
            titles = {
                str(task.id): task.title for task in list_tasks(session, data.user_id)
            }
        except PreviewUserNotFoundError as exc:
            raise HTTPException(status_code=404, detail="User not found") from exc
    else:
        assert data.preferences is not None
        assert data.tasks is not None
        preview = generate_schedule_preview(
            planning_window=data.planning_window.to_domain(),
            busy_intervals=busy,
            tasks=tuple(task.to_domain() for task in data.tasks),
            preferences=data.preferences.to_domain(),
        )
        titles = {task.id: task.title for task in data.tasks}
    response = SchedulePreviewResponse.from_domain(
        preview.planning_window, preview.free_intervals, preview.result
    )
    return InternalPreviewResponse(
        **response.model_dump(),
        task_titles=titles,
    )


@router.post("/api/review-export", response_model=ReviewExportPayload)
def review_export(
    data: ReviewExportRequest, _enabled: InternalToolsEnabled
) -> ReviewExportPayload:
    payload = build_export_payload(data)
    if contains_secret_fields(payload.model_dump(mode="json")):
        raise HTTPException(status_code=422, detail="Export contains forbidden fields")
    return payload
