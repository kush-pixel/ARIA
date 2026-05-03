"""setup_demo.py — ARIA demo environment setup.

Teardown-and-rebuild for all four demo patients.  Safe to run multiple times —
starts with a full teardown then rebuilds from scratch.

Usage (from repo root, aria conda env active):
    python scripts/setup_demo.py              # full teardown + rebuild + verify
    python scripts/setup_demo.py --dry-run    # show what would change, no writes
    python scripts/setup_demo.py --verify-only  # checklist only, no setup

Demo patients:
    1091      Patient A — Therapeutic inertia (existing patient, time-shifted)
    DEMO_GAP  Patient B — 9-day reading gap + rising trend
    DEMO_ADH  Patient C — Adherence concern Pattern A (58% adherence)
    DEMO_EHR  Patient D — EHR-only, triple whammy drug interaction
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

_BACKEND = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))
os.chdir(_BACKEND)  # config.py resolves .env relative to cwd

from sqlalchemy import delete, func, select, update

from app.db.base import AsyncSessionLocal
from app.models.alert import Alert
from app.models.alert_feedback import AlertFeedback
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.calibration_rule import CalibrationRule
from app.models.clinical_context import ClinicalContext
from app.models.gap_explanation import GapExplanation
from app.models.medication_confirmation import MedicationConfirmation
from app.models.outcome_verification import OutcomeVerification
from app.models.patient import Patient
from app.models.processing_job import ProcessingJob
from app.models.reading import Reading
from app.services.generator.confirmation_generator import (
    generate_full_timeline_confirmations,
)
from app.services.worker.processor import (
    _handle_briefing_generation,
    _handle_pattern_recompute,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_DEMO_DATE = date(2026, 5, 5)
_DEMO_APPT = datetime.combine(_DEMO_DATE, time(9, 30), tzinfo=UTC)

_PATIENT_A = "1091"
_SHIFT_DAYS = 5654
_SHIFT_SRC_START = datetime(2010, 7, 15, tzinfo=UTC)
_SHIFT_SRC_END = datetime(2010, 11, 12, tzinfo=UTC)   # exclusive upper bound
_SHIFT_DST_START = datetime(2026, 1, 6, tzinfo=UTC)
_SHIFT_DST_END = datetime(2026, 5, 6, tzinfo=UTC)     # exclusive upper bound

_DEMO_PATIENTS = ["DEMO_GAP", "DEMO_ADH", "DEMO_EHR"]
_ALL_PATIENTS = [_PATIENT_A] + _DEMO_PATIENTS

# Hardcoded adherence pattern for DEMO_ADH — deterministic across every run.
# Day indices 0-27 (Apr 7 = 0, May 4 = 27).  16 confirmed = 57.1% ≈ 58%.
# Structured as streaks: 3 good, 1 miss, 2 good, 2 miss, 3 good, 1 miss,
# 1 good, 2 miss, 3 good, 1 miss, 1 good, 2 miss, 1 good, 2 miss, 1 good.
_ADH_CONFIRMED_DAYS: frozenset[int] = frozenset({
    0, 1, 2,          # Apr 7-9
    4, 5,             # Apr 11-12
    8, 9, 10,         # Apr 15-17
    12,               # Apr 19
    15, 16, 17,       # Apr 22-24
    20,               # Apr 27
    22, 23,           # Apr 29-30
    26,               # May 3
})

# ── Reading generation helpers ─────────────────────────────────────────────────

def _anti_round(val: int) -> int:
    """Nudge a value that lands on a round number so readings look real."""
    if val % 5 == 0:
        val += random.choice([-2, -1, 1, 2, 3])
    return val


def _make_reading(
    patient_id: str,
    dt: datetime,
    sys1: int,
    dia1: int,
    sys2: int,
    dia2: int,
    hr: int,
    session_name: str,
) -> Reading:
    return Reading(
        patient_id=patient_id,
        systolic_1=sys1,
        diastolic_1=dia1,
        heart_rate_1=hr,
        systolic_2=sys2,
        diastolic_2=dia2,
        heart_rate_2=hr,
        systolic_avg=round((sys1 + sys2) / 2.0, 1),
        diastolic_avg=round((dia1 + dia2) / 2.0, 1),
        heart_rate_avg=float(hr),
        effective_datetime=dt,
        session=session_name,
        source="generated",
        submitted_by="generator",
        bp_position="seated",
        bp_site="left_arm",
        medication_taken="yes",
        consent_version="1.0",
    )


def _gen_demo_gap_readings() -> list[Reading]:
    """82 days of readings Feb 1 – Apr 23 with mild rising trend, gap Apr 24–May 5.

    Last reading is Apr 23 so the gap is ~9 days by May 2 (verification run) and
    ~12 days by May 5 (demo day) — safely above the >=9 day check threshold.
    """
    random.seed(42)
    rows: list[Reading] = []

    start_date = date(2026, 2, 1)
    end_date = date(2026, 4, 23)
    total_days = (end_date - start_date).days + 1  # 82

    for day_idx in range(total_days):
        d = start_date + timedelta(days=day_idx)
        # Linear baseline 143 → 155 mmHg with SD=10 day-to-day noise
        base = 143.0 + 12.0 * day_idx / (total_days - 1) + random.gauss(0, 10)

        for session_name, hour, morning_offset in [
            ("morning", 7, 7.0),
            ("evening", 21, 0.0),
        ]:
            sys1_f = base + morning_offset + random.uniform(-1.5, 1.5)
            sys1 = _anti_round(max(120, min(185, round(sys1_f))))
            sys2_f = sys1_f - random.uniform(2.0, 6.0)
            sys2 = _anti_round(max(110, min(180, round(sys2_f))))

            ratio = random.uniform(0.60, 0.66)
            dia1 = max(65, min(108, round(sys1 * ratio)))
            dia2 = max(62, min(105, round(sys2 * ratio)))
            hr = round(random.uniform(64, 82))

            jitter = timedelta(minutes=random.randint(-15, 15))
            dt = datetime.combine(d, time(hour, 0), tzinfo=UTC) + jitter
            rows.append(_make_reading("DEMO_GAP", dt, sys1, dia1, sys2, dia2, hr, session_name))

    return rows


def _gen_demo_adh_readings() -> list[Reading]:
    """28 days of morning-only readings Apr 7 – May 4, average ~152 mmHg."""
    random.seed(99)
    rows: list[Reading] = []

    start_date = date(2026, 4, 7)

    for day_idx in range(28):
        d = start_date + timedelta(days=day_idx)
        base = 152.0 + random.gauss(0, 10)

        # Morning only — offset 5-9 mmHg above evening baseline
        sys1_f = base + random.uniform(5, 9) + random.uniform(-1.5, 1.5)
        sys1 = _anti_round(max(125, min(185, round(sys1_f))))
        sys2_f = sys1_f - random.uniform(2.0, 6.0)
        sys2 = _anti_round(max(115, min(180, round(sys2_f))))

        ratio = random.uniform(0.60, 0.66)
        dia1 = max(68, min(110, round(sys1 * ratio)))
        dia2 = max(65, min(107, round(sys2 * ratio)))
        hr = round(random.uniform(66, 80))

        jitter = timedelta(minutes=random.randint(-15, 15))
        dt = datetime.combine(d, time(7, 0), tzinfo=UTC) + jitter
        rows.append(_make_reading("DEMO_ADH", dt, sys1, dia1, sys2, dia2, hr, "morning"))

    return rows


def _gen_demo_adh_confirmations() -> list[MedicationConfirmation]:
    """2 medications × 28 days at ~58% adherence with realistic streak pattern."""
    random.seed(55)
    rows: list[MedicationConfirmation] = []
    meds = [
        ("amlodipine 5mg", "17767"),
        ("lisinopril 10mg", "29046"),
    ]
    start_date = date(2026, 4, 7)

    for med_name, rxnorm in meds:
        for day_idx in range(28):
            d = start_date + timedelta(days=day_idx)
            scheduled = datetime.combine(d, time(8, 0), tzinfo=UTC)
            taken = day_idx in _ADH_CONFIRMED_DAYS
            confirmed_at = None
            minutes_off = None
            if taken:
                delta = random.randint(-5, 30)
                confirmed_at = scheduled + timedelta(minutes=delta)
                minutes_off = delta

            rows.append(MedicationConfirmation(
                patient_id="DEMO_ADH",
                medication_name=med_name,
                rxnorm_code=rxnorm,
                scheduled_time=scheduled,
                confirmed_at=confirmed_at,
                confirmation_type="synthetic_demo",
                confidence="self_report",
                minutes_from_schedule=minutes_off,
            ))

    return rows


# ── Phase 0 — Teardown ─────────────────────────────────────────────────────────

async def _teardown(dry_run: bool) -> None:
    print("\n── Phase 0: Teardown ─────────────────────────────────")

    _cascade_models: list[type] = [
        AuditEvent,
        Briefing,
        OutcomeVerification,   # references alert_feedback + alerts
        AlertFeedback,         # references alerts
        Alert,
        CalibrationRule,
        GapExplanation,
        ProcessingJob,
        MedicationConfirmation,
        Reading,
        ClinicalContext,
    ]

    if dry_run:
        print("  [DRY-RUN] Would delete all rows for DEMO_GAP, DEMO_ADH, DEMO_EHR")
        print("  [DRY-RUN] Would clear briefings, alerts, processing_jobs, audit_events for 1091")
        return

    async with AsyncSessionLocal() as session:
        # Cascade-delete all demo patient rows (FK-safe order)
        for model in _cascade_models:
            for pid in _DEMO_PATIENTS:
                result = await session.execute(
                    delete(model).where(model.patient_id == pid)
                )
                if result.rowcount:
                    print(f"  Deleted {result.rowcount:>4} {model.__tablename__} rows for {pid}")

        for pid in _DEMO_PATIENTS:
            result = await session.execute(
                delete(Patient).where(Patient.patient_id == pid)
            )
            if result.rowcount:
                print(f"  Deleted patient record: {pid}")

        # Clear transient tables for Patient A — readings + confirmations stay
        for model in [AuditEvent, Briefing, OutcomeVerification, AlertFeedback, Alert, CalibrationRule, GapExplanation, ProcessingJob]:
            result = await session.execute(
                delete(model).where(model.patient_id == _PATIENT_A)
            )
            if result.rowcount:
                print(f"  Cleared {result.rowcount:>4} {model.__tablename__} rows for {_PATIENT_A}")

        await session.commit()
        print("  Teardown complete.")


# ── Phase 1 — Timeshift Patient 1091 ──────────────────────────────────────────

async def _timeshift_patient_a(dry_run: bool) -> None:
    print("\n── Phase 1: Timeshift Patient 1091 ──────────────────")

    async with AsyncSessionLocal() as session:
        # Detect if shift was already applied
        shifted_count: int = await session.scalar(
            select(func.count())
            .select_from(Reading)
            .where(
                Reading.patient_id == _PATIENT_A,
                Reading.effective_datetime >= _SHIFT_DST_START,
                Reading.effective_datetime < _SHIFT_DST_END,
            )
        ) or 0

        src_count: int = await session.scalar(
            select(func.count())
            .select_from(Reading)
            .where(
                Reading.patient_id == _PATIENT_A,
                Reading.effective_datetime >= _SHIFT_SRC_START,
                Reading.effective_datetime < _SHIFT_SRC_END,
            )
        ) or 0

        if dry_run:
            if shifted_count and not src_count:
                print(f"  [DRY-RUN] Would un-shift {shifted_count} 2026 readings back to 2010, then re-shift")
            elif shifted_count and src_count:
                print(f"  [DRY-RUN] Would delete {shifted_count} 2026 duplicates, shift {src_count} 2010 originals")
            else:
                print(f"  [DRY-RUN] Would shift {src_count} readings +{_SHIFT_DAYS} days (2010 → 2026)")
            print("  [DRY-RUN] Would set next_appointment=2026-05-05, last_visit_date=2026-01-06")
            return

        if shifted_count and not src_count:
            # Clean state: all readings are in the 2026 range (from a previous run).
            # Un-shift them back to 2010 so the forward shift below can run again.
            # No unique conflict possible — the 2010 range is empty.
            print(f"  Un-shifting {shifted_count} 2026 readings back to 2010 for re-shift...")
            await session.execute(
                update(Reading)
                .where(
                    Reading.patient_id == _PATIENT_A,
                    Reading.effective_datetime >= _SHIFT_DST_START,
                    Reading.effective_datetime < _SHIFT_DST_END,
                )
                .values(effective_datetime=Reading.effective_datetime - timedelta(days=_SHIFT_DAYS))
            )
            await session.execute(
                update(MedicationConfirmation)
                .where(
                    MedicationConfirmation.patient_id == _PATIENT_A,
                    MedicationConfirmation.scheduled_time >= _SHIFT_DST_START,
                    MedicationConfirmation.scheduled_time < _SHIFT_DST_END,
                    MedicationConfirmation.confirmed_at.is_not(None),
                )
                .values(
                    scheduled_time=MedicationConfirmation.scheduled_time - timedelta(days=_SHIFT_DAYS),
                    confirmed_at=MedicationConfirmation.confirmed_at - timedelta(days=_SHIFT_DAYS),
                )
            )
            await session.execute(
                update(MedicationConfirmation)
                .where(
                    MedicationConfirmation.patient_id == _PATIENT_A,
                    MedicationConfirmation.scheduled_time >= _SHIFT_DST_START,
                    MedicationConfirmation.scheduled_time < _SHIFT_DST_END,
                    MedicationConfirmation.confirmed_at.is_(None),
                )
                .values(scheduled_time=MedicationConfirmation.scheduled_time - timedelta(days=_SHIFT_DAYS))
            )
            await session.commit()
        elif shifted_count and src_count:
            # Partial state: both 2010 originals and 2026 shifted copies exist
            # (e.g. after a generator re-run added new rows in the 2010 range).
            # Delete the 2026 duplicates and re-shift the 2010 originals.
            print(f"  Partial state: deleting {shifted_count} 2026 duplicates, keeping {src_count} 2010 originals...")
            await session.execute(
                delete(Reading)
                .where(
                    Reading.patient_id == _PATIENT_A,
                    Reading.effective_datetime >= _SHIFT_DST_START,
                    Reading.effective_datetime < _SHIFT_DST_END,
                )
            )
            conf_result = await session.execute(
                delete(MedicationConfirmation)
                .where(
                    MedicationConfirmation.patient_id == _PATIENT_A,
                    MedicationConfirmation.scheduled_time >= _SHIFT_DST_START,
                    MedicationConfirmation.scheduled_time < _SHIFT_DST_END,
                )
            )
            if conf_result.rowcount:
                print(f"  Deleted {conf_result.rowcount} stale 2026 confirmation rows.")
            await session.commit()

        # Re-read src_count after potential un-shift
        src_count = await session.scalar(
            select(func.count())
            .select_from(Reading)
            .where(
                Reading.patient_id == _PATIENT_A,
                Reading.effective_datetime >= _SHIFT_SRC_START,
                Reading.effective_datetime < _SHIFT_SRC_END,
            )
        ) or 0

        # Fill the Jul 15 – Nov 11, 2010 inter-visit gap only if it is empty.
        # Guarded by COUNT — avoids 78k INSERT attempts when already populated.
        conf_src_count = await session.scalar(
            select(func.count())
            .select_from(MedicationConfirmation)
            .where(
                MedicationConfirmation.patient_id == _PATIENT_A,
                MedicationConfirmation.scheduled_time >= _SHIFT_SRC_START,
                MedicationConfirmation.scheduled_time < _SHIFT_SRC_END,
            )
        ) or 0
        if conf_src_count == 0:
            confs_added = await generate_full_timeline_confirmations(_PATIENT_A, session)
            if confs_added:
                print(f"  Generated {confs_added} missing confirmation rows in 2010 window.")
        else:
            print(f"  Confirmation window populated ({conf_src_count} rows) — skipping generator.")

        print(f"  Shifting {src_count} readings +{_SHIFT_DAYS} days (2010 → 2026)...")

        await session.execute(
            update(Reading)
            .where(
                Reading.patient_id == _PATIENT_A,
                Reading.effective_datetime >= _SHIFT_SRC_START,
                Reading.effective_datetime < _SHIFT_SRC_END,
            )
            .values(effective_datetime=Reading.effective_datetime + timedelta(days=_SHIFT_DAYS))
        )

        # Shift confirmations with confirmed_at set
        await session.execute(
            update(MedicationConfirmation)
            .where(
                MedicationConfirmation.patient_id == _PATIENT_A,
                MedicationConfirmation.scheduled_time >= _SHIFT_SRC_START,
                MedicationConfirmation.scheduled_time < _SHIFT_SRC_END,
                MedicationConfirmation.confirmed_at.is_not(None),
            )
            .values(
                scheduled_time=MedicationConfirmation.scheduled_time + timedelta(days=_SHIFT_DAYS),
                confirmed_at=MedicationConfirmation.confirmed_at + timedelta(days=_SHIFT_DAYS),
            )
        )

        # Shift confirmations with NULL confirmed_at
        await session.execute(
            update(MedicationConfirmation)
            .where(
                MedicationConfirmation.patient_id == _PATIENT_A,
                MedicationConfirmation.scheduled_time >= _SHIFT_SRC_START,
                MedicationConfirmation.scheduled_time < _SHIFT_SRC_END,
                MedicationConfirmation.confirmed_at.is_(None),
            )
            .values(
                scheduled_time=MedicationConfirmation.scheduled_time + timedelta(days=_SHIFT_DAYS),
            )
        )

        # Update patient and clinical context.
        # enrolled_at is reset to 2025-01-06 — safely past the 21-day cold-start
        # window so inertia/adherence/deterioration detectors are not suppressed.
        await session.execute(
            update(Patient)
            .where(Patient.patient_id == _PATIENT_A)
            .values(
                next_appointment=_DEMO_APPT,
                enrolled_at=datetime(2025, 1, 6, tzinfo=UTC),
            )
        )
        await session.execute(
            update(ClinicalContext)
            .where(ClinicalContext.patient_id == _PATIENT_A)
            .values(
                last_visit_date=date(2026, 1, 6),
                last_clinic_systolic=162,
                last_clinic_diastolic=62,
            )
        )

        await session.commit()
        print(f"  Applied +{_SHIFT_DAYS} day shift. next_appointment → 2026-05-05.")


# ── Phase 2 — Seed DEMO_GAP ───────────────────────────────────────────────────

async def _seed_demo_gap(dry_run: bool) -> None:
    print("\n── Phase 2: Seed DEMO_GAP ────────────────────────────")

    readings = _gen_demo_gap_readings()
    print(f"  Generated {len(readings)} readings (Feb 1–Apr 23; gap Apr 24–May 5)")

    if dry_run:
        print("  [DRY-RUN] Would insert patient, clinical_context, and readings")
        return

    async with AsyncSessionLocal() as session:
        session.add(Patient(
            patient_id="DEMO_GAP",
            name="David Patel",
            gender="M",
            age=62,
            risk_tier="medium",
            monitoring_active=True,
            next_appointment=_DEMO_APPT,
            enrolled_at=datetime(2025, 11, 5, tzinfo=UTC),
            enrolled_by="setup_demo",
        ))
        session.add(ClinicalContext(
            patient_id="DEMO_GAP",
            active_problems=["Hypertension"],
            problem_codes=["I10"],
            current_medications=["amlodipine 5mg"],
            med_rxnorm_codes=["17767"],
            med_history=[{
                "name": "amlodipine 5mg",
                "rxnorm": "17767",
                "date": "2025-11-05",
                "activity": "active",
            }],
            last_med_change=date(2025, 11, 5),
            allergies=[],
            allergy_reactions=[],
            last_visit_date=date(2025, 11, 5),
            last_clinic_systolic=150,
            last_clinic_diastolic=90,
            historic_bp_systolic=[150],
            historic_bp_dates=["2025-11-05"],
            overdue_labs=[],
            social_context=None,
        ))
        for r in readings:
            session.add(r)
        await session.commit()
        print(f"  Inserted DEMO_GAP: patient + {len(readings)} readings.")


# ── Phase 3 — Seed DEMO_ADH ───────────────────────────────────────────────────

async def _seed_demo_adh(dry_run: bool) -> None:
    print("\n── Phase 3: Seed DEMO_ADH ────────────────────────────")

    readings = _gen_demo_adh_readings()
    confirmations = _gen_demo_adh_confirmations()
    confirmed_n = sum(1 for c in confirmations if c.confirmed_at is not None)
    total_n = len(confirmations)
    pct = round(confirmed_n / total_n * 100)
    print(f"  Generated {len(readings)} readings, {confirmed_n}/{total_n} confirmations ({pct}% adherence)")

    if dry_run:
        print("  [DRY-RUN] Would insert patient, clinical_context, readings, confirmations")
        return

    async with AsyncSessionLocal() as session:
        session.add(Patient(
            patient_id="DEMO_ADH",
            name="Sarah Mitchell",
            gender="F",
            age=55,
            risk_tier="medium",
            monitoring_active=True,
            next_appointment=_DEMO_APPT,
            enrolled_at=datetime(2025, 11, 5, tzinfo=UTC),
            enrolled_by="setup_demo",
        ))
        session.add(ClinicalContext(
            patient_id="DEMO_ADH",
            active_problems=["Hypertension"],
            problem_codes=["I10"],
            current_medications=["amlodipine 5mg", "lisinopril 10mg"],
            med_rxnorm_codes=["17767", "29046"],
            med_history=[
                {"name": "amlodipine 5mg", "rxnorm": "17767", "date": "2025-11-05", "activity": "active"},
                {"name": "lisinopril 10mg", "rxnorm": "29046", "date": "2025-11-05", "activity": "active"},
            ],
            last_med_change=date(2025, 11, 5),
            allergies=[],
            allergy_reactions=[],
            last_visit_date=date(2025, 11, 5),
            last_clinic_systolic=148,
            last_clinic_diastolic=92,
            historic_bp_systolic=[148],
            historic_bp_dates=["2025-11-05"],
            overdue_labs=[],
            social_context=None,
        ))
        for r in readings:
            session.add(r)
        for c in confirmations:
            session.add(c)
        await session.commit()
        print(f"  Inserted DEMO_ADH: patient + {len(readings)} readings + {len(confirmations)} confirmations.")


# ── Phase 4 — Seed DEMO_EHR ───────────────────────────────────────────────────

async def _seed_demo_ehr(dry_run: bool) -> None:
    print("\n── Phase 4: Seed DEMO_EHR ────────────────────────────")

    if dry_run:
        print("  [DRY-RUN] Would insert EHR-only patient with CHF drug interaction profile")
        return

    async with AsyncSessionLocal() as session:
        session.add(Patient(
            patient_id="DEMO_EHR",
            name="Robert Clarke",
            gender="M",
            age=73,
            risk_tier="high",
            tier_override="CHF in problem list",
            tier_override_source="system",
            monitoring_active=False,
            next_appointment=_DEMO_APPT,
            enrolled_at=datetime(2026, 1, 5, tzinfo=UTC),
            enrolled_by="setup_demo",
        ))
        session.add(ClinicalContext(
            patient_id="DEMO_EHR",
            active_problems=["Hypertension", "Heart failure", "Type 2 diabetes mellitus"],
            problem_codes=["I10", "I50.9", "E11"],
            current_medications=[
                "lisinopril 10mg",
                "furosemide 40mg",
                "diclofenac 50mg",
                "metformin 500mg",
            ],
            med_rxnorm_codes=["29046", "4603", "3355", "6809"],
            med_history=[
                {"name": "lisinopril 10mg", "rxnorm": "29046", "date": "2026-01-05", "activity": "active"},
                {"name": "furosemide 40mg", "rxnorm": "4603", "date": "2026-01-05", "activity": "active"},
                {"name": "diclofenac 50mg", "rxnorm": "3355", "date": "2026-01-05", "activity": "active"},
                {"name": "metformin 500mg", "rxnorm": "6809", "date": "2026-01-05", "activity": "active"},
            ],
            last_med_change=date(2026, 1, 5),
            allergies=[],
            allergy_reactions=[],
            last_visit_date=date(2026, 1, 5),
            last_clinic_systolic=158,
            last_clinic_diastolic=94,
            historic_bp_systolic=[158],
            historic_bp_dates=["2026-01-05"],
            overdue_labs=["HbA1c"],
            social_context=None,
        ))
        await session.commit()
        print("  Inserted DEMO_EHR: EHR-only patient, no readings.")


# ── Phase 5+6 — Pattern recompute + briefing generation ───────────────────────

def _make_job(job_type: str, patient_id: str) -> ProcessingJob:
    """Create an in-memory ProcessingJob data object for passing to handlers."""
    return ProcessingJob(
        job_id=str(uuid.uuid4()),
        job_type=job_type,
        patient_id=patient_id,
        idempotency_key=f"setup_demo:{job_type}:{patient_id}:{date.today()}",
        status="running",
        retry_count=0,
        queued_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        created_by="setup_demo",
    )


async def _run_patient(patient_id: str) -> None:
    """Run pattern recompute then briefing generation for one patient.

    Pattern recompute is skipped for EHR-only patients (monitoring_active=False)
    — they have no home readings so gap/inertia/adherence detectors are meaningless.
    """
    async with AsyncSessionLocal() as session:
        p = await session.get(Patient, patient_id)
        monitoring_active = p.monitoring_active if p else True

    if monitoring_active:
        print(f"  [{patient_id}] pattern_recompute...", end=" ", flush=True)
        async with AsyncSessionLocal() as session:
            await _handle_pattern_recompute(_make_job("pattern_recompute", patient_id), session)
            await session.commit()
        print("done")
    else:
        print(f"  [{patient_id}] skipping pattern_recompute (EHR-only, monitoring_active=False)")

    print(f"  [{patient_id}] briefing_generation...", end=" ", flush=True)
    async with AsyncSessionLocal() as session:
        await _handle_briefing_generation(_make_job("briefing_generation", patient_id), session)
        await session.commit()
    print("done")


# ── Phase 7 — Verify ──────────────────────────────────────────────────────────

def _chk(label: str, passed: bool) -> bool:
    print(f"    [{'✓' if passed else '✗'}] {label}")
    return passed


async def _verify() -> bool:
    print("\n── Phase 7: Verification ─────────────────────────────")
    ok = True

    async with AsyncSessionLocal() as session:

        # ── Patient A — 1091 ──────────────────────────────────
        print(f"\n  Patient A — {_PATIENT_A}")
        p_a = await session.get(Patient, _PATIENT_A)
        ok &= _chk("Patient exists", p_a is not None)
        if p_a:
            ok &= _chk("risk_tier = high", p_a.risk_tier == "high")
            ok &= _chk(
                "next_appointment = 2026-05-05",
                p_a.next_appointment is not None and p_a.next_appointment.date() == _DEMO_DATE,
            )
            ok &= _chk(
                "risk_score in [50, 100]",
                p_a.risk_score is not None and 50 <= float(p_a.risk_score) <= 100,
            )
            ok &= _chk(
                "risk_score_computed_at < 26h ago",
                p_a.risk_score_computed_at is not None
                and (datetime.now(UTC) - p_a.risk_score_computed_at) < timedelta(hours=26),
            )

        inertia_alert = await session.scalar(
            select(Alert)
            .where(
                Alert.patient_id == _PATIENT_A,
                Alert.alert_type == "inertia",
                Alert.acknowledged_at.is_(None),
            )
            .order_by(Alert.triggered_at.desc())
            .limit(1)
        )
        ok &= _chk("Inertia alert present (unacknowledged)", inertia_alert is not None)

        briefing_a = await session.scalar(
            select(Briefing)
            .where(
                Briefing.patient_id == _PATIENT_A,
                Briefing.appointment_date == _DEMO_DATE,
            )
            .order_by(Briefing.generated_at.desc())
            .limit(1)
        )
        ok &= _chk("Briefing exists for 2026-05-05", briefing_a is not None)
        if briefing_a:
            ok &= _chk(
                "Briefing has readable_summary",
                bool(briefing_a.llm_response.get("readable_summary")),
            )
            adh_raw = str(briefing_a.llm_response.get("adherence_summary", "")).lower()
            agenda_raw = str(briefing_a.llm_response.get("visit_agenda", [])).lower()
            ok &= _chk(
                "Treatment review / inertia in briefing",
                "treatment" in adh_raw or "treatment" in agenda_raw or "inertia" in adh_raw,
            )

        # ── Patient B — DEMO_GAP ──────────────────────────────
        print("\n  Patient B — DEMO_GAP")
        p_gap = await session.get(Patient, "DEMO_GAP")
        ok &= _chk("Patient exists", p_gap is not None)
        if p_gap:
            ok &= _chk("risk_tier = medium", p_gap.risk_tier == "medium")

        gap_alert = await session.scalar(
            select(Alert)
            .where(
                Alert.patient_id == "DEMO_GAP",
                Alert.alert_type.in_(["gap_urgent", "gap_briefing"]),
            )
            .order_by(Alert.triggered_at.desc())
            .limit(1)
        )
        ok &= _chk("Gap alert present", gap_alert is not None)
        if gap_alert:
            ok &= _chk(
                f"Gap >= 9 days (got {gap_alert.gap_days})",
                gap_alert.gap_days is not None and gap_alert.gap_days >= 9,
            )

        briefing_gap = await session.scalar(
            select(Briefing)
            .where(Briefing.patient_id == "DEMO_GAP")
            .order_by(Briefing.generated_at.desc())
            .limit(1)
        )
        ok &= _chk("Briefing generated", briefing_gap is not None)
        if briefing_gap:
            ok &= _chk(
                "Briefing has readable_summary",
                bool(briefing_gap.llm_response.get("readable_summary")),
            )

        # ── Patient C — DEMO_ADH ──────────────────────────────
        print("\n  Patient C — DEMO_ADH")
        p_adh = await session.get(Patient, "DEMO_ADH")
        ok &= _chk("Patient exists", p_adh is not None)
        if p_adh:
            ok &= _chk("risk_tier = medium", p_adh.risk_tier == "medium")

        adh_alert = await session.scalar(
            select(Alert)
            .where(Alert.patient_id == "DEMO_ADH", Alert.alert_type == "adherence")
            .order_by(Alert.triggered_at.desc())
            .limit(1)
        )
        ok &= _chk("Adherence alert present", adh_alert is not None)

        briefing_adh = await session.scalar(
            select(Briefing)
            .where(Briefing.patient_id == "DEMO_ADH")
            .order_by(Briefing.generated_at.desc())
            .limit(1)
        )
        ok &= _chk("Briefing generated", briefing_adh is not None)
        if briefing_adh:
            ok &= _chk(
                "Briefing has readable_summary",
                bool(briefing_adh.llm_response.get("readable_summary")),
            )
            adh_txt = str(briefing_adh.llm_response.get("adherence_summary", "")).lower()
            ok &= _chk(
                "Adherence concern flagged in briefing",
                "concern" in adh_txt or "pattern a" in adh_txt or "pattern_a" in adh_txt,
            )

        # ── Patient D — DEMO_EHR ──────────────────────────────
        print("\n  Patient D — DEMO_EHR")
        p_ehr = await session.get(Patient, "DEMO_EHR")
        ok &= _chk("Patient exists", p_ehr is not None)
        if p_ehr:
            ok &= _chk("risk_tier = high", p_ehr.risk_tier == "high")
            ok &= _chk("monitoring_active = False", p_ehr.monitoring_active is False)

        briefing_ehr = await session.scalar(
            select(Briefing)
            .where(Briefing.patient_id == "DEMO_EHR")
            .order_by(Briefing.generated_at.desc())
            .limit(1)
        )
        ok &= _chk("Briefing generated", briefing_ehr is not None)
        if briefing_ehr:
            ok &= _chk(
                "Briefing has readable_summary",
                bool(briefing_ehr.llm_response.get("readable_summary")),
            )
            interactions: list = briefing_ehr.llm_response.get("drug_interactions", [])
            ok &= _chk("drug_interactions non-empty", len(interactions) > 0)
            ok &= _chk(
                "Triple whammy rule present",
                any(i.get("rule") == "triple_whammy" for i in interactions),
            )
            ok &= _chk(
                "Overdue HbA1c in briefing",
                "HbA1c" in briefing_ehr.llm_response.get("overdue_labs", []),
            )

        # ── Dashboard order ───────────────────────────────────
        print("\n  Dashboard")
        high_ids = set(
            (await session.scalars(
                select(Patient.patient_id).where(Patient.risk_tier == "high")
            )).all()
        )
        ok &= _chk(f"1091 in High tier (found: {sorted(high_ids)})", _PATIENT_A in high_ids)
        ok &= _chk("DEMO_EHR in High tier", "DEMO_EHR" in high_ids)

        medium_ids = set(
            (await session.scalars(
                select(Patient.patient_id).where(Patient.risk_tier == "medium")
            )).all()
        )
        ok &= _chk("DEMO_GAP in Medium tier", "DEMO_GAP" in medium_ids)
        ok &= _chk("DEMO_ADH in Medium tier", "DEMO_ADH" in medium_ids)

    verdict = "ALL CHECKS PASSED ✓" if ok else "SOME CHECKS FAILED ✗ — review output above"
    print(f"\n  {verdict}")
    return ok


# ── Main ───────────────────────────────────────────────────────────────────────

async def _main(dry_run: bool, verify_only: bool) -> None:
    tag = " (DRY RUN)" if dry_run else ""
    print(f"=== ARIA Demo Setup{tag} ===")
    print(f"Target demo date : {_DEMO_DATE}")
    print(f"Demo appointment : {_DEMO_APPT.isoformat()}")

    if not verify_only:
        await _teardown(dry_run)
        await _timeshift_patient_a(dry_run)
        await _seed_demo_gap(dry_run)
        await _seed_demo_adh(dry_run)
        await _seed_demo_ehr(dry_run)

        if not dry_run:
            print("\n── Phase 5+6: Pattern recompute + briefing generation ─")
            for pid in _ALL_PATIENTS:
                await _run_patient(pid)
        else:
            print("\n── Phase 5+6: Pattern recompute + briefing generation ─")
            for pid in _ALL_PATIENTS:
                print(f"  [DRY-RUN] Would run pattern_recompute + briefing_generation for {pid}")

    if not dry_run:
        await _verify()

    print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARIA demo environment setup")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes, no DB writes")
    parser.add_argument("--verify-only", action="store_true", help="Run checklist only, no setup")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry_run, verify_only=args.verify_only))
