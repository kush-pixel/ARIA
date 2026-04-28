"""Unit and integration tests for the ARIA background worker.

Covers:
  processor.py — WorkerProcessor polling loop, status transitions,
                 claim guard, handler dispatch, error handling
  scheduler.py — enqueue_briefing_jobs(), idempotency key format,
                 skipping patients with existing briefings

Unit tests use only fixture data and mock AsyncSession objects — no real
database connection needed.

Run unit tests only (CI-safe):
    cd backend && python -m pytest tests/test_worker.py -v -m "not integration"

Run all tests (requires DATABASE_URL in backend/.env and live Supabase):
    cd backend && python -m pytest tests/test_worker.py -v
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.alert import Alert
from app.models.processing_job import ProcessingJob
from app.services.worker.processor import (
    _MAX_RETRIES,
    _RETRY_BACKOFF_SECONDS,
    WorkerProcessor,
    _handle_briefing_generation,
    _handle_bundle_import,
    _handle_pattern_recompute,
    _upsert_alert,
)
from app.services.worker.scheduler import (
    _briefing_idempotency_key,
    _pattern_recompute_idempotency_key,
    enqueue_briefing_jobs,
    enqueue_pattern_recompute_sweep,
)

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_job(
    job_id: str = "job-uuid-001",
    job_type: str = "bundle_import",
    patient_id: str = "1091",
    status: str = "queued",
    payload_ref: str | None = None,
    retry_count: int = 0,
) -> ProcessingJob:
    """Build a minimal ProcessingJob ORM instance for testing."""
    job = ProcessingJob()
    job.job_id = job_id
    job.job_type = job_type
    job.patient_id = patient_id
    job.status = status
    job.payload_ref = payload_ref
    job.retry_count = retry_count
    job.idempotency_key = f"{job_type}:{patient_id}:test"
    return job


def _make_session_factory(mock_session: AsyncMock) -> MagicMock:
    """Return a mock async session factory that yields mock_session as context manager."""
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock()
    factory.return_value = mock_cm
    return factory


def _mock_session() -> AsyncMock:
    """Return a fresh mock AsyncSession with commit and execute set up."""
    session = AsyncMock()
    session.commit = AsyncMock(return_value=None)
    return session


# ---------------------------------------------------------------------------
# processor.py — WorkerProcessor._process_batch
# ---------------------------------------------------------------------------


async def test_process_batch_empty_queue_returns_zero() -> None:
    """_process_batch returns 0 when no queued jobs exist."""
    session = _mock_session()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)

    count = await processor._process_batch()

    assert count == 0


async def test_process_batch_returns_count_of_dispatched_jobs() -> None:
    """_process_batch returns the number of successfully claimed jobs."""
    job1 = _make_job(job_id="job-001", job_type="bundle_import")
    job2 = _make_job(job_id="job-002", job_type="bundle_import")

    # Session for SELECT batch — returns two jobs
    select_session = _mock_session()
    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [job1, job2]
    select_session.execute = AsyncMock(return_value=select_result)

    factory = _make_session_factory(select_session)
    processor = WorkerProcessor(session_factory=factory)

    # Stub out _process_one so we don't need full DB mock chains
    processor._process_one = AsyncMock(return_value=True)  # type: ignore[method-assign]

    count = await processor._process_batch()

    assert count == 2
    assert processor._process_one.call_count == 2  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# processor.py — WorkerProcessor._process_one
# ---------------------------------------------------------------------------


async def test_process_one_claims_job_and_marks_succeeded() -> None:
    """_process_one transitions a job to running then succeeded on handler success."""
    job = _make_job(job_type="bundle_import")

    # Claim session: UPDATE rowcount = 1 (claimed)
    claim_session = _mock_session()
    claim_result = MagicMock()
    claim_result.rowcount = 1
    claim_session.execute = AsyncMock(return_value=claim_result)

    # Handler session: no-op
    handler_session = _mock_session()

    call_count = 0

    def factory_side_effect() -> AsyncMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_session_factory(claim_session).return_value
        return _make_session_factory(handler_session).return_value

    factory = MagicMock(side_effect=factory_side_effect)
    processor = WorkerProcessor(session_factory=factory)  # type: ignore[arg-type]

    # Replace handler + mark methods with no-op mocks
    processor._mark_succeeded = AsyncMock()  # type: ignore[method-assign]
    processor._mark_failed_or_retry = AsyncMock()  # type: ignore[method-assign]

    with patch(
        "app.services.worker.processor._HANDLERS",
        {"bundle_import": AsyncMock(return_value=None)},
    ):
        result = await processor._process_one(job)

    assert result is True
    processor._mark_succeeded.assert_called_once_with(job.job_id)
    processor._mark_failed_or_retry.assert_not_called()


async def test_process_one_skips_already_claimed_job() -> None:
    """_process_one returns False when rowcount=0 (job already claimed)."""
    job = _make_job()

    session = _mock_session()
    claim_result = MagicMock()
    claim_result.rowcount = 0  # another worker claimed it
    session.execute = AsyncMock(return_value=claim_result)

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)

    result = await processor._process_one(job)

    assert result is False


async def test_process_one_marks_failed_on_handler_exception() -> None:
    """_process_one marks the job as failed when the handler raises."""
    job = _make_job(job_type="bundle_import")

    claim_session = _mock_session()
    claim_result = MagicMock()
    claim_result.rowcount = 1
    claim_session.execute = AsyncMock(return_value=claim_result)

    handler_session = _mock_session()

    call_count = 0

    def factory_side_effect() -> AsyncMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_session_factory(claim_session).return_value
        return _make_session_factory(handler_session).return_value

    factory = MagicMock(side_effect=factory_side_effect)
    processor = WorkerProcessor(session_factory=factory)  # type: ignore[arg-type]
    processor._mark_succeeded = AsyncMock()  # type: ignore[method-assign]
    processor._mark_failed_or_retry = AsyncMock()  # type: ignore[method-assign]

    failing_handler = AsyncMock(side_effect=ValueError("test error"))

    with patch(
        "app.services.worker.processor._HANDLERS",
        {"bundle_import": failing_handler},
    ):
        result = await processor._process_one(job)

    assert result is True
    processor._mark_failed_or_retry.assert_called_once_with(job.job_id, "test error", 0)
    processor._mark_succeeded.assert_not_called()


async def test_process_one_marks_failed_for_unknown_job_type() -> None:
    """_process_one marks the job as failed when job_type has no handler."""
    job = _make_job(job_type="unknown_type")

    session = _mock_session()
    claim_result = MagicMock()
    claim_result.rowcount = 1
    session.execute = AsyncMock(return_value=claim_result)

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)
    processor._mark_failed_or_retry = AsyncMock()  # type: ignore[method-assign]

    result = await processor._process_one(job)

    assert result is True
    processor._mark_failed_or_retry.assert_called_once()
    error_msg = processor._mark_failed_or_retry.call_args[0][1]  # type: ignore[attr-defined]
    assert "unknown_type" in error_msg


# ---------------------------------------------------------------------------
# processor.py — WorkerProcessor._mark_succeeded / _mark_failed_or_retry
# ---------------------------------------------------------------------------


async def test_mark_succeeded_executes_update_with_correct_status() -> None:
    """_mark_succeeded sends an UPDATE with status='succeeded' and finished_at."""
    session = _mock_session()
    session.execute = AsyncMock(return_value=MagicMock())

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)

    await processor._mark_succeeded("job-abc")

    session.execute.assert_called_once()
    session.commit.assert_called_once()


async def test_mark_failed_or_retry_requeues_when_below_max_retries() -> None:
    """Below _MAX_RETRIES, job is re-queued with incremented retry_count and retry_after."""
    session = _mock_session()
    session.execute = AsyncMock(return_value=MagicMock())

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)

    # retry_count=0 → should schedule retry 1
    await processor._mark_failed_or_retry("job-abc", "transient error", current_retry_count=0)

    session.execute.assert_called_once()
    session.commit.assert_called_once()
    # Verify the UPDATE values contained status='queued' and retry_count=1
    call_args = session.execute.call_args[0][0]
    compiled = call_args.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "queued" in sql
    assert "retry_count" in sql


async def test_mark_failed_or_retry_uses_correct_backoff_per_attempt() -> None:
    """Each retry attempt uses the correct backoff from _RETRY_BACKOFF_SECONDS."""
    assert _RETRY_BACKOFF_SECONDS[0] == 30
    assert _RETRY_BACKOFF_SECONDS[1] == 120
    assert _RETRY_BACKOFF_SECONDS[2] == 480
    assert len(_RETRY_BACKOFF_SECONDS) == _MAX_RETRIES


async def test_mark_failed_or_retry_marks_dead_at_max_retries() -> None:
    """At _MAX_RETRIES, job is marked dead instead of re-queued."""
    session = _mock_session()
    session.execute = AsyncMock(return_value=MagicMock())

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)

    # retry_count already at _MAX_RETRIES → should go dead
    await processor._mark_failed_or_retry(
        "job-abc", "persistent error", current_retry_count=_MAX_RETRIES
    )

    session.execute.assert_called_once()
    session.commit.assert_called_once()
    call_args = session.execute.call_args[0][0]
    compiled = call_args.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "dead" in sql


# ---------------------------------------------------------------------------
# processor.py — WorkerProcessor.run loop
# ---------------------------------------------------------------------------


async def test_run_stops_on_cancelled_error() -> None:
    """run() exits cleanly when asyncio.CancelledError is raised."""
    processor = WorkerProcessor()
    processor._process_batch = AsyncMock(side_effect=asyncio.CancelledError)  # type: ignore[method-assign]

    await processor.run()

    assert processor._running is False


async def test_stop_sets_running_false() -> None:
    """stop() sets _running=False so the loop exits after the current batch."""
    processor = WorkerProcessor()
    processor._running = True
    processor.stop()
    assert processor._running is False


# ---------------------------------------------------------------------------
# processor.py — individual handler stubs
# ---------------------------------------------------------------------------


async def test_handle_pattern_recompute_calls_all_detectors_and_scorer() -> None:
    """_handle_pattern_recompute calls all 4 Layer 1 detectors then Layer 2 scorer."""
    job = _make_job(job_type="pattern_recompute")
    session = AsyncMock()
    _patient = MagicMock()
    _patient.enrolled_at = datetime(2024, 1, 1, tzinfo=UTC)
    _pat_result = MagicMock()
    _pat_result.scalar_one_or_none.return_value = _patient
    session.execute.return_value = _pat_result

    gap_result = {"gap_days": 2.0, "status": "flag", "threshold_used": {"flag": 1, "urgent": 3}}
    inertia_result = {"inertia_detected": True, "avg_systolic": 152.0, "elevated_count": 6, "duration_days": 14.0}
    adherence_result = {"pattern": "B", "adherence_pct": 91.0, "interpretation": "possible treatment-review case — elevated BP with high adherence signal"}
    deterioration_result = {"deterioration": False, "slope": -0.5, "recent_avg": 148.0, "baseline_avg": 152.0}
    variability_result = {"detected": False, "level": "none", "cv_pct": None, "visit_agenda_item": None, "variability_score": 0.0}

    with (
        patch("app.services.pattern_engine.gap_detector.run_gap_detector", AsyncMock(return_value=gap_result)) as gap_mock,
        patch("app.services.pattern_engine.inertia_detector.run_inertia_detector", AsyncMock(return_value=inertia_result)) as inertia_mock,
        patch("app.services.pattern_engine.adherence_analyzer.run_adherence_analyzer", AsyncMock(return_value=adherence_result)) as adherence_mock,
        patch("app.services.pattern_engine.deterioration_detector.run_deterioration_detector", AsyncMock(return_value=deterioration_result)) as deterioration_mock,
        patch("app.services.pattern_engine.variability_detector.run_variability_detector", AsyncMock(return_value=variability_result)),
        patch("app.services.pattern_engine.risk_scorer.compute_risk_score", AsyncMock(return_value=42.5)) as scorer_mock,
    ):
        await _handle_pattern_recompute(job, session)

    gap_mock.assert_awaited_once_with(session, "1091")
    inertia_mock.assert_awaited_once_with(session, "1091")
    adherence_mock.assert_awaited_once_with(session, "1091")
    deterioration_mock.assert_awaited_once_with(session, "1091")
    scorer_mock.assert_awaited_once_with("1091", session)


async def test_handle_pattern_recompute_writes_adherence_alert_for_pattern_a() -> None:
    """_handle_pattern_recompute calls _upsert_alert with 'adherence' when pattern == 'A' (Fix 11)."""
    job = _make_job(job_type="pattern_recompute")
    session = AsyncMock()
    session.add = MagicMock()

    gap_result = {"gap_days": 0.5, "status": "ok", "threshold_used": {"flag": 1, "urgent": 3}}
    inertia_result = {"inertia_detected": False}
    adherence_result = {"pattern": "A", "adherence_pct": 60.0, "interpretation": "possible adherence concern"}
    deterioration_result = {"deterioration": False, "slope": 0.1, "recent_avg": 148.0, "baseline_avg": 145.0}

    # scalar_one_or_none returns None → no existing alert → upsert proceeds to session.add
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    session.execute.return_value = no_existing

    added_alerts: list = []
    session.add = MagicMock(side_effect=added_alerts.append)

    with (
        patch("app.services.pattern_engine.gap_detector.run_gap_detector", AsyncMock(return_value=gap_result)),
        patch("app.services.pattern_engine.inertia_detector.run_inertia_detector", AsyncMock(return_value=inertia_result)),
        patch("app.services.pattern_engine.adherence_analyzer.run_adherence_analyzer", AsyncMock(return_value=adherence_result)),
        patch("app.services.pattern_engine.deterioration_detector.run_deterioration_detector", AsyncMock(return_value=deterioration_result)),
        patch("app.services.pattern_engine.variability_detector.run_variability_detector", AsyncMock(return_value={"detected": False, "level": "none", "cv_pct": None, "visit_agenda_item": None, "variability_score": 0.0})),
        patch("app.services.pattern_engine.risk_scorer.compute_risk_score", AsyncMock(return_value=55.0)),
    ):
        await _handle_pattern_recompute(job, session)

    alert_types = [a.alert_type for a in added_alerts]
    assert "adherence" in alert_types


async def test_handle_pattern_recompute_raises_without_patient_id() -> None:
    """_handle_pattern_recompute raises ValueError when patient_id is absent."""
    job = _make_job(job_type="pattern_recompute", patient_id=None)  # type: ignore[arg-type]
    session = AsyncMock()

    with pytest.raises(ValueError, match="missing patient_id"):
        await _handle_pattern_recompute(job, session)


async def test_handle_briefing_generation_calls_composer_and_summarizer() -> None:
    """_handle_briefing_generation calls compose_briefing then generate_llm_summary."""
    job = _make_job(job_type="briefing_generation")
    job.idempotency_key = "briefing_generation:1091:2026-04-18"

    # Fix 20: appointment date comes from patients.next_appointment
    mock_patient = MagicMock()
    mock_patient.next_appointment = datetime(2026, 4, 18, 7, 30, tzinfo=UTC)

    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = mock_patient
    session.execute.return_value = exec_result

    mock_briefing = MagicMock()

    with (
        patch("app.services.briefing.composer.compose_briefing", AsyncMock(return_value=mock_briefing)) as composer_mock,
        patch("app.services.briefing.summarizer.generate_llm_summary", AsyncMock(return_value=mock_briefing)) as summarizer_mock,
    ):
        await _handle_briefing_generation(job, session)

    composer_mock.assert_awaited_once_with(session, "1091", date(2026, 4, 18))
    summarizer_mock.assert_awaited_once_with(mock_briefing, session)


async def test_handle_briefing_generation_layer3_failure_does_not_fail_job() -> None:
    """Layer 3 LLM failure is caught and logged — job still succeeds."""
    job = _make_job(job_type="briefing_generation")
    job.idempotency_key = "briefing_generation:1091:2026-04-18"

    mock_patient = MagicMock()
    mock_patient.next_appointment = datetime(2026, 4, 18, 7, 30, tzinfo=UTC)

    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = mock_patient
    session.execute.return_value = exec_result

    mock_briefing = MagicMock()

    with (
        patch("app.services.briefing.composer.compose_briefing", AsyncMock(return_value=mock_briefing)),
        patch("app.services.briefing.summarizer.generate_llm_summary", AsyncMock(side_effect=RuntimeError("API down"))),
    ):
        # Should not raise — Layer 3 failure is handled gracefully
        await _handle_briefing_generation(job, session)


async def test_handle_briefing_generation_raises_without_patient_id() -> None:
    """_handle_briefing_generation raises ValueError when patient_id is absent."""
    job = _make_job(job_type="briefing_generation", patient_id=None)  # type: ignore[arg-type]
    session = AsyncMock()

    with pytest.raises(ValueError, match="missing patient_id"):
        await _handle_briefing_generation(job, session)


async def test_handle_briefing_generation_falls_back_to_today_when_no_appointment() -> None:
    """_handle_briefing_generation uses date.today() when next_appointment is None (Fix 20)."""
    job = _make_job(job_type="briefing_generation")
    job.idempotency_key = "briefing_generation:1091:2026-04-27"

    mock_patient = MagicMock()
    mock_patient.next_appointment = None

    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = mock_patient
    session.execute.return_value = exec_result

    mock_briefing = MagicMock()
    today = date.today()

    with (
        patch("app.services.briefing.composer.compose_briefing", AsyncMock(return_value=mock_briefing)) as composer_mock,
        patch("app.services.briefing.summarizer.generate_llm_summary", AsyncMock(return_value=mock_briefing)),
    ):
        await _handle_briefing_generation(job, session)

    composer_mock.assert_awaited_once_with(session, "1091", today)


async def test_handle_briefing_generation_falls_back_to_today_when_patient_not_found() -> None:
    """_handle_briefing_generation uses date.today() when patient row is absent (Fix 20)."""
    job = _make_job(job_type="briefing_generation")
    job.idempotency_key = "briefing_generation:1091:2026-04-27"

    session = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    session.execute.return_value = exec_result

    mock_briefing = MagicMock()
    today = date.today()

    with (
        patch("app.services.briefing.composer.compose_briefing", AsyncMock(return_value=mock_briefing)) as composer_mock,
        patch("app.services.briefing.summarizer.generate_llm_summary", AsyncMock(return_value=mock_briefing)),
    ):
        await _handle_briefing_generation(job, session)

    composer_mock.assert_awaited_once_with(session, "1091", today)


async def test_handle_bundle_import_raises_when_payload_ref_missing() -> None:
    """_handle_bundle_import raises ValueError when payload_ref is None."""
    job = _make_job(job_type="bundle_import", payload_ref=None)
    session = AsyncMock()

    with pytest.raises(ValueError, match="missing payload_ref"):
        await _handle_bundle_import(job, session)


async def test_handle_bundle_import_raises_when_file_not_found(tmp_path: pytest.fixture) -> None:  # type: ignore[valid-type]
    """_handle_bundle_import raises FileNotFoundError for a non-existent path."""
    job = _make_job(job_type="bundle_import", payload_ref=str(tmp_path / "missing.json"))
    session = AsyncMock()

    with pytest.raises(FileNotFoundError):
        await _handle_bundle_import(job, session)


# ---------------------------------------------------------------------------
# processor.py — _upsert_alert (Fix 30: delivered_at set on insert)
# ---------------------------------------------------------------------------


async def test_upsert_alert_sets_delivered_at_on_insert() -> None:
    """_upsert_alert sets delivered_at to now() on the newly created Alert (Fix 30)."""
    session = AsyncMock()
    # No existing alert found — scalar_one_or_none returns None
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    session.execute.return_value = no_existing

    # session.add() is synchronous in SQLAlchemy — use MagicMock so side_effect fires
    added_alerts: list[Alert] = []
    session.add = MagicMock(side_effect=added_alerts.append)

    await _upsert_alert(session, "1091", "inertia")

    assert len(added_alerts) == 1
    alert = added_alerts[0]
    assert alert.delivered_at is not None
    assert alert.triggered_at is not None
    # delivered_at and triggered_at should be essentially the same instant
    delta = abs((alert.delivered_at - alert.triggered_at).total_seconds())
    assert delta < 1.0


async def test_upsert_alert_skips_insert_when_alert_already_exists() -> None:
    """_upsert_alert does not add a second Alert row when one already exists today."""
    session = AsyncMock()
    existing_alert = MagicMock(spec=Alert)
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = existing_alert
    session.execute.return_value = existing

    await _upsert_alert(session, "1091", "inertia")

    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# scheduler.py — _briefing_idempotency_key
# ---------------------------------------------------------------------------


def test_briefing_idempotency_key_format() -> None:
    """_briefing_idempotency_key returns the expected string format."""
    key = _briefing_idempotency_key("1091", date(2026, 4, 15))
    assert key == "briefing_generation:1091:2026-04-15"


def test_briefing_idempotency_key_is_unique_per_patient_and_date() -> None:
    """Different patient or date produce different keys."""
    key_a = _briefing_idempotency_key("1091", date(2026, 4, 15))
    key_b = _briefing_idempotency_key("9999", date(2026, 4, 15))
    key_c = _briefing_idempotency_key("1091", date(2026, 4, 16))

    assert key_a != key_b
    assert key_a != key_c
    assert key_b != key_c


# ---------------------------------------------------------------------------
# scheduler.py — enqueue_briefing_jobs
# ---------------------------------------------------------------------------


async def test_enqueue_briefing_jobs_no_patients_returns_zero() -> None:
    """enqueue_briefing_jobs returns 0 when no appointment-day patients found."""
    session = _mock_session()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)

    factory = _make_session_factory(session)

    count = await enqueue_briefing_jobs(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 0
    # commit should not be called when there are no patients to process
    session.commit.assert_not_called()


async def test_enqueue_briefing_jobs_enqueues_one_job_per_patient() -> None:
    """enqueue_briefing_jobs inserts one job per qualifying patient."""
    from app.models.patient import Patient

    patient = Patient()
    patient.patient_id = "1091"
    patient.monitoring_active = True
    patient.next_appointment = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = [patient]
    insert_result = MagicMock()
    notify_result = MagicMock()
    session.execute = AsyncMock(side_effect=[patients_result, insert_result, notify_result])

    factory = _make_session_factory(session)

    count = await enqueue_briefing_jobs(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 1
    # execute called three times: SELECT patients + INSERT job + pg_notify
    assert session.execute.call_count == 3
    session.commit.assert_called_once()


async def test_enqueue_briefing_jobs_multiple_patients() -> None:
    """enqueue_briefing_jobs enqueues one job for each qualifying patient."""
    from app.models.patient import Patient

    def _patient(pid: str) -> Patient:
        p = Patient()
        p.patient_id = pid
        p.monitoring_active = True
        p.next_appointment = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)
        return p

    patients = [_patient("1091"), _patient("2001"), _patient("3005")]

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = patients
    insert_result = MagicMock()
    notify_result = MagicMock()
    session.execute = AsyncMock(
        side_effect=[patients_result, insert_result, insert_result, insert_result, notify_result]
    )

    factory = _make_session_factory(session)

    count = await enqueue_briefing_jobs(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 3
    # 1 SELECT + 3 INSERTs + 1 pg_notify
    assert session.execute.call_count == 5
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Integration test — requires live Supabase connection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# scheduler.py — _pattern_recompute_idempotency_key
# ---------------------------------------------------------------------------


def test_pattern_recompute_idempotency_key_format() -> None:
    """_pattern_recompute_idempotency_key returns the expected string format."""
    key = _pattern_recompute_idempotency_key("1091", date(2026, 4, 15))
    assert key == "pattern_recompute:1091:2026-04-15"


def test_pattern_recompute_idempotency_key_is_unique_per_patient_and_date() -> None:
    """Different patient or date produce different keys."""
    key_a = _pattern_recompute_idempotency_key("1091", date(2026, 4, 15))
    key_b = _pattern_recompute_idempotency_key("9999", date(2026, 4, 15))
    key_c = _pattern_recompute_idempotency_key("1091", date(2026, 4, 16))

    assert key_a != key_b
    assert key_a != key_c
    assert key_b != key_c


# ---------------------------------------------------------------------------
# scheduler.py — enqueue_pattern_recompute_sweep
# ---------------------------------------------------------------------------


async def test_enqueue_pattern_recompute_sweep_no_patients_returns_zero() -> None:
    """enqueue_pattern_recompute_sweep returns 0 when no monitoring-active patients exist."""
    session = _mock_session()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)

    factory = _make_session_factory(session)

    count = await enqueue_pattern_recompute_sweep(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 0
    session.commit.assert_not_called()


async def test_enqueue_pattern_recompute_sweep_enqueues_one_job_per_patient() -> None:
    """enqueue_pattern_recompute_sweep inserts one job for a single monitoring-active patient."""
    from app.models.patient import Patient

    patient = Patient()
    patient.patient_id = "1091"
    patient.monitoring_active = True

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = [patient]
    insert_result = MagicMock()
    notify_result = MagicMock()
    session.execute = AsyncMock(side_effect=[patients_result, insert_result, notify_result])

    factory = _make_session_factory(session)

    count = await enqueue_pattern_recompute_sweep(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 1
    assert session.execute.call_count == 3  # SELECT patients + INSERT job + pg_notify
    session.commit.assert_called_once()


async def test_enqueue_pattern_recompute_sweep_multiple_patients() -> None:
    """enqueue_pattern_recompute_sweep enqueues one job per monitoring-active patient."""
    from app.models.patient import Patient

    def _patient(pid: str) -> Patient:
        p = Patient()
        p.patient_id = pid
        p.monitoring_active = True
        return p

    patients = [_patient("1091"), _patient("2001"), _patient("3005")]

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = patients
    insert_result = MagicMock()
    notify_result = MagicMock()
    session.execute = AsyncMock(
        side_effect=[patients_result, insert_result, insert_result, insert_result, notify_result]
    )

    factory = _make_session_factory(session)

    count = await enqueue_pattern_recompute_sweep(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 3
    assert session.execute.call_count == 5  # 1 SELECT + 3 INSERTs + 1 pg_notify
    session.commit.assert_called_once()


async def test_enqueue_pattern_recompute_sweep_does_not_filter_by_appointment_date() -> None:
    """Sweep includes patients regardless of next_appointment value."""
    from app.models.patient import Patient

    # patient with no next_appointment — should still be included
    patient = Patient()
    patient.patient_id = "1091"
    patient.monitoring_active = True
    patient.next_appointment = None

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = [patient]
    insert_result = MagicMock()
    notify_result = MagicMock()
    session.execute = AsyncMock(side_effect=[patients_result, insert_result, notify_result])

    factory = _make_session_factory(session)

    count = await enqueue_pattern_recompute_sweep(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 1


# ---------------------------------------------------------------------------
# _handle_pattern_recompute — mini-briefing trigger (Fix 46)
# ---------------------------------------------------------------------------


def _build_pattern_recompute_session(
    gap_status: str = "none",
    inertia: bool = False,
    deterioration: bool = False,
) -> AsyncMock:
    """Build a mock session for _handle_pattern_recompute mini-briefing tests."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()

    upsert_result = MagicMock()
    session.execute = AsyncMock(return_value=upsert_result)
    return session


