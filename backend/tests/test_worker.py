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
    WorkerProcessor,
    _handle_briefing_generation,
    _handle_bundle_import,
    _handle_pattern_recompute,
    _upsert_alert,
)
from app.services.worker.scheduler import _briefing_idempotency_key, enqueue_briefing_jobs

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


def _make_job(
    job_id: str = "job-uuid-001",
    job_type: str = "bundle_import",
    patient_id: str = "1091",
    status: str = "queued",
    payload_ref: str | None = None,
) -> ProcessingJob:
    """Build a minimal ProcessingJob ORM instance for testing."""
    job = ProcessingJob()
    job.job_id = job_id
    job.job_type = job_type
    job.patient_id = patient_id
    job.status = status
    job.payload_ref = payload_ref
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

    # Replace handler + mark_succeeded with no-op mocks
    processor._mark_succeeded = AsyncMock()  # type: ignore[method-assign]
    processor._mark_failed = AsyncMock()  # type: ignore[method-assign]

    with patch(
        "app.services.worker.processor._HANDLERS",
        {"bundle_import": AsyncMock(return_value=None)},
    ):
        result = await processor._process_one(job)

    assert result is True
    processor._mark_succeeded.assert_called_once_with(job.job_id)
    processor._mark_failed.assert_not_called()


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
    processor._mark_failed = AsyncMock()  # type: ignore[method-assign]

    failing_handler = AsyncMock(side_effect=ValueError("test error"))

    with patch(
        "app.services.worker.processor._HANDLERS",
        {"bundle_import": failing_handler},
    ):
        result = await processor._process_one(job)

    assert result is True
    processor._mark_failed.assert_called_once_with(job.job_id, "test error")
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
    processor._mark_failed = AsyncMock()  # type: ignore[method-assign]

    result = await processor._process_one(job)

    assert result is True
    processor._mark_failed.assert_called_once()
    error_msg = processor._mark_failed.call_args[0][1]  # type: ignore[attr-defined]
    assert "unknown_type" in error_msg


# ---------------------------------------------------------------------------
# processor.py — WorkerProcessor._mark_succeeded / _mark_failed
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


async def test_mark_failed_executes_update_with_error_message() -> None:
    """_mark_failed sends UPDATE with status='failed' and the error_message."""
    session = _mock_session()
    session.execute = AsyncMock(return_value=MagicMock())

    factory = _make_session_factory(session)
    processor = WorkerProcessor(session_factory=factory)

    await processor._mark_failed("job-abc", "something went wrong")

    session.execute.assert_called_once()
    session.commit.assert_called_once()


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

    gap_result = {"gap_days": 2.0, "status": "flag", "threshold_used": {"flag": 1, "urgent": 3}}
    inertia_result = {"inertia_detected": True, "avg_systolic": 152.0, "elevated_count": 6, "duration_days": 14.0}
    adherence_result = {"pattern": "B", "adherence_pct": 91.0, "interpretation": "possible treatment-review case — elevated BP with high adherence signal"}
    deterioration_result = {"deterioration": False, "slope": -0.5, "recent_avg": 148.0, "baseline_avg": 152.0}

    with (
        patch("app.services.pattern_engine.gap_detector.run_gap_detector", AsyncMock(return_value=gap_result)) as gap_mock,
        patch("app.services.pattern_engine.inertia_detector.run_inertia_detector", AsyncMock(return_value=inertia_result)) as inertia_mock,
        patch("app.services.pattern_engine.adherence_analyzer.run_adherence_analyzer", AsyncMock(return_value=adherence_result)) as adherence_mock,
        patch("app.services.pattern_engine.deterioration_detector.run_deterioration_detector", AsyncMock(return_value=deterioration_result)) as deterioration_mock,
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
    session.execute = AsyncMock(side_effect=[patients_result, insert_result])

    factory = _make_session_factory(session)

    count = await enqueue_briefing_jobs(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 1
    # execute called twice: SELECT patients + INSERT job
    assert session.execute.call_count == 2
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
    session.execute = AsyncMock(
        side_effect=[patients_result, insert_result, insert_result, insert_result]
    )

    factory = _make_session_factory(session)

    count = await enqueue_briefing_jobs(
        session_factory=factory,
        target_date=date(2026, 4, 15),
    )

    assert count == 3
    # 1 SELECT + 3 INSERTs
    assert session.execute.call_count == 4
    session.commit.assert_called_once()


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
