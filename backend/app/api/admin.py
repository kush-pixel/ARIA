"""Admin API routes for ARIA.

POST /api/admin/trigger-scheduler  — manually fire the 7:30 AM briefing scheduler
                                     (demo mode only; guarded by DEMO_MODE setting)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.db.base import AsyncSessionLocal
from app.services.worker.scheduler import enqueue_briefing_jobs
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["admin"])


@router.post("/admin/trigger-scheduler")
async def trigger_scheduler() -> dict:
    """Manually trigger the 7:30 AM briefing scheduler.

    Enqueues briefing_generation jobs for all patients with appointments today.
    Only available when DEMO_MODE=true in settings.
    """
    if not settings.demo_mode:
        raise HTTPException(
            status_code=403,
            detail="Admin trigger is only available in demo mode (DEMO_MODE=true)",
        )

    enqueued = await enqueue_briefing_jobs(session_factory=AsyncSessionLocal)
    logger.info("Admin trigger: enqueued %d briefing jobs", enqueued)
    return {"enqueued": enqueued}
