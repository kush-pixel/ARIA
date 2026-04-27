"""Admin API routes for ARIA.

POST /api/admin/trigger-scheduler  — manually fire the 7:30 AM briefing scheduler
                                     (demo mode only; guarded by DEMO_MODE setting)
GET  /api/admin/dead-jobs          — list processing_jobs with status='dead' for inspection
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import AsyncSessionLocal
from app.db.session import get_session
from app.models.processing_job import ProcessingJob
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


@router.get("/admin/dead-jobs")
async def list_dead_jobs(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return all processing_jobs with status='dead', newest first.

    Dead jobs have exhausted all retry attempts. Use this endpoint to inspect
    persistent failures and decide whether to re-queue or discard them.
    Not gated by demo_mode — read-only inspection is useful in production too.
    """
    result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.status == "dead")
        .order_by(ProcessingJob.finished_at.desc())
    )
    jobs = result.scalars().all()
    return [_serialise_job(j) for j in jobs]


def _serialise_job(j: ProcessingJob) -> dict:
    return {
        "job_id": j.job_id,
        "job_type": j.job_type,
        "patient_id": j.patient_id,
        "status": j.status,
        "retry_count": j.retry_count,
        "retry_after": j.retry_after.isoformat() if j.retry_after else None,
        "error_message": j.error_message,
        "queued_at": j.queued_at.isoformat() if j.queued_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "created_by": j.created_by,
    }
