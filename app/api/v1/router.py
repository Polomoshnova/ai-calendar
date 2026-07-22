from fastapi import APIRouter

from app.api.v1.scheduling import router as scheduling_router
from app.api.v1.tasks import router as tasks_router

router = APIRouter(prefix="/api/v1")
router.include_router(tasks_router)
router.include_router(scheduling_router)
