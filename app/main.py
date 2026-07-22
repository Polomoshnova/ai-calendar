from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.v1.router import router as api_v1_router

app = FastAPI(
    title="AI Calendar API",
    version="0.1.0",
)
app.include_router(api_v1_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Hello World"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/time")
async def current_time() -> dict[str, str]:
    return {"utc_time": datetime.now(UTC).isoformat()}
