"""Synthetic medication confirmation generator for ARIA demo patients.

Two generation modes:

``generate_confirmations`` — 28-day scenario
    Reads the patient's current medication list from ``clinical_context`` and
    generates scheduled dose events for every medication over the past 28 days.
    Applies the 91% weekday / 78% weekend adherence profile described in CLAUDE.md.

``generate_full_timeline_confirmations`` — full care timeline (AUDIT.md Fix 15)
    For each consecutive pair of clinic readings, generates daily medication
    confirmations for every medication active during that interval, derived from
    ``clinical_context.med_history``.  Adherence rate per interval is drawn from
    a Beta distribution anchored near 91% with ±10-15 percentage-point variation.
    Inserts with ON CONFLICT DO NOTHING on (patient_id, medication_name,
    scheduled_time) — safe to re-run.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Adherence rates
ADHERENCE_RATE_WEEKDAY: float = 0.95
ADHERENCE_RATE_WEEKEND: float = 0.78  # Saturday (weekday() == 5), Sunday (== 6)

# Fixed column values for all generated confirmations
CONFIRMATION_CONFIDENCE: str = "simulated"
CONFIRMATION_TYPE: str = "synthetic_demo"

# Generation window
GENERATION_WINDOW_DAYS: int = 28

# Scheduled-time jitter: ±15 minutes around the nominal UTC hour
JITTER_LOW: int = -15
JITTER_HIGH: int = 15

# Delay between scheduled_time and confirmed_at for taken doses (minutes)
CONFIRM_DELAY_LOW: int = 0
CONFIRM_DELAY_HIGH: int = 15

# UTC hours for each dosing frequency
QD_HOURS: list[int] = [8]
BID_HOURS: list[int] = [8, 20]
TID_HOURS: list[int] = [8, 14, 20]
QID_HOURS: list[int] = [7, 12, 17, 22]

# Full-timeline generation constants
# Beta distribution parameters for per-interval adherence (mean ≈ 0.91, SD ≈ 0.10)
FULL_TIMELINE_BETA_ALPHA: float = 6.5
FULL_TIMELINE_BETA_BETA: float = 0.65
FULL_TIMELINE_ADHERENCE_FLOOR: float = 0.50   # clamp drawn rate above this
# Weekend discount applied on top of the per-interval Beta rate (mirrors 28-day ratio)
FULL_TIMELINE_WEEKEND_DISCOUNT: float = ADHERENCE_RATE_WEEKEND / ADHERENCE_RATE_WEEKDAY
# Batch commit size (rows per DB transaction)
FULL_TIMELINE_BATCH_SIZE: int = 200


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _determine_hours(med_name: str) -> list[int]:
    """Return the UTC dosing hours for a medication based on its name.

    Frequency keywords are matched case-insensitively against the medication
    name string.  If none match, once-daily (QD) is assumed.

    Args:
        med_name: Medication name as stored in ``clinical_context.current_medications``.

    Returns:
        List of UTC hours representing the nominal dosing times for one day.
    """
    lower = med_name.lower()
    if "qid" in lower or "four" in lower:
        return QID_HOURS
    if "tid" in lower or "three" in lower:
        return TID_HOURS
    if "bid" in lower or "twice" in lower:
        return BID_HOURS
    return QD_HOURS


def _make_scheduled_time(day_date: date, hour: int) -> datetime:
    """Build a timezone-aware UTC scheduled_time with ±15-minute jitter.

    Mirrors ``_make_datetime`` in ``reading_generator`` so the two generators
    use an identical time-randomisation pattern.

    Args:
        day_date: Calendar date of the scheduled dose.
        hour: Nominal UTC hour for this dosing slot.

    Returns:
        Timezone-aware UTC datetime.
    """
    base = datetime(day_date.year, day_date.month, day_date.day, hour, 0, 0, tzinfo=UTC)
    jitter = random.randint(JITTER_LOW, JITTER_HIGH)
    return base + timedelta(minutes=jitter)


def _build_confirmation(
    patient_id: str,
    med_name: str,
    rxnorm_code: str | None,
    scheduled_time: datetime,
) -> dict[str, Any]:
    """Build a single confirmation dict applying the adherence roll.

    The adherence rate is 91% on weekdays and 78% on weekends.  A confirmed
    dose sets ``confirmed_at`` and ``minutes_from_schedule``; a missed dose
    leaves both as ``None``.

    Args:
        patient_id: ARIA patient identifier.
        med_name: Medication name.
        rxnorm_code: RxNorm code, or ``None`` if unknown.
        scheduled_time: Timezone-aware UTC scheduled dose time.

    Returns:
        Dict of all ``medication_confirmations`` columns except
        ``confirmation_id`` (DB-generated).
    """
    is_weekend = scheduled_time.weekday() >= 5  # Saturday=5, Sunday=6
    rate = ADHERENCE_RATE_WEEKEND if is_weekend else ADHERENCE_RATE_WEEKDAY
    taken = random.random() < rate

    if taken:
        delay = random.randint(CONFIRM_DELAY_LOW, CONFIRM_DELAY_HIGH)
        confirmed_at: datetime | None = scheduled_time + timedelta(minutes=delay)
        conf_type: str | None = CONFIRMATION_TYPE
        minutes_from_schedule: int | None = delay
    else:
        confirmed_at = None
        conf_type = None
        minutes_from_schedule = None

    return {
        "patient_id": patient_id,
        "medication_name": med_name,
        "rxnorm_code": rxnorm_code,
        "scheduled_time": scheduled_time,
        "confirmed_at": confirmed_at,
        "confirmation_type": conf_type,
        "confidence": CONFIRMATION_CONFIDENCE,
        "minutes_from_schedule": minutes_from_schedule,
        "created_at": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# Full-timeline helpers
# ---------------------------------------------------------------------------


def _active_meds_at(
    med_history: list[dict],
    cutoff_date: date,
) -> list[tuple[str, str | None]]:
    """Return (med_name, rxnorm) pairs active at ``cutoff_date``.

    A drug is considered active when its most recent ``med_history`` entry on
    or before ``cutoff_date`` has an activity other than "discontinue" / "remove".
    Entries with a null activity are treated as active (add/start assumed).

    Args:
        med_history: JSONB list of {name, rxnorm, date, activity} entries.
        cutoff_date: The start of the inter-visit interval (date_a).

    Returns:
        List of (med_name, rxnorm_or_None) tuples active at ``cutoff_date``.
    """
    cutoff_str = cutoff_date.isoformat()
    drug_last: dict[str, dict] = {}

    for entry in med_history:
        name = (entry.get("name") or "").strip()
        entry_date = entry.get("date") or ""
        if not name or entry_date > cutoff_str:
            continue
        prior = drug_last.get(name)
        if prior is None or entry_date >= (prior.get("date") or ""):
            drug_last[name] = entry

    active: list[tuple[str, str | None]] = []
    for name, entry in drug_last.items():
        activity = (entry.get("activity") or "").lower()
        if activity not in ("discontinue", "remove"):
            rxnorm = (entry.get("rxnorm") or "").strip() or None
            active.append((name, rxnorm))

    return active


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_confirmations(
    patient_id: str,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Generate 28 days of synthetic medication confirmation events for a patient.

    Reads the patient's current medication list from ``clinical_context``,
    determines dosing frequency for each medication from its name, and
    generates one dict per scheduled dose over the past 28 days.

    Adherence profile:
      - Weekdays: 91% confirmed
      - Weekends: 78% confirmed

    The caller owns the session lifecycle; this function does not commit.

    Args:
        patient_id: ARIA patient identifier (e.g. ``"1091"``).
        session: SQLAlchemy async session.

    Returns:
        List of dicts, one per scheduled dose (confirmed or missed), containing
        all ``medication_confirmations`` columns except ``confirmation_id``
        (DB-generated).  Returns an empty list when the patient has no recorded
        medications.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: On database query failure.
    """
    # ── Step 1: Query clinical context for medication list ───────────────────
    result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = result.scalar_one_or_none()

    if ctx is None:
        logger.warning(
            "No ClinicalContext found for patient %s — returning empty confirmations",
            patient_id,
        )
        return []

    medications: list[str] = ctx.current_medications or []
    rxnorm_codes: list[str] = ctx.med_rxnorm_codes or []

    if not medications:
        logger.warning(
            "Patient %s has no current medications — returning empty confirmations",
            patient_id,
        )
        return []

    logger.info(
        "Patient %s: generating confirmations for %d medication(s)",
        patient_id,
        len(medications),
    )

    # ── Step 2: Build schedule and apply adherence rolls ─────────────────────
    start_date = date.today() - timedelta(days=GENERATION_WINDOW_DAYS - 1)
    confirmations: list[dict[str, Any]] = []

    for idx, med_name in enumerate(medications):
        # Parallel array: use empty string → None if rxnorm list is shorter
        raw_rxnorm = rxnorm_codes[idx] if idx < len(rxnorm_codes) else ""
        rxnorm_code: str | None = raw_rxnorm.strip() or None

        hours = _determine_hours(med_name)
        scheduled_count = 0

        for day_offset in range(GENERATION_WINDOW_DAYS):
            day_date = start_date + timedelta(days=day_offset)
            for hour in hours:
                scheduled_time = _make_scheduled_time(day_date, hour)
                conf = _build_confirmation(patient_id, med_name, rxnorm_code, scheduled_time)
                confirmations.append(conf)
                scheduled_count += 1

        confirmed = sum(1 for c in confirmations[-scheduled_count:] if c["confirmed_at"])
        logger.info(
            "  %s (%s): %d/%d doses confirmed (%.0f%%)",
            med_name,
            "weekend-adjusted" if any(
                (start_date + timedelta(days=d)).weekday() >= 5
                for d in range(GENERATION_WINDOW_DAYS)
            ) else "weekday",
            confirmed,
            scheduled_count,
            confirmed / scheduled_count * 100 if scheduled_count else 0,
        )

    logger.info(
        "Generated %d confirmation records for patient %s",
        len(confirmations),
        patient_id,
    )
    return confirmations


