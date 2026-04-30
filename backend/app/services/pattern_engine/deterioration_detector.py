"""Layer 1 – Deterioration Detection.

Detects a worsening systolic BP trend over the adaptive window using three
independent signals that must all be positive (reduces false positives):

  Signal 1 — Positive least-squares slope across the full window.
  Signal 2 — Recent 3-day average exceeds the days 4–10 baseline average.
  Signal 3 — recent_avg >= patient_threshold (absolute gate: prevents firing on
              a normotensive patient rising 115→119).

Step-change sub-detector (OR gate):
  If the 7-day rolling mean of the most recent week exceeds the 7-day rolling
  mean of 3 weeks ago by >= 15 mmHg AND recent_7d_avg >= patient_threshold,
  deterioration is flagged regardless of linear slope direction.

Adaptive window (Fix 28): window_days = min(90, max(14, next_appt − last_visit))
White-coat exclusion (Fix 27): readings within 5 days of next_appointment are
  excluded from all threshold comparisons (but remain in the DB).
Fewer than 7 readings → returns False (insufficient data).
Slope computed in pure Python; no numpy dependency.
Missing days are handled without interpolation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.models.patient import Patient
from app.models.reading import Reading
from app.services.pattern_engine.threshold_utils import (
    apply_comorbidity_adjustment,
    classify_comorbidity_concern,
    compute_patient_threshold,
    compute_slope,
    compute_window_days,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Backward-compatible alias — existing tests import this name directly.
_least_squares_slope = compute_slope

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RECENT_DAYS = 3            # "recent" sub-window: last N days
_BASELINE_DAYS = 10         # baseline sub-window extends back this many days
_MIN_READINGS = 7           # minimum total readings needed to produce a result
_STEP_CHANGE_RECENT_DAYS = 7
_STEP_CHANGE_OLD_DAYS = 14  # "3 weeks ago" window: 14–21 days ago
_STEP_CHANGE_OLD_END_DAYS = 21
_STEP_CHANGE_THRESHOLD_MMHG = 15.0
_WHITE_COAT_EXCLUSION_DAYS = 5  # Fix 27: drop readings within this many days of appointment
_MIN_SLOPE_MMHG_PER_DAY = 0.3   # minimum slope for clinical significance (avoids borderline FP)


class DeteriorationResult(TypedDict):
    """Structured output of the deterioration detector."""

    deterioration: bool
    slope: float | None         # mmHg/day; None if insufficient data
    recent_avg: float | None    # average systolic over last 3 days
    baseline_avg: float | None  # average systolic over days 4–10 ago


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_deterioration_detector(
    session: AsyncSession,
    patient_id: str,
    as_of: datetime | None = None,
) -> DeteriorationResult:
    """Detect worsening systolic BP trend over the adaptive window.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).
        as_of: Reference datetime. Defaults to now (production). Pass a historical
            datetime to replay the detector at a past point (shadow mode only).

    Returns:
        DeteriorationResult with deterioration flag, slope, and window averages.
    """
    now = as_of if as_of is not None else datetime.now(tz=UTC)

    # --- Query 1: clinical context for patient-adaptive threshold ---
    cc_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    cc: ClinicalContext | None = cc_result.scalar_one_or_none()

    historic_bp = cc.historic_bp_systolic if cc else None
    problem_codes = cc.problem_codes if cc else None
    last_visit_date = cc.last_visit_date if cc else None

    patient_threshold, threshold_mode = compute_patient_threshold(historic_bp)
    concern_state = classify_comorbidity_concern(problem_codes)
    patient_threshold, adj_mode = apply_comorbidity_adjustment(patient_threshold, concern_state)

    # --- Query 1b: Patient for adaptive window + white-coat exclusion (Fix 28, 27) ---
    patient_result = await session.execute(
        select(Patient.next_appointment).where(Patient.patient_id == patient_id)
    )
    patient_row = patient_result.one_or_none()
    next_appointment = patient_row[0] if patient_row else None

    window_days, window_source = compute_window_days(next_appointment, last_visit_date)
    window_start = now - timedelta(days=window_days)

    logger.debug(
        "patient=%s deterioration threshold=%.1f mode=%s adj=%s window_days=%d source=%s",
        patient_id, patient_threshold, threshold_mode, adj_mode, window_days, window_source,
    )

    # --- Query 2: readings in adaptive window ---
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

    # Fix 27: exclude white-coat pre-visit dip window (5 days before next_appointment)
    if next_appointment is not None:
        appt_aware = (
            next_appointment.replace(tzinfo=UTC)
            if next_appointment.tzinfo is None
            else next_appointment
        )
        wc_cutoff = appt_aware - timedelta(days=_WHITE_COAT_EXCLUSION_DAYS)
        excluded = sum(1 for dt, _ in readings if dt >= wc_cutoff)
        if excluded:
            logger.debug(
                "patient=%s white_coat_exclusion: dropped %d reading(s) within %d days of appointment",
                patient_id, excluded, _WHITE_COAT_EXCLUSION_DAYS,
            )
        readings = [(dt, s) for dt, s in readings if dt < wc_cutoff]

    _no_detect: DeteriorationResult = {
        "deterioration": False,
        "slope": None,
        "recent_avg": None,
        "baseline_avg": None,
    }

    if len(readings) < _MIN_READINGS:
        logger.debug(
            "patient=%s deterioration=False (only %d readings, need %d)",
            patient_id, len(readings), _MIN_READINGS,
        )
        return _no_detect

    # Signal 1: least-squares slope across the full 14-day window
    origin = readings[0][0]
    points: list[tuple[float, float]] = [
        ((dt - origin).total_seconds() / 86400.0, sys_avg)
        for dt, sys_avg in readings
    ]
    slope = compute_slope(points)

    # Signal 2: recent 3-day average vs days 4–10 ago baseline
    recent_cutoff = now - timedelta(days=_RECENT_DAYS)
    baseline_cutoff = now - timedelta(days=_BASELINE_DAYS)

    recent_vals = [s for dt, s in readings if dt >= recent_cutoff]
    baseline_vals = [s for dt, s in readings if baseline_cutoff <= dt < recent_cutoff]

    recent_avg: float | None = None
    baseline_avg: float | None = None

    if recent_vals:
        recent_avg = round(sum(recent_vals) / len(recent_vals), 1)
    if baseline_vals:
        baseline_avg = round(sum(baseline_vals) / len(baseline_vals), 1)

    if recent_avg is None or baseline_avg is None:
        logger.debug(
            "patient=%s deterioration=False (recent=%d baseline=%d readings in sub-windows)",
            patient_id, len(recent_vals), len(baseline_vals),
        )
        return {**_no_detect, "slope": round(slope, 3)}

    # Step-change sub-detector: acute jump overrides linear-slope requirement
    step_change_recent_cutoff = now - timedelta(days=_STEP_CHANGE_RECENT_DAYS)
    step_change_old_start = now - timedelta(days=_STEP_CHANGE_OLD_END_DAYS)
    step_change_old_end = now - timedelta(days=_STEP_CHANGE_OLD_DAYS)

    recent_7d_vals = [s for dt, s in readings if dt >= step_change_recent_cutoff]
    old_7d_vals = [
        s for dt, s in readings
        if step_change_old_start <= dt < step_change_old_end
    ]

    step_change_detected = False
    if recent_7d_vals and old_7d_vals:
        recent_7d_avg = sum(recent_7d_vals) / len(recent_7d_vals)
        old_7d_avg = sum(old_7d_vals) / len(old_7d_vals)
        if (
            recent_7d_avg - old_7d_avg >= _STEP_CHANGE_THRESHOLD_MMHG
            and recent_7d_avg >= patient_threshold
        ):
            step_change_detected = True
            logger.info(
                "patient=%s step_change detected: recent_7d=%.1f old_7d=%.1f delta=%.1f",
                patient_id, recent_7d_avg, old_7d_avg, recent_7d_avg - old_7d_avg,
            )

    # Signal 3 + final combination
    # Signal 3: absolute gate — recent_avg must be at or above patient_threshold
    signal_3 = recent_avg >= patient_threshold
    deterioration = (
        (slope >= _MIN_SLOPE_MMHG_PER_DAY and recent_avg > baseline_avg and signal_3)
        or step_change_detected
    )

    logger.info(
        "patient=%s deterioration=%s slope=%.3f recent_avg=%.1f baseline_avg=%.1f "
        "threshold=%.1f step_change=%s",
        patient_id, deterioration, slope, recent_avg, baseline_avg,
        patient_threshold, step_change_detected,
    )
    return {
        "deterioration": deterioration,
        "slope": round(slope, 3),
        "recent_avg": recent_avg,
        "baseline_avg": baseline_avg,
    }
