"""CLI script: generate synthetic home BP readings and medication confirmation
events for a patient.

Usage (from project root, with the aria conda environment active):
    python scripts/run_generator.py --patient 1091
    python scripts/run_generator.py --patient 1091 --mode demo
    python scripts/run_generator.py --patient 1091 --mode full-timeline

Modes:
    demo (default):     Generates 28 days of Patient A scenario readings (47
                        readings) plus medication confirmations for the same window.
    full-timeline:      Generates synthetic home BP readings spanning the patient's
                        complete care timeline by interpolating between every
                        consecutive pair of clinic readings.

Idempotency:
  - demo readings:      Skipped if generated readings already exist (source='generated').
  - full-timeline:      Uses ON CONFLICT DO NOTHING per reading — safe to re-run.
  - Confirmations:      Skipped if simulated confirmations already exist
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
from app.services.generator.confirmation_generator import (  # noqa: E402
    generate_confirmations,
    generate_full_timeline_confirmations,
)
from app.services.generator.reading_generator import (  # noqa: E402
    generate_full_timeline_readings,
    generate_readings,
)


async def _run_demo(patient_id: str) -> None:
    """Generate 28-day Patient A scenario readings and medication confirmations.

    Each data type is checked and inserted independently so a previously
    completed readings run does not block confirmation generation.

    Args:
        patient_id: ARIA patient identifier (e.g. ``"1091"``).
    """
    readings: list[dict] = []
    confs: list[dict] = []

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

    print(f"\nGeneration complete (mode=demo):")
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


async def _run_full_timeline(patient_id: str) -> None:
    """Generate synthetic home BP readings spanning the patient's full care timeline.

    Iterates every consecutive pair of clinic readings and interpolates daily
    home readings between them.  Uses ON CONFLICT DO NOTHING per reading — safe
    to re-run.  Prints inserted count and date range on completion.

    Args:
        patient_id: ARIA patient identifier (e.g. ``"1091"``).
    """
    async with AsyncSessionLocal() as session:
        readings_inserted = await generate_full_timeline_readings(patient_id, session)

    async with AsyncSessionLocal() as session:
        confs_inserted = await generate_full_timeline_confirmations(patient_id, session)

    # ── Print date range of all source='generated' readings ──────────────────
    async with AsyncSessionLocal() as session:
        range_result = await session.execute(
            select(
                sa_func.min(Reading.effective_datetime),
                sa_func.max(Reading.effective_datetime),
            ).where(
                Reading.patient_id == patient_id,
                Reading.source == "generated",
            )
        )
        row = range_result.one_or_none()
        min_dt = row[0] if row else None
        max_dt = row[1] if row else None

    print(f"\nGeneration complete (mode=full-timeline):")
    print(f"  patient_id:               {patient_id}")
    print(f"  readings_inserted:        {readings_inserted}")
    print(f"  confirmations_inserted:   {confs_inserted}")
    if min_dt and max_dt:
        print(f"  date_range_start:         {min_dt.date().isoformat()}")
        print(f"  date_range_end:           {max_dt.date().isoformat()}")
    else:
        print(f"  date_range:               (no generated readings found)")


def main() -> None:
    """Parse CLI arguments and run the generator."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate synthetic home BP readings and medication confirmation "
            "events for an ARIA patient."
        )
    )
    parser.add_argument(
        "--patient",
        required=True,
        help="Patient ID to generate data for (e.g. 1091)",
    )
    parser.add_argument(
        "--mode",
        choices=["demo", "full-timeline"],
        default="demo",
        help=(
            "Generation mode: 'demo' generates 28-day Patient A scenario (47 readings); "
            "'full-timeline' interpolates between every consecutive clinic visit pair."
        ),
    )
    args = parser.parse_args()

    try:
        if args.mode == "full-timeline":
            asyncio.run(_run_full_timeline(args.patient))
        else:
            asyncio.run(_run_demo(args.patient))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
