"""Layer 1 – BP Variability Detection (Fix 59).

High day-to-day BP variability is an independent cardiovascular risk factor
separate from elevated mean.  Coefficient of variation (CV = SD / mean × 100)
is computed over the adaptive window:

  CV >= 15%  → high variability — consider ABPM referral
  CV 12–14%  → moderate variability — monitor trend
  CV < 12%   → no variability flag

The variability_score (0–100) is passed to the Layer 2 risk scorer where it
carries a 5% weight (drawn from the systolic signal allocation).

Prerequisite: Fix 15 (full-timeline readings) for meaningful long-window CV.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.models.patient import Patient
from app.models.reading import Reading
from app.services.pattern_engine.threshold_utils import compute_window_days
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_CV_HIGH_THRESHOLD = 15.0    # CV >= this → high variability
_CV_MODERATE_THRESHOLD = 12.0  # CV >= this (and < HIGH) → moderate
_MIN_READINGS = 7             # need at least 7 readings for meaningful CV


class VariabilityResult(TypedDict):
    """Structured output of the BP variability detector."""

    detected: bool
    level: str          # "none" | "moderate" | "high"
    cv_pct: float | None
    visit_agenda_item: str | None  # None when level is "none"
    variability_score: float       # 0–100 normalised signal for risk scorer


async def run_variability_detector(
    session: AsyncSession,
    patient_id: str,
    as_of: datetime | None = None,
) -> VariabilityResult:
    """Compute BP coefficient of variation over the adaptive window.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).
        as_of: Reference datetime. Defaults to now (production). Pass a historical
            datetime to replay the detector at a past point (shadow mode only).

    Returns:
        VariabilityResult with level, CV%, agenda item, and scorer signal.
    """
    now = as_of if as_of is not None else datetime.now(tz=UTC)

    _no_detect: VariabilityResult = {
        "detected": False,
        "level": "none",
        "cv_pct": None,
        "visit_agenda_item": None,
        "variability_score": 0.0,
    }

    # --- Query 1: ClinicalContext for last_visit_date ---
    cc_result = await session.execute(
        select(ClinicalContext.last_visit_date).where(
            ClinicalContext.patient_id == patient_id
        )
    )
    cc_row = cc_result.one_or_none()
    last_visit_date = cc_row[0] if cc_row else None

    # --- Query 2: Patient for next_appointment (adaptive window) ---
    patient_result = await session.execute(
        select(Patient.next_appointment).where(Patient.patient_id == patient_id)
    )
    patient_row = patient_result.one_or_none()
    next_appointment = patient_row[0] if patient_row else None

    window_days, window_source = compute_window_days(next_appointment, last_visit_date)
    window_start = now - timedelta(days=window_days)

    logger.debug(
        "patient=%s variability window_days=%d source=%s",
        patient_id, window_days, window_source,
    )

    # --- Query 3: systolic readings in adaptive window ---
    rows = await session.execute(
        select(Reading.systolic_avg).where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime >= window_start,
            Reading.effective_datetime <= now,
        )
    )
    systolics = [float(row[0]) for row in rows]

    if len(systolics) < _MIN_READINGS:
        logger.debug(
            "patient=%s variability=none (only %d readings, need %d)",
            patient_id, len(systolics), _MIN_READINGS,
        )
        return _no_detect

    mean_sys = statistics.mean(systolics)
    if mean_sys <= 0:
        return _no_detect

    cv_pct = round(statistics.pstdev(systolics) / mean_sys * 100.0, 1)

    if cv_pct >= _CV_HIGH_THRESHOLD:
        level = "high"
        agenda_item = (
            f"High BP variability detected (CV {cv_pct:.0f}%) — "
            "consider ambulatory monitoring or ABPM referral."
        )
    elif cv_pct >= _CV_MODERATE_THRESHOLD:
        level = "moderate"
        agenda_item = f"Moderate BP variability noted (CV {cv_pct:.0f}%) — monitor trend."
    else:
        level = "none"
        agenda_item = None

    # variability_score: linear 0–100 normalised against CV (saturates at 20% CV)
    variability_score = round(min(100.0, cv_pct / 20.0 * 100.0), 2)

    detected = level != "none"
    logger.info(
        "patient=%s variability=%s cv_pct=%.1f score=%.2f",
        patient_id, level, cv_pct, variability_score,
    )
    return {
        "detected": detected,
        "level": level,
        "cv_pct": cv_pct,
        "visit_agenda_item": agenda_item,
        "variability_score": variability_score,
    }
