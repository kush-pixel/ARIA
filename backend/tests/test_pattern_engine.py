"""Unit tests for Layer 1 Pattern Engine detectors.

All tests use mocked AsyncSession objects; no live database is required.
Mock call order in each _session_for helper must exactly match the query
execution order in the corresponding detector function.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.pattern_engine.adherence_analyzer import run_adherence_analyzer
from app.services.pattern_engine.deterioration_detector import (
    _least_squares_slope,
    run_deterioration_detector,
)
from app.services.pattern_engine.gap_detector import run_gap_detector
from app.services.pattern_engine.inertia_detector import run_inertia_detector

# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _scalar(value: object) -> MagicMock:
    """Mock a SQLAlchemy result that returns value from scalar_one_or_none()."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _one(*values: object) -> MagicMock:
    """Mock a SQLAlchemy result that returns values from one() (tuple unpack)."""
    r = MagicMock()
    r.one.return_value = values
    return r


def _rows(*row_dicts: dict) -> MagicMock:
    """Mock a SQLAlchemy result that is iterable, yielding Row-like objects."""
    mocked_rows = []
    for d in row_dicts:
        row = MagicMock()
        for k, v in d.items():
            setattr(row, k, v)
        mocked_rows.append(row)
    result = MagicMock()
    result.__iter__ = lambda _self: iter(mocked_rows)
    return result


def _session(*side_effects: object) -> AsyncMock:
    """Build a mocked AsyncSession with execute responses in call order."""
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=list(side_effects))
    return session


# ---------------------------------------------------------------------------
# Helpers for building reading rows
# ---------------------------------------------------------------------------


def _reading_row(dt: datetime, systolic: float) -> dict:
    return {"effective_datetime": dt, "systolic_avg": systolic}


