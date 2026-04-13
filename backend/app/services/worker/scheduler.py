"""7:30 AM briefing scheduler and demo-mode trigger for ARIA.

What was implemented
---------------------
enqueue_briefing_jobs(): finds patients whose next_appointment falls on
today (UTC) and who have monitoring_active=True and no existing briefing
for today, then inserts a briefing_generation job into processing_jobs
for each qualifying patient.

Key design decisions:
- Idempotent: uses ON CONFLICT DO NOTHING on the unique idempotency_key
  ("briefing_generation:{patient_id}:{YYYY-MM-DD}") so calling this
  function twice on the same day produces zero duplicate jobs.
- Does NOT perform briefing work inline — only enqueues. WorkerProcessor
  picks up the jobs within 30 seconds.
- Query mirrors spec Section 7.4 exactly:
    patients WHERE next_appointment::DATE = CURRENT_DATE
    AND monitoring_active = TRUE
    AND NOT EXISTS (SELECT 1 FROM briefings WHERE ... AND appointment_date = today)
- session_factory is injectable for unit testing.

Execution paths
---------------
1. Automatic (7:30 AM UTC): APScheduler cron in scripts/run_worker.py
   calls enqueue_briefing_jobs() at the scheduled time.
2. Demo mode (on demand): POST /api/admin/trigger-scheduler calls
   enqueue_briefing_jobs() directly so briefings can be fired any time
   during a demo without waiting for 7:30 AM.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import cast, not_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.types import Date as SADate

from app.db.base import AsyncSessionLocal
from app.models.briefing import Briefing
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _briefing_idempotency_key(patient_id: str, appointment_date: date) -> str:
    """Return the canonical idempotency key for a briefing_generation job.

    Format: ``briefing_generation:{patient_id}:{YYYY-MM-DD}``

    This key ensures that at most one briefing_generation job can exist in
    processing_jobs for a given patient on a given day, regardless of how
    many times the scheduler runs.

    Args:
        patient_id: The patient's MED_REC_NO / FHIR Patient.id.
        appointment_date: The appointment date the briefing covers.

    Returns:
        Idempotency key string.
    """
    return f"briefing_generation:{patient_id}:{appointment_date.isoformat()}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def enqueue_briefing_jobs(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    target_date: date | None = None,
) -> int:
    """Find appointment-day patients and enqueue briefing_generation jobs.

    Mirrors the spec Section 7.4 scheduler query:
      - patients with monitoring_active=TRUE
      - whose next_appointment::DATE = today (UTC)
      - who do NOT already have a briefing row for today

    For each qualifying patient, inserts a briefing_generation job into
    processing_jobs using ON CONFLICT DO NOTHING so re-runs are safe.

    Called by:
      - APScheduler cron at 7:30 AM UTC (scripts/run_worker.py)
      - POST /api/admin/trigger-scheduler for demo-mode on-demand firing

    Args:
        session_factory: SQLAlchemy async session factory. Defaults to
            AsyncSessionLocal. Override in tests to inject mock factories.
        target_date: Date to check for appointments. Defaults to today (UTC
            local date). Override in tests to check a specific date.

    Returns:
        Number of briefing_generation jobs inserted in this call.
        Re-runs that hit ON CONFLICT DO NOTHING return 0 for those patients.
    """
    today = target_date or date.today()
    factory = session_factory if session_factory is not None else AsyncSessionLocal

    async with factory() as session:
        # Subquery: TRUE if a briefing already exists for this patient today
        briefing_exists = (
            select(Briefing)
            .where(
                Briefing.patient_id == Patient.patient_id,
                Briefing.appointment_date == today,
            )
            .exists()
        )

        # Main query — mirrors spec Section 7.4
        stmt = (
            select(Patient)
            .where(
                Patient.monitoring_active.is_(True),
                cast(Patient.next_appointment, SADate) == today,
                not_(briefing_exists),
            )
        )

        result = await session.execute(stmt)
        patients = list(result.scalars().all())

        if not patients:
            logger.info(
                "Scheduler: no appointment-day patients without briefings for %s", today
            )
            return 0

        enqueued = 0
        for patient in patients:
            idempotency_key = _briefing_idempotency_key(patient.patient_id, today)

            # ON CONFLICT DO NOTHING prevents duplicate jobs on re-runs
            await session.execute(
                pg_insert(ProcessingJob)
                .values(
                    job_type="briefing_generation",
                    patient_id=patient.patient_id,
                    idempotency_key=idempotency_key,
                    status="queued",
                    created_by="scheduler",
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            enqueued += 1
            logger.info(
                "Scheduler: enqueued briefing_generation for patient=%s date=%s",
                patient.patient_id,
                today,
            )

        await session.commit()
        logger.info(
            "Scheduler: %d briefing_generation job(s) enqueued for %s", enqueued, today
        )
        return enqueued
