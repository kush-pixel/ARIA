"""CLI script: generate synthetic 28-day home BP readings and medication
confirmation events for a patient.

Usage (from project root, with the aria conda environment active):
    python scripts/run_generator.py --patient 1091

Reads anchor data from clinical_context in the database, generates 47
synthetic readings following the Patient A scenario, and generates
medication confirmation events for all active medications over 28 days.

Each data type has an independent idempotency check:
  - Readings are skipped if generated readings already exist (source='generated').
  - Confirmations are skipped if simulated confirmations already exist
    (confidence='simulated').

Needs: DATABASE_URL set in backend/.env
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure the backend package is importable when run from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = PROJECT_ROOT / "backend"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

# Load DATABASE_URL and other settings from backend/.env before importing
# app modules that read config at import time.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_SRC / ".env")

from sqlalchemy import func as sa_func, select  # noqa: E402

from app.db.base import AsyncSessionLocal  # noqa: E402
from app.models.medication_confirmation import MedicationConfirmation  # noqa: E402
from app.models.reading import Reading  # noqa: E402
from app.services.generator.confirmation_generator import generate_confirmations  # noqa: E402
from app.services.generator.reading_generator import generate_readings  # noqa: E402


async def _run(patient_id: str) -> None:
    """Generate and persist synthetic readings and confirmations for the given patient.

    Each data type is checked and inserted independently so a previously
    completed readings run does not block confirmation generation.

    Args:
        patient_id: ARIA patient identifier (e.g. ``"1091"``).
    """
    async with AsyncSessionLocal() as session:

        # ── Readings ─────────────────────────────────────────────────────────
        count_result = await session.execute(
            select(sa_func.count())
            .select_from(Reading)
            .where(Reading.patient_id == patient_id, Reading.source == "generated")
        )
        existing_readings = count_result.scalar() or 0

        if existing_readings > 0:
            print(
                f"Skipping readings: {existing_readings} generated readings already exist "
                f"for patient {patient_id}"
            )
            readings_inserted = 0
        else:
            readings = await generate_readings(patient_id, session)
            session.add_all([Reading(**r) for r in readings])
            await session.commit()
            readings_inserted = len(readings)

        # ── Confirmations ─────────────────────────────────────────────────────
        conf_count_result = await session.execute(
            select(sa_func.count())
            .select_from(MedicationConfirmation)
            .where(
                MedicationConfirmation.patient_id == patient_id,
                MedicationConfirmation.confidence == "simulated",
            )
        )
        existing_confs = conf_count_result.scalar() or 0

        if existing_confs > 0:
            print(
                f"Skipping confirmations: {existing_confs} simulated confirmation records "
                f"already exist for patient {patient_id}"
            )
            confs_inserted = 0
        else:
            confs = await generate_confirmations(patient_id, session)
            session.add_all([MedicationConfirmation(**c) for c in confs])
            await session.commit()
            confs_inserted = len(confs)

    print(f"\nGeneration complete:")
    print(f"  patient_id:               {patient_id}")
    print(f"  readings_inserted:        {readings_inserted}")
    print(f"  confirmations_inserted:   {confs_inserted}")

    if readings_inserted > 0:
        print(f"\nFirst 3 readings:")
        for r in readings[:3]:
            print(
                f"  {r['session']:7s}  {r['effective_datetime'].date()}"
                f"  sys_avg={r['systolic_avg']}  dia_avg={r['diastolic_avg']}"
            )

    if confs_inserted > 0:
        confirmed_count = sum(1 for c in confs if c["confirmed_at"] is not None)
        missed_count = confs_inserted - confirmed_count
        adherence_pct = confirmed_count / confs_inserted * 100 if confs_inserted else 0.0
        print(f"\nConfirmation summary:")
        print(f"  total_scheduled:  {confs_inserted}")
        print(f"  confirmed:        {confirmed_count}  ({adherence_pct:.1f}%)")
        print(f"  missed:           {missed_count}")


def main() -> None:
    """Parse CLI arguments and run the generator."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate synthetic 28-day home BP readings and medication confirmation "
            "events for an ARIA patient."
        )
    )
    parser.add_argument(
        "--patient",
        required=True,
        help="Patient ID to generate data for (e.g. 1091)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run(args.patient))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