@pytest.mark.asyncio
async def test_pattern_recompute_triggers_mini_briefing_on_gap_urgent() -> None:
    """compose_mini_briefing must be called when gap status is urgent."""
    job = _make_job(job_type="pattern_recompute", patient_id="1091")
    session = AsyncMock()
    session.flush = AsyncMock()
    _patient = MagicMock()
    _patient.enrolled_at = datetime(2024, 1, 1, tzinfo=UTC)
    _pat_result = MagicMock()
    _pat_result.scalar_one_or_none.return_value = _patient
    session.execute.return_value = _pat_result

    gap_result = {"gap_days": 4, "status": "urgent"}
    inertia_result = {"inertia_detected": False, "avg_systolic": None}
    adherence_result = {"pattern": "none", "adherence_pct": 91.0}
    deterioration_result = {"deterioration": False, "slope": 0.1}

    with (
        patch("app.services.pattern_engine.gap_detector.run_gap_detector", AsyncMock(return_value=gap_result)),
        patch("app.services.pattern_engine.inertia_detector.run_inertia_detector", AsyncMock(return_value=inertia_result)),
        patch("app.services.pattern_engine.adherence_analyzer.run_adherence_analyzer", AsyncMock(return_value=adherence_result)),
        patch("app.services.pattern_engine.deterioration_detector.run_deterioration_detector", AsyncMock(return_value=deterioration_result)),
        patch("app.services.pattern_engine.variability_detector.run_variability_detector", AsyncMock(return_value={"detected": False, "level": "none", "cv_pct": None, "visit_agenda_item": None, "variability_score": 0.0})),
        patch("app.services.pattern_engine.risk_scorer.compute_risk_score", AsyncMock(return_value=72.5)),
        patch("app.services.worker.processor._upsert_alert", new_callable=AsyncMock),
        patch("app.services.briefing.composer.compose_mini_briefing", new_callable=AsyncMock) as mock_mini,
    ):
        await _handle_pattern_recompute(job, session)

    mock_mini.assert_awaited_once_with(session, "1091", "gap_urgent")


