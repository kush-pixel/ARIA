"""End-to-end ARIA pipeline test (Fix 14 — multi-patient).

Runs the 12 pipeline checkpoints across one or more patients without
permanently modifying production data.  Uses httpx.AsyncClient with
ASGITransport (no live uvicorn).  Cleans up any test data created during
the run.

Usage (from repo root, aria conda env active):

    # Default — patient 1091 + 1015269
    python scripts/run_pipeline_tests.py

    # Single patient
    python scripts/run_pipeline_tests.py --patients 1091

    # Custom list
    python scripts/run_pipeline_tests.py --patients 1091,1015269,2045

The shared (non-patient-scoped) checks run once: DB connectivity, API health,
GET /api/patients (panel-wide), GET /api/alerts (panel-wide), worker queue.
Per-patient checks run for every patient supplied: GET /api/patients/{id},
briefing fetch, readings, adherence, Layer 1 detectors, Layer 2 risk scorer,
briefing composer (when an appointment date is known for that patient).
"""

from __future__ import annotations

import argparse
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

_DEFAULT_PATIENTS = ["1091", "1015269"]

# Per-patient appointment dates used by the briefing composer test.  Patients
# without an entry skip the composer test (we don't want to fabricate a date).
_PATIENT_APPT_DATES: dict[str, date] = {
    "1091": date(2026, 4, 24),
}

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


async def test_get_patient(
    runner: TestRunner, client: httpx.AsyncClient, patient_id: str,
) -> None:
    """Test 4 — GET /api/patients/{id}."""
    name = f"GET /api/patients/{patient_id}"
    try:
        resp = await client.get(f"/api/patients/{patient_id}")
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if data.get("patient_id") == patient_id:
            runner.record(name, True, f"patient_id={patient_id} confirmed")
        else:
            runner.record(name, False, f"unexpected patient_id={data.get('patient_id')}")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_briefing(
    runner: TestRunner, client: httpx.AsyncClient, patient_id: str,
) -> None:
    """Test 5 — GET /api/briefings/{id} (read-only check)."""
    name = f"GET /api/briefings/{patient_id}"
    try:
        resp = await client.get(f"/api/briefings/{patient_id}")
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


async def test_get_readings(
    runner: TestRunner, client: httpx.AsyncClient, patient_id: str,
) -> None:
    """Test 6 — GET /api/readings?patient_id={id}."""
    name = f"GET /api/readings?patient_id={patient_id}"
    try:
        resp = await client.get("/api/readings", params={"patient_id": patient_id})
        if resp.status_code != 200:
            runner.record(name, False, f"status={resp.status_code}")
            return
        data = resp.json()
        if not isinstance(data, list):
            runner.record(name, False, "expected list")
            return
        if len(data) == 0:
            runner.record(name, True, "0 readings (acceptable for new/EHR-only patients)")
            return
        if "systolic_avg" not in data[0]:
            runner.record(name, False, "missing systolic_avg in first item")
        else:
            runner.record(name, True, f"{len(data)} readings returned")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_get_adherence(
    runner: TestRunner, client: httpx.AsyncClient, patient_id: str,
) -> None:
    """Test 7 — GET /api/adherence/{id}."""
    name = f"GET /api/adherence/{patient_id}"
    try:
        resp = await client.get(f"/api/adherence/{patient_id}")
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


async def test_layer1_detectors(runner: TestRunner, patient_id: str) -> None:
    """Test 9 — All four Layer 1 detectors on a specific patient."""
    from app.services.pattern_engine.adherence_analyzer import run_adherence_analyzer
    from app.services.pattern_engine.deterioration_detector import run_deterioration_detector
    from app.services.pattern_engine.gap_detector import run_gap_detector
    from app.services.pattern_engine.inertia_detector import run_inertia_detector

    name = f"Layer 1 detectors ({patient_id})"
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
                result = await fn(session, patient_id)
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


