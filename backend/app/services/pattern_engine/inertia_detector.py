"""Layer 1 – Therapeutic Inertia Detection.

All five conditions must be simultaneously true for inertia to be flagged:

  1. Average systolic >= patient_threshold over the adaptive window
     (patient_threshold is adaptive: max(130, mean + 1.5×SD) capped at 145;
      falls back to 140 mmHg when fewer than 3 clinic readings are available)
  2. At least 5 readings with systolic_avg >= patient_threshold
  3. Elevated condition spans > 7 days (from first elevated reading to now)
  4. No medication change on or after the first elevated reading
     (uses clinical_context.med_history JSONB; falls back to last_med_change)
  5. 7-day recent average >= patient_threshold  (BP not currently declining)

Adaptive window (Fix 28): window_days = min(90, max(14, next_appt − last_visit))
White-coat exclusion (Fix 27): readings within 5 days of next_appointment are
  excluded from all threshold comparisons (but remain in the DB).
Fail-safe: sparse data, missing context, or any unmet condition → False.
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
    compute_window_days,
    get_last_med_change_date,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ELEVATED_THRESHOLD = 140       # FALLBACK ONLY — primary is patient-adaptive threshold
_MIN_ELEVATED_COUNT = 5
_MIN_DURATION_DAYS = 7
_RECENT_SLOPE_WINDOW = 7        # days for slope direction check (condition 5)
_WHITE_COAT_EXCLUSION_DAYS = 5  # Fix 27: drop readings within this many days of appointment


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
    """Detect therapeutic inertia for a patient over the adaptive window.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).

    Returns:
        InertiaResult with inertia_detected flag and supporting evidence values.
    """
    now = datetime.now(tz=UTC)

    # --- Query 1: clinical context for threshold and medication history ---
    cc_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    cc: ClinicalContext | None = cc_result.scalar_one_or_none()

    historic_bp = cc.historic_bp_systolic if cc else None
    problem_codes = cc.problem_codes if cc else None
    med_history = cc.med_history if cc else None
    last_med_change_field = cc.last_med_change if cc else None
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
        "patient=%s inertia threshold=%.1f mode=%s adj=%s window_days=%d source=%s",
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

    if not readings:
        return _false_result(avg_systolic=None, elevated_count=0)

    all_systolics = [s for _, s in readings]
    avg_systolic = round(sum(all_systolics) / len(all_systolics), 1)

    elevated = [(dt, s) for dt, s in readings if s >= patient_threshold]
    elevated_count = len(elevated)

    # Condition 1: average systolic >= patient_threshold
    if avg_systolic < patient_threshold:
        logger.debug(
            "patient=%s inertia=False (avg_sys=%.1f below threshold=%.1f)",
            patient_id, avg_systolic, patient_threshold,
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
    # Use med_history JSONB traversal; fall back to last_med_change date field.
    last_med_date = get_last_med_change_date(med_history, last_med_change_field)

    if last_med_date is not None:
        last_med_dt = datetime(
            last_med_date.year, last_med_date.month, last_med_date.day, tzinfo=UTC
        )
        if last_med_dt >= first_elevated_dt:
            logger.debug(
                "patient=%s inertia=False (med change %s on/after first elevated %s)",
                patient_id, last_med_date, first_elevated_dt,
            )
            return _false_result(avg_systolic, elevated_count, duration_days)

    # Condition 5: slope direction — 7-day recent avg must be >= patient_threshold
    # (If BP is declining, do not flag inertia)
    recent_cutoff = now - timedelta(days=_RECENT_SLOPE_WINDOW)
    recent_vals = [s for dt, s in readings if dt >= recent_cutoff]
    if recent_vals:
        recent_7d_avg = sum(recent_vals) / len(recent_vals)
        if recent_7d_avg < patient_threshold:
            logger.debug(
                "patient=%s inertia=False (recent_7d_avg=%.1f < threshold=%.1f — BP declining)",
                patient_id, recent_7d_avg, patient_threshold,
            )
            return _false_result(avg_systolic, elevated_count, duration_days)

    logger.info(
        "patient=%s inertia=True avg_sys=%.1f elevated=%d duration=%.1f days threshold=%.1f",
        patient_id, avg_systolic, elevated_count, duration_days, patient_threshold,
    )
    return {
        "inertia_detected": True,
        "avg_systolic": avg_systolic,
        "elevated_count": elevated_count,
        "duration_days": duration_days,
    }
