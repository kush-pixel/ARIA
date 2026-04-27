"""ARIA FastAPI application entry point.

Registers all API routers, configures CORS, and starts/stops the background
worker on application lifespan.

Run from backend/ directory:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import adherence, admin, alerts, briefings, ingest, patients, readings, shadow_mode
from app.config import settings
from app.db.base import AsyncSessionLocal
from app.limiter import limiter  # shared limiter instance (Fix 37)
from app.services.worker.processor import WorkerProcessor
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_worker: WorkerProcessor | None = None
_worker_task: asyncio.Task | None = None  # type: ignore[type-arg]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start background worker on startup; stop it cleanly on shutdown."""
    global _worker, _worker_task
    logger.info("ARIA backend starting (env=%s)", settings.app_env)

    _worker = WorkerProcessor(session_factory=AsyncSessionLocal)
    _worker_task = asyncio.create_task(_worker.run())
    logger.info("Background worker started")

    yield

    logger.info("ARIA backend shutting down")
    if _worker is not None:
        _worker.stop()
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    logger.info("Background worker stopped")


app = FastAPI(
    title="ARIA Clinical Intelligence Platform",
    version="4.3.0",
    description="Between-visit hypertension management decision support for clinicians.",
    lifespan=lifespan,
)

# Wire rate limiter (Fix 37) — routes apply @limiter.limit() decorators
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router, prefix="/api")
app.include_router(readings.router, prefix="/api")
app.include_router(briefings.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(ingest.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(adherence.router, prefix="/api")
app.include_router(shadow_mode.router, prefix="/api")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
