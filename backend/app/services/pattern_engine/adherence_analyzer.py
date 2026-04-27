"""Layer 1 – Adherence Analysis.

Computes medication adherence rate from the last 28 days of confirmations
and classifies the clinical pattern relative to average BP over the same window.

Pattern matrix:
  A: elevated BP  + low adherence  → "possible adherence concern"
  B: elevated BP  + high adherence → "treatment review warranted"
  C: normal BP    + low adherence  → "contextual review"
  none: normal BP + high adherence → no clinical concern

Pattern B suppression (treatment is working — do NOT fire Pattern B if ALL true):
  slope < -0.3 mmHg/day  AND
  7-day recent avg < elevated BP threshold  AND
  days_since_med_change <= 14
  → suppressed to "none" with interpretation "treatment appears effective — monitoring"
  Suppression MUST NOT apply when no recent medication change is recorded.

Clinical language boundary (enforced at code level):
  NEVER use "non-adherent". Always use "possible adherence concern".
  NEVER use "medication failure". Always use "treatment review warranted".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.reading import Reading
from app.services.pattern_engine.threshold_utils import (
    compute_slope,
    get_last_med_change_date,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOW_DAYS = 28
_LOW_ADHERENCE_THRESHOLD = 80.0       # adherence_pct below this = low adherence
_HIGH_BP_SYSTOLIC_THRESHOLD = 140     # avg systolic >= this = elevated
_SUPPRESSION_SLOPE_THRESHOLD = -0.3   # mmHg/day — must be more negative to suppress
_SUPPRESSION_RECENT_DAYS = 7
_SUPPRESSION_MED_CHANGE_DAYS = 14     # med change must be within this many days

_SUPPRESSED_B_INTERPRETATION = "treatment appears effective — monitoring"

_INTERPRETATIONS: dict[str, str] = {
    "A":    "possible adherence concern",
    "B":    "treatment review warranted",
    "C":    "contextual review",
    "none": "no adherence concern identified",
}


class AdherenceResult(TypedDict):
    """Structured output of the adherence analysis detector."""

    adherence_pct: float | None
    pattern: Literal["A", "B", "C", "none"]
    interpretation: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _days_since(change_date: object, now: datetime) -> float:
    """Return fractional days from a date/datetime to now.  Returns inf if None."""
    if change_date is None:
        return float("inf")
    from datetime import date as _date
    if isinstance(change_date, _date) and not isinstance(change_date, datetime):
        from datetime import UTC as _UTC
        change_dt = datetime(
            change_date.year, change_date.month, change_date.day, tzinfo=_UTC
        )
    else:
        change_dt = change_date  # type: ignore[assignment]
    return (now - change_dt).total_seconds() / 86400.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_adherence_analyzer(session: AsyncSession, patient_id: str) -> AdherenceResult:
    """Compute medication adherence and classify the clinical pattern.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).

    Returns:
        AdherenceResult with adherence_pct, pattern label, and interpretation string.
    """
    now = datetime.now(tz=UTC)
    window_start = now - timedelta(days=_WINDOW_DAYS)

    # --- Query 1: medication confirmation counts ---
    conf_row = await session.execute(
        select(
            func.count(MedicationConfirmation.confirmation_id),
            func.count(MedicationConfirmation.confirmed_at),
        ).where(
            MedicationConfirmation.patient_id == patient_id,
            MedicationConfirmation.scheduled_time >= window_start,
            MedicationConfirmation.scheduled_time <= now,
        )
    )
    total_doses, confirmed_doses = conf_row.one()
    total_doses = total_doses or 0
    confirmed_doses = confirmed_doses or 0

    if total_doses == 0:
        logger.warning(
            "patient=%s no scheduled doses in window — adherence unknown", patient_id
        )
        return {
            "adherence_pct": None,
            "pattern": "none",
            "interpretation": _INTERPRETATIONS["none"],
        }

    adherence_pct = round((confirmed_doses / total_doses) * 100.0, 1)
    low_adherence = adherence_pct < _LOW_ADHERENCE_THRESHOLD

    # --- Query 2: 28-day average systolic ---
    bp_row = await session.execute(
        select(func.avg(Reading.systolic_avg)).where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime >= window_start,
            Reading.effective_datetime <= now,
        )
    )
    avg_systolic_raw = bp_row.scalar_one_or_none()
    avg_systolic: float | None = (
        float(avg_systolic_raw) if avg_systolic_raw is not None else None
    )
    high_bp = avg_systolic is not None and avg_systolic >= _HIGH_BP_SYSTOLIC_THRESHOLD

    if high_bp and low_adherence:
        pattern: Literal["A", "B", "C", "none"] = "A"
    elif high_bp and not low_adherence:
        pattern = "B"
    elif not high_bp and low_adherence:
        pattern = "C"
    else:
        pattern = "none"

    # --- Pattern B suppression check ---
    if pattern == "B":
        pattern, interpretation = await _check_pattern_b_suppression(
            session, patient_id, now, window_start
        )
    else:
        interpretation = _INTERPRETATIONS[pattern]

    logger.info(
        "patient=%s adherence=%.1f%% avg_sys=%s pattern=%s",
        patient_id,
        adherence_pct,
        f"{avg_systolic:.1f}" if avg_systolic is not None else "n/a",
        pattern,
    )
    return {
        "adherence_pct": adherence_pct,
        "pattern": pattern,
        "interpretation": interpretation,
    }


async def _check_pattern_b_suppression(
    session: AsyncSession,
    patient_id: str,
    now: datetime,
    window_start: datetime,
) -> tuple[Literal["A", "B", "C", "none"], str]:
    """Return (pattern, interpretation) after applying Pattern B suppression logic.

    Suppression requires ALL of:
      1. Slope < -0.3 mmHg/day over the 28-day window
      2. 7-day recent average < elevated BP threshold
      3. days_since_med_change <= 14

    If no recent medication change exists, suppression MUST NOT apply.
    """
    # --- Query 3: individual readings for slope and recent average ---
    readings_result = await session.execute(
        select(Reading.effective_datetime, Reading.systolic_avg)
        .where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime >= window_start,
            Reading.effective_datetime <= now,
        )
        .order_by(Reading.effective_datetime.asc())
    )
    readings: list[tuple[datetime, float]] = [
        (row.effective_datetime, float(row.systolic_avg)) for row in readings_result
    ]

    if len(readings) < 2:
        return ("B", _INTERPRETATIONS["B"])

    origin = readings[0][0]
    points = [
        ((dt - origin).total_seconds() / 86400.0, s)
        for dt, s in readings
    ]
    slope = compute_slope(points)

    recent_cutoff = now - timedelta(days=_SUPPRESSION_RECENT_DAYS)
    recent_vals = [s for dt, s in readings if dt >= recent_cutoff]
    recent_7d_avg = (
        sum(recent_vals) / len(recent_vals) if recent_vals else float("inf")
    )

    # --- Query 4: medication change date for suppression 14-day gate ---
    cc_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    cc: ClinicalContext | None = cc_result.scalar_one_or_none()

    med_history = cc.med_history if cc else None
    last_med_change_field = cc.last_med_change if cc else None
    last_med_date = get_last_med_change_date(med_history, last_med_change_field)
    days_since = _days_since(last_med_date, now)

    should_suppress = (
        slope < _SUPPRESSION_SLOPE_THRESHOLD
        and recent_7d_avg < _HIGH_BP_SYSTOLIC_THRESHOLD
        and days_since <= _SUPPRESSION_MED_CHANGE_DAYS
    )

    logger.debug(
        "patient=%s pattern_b_suppression: slope=%.3f recent_7d=%.1f days_since_med=%.1f "
        "suppress=%s",
        patient_id, slope, recent_7d_avg, days_since, should_suppress,
    )

    if should_suppress:
        return ("none", _SUPPRESSED_B_INTERPRETATION)
    return ("B", _INTERPRETATIONS["B"])
