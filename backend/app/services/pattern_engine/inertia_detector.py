"""Layer 1 – Therapeutic Inertia Detection.

All four conditions must be simultaneously true for inertia to be flagged:

  1. Average systolic >= 140 over the last 28 days
  2. At least 5 readings with systolic_avg >= 140
  3. Elevated condition spans > 7 days (from first elevated reading to now)
  4. No medication change on or after the first elevated reading

Fail-safe: sparse data, missing context, or any unmet condition → False.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOW_DAYS = 28
_ELEVATED_THRESHOLD = 140       # systolic_avg >= this qualifies as elevated
_MIN_ELEVATED_COUNT = 5         # minimum elevated readings required to trigger
_MIN_DURATION_DAYS = 7          # elevated condition must persist at least this long


class InertiaResult(TypedDict):
    """Structured output of the therapeutic inertia detector."""

    inertia_detected: bool
    avg_systolic: float | None
    elevated_count: int
    duration_days: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _false_result(
    avg_systolic: float | None,
    elevated_count: int,
    duration_days: float = 0.0,
) -> InertiaResult:
    """Return a standardised negative (no-inertia) result."""
    return {
        "inertia_detected": False,
        "avg_systolic": avg_systolic,
        "elevated_count": elevated_count,
        "duration_days": duration_days,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_inertia_detector(session: AsyncSession, patient_id: str) -> InertiaResult:
    """Detect therapeutic inertia for a patient over the last 28 days.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).

    Returns:
        InertiaResult with inertia_detected flag and supporting evidence values.
    """
    now = datetime.now(tz=UTC)
    window_start = now - timedelta(days=_WINDOW_DAYS)

    rows = await session.execute(
        select(Reading.effective_datetime, Reading.systolic_avg)
        .where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime >= window_start,
            Reading.effective_datetime <= now,
        )
        .order_by(Reading.effective_datetime.asc())
    )
    readings: list[tuple[datetime, float]] = [
        (row.effective_datetime, float(row.systolic_avg)) for row in rows
    ]

    if not readings:
        return _false_result(avg_systolic=None, elevated_count=0)

    all_systolics = [s for _, s in readings]
    avg_systolic = round(sum(all_systolics) / len(all_systolics), 1)

    elevated = [(dt, s) for dt, s in readings if s >= _ELEVATED_THRESHOLD]
    elevated_count = len(elevated)

    # Condition 1: average systolic >= 140
    if avg_systolic < _ELEVATED_THRESHOLD:
        logger.debug(
            "patient=%s inertia=False (avg_sys=%.1f below threshold)", patient_id, avg_systolic
        )
        return _false_result(avg_systolic, elevated_count)

    # Condition 2: at least 5 elevated readings
    if elevated_count < _MIN_ELEVATED_COUNT:
        logger.debug(
            "patient=%s inertia=False (elevated_count=%d < %d)",
            patient_id, elevated_count, _MIN_ELEVATED_COUNT,
        )
        return _false_result(avg_systolic, elevated_count)

    # Condition 3: elevated condition persists > 7 days
    first_elevated_dt = elevated[0][0]
    duration_days = round((now - first_elevated_dt).total_seconds() / 86400.0, 1)

    if duration_days <= _MIN_DURATION_DAYS:
        logger.debug(
            "patient=%s inertia=False (duration_days=%.1f <= %d)",
            patient_id, duration_days, _MIN_DURATION_DAYS,
        )
        return _false_result(avg_systolic, elevated_count, duration_days)

    # Condition 4: no medication change on or after first elevated reading
    cc_row = await session.execute(
        select(ClinicalContext.last_med_change).where(
            ClinicalContext.patient_id == patient_id
        )
    )
    last_med_change: date | None = cc_row.scalar_one_or_none()

    if last_med_change is not None:
        last_med_dt = datetime(
            last_med_change.year,
            last_med_change.month,
            last_med_change.day,
            tzinfo=UTC,
        )
        if last_med_dt >= first_elevated_dt:
            logger.debug(
                "patient=%s inertia=False (med change %s on/after first elevated %s)",
                patient_id, last_med_change, first_elevated_dt,
            )
            return _false_result(avg_systolic, elevated_count, duration_days)

    logger.info(
        "patient=%s inertia=True avg_sys=%.1f elevated=%d duration=%.1f days",
        patient_id, avg_systolic, elevated_count, duration_days,
    )
    return {
        "inertia_detected": True,
        "avg_systolic": avg_systolic,
        "elevated_count": elevated_count,
        "duration_days": duration_days,
    }
