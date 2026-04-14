"""Synthetic 28-day medication confirmation generator for ARIA demo patients.

Reads the patient's current medication list from ``clinical_context`` and
generates scheduled dose events for every medication over the past 28 days.
Applies the 91% weekday / 78% weekend adherence profile described in CLAUDE.md.

Returns a list of dicts ready to insert into the ``medication_confirmations``
table.  A ``confirmed_at`` of ``None`` represents a missed dose; absent rows
are never generated — every scheduled slot produces exactly one dict.

Usage::

    confs = await generate_confirmations("1091", session)
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
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