async def generate_full_timeline_confirmations(
    patient_id: str,
    session: AsyncSession,
) -> int:
    """Generate medication confirmations spanning the patient's full care timeline.

    For each consecutive pair of clinic readings, determines which medications
    were active during that interval from ``clinical_context.med_history`` and
    generates daily confirmation events.  Adherence rate per interval is drawn
    from a Beta distribution (α=6.5, β=0.65; mean≈91%, SD≈10%) with a weekend
    discount applied on top.

    Inserts with ON CONFLICT DO NOTHING on
    ``(patient_id, medication_name, scheduled_time)`` — safe to re-run.

    Args:
        patient_id: ARIA patient identifier (e.g. ``"1091"``).
        session: SQLAlchemy async session.  Commits in batches of
            ``FULL_TIMELINE_BATCH_SIZE`` rows.

    Returns:
        Total number of new rows inserted (0 on complete re-run).
    """
    # ── Step 1: Load clinic readings for interval boundaries ─────────────────
    clinic_result = await session.execute(
        select(Reading.effective_datetime)
        .where(Reading.patient_id == patient_id, Reading.source == "clinic")
        .order_by(Reading.effective_datetime.asc())
    )
    clinic_dates: list[date] = [row.effective_datetime.date() for row in clinic_result]

    if len(clinic_dates) < 2:
        logger.warning(
            "Patient %s: fewer than 2 clinic readings — no intervals to generate confirmations for",
            patient_id,
        )
        return 0

    # ── Step 2: Load med_history from clinical context ────────────────────────
    cc_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    cc = cc_result.scalar_one_or_none()

    med_history: list[dict] = (cc.med_history or []) if cc else []
    if not med_history:
        logger.warning(
            "Patient %s: no med_history found — falling back to current_medications",
            patient_id,
        )
        # Fallback: treat current_medications as active for all intervals
        current_meds = (cc.current_medications or []) if cc else []
        rxnorm_codes = (cc.med_rxnorm_codes or []) if cc else []
        med_history = [
            {"name": name, "rxnorm": rxnorm_codes[i] if i < len(rxnorm_codes) else "",
             "date": clinic_dates[0].isoformat(), "activity": None}
            for i, name in enumerate(current_meds)
        ]

    logger.info(
        "Patient %s: generating full-timeline confirmations across %d inter-visit intervals",
        patient_id,
        len(clinic_dates) - 1,
    )

    total_inserted = 0
    pending_batch = 0

    for i in range(len(clinic_dates) - 1):
        date_a = clinic_dates[i]
        date_b = clinic_dates[i + 1]

        window_start = date_a + timedelta(days=1)
        window_end = date_b - timedelta(days=1)

        if window_end < window_start:
            continue   # adjacent visits — no home monitoring window

        # ── Per-interval Beta-drawn adherence rate ────────────────────────────
        interval_rate = max(
            FULL_TIMELINE_ADHERENCE_FLOOR,
            random.betavariate(FULL_TIMELINE_BETA_ALPHA, FULL_TIMELINE_BETA_BETA),
        )
        weekend_rate = max(
            FULL_TIMELINE_ADHERENCE_FLOOR,
            interval_rate * FULL_TIMELINE_WEEKEND_DISCOUNT,
        )

        # ── Active medications during this interval ───────────────────────────
        active_meds = _active_meds_at(med_history, date_a)
        if not active_meds:
            continue

        # ── Generate one row per dose slot per day per medication ─────────────
        current_day = window_start
        while current_day <= window_end:
            is_weekend = current_day.weekday() >= 5
            rate = weekend_rate if is_weekend else interval_rate

            for med_name, rxnorm_code in active_meds:
                for hour in _determine_hours(med_name):
                    scheduled_time = _make_scheduled_time(current_day, hour)
                    taken = random.random() < rate

                    if taken:
                        delay = random.randint(CONFIRM_DELAY_LOW, CONFIRM_DELAY_HIGH)
                        confirmed_at: datetime | None = scheduled_time + timedelta(minutes=delay)
                        conf_type: str | None = CONFIRMATION_TYPE
                        minutes_diff: int | None = delay
                    else:
                        confirmed_at = None
                        conf_type = None
                        minutes_diff = None

                    stmt = (
                        pg_insert(MedicationConfirmation)
                        .values(
                            patient_id=patient_id,
                            medication_name=med_name,
                            rxnorm_code=rxnorm_code,
                            scheduled_time=scheduled_time,
                            confirmed_at=confirmed_at,
                            confirmation_type=conf_type,
                            confidence=CONFIRMATION_CONFIDENCE,
                            minutes_from_schedule=minutes_diff,
                            created_at=datetime.now(UTC),
                        )
                        .on_conflict_do_nothing(
                            index_elements=["patient_id", "medication_name", "scheduled_time"]
                        )
                    )
                    result = await session.execute(stmt)
                    total_inserted += result.rowcount
                    pending_batch += 1

            current_day += timedelta(days=1)

            if pending_batch >= FULL_TIMELINE_BATCH_SIZE:
                await session.commit()
                pending_batch = 0

    if pending_batch > 0:
        await session.commit()

    logger.info(
        "Patient %s: full-timeline confirmations complete — %d rows inserted",
        patient_id,
        total_inserted,
    )
    return total_inserted