def _days_ago(n: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


# ===========================================================================
# GAP DETECTOR
# ===========================================================================


async def test_gap_no_readings_is_urgent() -> None:
    """No readings at all → status=urgent regardless of tier."""
    session = _session(_scalar("high"), _scalar(None))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "urgent"
    assert result["gap_days"] == float("inf")


async def test_gap_high_tier_flag() -> None:
    """High-tier patient with 2-day gap → flag (threshold: flag>=1, urgent>=3)."""
    session = _session(_scalar("high"), _scalar(_days_ago(2)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "flag"
    assert result["threshold_used"] == {"flag": 1, "urgent": 3}


async def test_gap_high_tier_urgent() -> None:
    """High-tier patient with 4-day gap → urgent."""
    session = _session(_scalar("high"), _scalar(_days_ago(4)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "urgent"


async def test_gap_medium_tier_none() -> None:
    """Medium-tier patient with 1-day gap → none (flag threshold=3)."""
    session = _session(_scalar("medium"), _scalar(_days_ago(1)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "none"


async def test_gap_medium_tier_flag() -> None:
    """Medium-tier patient with 4-day gap → flag."""
    session = _session(_scalar("medium"), _scalar(_days_ago(4)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "flag"


async def test_gap_medium_tier_urgent() -> None:
    """Medium-tier patient with 6-day gap → urgent."""
    session = _session(_scalar("medium"), _scalar(_days_ago(6)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "urgent"


async def test_gap_low_tier_none() -> None:
    """Low-tier patient with 5-day gap → none (flag threshold=7)."""
    session = _session(_scalar("low"), _scalar(_days_ago(5)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "none"


async def test_gap_low_tier_urgent() -> None:
    """Low-tier patient with 15-day gap → urgent."""
    session = _session(_scalar("low"), _scalar(_days_ago(15)))
    result = await run_gap_detector(session, "1091")
    assert result["status"] == "urgent"


async def test_gap_unknown_tier_falls_back_to_medium() -> None:
    """Unrecognised tier falls back to medium thresholds."""
    session = _session(_scalar("unknown_tier"), _scalar(_days_ago(4)))
    result = await run_gap_detector(session, "1091")
    assert result["threshold_used"] == {"flag": 3, "urgent": 5}
    assert result["status"] == "flag"


async def test_gap_missing_patient_falls_back_to_medium() -> None:
    """NULL risk_tier (patient not found) falls back to medium thresholds."""
    session = _session(_scalar(None), _scalar(_days_ago(4)))
    result = await run_gap_detector(session, "missing")
    assert result["threshold_used"] == {"flag": 3, "urgent": 5}


async def test_gap_days_value_is_rounded() -> None:
    """gap_days is rounded to 2 decimal places."""
    session = _session(_scalar("high"), _scalar(_days_ago(1.123456789)))
    result = await run_gap_detector(session, "1091")
    assert result["gap_days"] == round(result["gap_days"], 2)


# ===========================================================================
# INERTIA DETECTOR
# ===========================================================================


def _elevated_readings(count: int, days_back: float = 20.0) -> list[dict]:
    """Build `count` elevated readings spread over the last `days_back` days."""
    step = days_back / max(count, 1)
    return [
        _reading_row(_days_ago(days_back - i * step), 150.0)
        for i in range(count)
    ]


async def test_inertia_all_conditions_met() -> None:
    """All four conditions met → inertia_detected=True."""
    readings = _elevated_readings(count=6, days_back=20.0)
    # last_med_change well before first elevated reading
    old_change = date.today() - timedelta(days=60)
    session = _session(_rows(*readings), _scalar(old_change))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is True
    assert result["elevated_count"] == 6
    assert result["avg_systolic"] == 150.0


async def test_inertia_no_readings() -> None:
    """No readings → inertia_detected=False (only one query executed)."""
    session = _session(_rows())
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["avg_systolic"] is None
    assert result["elevated_count"] == 0


async def test_inertia_avg_below_threshold() -> None:
    """Average systolic below 140 → inertia_detected=False."""
    readings = [_reading_row(_days_ago(i), 130.0) for i in range(6)]
    session = _session(_rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["avg_systolic"] == 130.0


async def test_inertia_fewer_than_five_elevated() -> None:
    """Only 4 elevated readings → condition 2 fails → inertia_detected=False."""
    readings = [_reading_row(_days_ago(i * 3), 150.0) for i in range(4)]
    session = _session(_rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["elevated_count"] == 4


async def test_inertia_duration_too_short() -> None:
    """5 elevated readings all within 3 days → duration condition fails."""
    readings = [_reading_row(_days_ago(3 - i * 0.5), 150.0) for i in range(5)]
    session = _session(_rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["elevated_count"] == 5


async def test_inertia_recent_med_change_blocks() -> None:
    """Medication changed after first elevated reading → condition 4 fails."""
    readings = _elevated_readings(count=6, days_back=20.0)
    # Med change happened 10 days ago — after the 20-day-ago first elevated reading
    recent_change = date.today() - timedelta(days=10)
    session = _session(_rows(*readings), _scalar(recent_change))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False


async def test_inertia_null_med_change_triggers() -> None:
    """NULL last_med_change (never changed) → condition 4 passes → True."""
    readings = _elevated_readings(count=6, days_back=20.0)
    session = _session(_rows(*readings), _scalar(None))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is True


async def test_inertia_mixed_elevated_and_normal() -> None:
    """Only 3 of 8 readings elevated → elevated_count condition fails."""
    readings = (
        [_reading_row(_days_ago(20 - i), 150.0) for i in range(3)]   # elevated
        + [_reading_row(_days_ago(10 - i), 125.0) for i in range(5)]  # normal
    )
    session = _session(_rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["elevated_count"] == 3


# ===========================================================================
# ADHERENCE ANALYZER
# ===========================================================================


async def test_adherence_pattern_a_high_bp_low_adherence() -> None:
    """High BP + low adherence → Pattern A."""
    session = _session(_one(20, 14), _scalar(155.0))   # 70% adherence, avg sys=155
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "A"
    assert result["interpretation"] == "possible adherence concern"
    assert result["adherence_pct"] == 70.0


async def test_adherence_pattern_b_high_bp_high_adherence() -> None:
    """High BP + high adherence → Pattern B."""
    session = _session(_one(20, 20), _scalar(155.0))   # 100% adherence
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "B"
    assert result["interpretation"] == "treatment review warranted"


async def test_adherence_pattern_c_normal_bp_low_adherence() -> None:
    """Normal BP + low adherence → Pattern C."""
    session = _session(_one(20, 14), _scalar(128.0))   # 70% adherence, normal BP
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "C"
    assert result["interpretation"] == "contextual review"


async def test_adherence_pattern_none_normal_bp_high_adherence() -> None:
    """Normal BP + high adherence → no concern."""
    session = _session(_one(20, 20), _scalar(128.0))
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "none"
    assert result["interpretation"] == "no adherence concern identified"


async def test_adherence_no_doses_returns_none_pct() -> None:
    """Zero scheduled doses → adherence_pct=None, pattern=none (no div-by-zero)."""
    session = _session(_one(0, 0))
    result = await run_adherence_analyzer(session, "1091")
    assert result["adherence_pct"] is None
    assert result["pattern"] == "none"
    # Only one query executed (second query skipped when no doses)
    assert session.execute.call_count == 1


async def test_adherence_no_bp_readings_pattern_c_when_low_adherence() -> None:
    """No readings → avg_systolic=None → treated as normal BP → Pattern C if low adherence."""
    session = _session(_one(20, 14), _scalar(None))    # 70% adherence, no BP
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "C"


async def test_adherence_boundary_exactly_80_pct_is_not_low() -> None:
    """Exactly 80% adherence is NOT classified as low adherence."""
    session = _session(_one(10, 8), _scalar(155.0))    # 80% adherence, high BP
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "B"    # high BP + NOT low adherence


async def test_adherence_language_never_says_non_adherent() -> None:
    """Clinical language constraint: interpretation must not contain 'non-adherent'."""
    from app.services.pattern_engine.adherence_analyzer import _INTERPRETATIONS

    for text in _INTERPRETATIONS.values():
        assert "non-adherent" not in text.lower()


# ===========================================================================
# DETERIORATION DETECTOR
# ===========================================================================


def _worsening_readings(
    count: int = 10,
    start_systolic: float = 145.0,
    end_systolic: float = 165.0,
    window_days: int = 14,
) -> list[dict]:
    """Build readings with a clear upward trend over the window."""
    step_days = window_days / max(count - 1, 1)
    step_sys = (end_systolic - start_systolic) / max(count - 1, 1)
    return [
        _reading_row(
            _days_ago(window_days - i * step_days),
            round(start_systolic + i * step_sys, 1),
        )
        for i in range(count)
    ]


def _improving_readings(count: int = 10, window_days: int = 14) -> list[dict]:
    """Build readings with a clear downward trend."""
    return _worsening_readings(
        count=count, start_systolic=165.0, end_systolic=145.0, window_days=window_days
    )


async def test_deterioration_worsening_trend() -> None:
    """Clear upward trend → deterioration=True, positive slope."""
    session = _session(_rows(*_worsening_readings()))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is True
    assert result["slope"] is not None and result["slope"] > 0
    assert result["recent_avg"] is not None
    assert result["baseline_avg"] is not None
    assert result["recent_avg"] > result["baseline_avg"]


async def test_deterioration_improving_trend() -> None:
    """Downward trend → deterioration=False (negative slope)."""
    session = _session(_rows(*_improving_readings()))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_fewer_than_7_readings() -> None:
    """Fewer than 7 readings → deterioration=False, no slope computed."""
    readings = [_reading_row(_days_ago(i), 155.0) for i in range(5)]
    session = _session(_rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False
    assert result["slope"] is None


async def test_deterioration_no_readings() -> None:
    """Zero readings → deterioration=False."""
    session = _session(_rows())
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_flat_trend() -> None:
    """Flat BP (zero slope) → deterioration=False."""
    readings = [_reading_row(_days_ago(14 - i), 150.0) for i in range(10)]
    session = _session(_rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False
    assert result["slope"] == 0.0


async def test_deterioration_positive_slope_but_recent_not_higher() -> None:
    """Positive overall slope but recent_avg <= baseline_avg → False (both signals required)."""
    # Spike in the middle, then drop — slope positive, but recent is below baseline
    readings = [
        _reading_row(_days_ago(14), 145.0),
        _reading_row(_days_ago(13), 148.0),
        _reading_row(_days_ago(11), 165.0),   # spike
        _reading_row(_days_ago(10), 163.0),
        _reading_row(_days_ago(8),  160.0),
        _reading_row(_days_ago(5),  148.0),
        _reading_row(_days_ago(2),  143.0),   # recent drops below baseline
        _reading_row(_days_ago(1),  142.0),
    ]
    session = _session(_rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    # recent_avg ≈ 142.5, baseline (days 4-10 ago) ≈ 157 → recent NOT higher
    assert result["deterioration"] is False


async def test_deterioration_readings_only_in_recent_window() -> None:
    """All readings in last 3 days → no baseline values → deterioration=False."""
    readings = [_reading_row(_days_ago(i * 0.3), 160.0) for i in range(8)]
    session = _session(_rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


# ---------------------------------------------------------------------------
# Unit tests for the pure-Python slope helper
# ---------------------------------------------------------------------------


def test_slope_positive_trend() -> None:
    """Perfect linear upward trend → slope == exact rise/run."""
    # y = 10x + 150, x in [0, 1, 2]
    points = [(0.0, 150.0), (1.0, 160.0), (2.0, 170.0)]
    slope = _least_squares_slope(points)
    assert abs(slope - 10.0) < 0.001


def test_slope_negative_trend() -> None:
    """Perfect linear downward trend → negative slope."""
    points = [(0.0, 170.0), (1.0, 160.0), (2.0, 150.0)]
    slope = _least_squares_slope(points)
    assert abs(slope - (-10.0)) < 0.001


def test_slope_flat_returns_zero() -> None:
    """Constant y → slope == 0."""
    points = [(0.0, 150.0), (1.0, 150.0), (2.0, 150.0)]
    assert _least_squares_slope(points) == 0.0


def test_slope_single_point_returns_zero() -> None:
    """Single point → slope is undefined → returns 0."""
    assert _least_squares_slope([(0.0, 150.0)]) == 0.0


def test_slope_empty_returns_zero() -> None:
    """Empty input → returns 0."""
    assert _least_squares_slope([]) == 0.0