async def test_risk_scorer(runner: TestRunner, patient_id: str) -> None:
    """Test 10 — Layer 2 risk scorer on a specific patient."""
    from app.services.pattern_engine.risk_scorer import compute_risk_score

    name = f"Layer 2 risk scorer ({patient_id})"
    try:
        async with AsyncSessionLocal() as session:
            score = await compute_risk_score(patient_id, session)
        if isinstance(score, (int, float)) and 0.0 <= float(score) <= 100.0:
            runner.record(name, True, f"risk_score={score}")
        else:
            runner.record(name, False, f"score={score!r} is not float in [0,100]")
    except Exception as exc:
        runner.record(name, False, str(exc))


async def test_briefing_composer(
    runner: TestRunner, patient_id: str, appt_date: date,
) -> None:
    """Test 11 — Briefing composer for a specific patient and appointment date.

    Safety: checks for an existing briefing first.  If one exists, verifies
    its fields without any DB write.  If none exists, composes, verifies,
    then immediately deletes the new rows.
    """
    from app.services.briefing.composer import compose_briefing

    name = f"Briefing composer ({patient_id})"

    async with AsyncSessionLocal() as session:
        # Check for an existing briefing on the demo appointment date.
        existing = await session.execute(
            select(Briefing).where(
                Briefing.patient_id == patient_id,
                Briefing.appointment_date == appt_date,
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
            briefing = await compose_briefing(session, patient_id, appt_date)
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

async def _patient_exists(patient_id: str) -> bool:
    """Return True when the patient row is present in the DB."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Patient.patient_id).where(Patient.patient_id == patient_id)
        )
        return result.scalar_one_or_none() is not None


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="ARIA end-to-end pipeline test (Fix 14 — multi-patient).",
    )
    parser.add_argument(
        "--patients",
        default=",".join(_DEFAULT_PATIENTS),
        help=f"Comma-separated patient IDs (default: {','.join(_DEFAULT_PATIENTS)})",
    )
    args = parser.parse_args()

    patient_ids = [p.strip() for p in args.patients.split(",") if p.strip()]
    if not patient_ids:
        print("ERROR: --patients must include at least one ID", file=sys.stderr)
        sys.exit(2)

    print(f"=== ARIA Pipeline Test — patients: {', '.join(patient_ids)} ===\n")

    runner = TestRunner()

    # ── Shared (non-patient) tests ─────────────────────────────────────────────
    await test_db_connectivity(runner)

    # Skip patients that don't exist in the DB so the runner doesn't report
    # spurious 404s — also report skipping so the operator knows.
    available: list[str] = []
    for pid in patient_ids:
        if await _patient_exists(pid):
            available.append(pid)
        else:
            runner.record(
                f"Patient existence ({pid})",
                False,
                "patient row missing — run adapter + ingestion first",
            )
    if not available:
        print("\nNo patients available in the DB — aborting.", file=sys.stderr)
        runner.summary()
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        await _insert_test_patient(session)

    try:
        from app.main import app

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            # Panel-wide tests
            await test_api_health(runner, client)
            await test_get_patients(runner, client)
            await test_get_alerts(runner, client)

            # Per-patient HTTP tests
            for pid in available:
                await test_get_patient(runner, client, pid)
                await test_get_briefing(runner, client, pid)
                await test_get_readings(runner, client, pid)
                await test_get_adherence(runner, client, pid)

        # Per-patient service tests (direct DB)
        for pid in available:
            await test_layer1_detectors(runner, pid)
            await test_risk_scorer(runner, pid)
            appt = _PATIENT_APPT_DATES.get(pid)
            if appt is not None:
                await test_briefing_composer(runner, pid, appt)

        # Worker queue last
        await test_worker_queue(runner)

    finally:
        async with AsyncSessionLocal() as session:
            await _delete_test_patient(session)

    runner.summary()
    sys.exit(0 if runner.all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
