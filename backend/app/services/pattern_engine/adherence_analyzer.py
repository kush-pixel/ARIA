"""Layer 1 – Adherence Analysis.

Computes medication adherence rate from the last 28 days of confirmations
and classifies the clinical pattern relative to average BP over the same window.

Pattern matrix:
  A: elevated BP  + low adherence  → "possible adherence concern"
  B: elevated BP  + high adherence → "treatment review warranted"
  C: normal BP    + low adherence  → "contextual review"
  none: normal BP + high adherence → no clinical concern

Clinical language boundary (enforced at code level):
  NEVER use "non-adherent". Always use "possible adherence concern".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.medication_confirmation import MedicationConfirmation
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOW_DAYS = 28
_LOW_ADHERENCE_THRESHOLD = 80.0       # adherence_pct below this = low adherence
_HIGH_BP_SYSTOLIC_THRESHOLD = 140     # avg systolic >= this = elevated

_INTERPRETATIONS: dict[str, str] = {
    "A":    "possible adherence concern",
    "B":    "possible treatment-review case — elevated BP with high adherence signal",
    "C":    "contextual review",
    "none": "no adherence concern identified",
}


class AdherenceResult(TypedDict):
    """Structured output of the adherence analysis detector."""

    adherence_pct: float | None
    pattern: Literal["A", "B", "C", "none"]
    interpretation: str


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

    bp_row = await session.execute(
        select(func.avg(Reading.systolic_avg)).where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime >= window_start,
            Reading.effective_datetime <= now,
        )
    )
    avg_systolic_raw = bp_row.scalar_one_or_none()
    avg_systolic: float | None = float(avg_systolic_raw) if avg_systolic_raw is not None else None
    high_bp = avg_systolic is not None and avg_systolic >= _HIGH_BP_SYSTOLIC_THRESHOLD

    if high_bp and low_adherence:
        pattern: Literal["A", "B", "C", "none"] = "A"
    elif high_bp and not low_adherence:
        pattern = "B"
    elif not high_bp and low_adherence:
        pattern = "C"
    else:
        pattern = "none"

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
        "interpretation": _INTERPRETATIONS[pattern],
    }
