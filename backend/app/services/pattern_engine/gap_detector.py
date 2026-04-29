"""Layer 1 – Gap Detection.

Detects how many days have elapsed since a patient's last home BP reading.
Gap severity is tier-aware: high-risk patients are flagged sooner than
low-risk patients.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIER = "medium"

# Per-tier thresholds in days: flag = raise concern, urgent = escalate
_GAP_THRESHOLDS: dict[str, dict[str, int]] = {
    "high":   {"flag": 1,  "urgent": 3},
    "medium": {"flag": 3,  "urgent": 5},
    "low":    {"flag": 7,  "urgent": 14},
}


class GapResult(TypedDict):
    """Structured output of the gap detector."""

    gap_days: float
    status: Literal["none", "flag", "urgent"]
    threshold_used: dict[str, int]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_gap_detector(
    session: AsyncSession,
    patient_id: str,
    as_of: datetime | None = None,
) -> GapResult:
    """Detect reading gap and classify severity relative to the patient's risk tier.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).
        as_of: Reference datetime. Defaults to now (production). Pass a historical
            datetime to replay the detector at a past point (shadow mode only).

    Returns:
        GapResult with gap_days, status ("none" | "flag" | "urgent"), and the
        tier-specific thresholds that were applied.
    """
    now = as_of if as_of is not None else datetime.now(tz=UTC)

    tier_row = await session.execute(
        select(Patient.risk_tier).where(Patient.patient_id == patient_id)
    )
    risk_tier: str = (tier_row.scalar_one_or_none() or _DEFAULT_TIER).lower()
    thresholds = _GAP_THRESHOLDS.get(risk_tier, _GAP_THRESHOLDS[_DEFAULT_TIER])

    # Exclude future-dated entries — they are data-entry errors, not real readings
    last_dt_row = await session.execute(
        select(func.max(Reading.effective_datetime)).where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime <= now,
        )
    )
    last_reading: datetime | None = last_dt_row.scalar_one_or_none()

    if last_reading is None:
        logger.warning("patient=%s no readings found — classifying as urgent gap", patient_id)
        return {"gap_days": float("inf"), "status": "urgent", "threshold_used": thresholds}

    gap_days = round((now - last_reading).total_seconds() / 86400.0, 2)

    if gap_days >= thresholds["urgent"]:
        status: Literal["none", "flag", "urgent"] = "urgent"
    elif gap_days >= thresholds["flag"]:
        status = "flag"
    else:
        status = "none"

    logger.info(
        "patient=%s tier=%s gap_days=%.2f status=%s",
        patient_id, risk_tier, gap_days, status,
    )
    return {"gap_days": gap_days, "status": status, "threshold_used": thresholds}