@pytest.mark.asyncio
async def test_pattern_recompute_triggers_mini_briefing_on_deterioration() -> None:
    """compose_mini_briefing must be called when deterioration fires."""
    job = _make_job(job_type="pattern_recompute", patient_id="1091")
    session = AsyncMock()
    session.flush = AsyncMock()
    _patient = MagicMock()
    _patient.enrolled_at = datetime(2024, 1, 1, tzinfo=UTC)
    _pat_result = MagicMock()
    _pat_result.scalar_one_or_none.return_value = _patient
    session.execute.return_value = _pat_result

    gap_result = {"gap_days": 0, "status": "none"}
    inertia_result = {"inertia_detected": False, "avg_systolic": None}
    adherence_result = {"pattern": "none", "adherence_pct": 91.0}
    deterioration_result = {"deterioration": True, "slope": 2.4}

    with (
        patch("app.services.pattern_engine.gap_detector.run_gap_detector", AsyncMock(return_value=gap_result)),
        patch("app.services.pattern_engine.inertia_detector.run_inertia_detector", AsyncMock(return_value=inertia_result)),
        patch("app.services.pattern_engine.adherence_analyzer.run_adherence_analyzer", AsyncMock(return_value=adherence_result)),
        patch("app.services.pattern_engine.deterioration_detector.run_deterioration_detector", AsyncMock(return_value=deterioration_result)),
        patch("app.services.pattern_engine.variability_detector.run_variability_detector", AsyncMock(return_value={"detected": False, "level": "none", "cv_pct": None, "visit_agenda_item": None, "variability_score": 0.0})),
        patch("app.services.pattern_engine.risk_scorer.compute_risk_score", AsyncMock(return_value=68.0)),
        patch("app.services.worker.processor._upsert_alert", new_callable=AsyncMock),
        patch("app.services.briefing.composer.compose_mini_briefing", new_callable=AsyncMock) as mock_mini,
    ):
        await _handle_pattern_recompute(job, session)

    mock_mini.assert_awaited_once_with(session, "1091", "deterioration")


