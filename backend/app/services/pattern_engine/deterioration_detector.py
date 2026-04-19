"""Layer 1 – Deterioration Detection.

Detects a worsening systolic BP trend over the last 14 days using two
independent signals that must both be positive:

  Signal 1 — Positive least-squares slope across the full 14-day window.
  Signal 2 — Recent 3-day average exceeds the days 4–10 baseline average.

Requiring both signals reduces false positives from single outlier readings.
Fewer than 7 readings → returns False (insufficient data for any result).

Slope is computed in pure Python; no numpy dependency.
Missing days are normal (device outages) and are handled without interpolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOW_DAYS = 14           # full analysis window in days
_RECENT_DAYS = 3            # "recent" sub-window: last N days
_BASELINE_DAYS = 10         # baseline sub-window extends back this many days
_MIN_READINGS = 7           # minimum total readings needed to produce a result


class DeteriorationResult(TypedDict):
    """Structured output of the deterioration detector."""

    deterioration: bool
    slope: float | None         # mmHg/day; None if insufficient data
    recent_avg: float | None    # average systolic over last 3 days
    baseline_avg: float | None  # average systolic over days 4–10 ago


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _least_squares_slope(points: list[tuple[float, float]]) -> float:
    """Compute the least-squares linear regression slope for (x, y) pairs.

    Args:
        points: List of (x, y) tuples where x is days from origin and y is
                systolic value.

    Returns:
        Slope in mmHg/day. Returns 0.0 for degenerate or single-point input.
    """
    n = len(points)
    if n < 2:
        return 0.0

    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_x2 = sum(p[0] ** 2 for p in points)

    denom = n * sum_x2 - sum_x ** 2
    if denom == 0.0:
        return 0.0

    return (n * sum_xy - sum_x * sum_y) / denom


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_deterioration_detector(
    session: AsyncSession, patient_id: str
) -> DeteriorationResult:
    """Detect worsening systolic BP trend over the last 14 days.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient identifier (iEMR MED_REC_NO).

    Returns:
        DeteriorationResult with deterioration flag, slope, and window averages.
    """
    now = datetime.now(tz=timezone.utc)
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
    slope = _least_squares_slope(points)

    # Signal 2: recent 3-day average vs days 4–10 ago baseline
    recent_cutoff = now - timedelta(days=_RECENT_DAYS)
    baseline_cutoff = now - timedelta(days=_BASELINE_DAYS)

    recent_vals = [s for dt, s in readings if dt >= recent_cutoff]
    baseline_vals = [s for dt, s in readings if baseline_cutoff <= dt < recent_cutoff]

    if not recent_vals or not baseline_vals:
        logger.debug(
            "patient=%s deterioration=False (recent=%d baseline=%d readings in sub-windows)",
            patient_id, len(recent_vals), len(baseline_vals),
        )
        return {**_no_detect, "slope": round(slope, 3)}

    recent_avg = round(sum(recent_vals) / len(recent_vals), 1)
    baseline_avg = round(sum(baseline_vals) / len(baseline_vals), 1)
    deterioration = slope > 0.0 and recent_avg > baseline_avg

    logger.info(
        "patient=%s deterioration=%s slope=%.3f recent_avg=%.1f baseline_avg=%.1f",
        patient_id, deterioration, slope, recent_avg, baseline_avg,
    )
    return {
        "deterioration": deterioration,
        "slope": round(slope, 3),
        "recent_avg": recent_avg,
        "baseline_avg": baseline_avg,
    }
