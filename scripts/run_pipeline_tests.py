"""End-to-end ARIA pipeline test.

Tests all 12 checkpoints without modifying production data permanently.
Uses httpx.AsyncClient with ASGITransport (no live uvicorn).
Cleans up any test data created during the run.

Usage (from repo root, aria conda env active):
    python scripts/run_pipeline_tests.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))
os.chdir(_BACKEND)  # config.py resolves .env relative to cwd

import httpx
from sqlalchemy import delete, func, select, text

from app.db.base import AsyncSessionLocal
from app.models.alert import Alert
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.models.reading import Reading

_DEMO_PATIENT = "1091"
_DEMO_APPT_DATE = date(2026, 4, 24)
_TEST_PATIENT = "TEST_PIPELINE"

_BRIEFING_FIELDS = {
    "trend_summary",
    "medication_status",
    "adherence_summary",
    "active_problems",
    "overdue_labs",
    "visit_agenda",
    "urgent_flags",
    "risk_score",
    "data_limitations",
}


# ── Test runner ────────────────────────────────────────────────────────────────

class TestRunner:
    """Accumulates pass/fail results and prints a final summary."""

    def __init__(self) -> None:
        self._passed: int = 0
        self._failed: int = 0
        self._failures: list[str] = []

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        if passed:
            self._passed += 1
            print(f"PASS: {name}{(' — ' + detail) if detail else ''}")
        else:
            self._failed += 1
            self._failures.append(f"  - {name} — {detail}")
            print(f"FAIL: {name} — {detail}")

    def summary(self) -> None:
        total = self._passed + self._failed
        print(f"\n=== Test Summary ===")
        print(f"{self._passed}/{total} tests passed")
        if self._failures:
            print("\nFailures:")
            for f in self._failures:
                print(f)

    @property
    def all_passed(self) -> bool:
        return self._failed == 0


# ── Individual tests ───────────────────────────────────────────────────────────

async def test_db_connectivity(runner: TestRunner) -> None:
    """Test 1 — DB connectivity and row counts."""
    name = "DB connectivity"
    try:
        async with AsyncSessionLocal() as session:
            tables = [
                Patient, ClinicalContext, Reading, MedicationConfirmation,
                Alert, Briefing, ProcessingJob, AuditEvent,
            ]
            counts: dict[str, int] = {}
            for model in tables:
                result = await session.execute(
                    select(func.count()).select_from(model)
                )
                counts[model.__tablename__] = result.scalar_one()

        patients = counts["patients"]
        readings = counts["readings"]
        confirmations = counts["medication_confirmations"]

        checks = [
            (patients >= 1, f"patients={patients} (need ≥1)"),
            (readings >= 47, f"readings={readings} (need ≥47)"),
            (confirmations >= 400, f"medication_confirmations={confirmations} (need ≥400, actual={confirmations})"),
        ]
        failures = [msg for ok, msg in checks if not ok]
        if failures:
            runner.record(name, False, "; ".join(failures))
        else:
            detail = (
                f"all 8 tables reachable — "
                f"patients={patients}, readings={readings}, "
                f"confirmations={confirmations}"
            )
            runner.record(name, True, detail)
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_api_health(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 2 — GET /health."""
    name = "API health"
    try:
        resp = await client.get("/health")
        body = resp.json()
        if resp.status_code == 200 and body.get("status") == "ok":
            runner.record(name, True, f"GET /health returned 200")
        else:
            runner.record(name, False, f"status={resp.status_code} body={body}")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_patients(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 3 — GET /api/patients."""
    name = "GET /api/patients"
    try:
        resp = await client.get("/api/patients")
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            runner.record(name, False, "expected non-empty list")
            return
        first = data[0]
        required = {"patient_id", "risk_tier", "risk_score"}
        missing = required - first.keys()
        if missing:
            runner.record(name, False, f"missing fields: {missing}")
        else:
            runner.record(name, True, f"{len(data)} patients returned with required fields")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_patient_1091(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 4 — GET /api/patients/1091."""
    name = "GET /api/patients/1091"
    try:
        resp = await client.get(f"/api/patients/{_DEMO_PATIENT}")
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if data.get("patient_id") == _DEMO_PATIENT:
            runner.record(name, True, f"patient_id={_DEMO_PATIENT} confirmed")
        else:
            runner.record(name, False, f"unexpected patient_id={data.get('patient_id')}")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_briefing(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 5 — GET /api/briefings/1091 (read-only check)."""
    name = "GET /api/briefings/1091"
    try:
        resp = await client.get(f"/api/briefings/{_DEMO_PATIENT}")
        if resp.status_code == 404:
            runner.record(name, True, "no briefing yet (404 is valid)")
            return
        if resp.status_code != 200:
            runner.record(name, False, f"unexpected status={resp.status_code}")
            return
        data = resp.json()
        required = {"briefing_id", "llm_response"}
        missing = required - data.keys()
        if missing:
            runner.record(name, False, f"missing fields: {missing}")
        else:
            runner.record(name, True, "briefing returned with briefing_id, llm_response")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_readings(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 6 — GET /api/readings?patient_id=1091."""
    name = "GET /api/readings?patient_id=1091"
    try:
        resp = await client.get("/api/readings", params={"patient_id": _DEMO_PATIENT})
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            runner.record(name, False, "expected non-empty list")
            return
        if "systolic_avg" not in data[0]:
            runner.record(name, False, "missing systolic_avg in first item")
        else:
            runner.record(name, True, f"{len(data)} readings returned")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_adherence(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 7 — GET /api/adherence/1091."""
    name = "GET /api/adherence/1091"
    try:
        resp = await client.get(f"/api/adherence/{_DEMO_PATIENT}")
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if not isinstance(data, list):
            runner.record(name, False, "expected list")
            return
        if len(data) > 0:
            first = data[0]
            required = {"medication_name", "adherence_pct"}
            missing = required - first.keys()
            if missing:
                runner.record(name, False, f"missing fields: {missing}")
                return
        runner.record(name, True, f"{len(data)} medications returned")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_alerts(runner: TestRunner, client: httpx.AsyncClient) -> None:
    """Test 8 — GET /api/alerts."""
    name = "GET /api/alerts"
    try:
        resp = await client.get("/api/alerts")
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if not isinstance(data, list):
            runner.record(name, False, "expected list")
        else:
            runner.record(name, True, f"{len(data)} alerts returned (may be empty)")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_layer1_detectors(runner: TestRunner) -> None:
    """Test 9 — All four Layer 1 detectors on patient 1091."""
    from app.services.pattern_engine.adherence_analyzer import run_adherence_analyzer
    from app.services.pattern_engine.deterioration_detector import run_deterioration_detector
    from app.services.pattern_engine.gap_detector import run_gap_detector
    from app.services.pattern_engine.inertia_detector import run_inertia_detector

    name = "Layer 1 detectors"
    errors: list[str] = []

    async with AsyncSessionLocal() as session:
        detectors = [
            ("gap", run_gap_detector),
            ("inertia", run_inertia_detector),
            ("adherence", run_adherence_analyzer),
            ("deterioration", run_deterioration_detector),
        ]
        results: dict[str, Any] = {}
        for label, fn in detectors:
            try:
                result = await fn(session, _DEMO_PATIENT)
                results[label] = result
            except Exception as exc:
                errors.append(f"{label}: {exc}")

    if errors:
        runner.record(name, False, "; ".join(errors))
    else:
        detail = (
            f"gap={results['gap']['gap_days']}d, "
            f"inertia={results['inertia']['inertia_detected']}, "
            f"adherence={results['adherence']['adherence_pct']}%, "
            f"deterioration={results['deterioration']['deterioration']}"
        )
        runner.record(name, True, detail)


async def test_risk_scorer(runner: TestRunner) -> None:
    """Test 10 — Layer 2 risk scorer on patient 1091."""
    from app.services.pattern_engine.risk_scorer import compute_risk_score

    name = "Layer 2 risk scorer"
    try:
        async with AsyncSessionLocal() as session:
            score = await compute_risk_score(_DEMO_PATIENT, session)
        if isinstance(score, (int, float)) and 0.0 <= float(score) <= 100.0:
            runner.record(name, True, f"risk_score={score}")
        else:
            runner.record(name, False, f"score={score!r} is not float in [0,100]")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_briefing_composer(runner: TestRunner) -> None:
    """Test 11 — Briefing composer for patient 1091.

    Safety: checks for an existing briefing on 2026-04-24 first.
    If one exists, verifies its fields without any DB write.
    If none exists, composes, verifies, then immediately deletes the new rows.
    """
    from app.services.briefing.composer import compose_briefing

    name = "Briefing composer"

    async with AsyncSessionLocal() as session:
        # Check for an existing briefing on the demo appointment date.
        existing = await session.execute(
            select(Briefing).where(
                Briefing.patient_id == _DEMO_PATIENT,
                Briefing.appointment_date == _DEMO_APPT_DATE,
            )
        )
        existing_briefing = existing.scalar_one_or_none()

        if existing_briefing is not None:
            payload: dict = existing_briefing.llm_response or {}
            missing = _BRIEFING_FIELDS - payload.keys()
            if missing:
                runner.record(name, False, f"existing briefing missing fields: {missing}")
                return
            agenda = payload.get("visit_agenda", [])
            if len(agenda) < 1:
                runner.record(name, False, "visit_agenda is empty")
                return
            adherence_text = payload.get("adherence_summary", "")
            if "possible" not in adherence_text.lower():
                runner.record(
                    name, False,
                    f"'possible' not in adherence_summary: {adherence_text!r}"
                )
                return
            runner.record(
                name, True,
                f"verified existing briefing (id={existing_briefing.briefing_id})"
            )
            return

        # No existing briefing — compose one, verify, then clean up.
        composed_id: str | None = None
        try:
            briefing = await compose_briefing(session, _DEMO_PATIENT, _DEMO_APPT_DATE)
            composed_id = briefing.briefing_id
            payload = briefing.llm_response or {}

            missing = _BRIEFING_FIELDS - payload.keys()
            if missing:
                runner.record(name, False, f"missing briefing fields: {missing}")
                return

            agenda = payload.get("visit_agenda", [])
            if len(agenda) < 1:
                runner.record(name, False, "visit_agenda is empty")
                return

            adherence_text = payload.get("adherence_summary", "")
            if "possible" not in adherence_text.lower():
                runner.record(
                    name, False,
                    f"'possible' not in adherence_summary: {adherence_text!r}"
                )
                return

            runner.record(
                name, True,
                f"all 9 fields present, visit_agenda has {len(agenda)} items"
            )
        except Exception as exc:
            runner.record(name, False, str(exc))
        finally:
            # Always clean up the composed briefing and its audit event.
            if composed_id is not None:
                await session.execute(
                    delete(AuditEvent).where(AuditEvent.resource_id == composed_id)
                )
                await session.execute(
                    delete(Briefing).where(Briefing.briefing_id == composed_id)
                )
                await session.commit()


async def test_worker_queue(runner: TestRunner) -> None:
    """Test 12 — processing_jobs table is reachable."""
    name = "Worker queue"
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.count()).select_from(ProcessingJob)
            )
            count = result.scalar_one()
        runner.record(name, True, f"processing_jobs table reachable ({count} rows)")
    except Exception as exc:
        runner.record(name, False, str(exc))


# ── Setup / teardown ───────────────────────────────────────────────────────────

async def _insert_test_patient(session) -> None:
    """Insert TEST_PIPELINE patient if it doesn't already exist."""
    existing = await session.execute(
        select(Patient).where(Patient.patient_id == _TEST_PATIENT)
    )
    if existing.scalar_one_or_none() is None:
        session.add(Patient(
            patient_id=_TEST_PATIENT,
            gender="U",
            age=50,
            risk_tier="low",
            monitoring_active=False,
            enrolled_at=datetime.now(timezone.utc),
            enrolled_by="pipeline_test",
        ))
        await session.commit()


async def _delete_test_patient(session) -> None:
    """Remove TEST_PIPELINE patient and any FK-referenced rows."""
    for model in (AuditEvent, Alert, Briefing, ProcessingJob, Reading,
                  MedicationConfirmation, ClinicalContext):
        await session.execute(
            delete(model).where(model.patient_id == _TEST_PATIENT)  # type: ignore[attr-defined]
        )
    await session.execute(
        delete(Patient).where(Patient.patient_id == _TEST_PATIENT)
    )
    await session.commit()


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=== ARIA Pipeline Test ===\n")

    runner = TestRunner()

    # Test 1: DB before anything else
    await test_db_connectivity(runner)

    # Setup test patient (needed so FK constraints are satisfied if any writes happen)
    async with AsyncSessionLocal() as session:
        await _insert_test_patient(session)

    try:
        # Import app here so the lifespan worker doesn't start until we enter the client
        from app.main import app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            await test_api_health(runner, client)
            await test_get_patients(runner, client)
            await test_get_patient_1091(runner, client)
            await test_get_briefing(runner, client)
            await test_get_readings(runner, client)
            await test_get_adherence(runner, client)
            await test_get_alerts(runner, client)

        # Service tests (outside the HTTP client, direct DB)
        await test_layer1_detectors(runner)
        await test_risk_scorer(runner)
        await test_briefing_composer(runner)
        await test_worker_queue(runner)

    finally:
        async with AsyncSessionLocal() as session:
            await _delete_test_patient(session)

    runner.summary()


if __name__ == "__main__":
    asyncio.run(main())