@pytest.mark.asyncio
async def test_pattern_recompute_does_not_trigger_mini_briefing_on_inertia() -> None:
    """compose_mini_briefing must NOT be called when only inertia fires."""
    job = _make_job(job_type="pattern_recompute", patient_id="1091")
    session = AsyncMock()
    session.flush = AsyncMock()
    _patient = MagicMock()
    _patient.enrolled_at = datetime(2024, 1, 1, tzinfo=UTC)
    _pat_result = MagicMock()
    _pat_result.scalar_one_or_none.return_value = _patient
    session.execute.return_value = _pat_result

    gap_result = {"gap_days": 0, "status": "none"}
    inertia_result = {"inertia_detected": True, "avg_systolic": 158.0}
    adherence_result = {"pattern": "none", "adherence_pct": 91.0}
    deterioration_result = {"deterioration": False, "slope": 0.1}

    with (
        patch("app.services.pattern_engine.gap_detector.run_gap_detector", AsyncMock(return_value=gap_result)),
        patch("app.services.pattern_engine.inertia_detector.run_inertia_detector", AsyncMock(return_value=inertia_result)),
        patch("app.services.pattern_engine.adherence_analyzer.run_adherence_analyzer", AsyncMock(return_value=adherence_result)),
        patch("app.services.pattern_engine.deterioration_detector.run_deterioration_detector", AsyncMock(return_value=deterioration_result)),
        patch("app.services.pattern_engine.variability_detector.run_variability_detector", AsyncMock(return_value={"detected": False, "level": "none", "cv_pct": None, "visit_agenda_item": None, "variability_score": 0.0})),
        patch("app.services.pattern_engine.risk_scorer.compute_risk_score", AsyncMock(return_value=60.0)),
        patch("app.services.worker.processor._upsert_alert", new_callable=AsyncMock),
        patch("app.services.briefing.composer.compose_mini_briefing", new_callable=AsyncMock) as mock_mini,
    ):
        await _handle_pattern_recompute(job, session)

    mock_mini.assert_not_awaited()


