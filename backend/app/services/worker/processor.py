"""Background job processor for ARIA.

WorkerProcessor: an async polling class that claims and executes queued jobs
from the processing_jobs table.

- Polling loop runs every 30 seconds (configurable); drains the queue without
  waiting between jobs so bursts are handled quickly.
- Status transitions: queued → running → succeeded | failed.
- Claim step uses a conditional UPDATE (WHERE status='queued') so the same job
  cannot be double-processed if two workers run concurrently in future.
- finished_at is always written on completion or failure.
- error_message is written on failure so the cause is visible in the DB.

Three job-type handlers are registered:
  bundle_import       — loads FHIR Bundle JSON from payload_ref path and
                        calls ingest_fhir_bundle() [fully implemented].
  pattern_recompute   — runs all 4 Layer 1 detectors then Layer 2 risk scorer.
  briefing_generation — composes deterministic briefing (Layer 1) then optional
                        LLM summary (Layer 3). Layer 3 failure is logged but
                        does not fail the job.

The session_factory parameter is injectable for unit testing — pass a mock
factory to avoid real DB connections in tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import AsyncSessionLocal
from app.models.alert import Alert
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Worker tuning constants — do not hardcode in call sites
_POLL_INTERVAL_SECONDS: int = 30
_FALLBACK_POLL_SECONDS: int = 60   # idle timeout when LISTEN/NOTIFY is active
_BATCH_SIZE: int = 10
_MAX_RETRIES: int = 3
_RETRY_BACKOFF_SECONDS: list[int] = [30, 120, 480]  # backoff for retry 1, 2, 3

# Off-hours window: 6 PM (18) to 8 AM (8) local UTC, or weekends
_OFF_HOURS_START: int = 18
_OFF_HOURS_END: int = 8
# Alert types eligible for escalation after 24h unacknowledged
_ESCALATION_ALERT_TYPES: tuple[str, ...] = ("gap_urgent", "deterioration")

# Type alias for async job handler functions
_JobHandler = Callable[[ProcessingJob, AsyncSession], Awaitable[None]]


# ---------------------------------------------------------------------------
# Off-hours helper
# ---------------------------------------------------------------------------


def _is_off_hours(dt: datetime) -> bool:
    """Return True if dt falls between 6 PM–8 AM UTC or on a weekend."""
    hour = dt.hour
    weekday = dt.weekday()  # 5=Saturday, 6=Sunday
    if weekday >= 5:
        return True
    return hour >= _OFF_HOURS_START or hour < _OFF_HOURS_END


# ---------------------------------------------------------------------------
# Alert helper
# ---------------------------------------------------------------------------


async def _upsert_alert(
    session: AsyncSession,
    patient_id: str,
    alert_type: str,
    gap_days: int | None = None,
) -> None:
    """Insert an alert row for today if one does not already exist.

    Deduplicates by (patient_id, alert_type, date(triggered_at)) so
    re-running pattern_recompute on the same day does not create duplicates.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient to alert on.
        alert_type: gap_urgent | gap_briefing | inertia | deterioration
        gap_days: Only set for gap_urgent and gap_briefing alert types.
    """
    today = datetime.now(UTC).date()
    existing = await session.execute(
        select(Alert)
        .where(
            Alert.patient_id == patient_id,
            Alert.alert_type == alert_type,
            func.date(Alert.triggered_at) == today,
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is None:
        now = datetime.now(UTC)
        session.add(
            Alert(
                patient_id=patient_id,
                alert_type=alert_type,
                gap_days=gap_days,
                triggered_at=now,
                delivered_at=now,
                off_hours=_is_off_hours(now),
            )
        )


# ---------------------------------------------------------------------------
# Job handlers (module-level so they can be tested independently)
# ---------------------------------------------------------------------------


async def _handle_bundle_import(job: ProcessingJob, session: AsyncSession) -> None:
    """Ingest the FHIR Bundle located at job.payload_ref into the database.

    Loads the JSON file at payload_ref, validates the bundle structure, then
    calls ingest_fhir_bundle() which populates all relevant tables and writes
    its own audit event. The session passed in is used by ingest_fhir_bundle
    for its own commits.

    Args:
        job: The ProcessingJob being executed. payload_ref must point to a
            valid FHIR Bundle JSON file on disk.
        session: Async database session forwarded to ingest_fhir_bundle().

    Raises:
        ValueError: payload_ref is missing, or bundle fails validation.
        FileNotFoundError: The file at payload_ref does not exist.
        json.JSONDecodeError: The file at payload_ref is not valid JSON.
    """
    # Deferred import avoids circular dependency at module load
    from app.services.fhir.ingestion import ingest_fhir_bundle
    from app.services.fhir.validator import validate_fhir_bundle

    if not job.payload_ref:
        raise ValueError(
            "bundle_import job is missing payload_ref — cannot locate FHIR Bundle"
        )

    bundle_path = Path(job.payload_ref)
    if not bundle_path.exists():
        raise FileNotFoundError(f"FHIR Bundle file not found: {bundle_path}")

    bundle: dict = json.loads(bundle_path.read_text(encoding="utf-8"))

    errors = validate_fhir_bundle(bundle)
    if errors:
        raise ValueError(
            f"Invalid FHIR Bundle ({len(errors)} error(s)): {'; '.join(errors)}"
        )

    summary = await ingest_fhir_bundle(bundle, session)
    logger.info("bundle_import completed: %s", summary)


async def _handle_pattern_recompute(job: ProcessingJob, session: AsyncSession) -> None:
    """Run all 4 Layer 1 detectors then Layer 2 risk scorer for the patient.

    Execution order is strict — Layer 2 MUST run after all Layer 1 detectors.
    Do not parallelize these calls.

      1. run_gap_detector()            — days without a home BP reading
      2. run_inertia_detector()        — sustained elevation with no med change
      3. run_adherence_analyzer()      — adherence rate vs BP pattern
      4. run_deterioration_detector()  — worsening systolic trend
      5. compute_risk_score()          — Layer 2 weighted priority score

    Args:
        job: The ProcessingJob being executed. patient_id must be set.
        session: Async database session.

    Raises:
        ValueError: patient_id is missing from the job.
    """
    from app.services.pattern_engine.adherence_analyzer import run_adherence_analyzer
    from app.services.pattern_engine.deterioration_detector import run_deterioration_detector
    from app.services.pattern_engine.gap_detector import run_gap_detector
    from app.services.pattern_engine.inertia_detector import run_inertia_detector
    from app.services.pattern_engine.risk_scorer import compute_risk_score
    from app.services.pattern_engine.variability_detector import run_variability_detector

    if not job.patient_id:
        raise ValueError("pattern_recompute job is missing patient_id")

    pid = job.patient_id

    # Fix 17: cold-start suppression — skip non-gap detectors for recently enrolled patients
    cold_start_days = 21
    patient_row = await session.execute(
        select(Patient).where(Patient.patient_id == pid)
    )
    patient_obj = patient_row.scalar_one_or_none()
    enrolled_at = patient_obj.enrolled_at if patient_obj is not None else None
    days_enrolled = (
        (datetime.now(UTC) - enrolled_at).days
        if enrolled_at is not None
        else cold_start_days + 1
    )
    cold_start = days_enrolled < cold_start_days
    if cold_start:
        logger.info(
            "cold_start_suppression: patient=%s enrolled=%d days — "
            "skipping inertia/adherence/deterioration/variability detectors",
            pid, days_enrolled,
        )

    gap = await run_gap_detector(session, pid)
    logger.info(
        "gap_detector: patient=%s gap_days=%s status=%s",
        pid, gap["gap_days"], gap["status"],
    )

    if cold_start:
        inertia: dict = {"inertia_detected": False, "avg_systolic": None, "elevated_count": 0, "duration_days": 0.0}
        adherence: dict = {"adherence_pct": None, "pattern": "none", "interpretation": "cold_start_suppressed"}
        deterioration: dict = {"deterioration": False, "slope": None, "recent_avg": None, "baseline_avg": None}
        variability: dict = {"detected": False, "level": "none", "cv_pct": None, "visit_agenda_item": None, "variability_score": 0.0}
    else:
        inertia = await run_inertia_detector(session, pid)
        logger.info(
            "inertia_detector: patient=%s detected=%s avg_systolic=%s",
            pid, inertia["inertia_detected"], inertia.get("avg_systolic"),
        )

        adherence = await run_adherence_analyzer(session, pid)
        logger.info(
            "adherence_analyzer: patient=%s pattern=%s adherence_pct=%s",
            pid, adherence["pattern"], adherence.get("adherence_pct"),
        )

        deterioration = await run_deterioration_detector(session, pid)
        logger.info(
            "deterioration_detector: patient=%s detected=%s slope=%s",
            pid, deterioration["deterioration"], deterioration.get("slope"),
        )

        variability = await run_variability_detector(session, pid)
        logger.info(
            "variability_detector: patient=%s level=%s cv_pct=%s",
            pid, variability["level"], variability.get("cv_pct"),
        )

    # Write alert rows for triggered conditions (deduplicated by date)
    if gap["status"] in ("flag", "urgent"):
        alert_type = "gap_urgent" if gap["status"] == "urgent" else "gap_briefing"
        await _upsert_alert(session, pid, alert_type, gap_days=int(gap["gap_days"]))
    if inertia["inertia_detected"]:
        await _upsert_alert(session, pid, "inertia")
    if adherence["pattern"] == "A":
        await _upsert_alert(session, pid, "adherence")
    if deterioration["deterioration"]:
        await _upsert_alert(session, pid, "deterioration")
    await session.flush()
    logger.info("Alert rows written for patient=%s", pid)

    # Trigger mini-briefing for urgent alerts only (gap_urgent or deterioration)
    if gap["status"] == "urgent" or deterioration["deterioration"]:
        from app.services.briefing.composer import compose_mini_briefing
        trigger = "gap_urgent" if gap["status"] == "urgent" else "deterioration"
        await compose_mini_briefing(session, pid, trigger)

    score = await compute_risk_score(pid, session)
    logger.info(
        "pattern_recompute completed: patient=%s risk_score=%.2f",
        pid,
        score,
    )


async def _handle_briefing_generation(job: ProcessingJob, session: AsyncSession) -> None:
    """Compose a deterministic briefing JSON and optional LLM summary.

    Layer execution order is strict — never reverse:
      1. compose_briefing()      — Layer 1 deterministic JSON (9 fields)
      2. generate_llm_summary()  — Layer 3 optional LLM readable summary

    Appointment date is sourced from patients.next_appointment (Fix 20).
    Falls back to today when next_appointment is None (preserves demo-mode
    behaviour where next_appointment may not be set).

    Layer 3 failure (e.g. missing API key, network error) is logged but does
    NOT fail the job — Layer 1 briefing is already persisted and useful.

    Args:
        job: The ProcessingJob being executed. patient_id must be set.
        session: Async database session.

    Raises:
        ValueError: patient_id is missing from the job.
    """
    from app.services.briefing.composer import compose_briefing
    from app.services.briefing.summarizer import generate_llm_summary

    if not job.patient_id:
        raise ValueError("briefing_generation job is missing patient_id")

    # Fix 20: source appointment date from patients.next_appointment, NOT
    # from idempotency_key parsing (key is for deduplication only).
    patient_row = await session.execute(
        select(Patient).where(Patient.patient_id == job.patient_id)
    )
    patient = patient_row.scalar_one_or_none()
    if patient is not None and patient.next_appointment is not None:
        appointment_date: date = patient.next_appointment.date()
    else:
        appointment_date = date.today()

    briefing = await compose_briefing(session, job.patient_id, appointment_date)
    logger.info(
        "briefing_generation Layer 1 complete: patient=%s briefing_id=%s",
        job.patient_id,
        briefing.briefing_id,
    )

    # Layer 3 is optional — log failure but do not fail the job
    try:
        await generate_llm_summary(briefing, session)
        logger.info(
            "briefing_generation Layer 3 complete: patient=%s briefing_id=%s",
            job.patient_id,
            briefing.briefing_id,
        )
    except Exception as exc:
        logger.warning(
            "Layer 3 LLM summary skipped for briefing=%s: %s",
            briefing.briefing_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Periodic sweeps (run every poll cycle regardless of job queue state)
# ---------------------------------------------------------------------------


async def _run_escalation_sweep(session: AsyncSession) -> int:
    """Escalate gap_urgent/deterioration alerts unacknowledged for > 24 hours.

    Sets escalated=True on qualifying alerts. Runs once per poll cycle so the
    24-hour window is checked with ~30-second precision.

    Returns:
        Number of alerts newly escalated.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    result = await session.execute(
        update(Alert)
        .where(
            Alert.alert_type.in_(_ESCALATION_ALERT_TYPES),
            Alert.acknowledged_at.is_(None),
            Alert.escalated.is_(False),
            Alert.triggered_at <= cutoff,
        )
        .values(escalated=True)
    )
    count = result.rowcount
    if count:
        await session.commit()
        logger.info("escalation_sweep: %d alert(s) escalated", count)
    return count


