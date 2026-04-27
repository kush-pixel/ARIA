"""ARIA scheduler: 7:30 AM briefing enqueue + midnight pattern_recompute sweep.

Two scheduled functions
-----------------------
enqueue_briefing_jobs():
    Finds patients whose next_appointment falls on today (UTC) with
    monitoring_active=True and no existing briefing for today, then
    inserts a briefing_generation job for each into processing_jobs.

enqueue_pattern_recompute_sweep():
    Enqueues a pattern_recompute job for EVERY monitoring_active=True
    patient, regardless of appointment date. Runs at midnight UTC so gap
    counters, risk scores, inertia flags, and deterioration flags stay
    current between appointments.

Key design decisions (both functions)
--------------------------------------
- Idempotent: ON CONFLICT DO NOTHING on idempotency_key — safe to call
  multiple times per day.
- Does NOT perform any detection work inline — only enqueues.
  WorkerProcessor picks up jobs within 30 seconds.
- session_factory is injectable for unit testing.

Execution paths
---------------
1. Automatic (7:30 AM UTC):  APScheduler cron calls enqueue_briefing_jobs().
2. Automatic (midnight UTC): APScheduler cron calls enqueue_pattern_recompute_sweep().
3. Demo mode (on demand):    POST /api/admin/trigger-scheduler calls
                             enqueue_briefing_jobs() directly.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import cast, not_, select, text
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

        # Wake the worker immediately — one notification per sweep is enough.
        # Sent inside the same transaction as the INSERTs so the worker cannot
        # receive the notification before the rows are committed and visible.
        await session.execute(text("SELECT pg_notify('aria_jobs', '')"))
        await session.commit()
        logger.info(
            "Scheduler: %d briefing_generation job(s) enqueued for %s", enqueued, today
        )
        return enqueued


def _pattern_recompute_idempotency_key(patient_id: str, sweep_date: date) -> str:
    """Return the canonical idempotency key for a pattern_recompute job.

    Format: ``pattern_recompute:{patient_id}:{YYYY-MM-DD}``

    Args:
        patient_id: The patient's MED_REC_NO / FHIR Patient.id.
        sweep_date: The date the sweep runs (normally today UTC).

    Returns:
        Idempotency key string.
    """
    return f"pattern_recompute:{patient_id}:{sweep_date.isoformat()}"


async def enqueue_pattern_recompute_sweep(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    target_date: date | None = None,
) -> int:
    """Enqueue a pattern_recompute job for every monitoring-active patient.

    Runs at midnight UTC so gap counters, risk scores, inertia flags, and
    deterioration flags stay current for patients who have no appointment
    today and would otherwise never receive a pattern_recompute job.

    Each job uses idempotency key ``pattern_recompute:{patient_id}:{YYYY-MM-DD}``
    so re-running the sweep on the same day is safe (ON CONFLICT DO NOTHING).

    Does NOT filter by appointment date — all monitoring_active=TRUE patients
    are included.

    Args:
        session_factory: SQLAlchemy async session factory. Defaults to
            AsyncSessionLocal. Override in tests to inject mock factories.
        target_date: Date to use for idempotency keys. Defaults to today (UTC
            local date). Override in tests to check a specific date.

    Returns:
        Number of pattern_recompute jobs inserted in this call.
        Re-runs that hit ON CONFLICT DO NOTHING return 0 for those patients.
    """
    today = target_date or date.today()
    factory = session_factory if session_factory is not None else AsyncSessionLocal

    async with factory() as session:
        result = await session.execute(
            select(Patient).where(Patient.monitoring_active.is_(True))
        )
        patients = list(result.scalars().all())

        if not patients:
            logger.info("Sweep: no monitoring-active patients to recompute for %s", today)
            return 0

        enqueued = 0
        for patient in patients:
            idempotency_key = _pattern_recompute_idempotency_key(patient.patient_id, today)

            await session.execute(
                pg_insert(ProcessingJob)
                .values(
                    job_type="pattern_recompute",
                    patient_id=patient.patient_id,
                    idempotency_key=idempotency_key,
                    status="queued",
                    created_by="scheduler",
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            enqueued += 1
            logger.info(
                "Sweep: enqueued pattern_recompute for patient=%s date=%s",
                patient.patient_id,
                today,
            )

        await session.execute(text("SELECT pg_notify('aria_jobs', '')"))
        await session.commit()
        logger.info(
            "Sweep: %d pattern_recompute job(s) enqueued for %s", enqueued, today
        )
        return enqueued