# ---------------------------------------------------------------------------
# scheduler.py — Fix 60: pg_notify sent after enqueue
# ---------------------------------------------------------------------------


async def test_enqueue_briefing_jobs_sends_pg_notify_before_commit() -> None:
    """enqueue_briefing_jobs sends a pg_notify call inside the same transaction as INSERTs."""
    from app.models.patient import Patient

    patient = Patient()
    patient.patient_id = "1091"
    patient.monitoring_active = True
    patient.next_appointment = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = [patient]
    session.execute = AsyncMock(
        side_effect=[patients_result, MagicMock(), MagicMock()]
    )
    factory = _make_session_factory(session)

    await enqueue_briefing_jobs(session_factory=factory, target_date=date(2026, 4, 15))

    # At least one execute call must carry a TextClause containing pg_notify
    notify_found = any(
        c.args and "pg_notify" in str(c.args[0])
        for c in session.execute.call_args_list
    )
    assert notify_found, "Expected pg_notify in execute calls"
    session.commit.assert_called_once()


async def test_enqueue_pattern_recompute_sweep_sends_pg_notify_before_commit() -> None:
    """enqueue_pattern_recompute_sweep sends a pg_notify call when jobs are enqueued."""
    from app.models.patient import Patient

    patient = Patient()
    patient.patient_id = "1091"
    patient.monitoring_active = True

    session = _mock_session()
    patients_result = MagicMock()
    patients_result.scalars.return_value.all.return_value = [patient]
    session.execute = AsyncMock(
        side_effect=[patients_result, MagicMock(), MagicMock()]
    )
    factory = _make_session_factory(session)

    await enqueue_pattern_recompute_sweep(session_factory=factory, target_date=date(2026, 4, 15))

    notify_found = any(
        c.args and "pg_notify" in str(c.args[0])
        for c in session.execute.call_args_list
    )
    assert notify_found, "Expected pg_notify in execute calls"
    session.commit.assert_called_once()


