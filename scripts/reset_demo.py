"""Reset the ARIA database to a clean demo state.

Deletes: briefings, alerts, processing_jobs, audit_events
Keeps:   patients, clinical_context, readings, medication_confirmations
Sets patient 1091 next_appointment to 2026-04-24 09:30:00+00

Safe to run multiple times.

Usage (from repo root, aria conda env active):
    python scripts/reset_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))
os.chdir(_BACKEND)  # config.py resolves .env relative to cwd

from sqlalchemy import delete, func, select, update

from app.db.base import AsyncSessionLocal
from app.models.alert import Alert
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.models.reading import Reading

_DEMO_PATIENT_ID = "1091"
_DEMO_APPOINTMENT = datetime.combine(date.today(), time(9, 30, 0), tzinfo=timezone.utc)

_CLEAR_TABLES: list[tuple[str, type]] = [
    ("audit_events", AuditEvent),
    ("briefings", Briefing),
    ("alerts", Alert),
    ("processing_jobs", ProcessingJob),
]

_KEEP_TABLES: list[tuple[str, type]] = [
    ("patients", Patient),
    ("clinical_context", ClinicalContext),
    ("readings", Reading),
    ("medication_confirmations", MedicationConfirmation),
]


async def _count(session, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return result.scalar_one()


async def main() -> None:
    print("=== ARIA Demo Reset ===\n")

    async with AsyncSessionLocal() as session:
        print("DELETING transient tables...")
        for name, model in _CLEAR_TABLES:
            result = await session.execute(delete(model))
            print(f"  {name:<28} deleted {result.rowcount} rows")

        print()

        await session.execute(
            update(Patient)
            .where(Patient.patient_id == _DEMO_PATIENT_ID)
            .values(next_appointment=_DEMO_APPOINTMENT)
        )

        await session.commit()

        print("PRESERVED tables (unchanged):")
        for name, model in _KEEP_TABLES:
            count = await _count(session, model)
            print(f"  {name:<28} {count} rows")

        print()
        print(
            f"Patient {_DEMO_PATIENT_ID} next_appointment "
            f"→ {_DEMO_APPOINTMENT.isoformat()}"
        )
        print("\n=== Reset complete ===")


if __name__ == "__main__":
    asyncio.run(main())
