from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.v1.router import router as api_v1_router
from app.internal.ai_intake_router import router as ai_intake_router
from app.internal.calendar_router import router as calendar_router
from app.internal.calendar_sync_router import router as calendar_sync_router
from app.internal.dev_users_router import router as dev_users_router
from app.internal.router import router as internal_router
from app.internal.schedule_plans_router import router as schedule_plans_router
from app.internal.task_confirmation_router import router as task_confirmation_router
from app.internal.workflow_router import router as workflow_router

app = FastAPI(
    title="AI Calendar API",
    version="0.1.0",
)
app.include_router(api_v1_router)
app.include_router(internal_router)
app.include_router(ai_intake_router)
app.include_router(task_confirmation_router)
app.include_router(workflow_router)
app.include_router(calendar_router)
app.include_router(calendar_sync_router)
app.include_router(dev_users_router)
app.include_router(schedule_plans_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Hello World"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/time")
async def current_time() -> dict[str, str]:
    return {"utc_time": datetime.now(UTC).isoformat()}