# Registry of all supported job_type values → handler functions.
# Add new handlers here when new job types are introduced.
_HANDLERS: dict[str, _JobHandler] = {
    "bundle_import": _handle_bundle_import,
    "pattern_recompute": _handle_pattern_recompute,
    "briefing_generation": _handle_briefing_generation,
}


# ---------------------------------------------------------------------------
# WorkerProcessor
# ---------------------------------------------------------------------------


class WorkerProcessor:
    """Polls processing_jobs and dispatches queued jobs to their handlers.

    The worker runs an infinite async loop, checking for queued jobs every
    ``poll_interval`` seconds. When jobs are present it processes the whole
    batch without sleeping so bursts drain quickly. Sleep only occurs when
    the queue is empty.

    Job lifecycle
    -------------
    queued  → running   : claimed via conditional UPDATE (rowcount guard)
    running → succeeded : handler returned without raising
    running → failed    : handler raised any exception

    All transitions record started_at / finished_at. Failures record
    error_message so the cause is visible in the processing_jobs table.

    Args:
        poll_interval: Seconds to wait between polls when the queue is empty.
            Default is 30 seconds per the ARIA specification.
        session_factory: SQLAlchemy async session factory. Defaults to the
            application AsyncSessionLocal. Override in tests to inject mocks.
    """

    def __init__(
        self,
        poll_interval: int = _POLL_INTERVAL_SECONDS,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        listen_url: str | None = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._session_factory: async_sessionmaker[AsyncSession] = (
            session_factory if session_factory is not None else AsyncSessionLocal
        )
        self._running = False
        self._listen_url = listen_url
        self._wake_event: asyncio.Event = asyncio.Event()

    async def run(self) -> None:
        """Start the polling loop. Runs until stop() is called or cancelled.

        Handles asyncio.CancelledError (SIGINT / task cancellation) cleanly.
        Unexpected exceptions in the loop body are logged and retried after
        one poll interval to keep the worker alive through transient failures.
        """
        self._running = True
        logger.info(
            "WorkerProcessor started — batch_size=%d poll_interval=%ds",
            _BATCH_SIZE,
            self._poll_interval,
        )

        # Start LISTEN/NOTIFY listener if a raw DB URL was supplied.
        # Best-effort: on failure the worker falls back to _FALLBACK_POLL_SECONDS polling.
        listener_task = None
        if self._listen_url:
            listener_task = asyncio.create_task(self._start_listener())

        while self._running:
            try:
                processed = await self._process_batch()
                await self._run_periodic_sweeps()
                if processed == 0:
                    # Wait for a NOTIFY wake-up or the 60-second fallback timeout.
                    # This replaces the fixed 30s sleep — the worker is idle only when
                    # the queue is genuinely empty and no notification has arrived.
                    try:
                        await asyncio.wait_for(
                            self._wake_event.wait(),
                            timeout=_FALLBACK_POLL_SECONDS,
                        )
                    except TimeoutError:
                        pass
                    self._wake_event.clear()
            except asyncio.CancelledError:
                logger.info("WorkerProcessor cancelled — shutting down cleanly")
                self._running = False
                break
            except Exception as exc:
                logger.error(
                    "WorkerProcessor loop error (will retry after %ds): %s",
                    self._poll_interval,
                    exc,
                )
                await asyncio.sleep(self._poll_interval)

        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except (asyncio.CancelledError, Exception):
                pass

        logger.info("WorkerProcessor stopped")

    def stop(self) -> None:
        """Signal the worker to stop after the current batch completes.

        Does not cancel in-flight jobs — the current batch finishes first.
        """
        self._running = False
        logger.info("WorkerProcessor stop requested")

    async def _run_periodic_sweeps(self) -> None:
        """Run escalation sweep and outcome checks once per poll cycle."""
        from app.services.feedback.outcome_tracker import run_outcome_checks

        try:
            async with self._session_factory() as session:
                await _run_escalation_sweep(session)
        except Exception as exc:
            logger.warning("escalation_sweep error: %s", exc)

        try:
            async with self._session_factory() as session:
                resolved = await run_outcome_checks(session)
                if resolved:
                    await session.commit()
        except Exception as exc:
            logger.warning("outcome_checks error: %s", exc)

    async def _process_batch(self) -> int:
        """Fetch up to _BATCH_SIZE queued jobs and dispatch each one.

        Returns:
            Number of jobs that were claimed and dispatched in this batch
            (includes jobs that subsequently transitioned to 'failed').
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProcessingJob)
                .where(
                    ProcessingJob.status == "queued",
                    (ProcessingJob.retry_after.is_(None))
                    | (ProcessingJob.retry_after <= func.now()),
                )
                .order_by(ProcessingJob.queued_at.asc())
                .limit(_BATCH_SIZE)
            )
            jobs = list(result.scalars().all())

        dispatched = 0
        for job in jobs:
            claimed = await self._process_one(job)
            if claimed:
                dispatched += 1

        return dispatched

    async def _process_one(self, job: ProcessingJob) -> bool:
        """Claim and execute a single job.

        The claim step is an UPDATE WHERE status='queued'. If rowcount is 0
        another worker already claimed the job and we skip it — safe for
        future multi-worker deployments without additional locking.

        Args:
            job: The ProcessingJob to attempt to claim and execute.

        Returns:
            True  — job was claimed and processed (success or failure).
            False — job was already claimed by another worker; skipped.
        """
        # --- Claim atomically -------------------------------------------
        async with self._session_factory() as session:
            claim_result = await session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.job_id == job.job_id,
                    ProcessingJob.status == "queued",
                )
                .values(
                    status="running",
                    started_at=datetime.now(UTC),
                )
            )
            await session.commit()

        if claim_result.rowcount == 0:
            logger.debug("Job %s already claimed by another worker — skipping", job.job_id)
            return False

        logger.info(
            "Job %s [%s] claimed for patient=%s",
            job.job_id,
            job.job_type,
            job.patient_id,
        )

        # --- Dispatch to handler ----------------------------------------
        handler = _HANDLERS.get(job.job_type)
        if handler is None:
            error_msg = f"Unknown job_type {job.job_type!r} — no handler registered"
            logger.error("Job %s: %s", job.job_id, error_msg)
            await self._mark_failed_or_retry(job.job_id, error_msg, job.retry_count)
            return True

        try:
            async with self._session_factory() as handler_session:
                await handler(job, handler_session)
            await self._mark_succeeded(job.job_id)
            logger.info("Job %s [%s] succeeded", job.job_id, job.job_type)
        except Exception as exc:
            error_msg = str(exc)
            logger.error("Job %s [%s] failed: %s", job.job_id, job.job_type, error_msg)
            await self._mark_failed_or_retry(job.job_id, error_msg, job.retry_count)

        return True

    async def _mark_succeeded(self, job_id: str) -> None:
        """Transition a job to succeeded and record finished_at.

        Args:
            job_id: UUID string of the ProcessingJob to update.
        """
        async with self._session_factory() as session:
            await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.job_id == job_id)
                .values(
                    status="succeeded",
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def _mark_failed_or_retry(
        self,
        job_id: str,
        error_message: str,
        current_retry_count: int,
    ) -> None:
        """On failure, schedule a retry with exponential backoff or mark dead.

        If ``current_retry_count < _MAX_RETRIES``, increments retry_count,
        computes the next retry window using ``_RETRY_BACKOFF_SECONDS``, and
        resets status to ``queued`` with ``retry_after`` set so the batch
        query skips the job until the backoff expires.

        If ``current_retry_count >= _MAX_RETRIES`` (all 3 retries exhausted),
        sets status to ``dead`` so the job is no longer picked up and can be
        inspected via ``GET /api/admin/dead-jobs``.

        Backoff schedule (indexed by current_retry_count):
          0 → 30 s, 1 → 120 s, 2 → 480 s

        Args:
            job_id: UUID string of the ProcessingJob to update.
            error_message: Human-readable cause of failure. No PHI.
            current_retry_count: Value of retry_count on the job before this
                failure (i.e. how many retries have already been scheduled).
        """
        now = datetime.now(UTC)
        if current_retry_count < _MAX_RETRIES:
            new_retry_count = current_retry_count + 1
            backoff = _RETRY_BACKOFF_SECONDS[current_retry_count]
            retry_after = now + timedelta(seconds=backoff)
            logger.warning(
                "Job %s failed (attempt %d/%d) — retrying in %ds at %s: %s",
                job_id,
                new_retry_count,
                _MAX_RETRIES + 1,
                backoff,
                retry_after.isoformat(),
                error_message,
            )
            async with self._session_factory() as session:
                await session.execute(
                    update(ProcessingJob)
                    .where(ProcessingJob.job_id == job_id)
                    .values(
                        status="queued",
                        retry_count=new_retry_count,
                        retry_after=retry_after,
                        error_message=error_message,
                        finished_at=now,
                    )
                )
                await session.commit()
        else:
            logger.error(
                "Job %s exhausted all %d retries — marking dead: %s",
                job_id,
                _MAX_RETRIES,
                error_message,
            )
            async with self._session_factory() as session:
                await session.execute(
                    update(ProcessingJob)
                    .where(ProcessingJob.job_id == job_id)
                    .values(
                        status="dead",
                        error_message=error_message,
                        finished_at=now,
                    )
                )
                await session.commit()