async def test_enqueue_briefing_jobs_no_notify_when_no_patients() -> None:
    """enqueue_briefing_jobs sends no pg_notify and no commit when no patients qualify."""
    session = _mock_session()
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_result)
    factory = _make_session_factory(session)

    count = await enqueue_briefing_jobs(session_factory=factory, target_date=date(2026, 4, 15))

    assert count == 0
    notify_found = any(
        c.args and "pg_notify" in str(c.args[0])
        for c in session.execute.call_args_list
    )
    assert not notify_found, "pg_notify must not be called when no jobs were enqueued"
    session.commit.assert_not_called()


def test_worker_processor_wake_event_set_wakes_idle_loop() -> None:
    """_wake_event.set() on WorkerProcessor is callable and sets the event."""
    processor = WorkerProcessor()
    assert not processor._wake_event.is_set()
    processor._wake_event.set()
    assert processor._wake_event.is_set()
    processor._wake_event.clear()
    assert not processor._wake_event.is_set()


def test_worker_processor_listen_url_stored() -> None:
    """listen_url passed to constructor is stored and accessible."""
    processor = WorkerProcessor(listen_url="postgresql://host/db")
    assert processor._listen_url == "postgresql://host/db"


def test_worker_processor_no_listen_url_by_default() -> None:
    """listen_url defaults to None — no listener started in unit-test deployments."""
    processor = WorkerProcessor()
    assert processor._listen_url is None


# ---------------------------------------------------------------------------
# Integration test — requires live Supabase connection
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_enqueue_briefing_jobs_integration_no_appointments_today() -> None:
    """Scheduler runs against real DB and returns without error.

    This test does not create any data — it verifies the query executes
    successfully and returns a non-negative integer. The exact count depends
    on what appointments are set for today in the Supabase instance.
    """
    count = await enqueue_briefing_jobs()
    assert isinstance(count, int)
    assert count >= 0
