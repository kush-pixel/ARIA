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


# ---------------------------------------------------------------------------
# Mock ClinicalContext object builder
# ---------------------------------------------------------------------------


def _cc(
    *,
    historic_bp_systolic: list | None = None,
    problem_codes: list | None = None,
    med_history: list | None = None,
    last_med_change: date | None = None,
    last_visit_date: date | None = None,
) -> MagicMock:
    """Build a mock ClinicalContext ORM object with the given field values."""
    obj = MagicMock()
    obj.historic_bp_systolic = historic_bp_systolic
    obj.problem_codes = problem_codes
    obj.med_history = med_history
    obj.last_med_change = last_med_change
    obj.last_visit_date = last_visit_date
    return obj


# ===========================================================================
# GAP DETECTOR
# (query order: Patient.risk_tier, MAX(Reading.effective_datetime) — unchanged)
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
# Query order: ClinicalContext (scalar_one_or_none), then readings (iterable).
# ClinicalContext provides threshold inputs and medication history.
# ===========================================================================


def _elevated_readings(count: int, days_back: float = 20.0) -> list[dict]:
    """Build `count` elevated readings spread over the last `days_back` days."""
    step = days_back / max(count, 1)
    return [
        _reading_row(_days_ago(days_back - i * step), 150.0)
        for i in range(count)
    ]


def _cc_scalar(cc_obj: MagicMock | None) -> MagicMock:
    """Wrap a _cc() object (or None) in a scalar_one_or_none mock result."""
    return _scalar(cc_obj)


def _appt(next_appt_dt=None) -> MagicMock:
    """Mock Patient.next_appointment query result (one_or_none)."""
    r = MagicMock()
    r.one_or_none.return_value = (next_appt_dt,) if next_appt_dt is not None else None
    return r


async def test_inertia_all_conditions_met() -> None:
    """All five conditions met → inertia_detected=True."""
    readings = _elevated_readings(count=6, days_back=20.0)
    old_change = date.today() - timedelta(days=60)
    cc_obj = _cc(last_med_change=old_change)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is True
    assert result["elevated_count"] == 6
    assert result["avg_systolic"] == 150.0


async def test_inertia_no_readings() -> None:
    """No readings → inertia_detected=False (CC queried, then readings empty)."""
    cc_obj = _cc()
    session = _session(_cc_scalar(cc_obj), _appt(), _rows())
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["avg_systolic"] is None
    assert result["elevated_count"] == 0


async def test_inertia_avg_below_threshold() -> None:
    """Average systolic below 140 → inertia_detected=False."""
    readings = [_reading_row(_days_ago(i), 130.0) for i in range(6)]
    cc_obj = _cc()
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["avg_systolic"] == 130.0


async def test_inertia_fewer_than_five_elevated() -> None:
    """Only 4 elevated readings → condition 2 fails → inertia_detected=False."""
    readings = [_reading_row(_days_ago(i * 3), 150.0) for i in range(4)]
    cc_obj = _cc()
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["elevated_count"] == 4


async def test_inertia_duration_too_short() -> None:
    """5 elevated readings all within 3 days → duration condition fails."""
    readings = [_reading_row(_days_ago(3 - i * 0.5), 150.0) for i in range(5)]
    cc_obj = _cc()
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["elevated_count"] == 5


async def test_inertia_recent_med_change_blocks() -> None:
    """Medication changed after first elevated reading → condition 4 fails."""
    readings = _elevated_readings(count=6, days_back=20.0)
    recent_change = date.today() - timedelta(days=10)
    cc_obj = _cc(last_med_change=recent_change)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False


async def test_inertia_null_med_change_triggers() -> None:
    """NULL last_med_change (never changed) → condition 4 passes → True."""
    readings = _elevated_readings(count=6, days_back=20.0)
    cc_obj = _cc(last_med_change=None, med_history=None)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is True


async def test_inertia_mixed_elevated_and_normal() -> None:
    """Only 3 of 8 readings elevated → elevated_count condition fails."""
    readings = (
        [_reading_row(_days_ago(20 - i), 150.0) for i in range(3)]   # elevated
        + [_reading_row(_days_ago(10 - i), 125.0) for i in range(5)]  # normal
    )
    cc_obj = _cc()
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False
    assert result["elevated_count"] == 3


async def test_inertia_med_history_blocks_when_recent_change() -> None:
    """med_history entry with date after first elevated → condition 4 fails."""
    readings = _elevated_readings(count=6, days_back=20.0)
    # Med change 5 days ago → after first elevated reading 20 days ago
    recent_date_str = (date.today() - timedelta(days=5)).isoformat()
    med_history = [{"name": "Lisinopril", "rxnorm": "29046", "date": recent_date_str, "activity": "increased"}]
    cc_obj = _cc(med_history=med_history, last_med_change=None)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False


async def test_inertia_condition5_declining_bp_blocks() -> None:
    """7-day recent avg below threshold → condition 5 (slope direction) fails → False."""
    # All readings elevated overall, but last 7 days are below 140
    readings = (
        [_reading_row(_days_ago(20 - i * 2), 155.0) for i in range(5)]  # old elevated
        + [_reading_row(_days_ago(6 - i), 130.0) for i in range(5)]     # recent: below 140
    )
    cc_obj = _cc(last_med_change=None)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False


async def test_inertia_no_cc_row_falls_back_to_default_threshold() -> None:
    """No ClinicalContext row → falls back to 140 threshold, still detects inertia."""
    readings = _elevated_readings(count=6, days_back=20.0)
    session = _session(_cc_scalar(None), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is True


# ===========================================================================
# ADHERENCE ANALYZER
# Query order:
#   Q1: medication_confirmations count (one)
#   Q2: avg systolic (scalar_one_or_none)
#   Q3 (only if Pattern B): individual readings (iterable)
#   Q4 (only if Pattern B): ClinicalContext (scalar_one_or_none)
# ===========================================================================


def _flat_readings_for_b_suppression_check() -> MagicMock:
    """Readings with zero/positive slope — suppression does NOT trigger."""
    return _rows(
        _reading_row(_days_ago(14), 155.0),
        _reading_row(_days_ago(7), 155.0),
        _reading_row(_days_ago(3), 158.0),
    )


def _no_recent_med_change_cc() -> MagicMock:
    """ClinicalContext mock with no medication change — suppression does NOT trigger."""
    return _cc_scalar(_cc(med_history=None, last_med_change=None))


async def test_adherence_pattern_a_high_bp_low_adherence() -> None:
    """High BP + low adherence → Pattern A."""
    session = _session(_cc_scalar(_cc()), _appt(), _one(20, 14), _scalar(155.0))
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "A"
    assert result["interpretation"] == "possible adherence concern"
    assert result["adherence_pct"] == 70.0


async def test_adherence_pattern_b_high_bp_high_adherence() -> None:
    """High BP + high adherence → Pattern B (not suppressed: flat slope, no med change)."""
    session = _session(
        _cc_scalar(_cc()),                       # no historic_bp → threshold=140
        _appt(),                                 # Patient.next_appointment → None → 28d window
        _one(20, 20),                            # 100% adherence
        _scalar(155.0),                          # avg systolic >= 140
        _flat_readings_for_b_suppression_check(),# flat slope → no suppression
    )
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "B"
    assert result["interpretation"] == "treatment review warranted"


async def test_adherence_pattern_c_normal_bp_low_adherence() -> None:
    """Normal BP + low adherence → Pattern C."""
    session = _session(_cc_scalar(_cc()), _appt(), _one(20, 14), _scalar(128.0))
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "C"
    assert result["interpretation"] == "contextual review"


async def test_adherence_pattern_none_normal_bp_high_adherence() -> None:
    """Normal BP + high adherence → no concern."""
    session = _session(_cc_scalar(_cc()), _appt(), _one(20, 20), _scalar(128.0))
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "none"
    assert result["interpretation"] == "no adherence concern identified"


async def test_adherence_no_doses_returns_none_pct() -> None:
    """Zero scheduled doses → adherence_pct=None, pattern=none (no div-by-zero)."""
    session = _session(_cc_scalar(_cc()), _appt(), _one(0, 0))
    result = await run_adherence_analyzer(session, "1091")
    assert result["adherence_pct"] is None
    assert result["pattern"] == "none"
    assert session.execute.call_count == 3   # cc + Patient + confirmations


async def test_adherence_no_bp_readings_pattern_c_when_low_adherence() -> None:
    """No readings → avg_systolic=None → treated as normal BP → Pattern C if low adherence."""
    session = _session(_cc_scalar(_cc()), _appt(), _one(20, 14), _scalar(None))
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "C"


async def test_adherence_boundary_exactly_80_pct_is_not_low() -> None:
    """Exactly 80% adherence is NOT classified as low adherence → Pattern B."""
    session = _session(
        _cc_scalar(_cc()),
        _appt(),                                 # Patient.next_appointment → None → 28d window
        _one(10, 8),                             # 80% adherence
        _scalar(155.0),                          # high BP → Pattern B candidate
        _flat_readings_for_b_suppression_check(),
    )
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "B"


async def test_adherence_language_never_says_non_adherent() -> None:
    """Clinical language constraint: interpretation must not contain 'non-adherent'."""
    from app.services.pattern_engine.adherence_analyzer import _INTERPRETATIONS

    for text in _INTERPRETATIONS.values():
        assert "non-adherent" not in text.lower()


async def test_adherence_pattern_b_suppressed_when_treatment_working() -> None:
    """Pattern B suppressed when slope < -0.3, 7d avg < threshold, med change within window."""
    readings = [
        _reading_row(_days_ago(28), 158.0),
        _reading_row(_days_ago(21), 153.0),
        _reading_row(_days_ago(14), 147.0),
        _reading_row(_days_ago(7),  142.0),
        _reading_row(_days_ago(3),  137.0),
        _reading_row(_days_ago(1),  135.0),
    ]
    recent_change = date.today() - timedelta(days=7)
    cc_obj = _cc(med_history=None, last_med_change=recent_change)
    session = _session(
        _cc_scalar(cc_obj),      # cc first: threshold=140 (no historic_bp), med change 7d ago
        _appt(),
        _one(20, 20),
        _scalar(148.0),
        _rows(*readings),
    )
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "none"
    assert result["interpretation"] == "treatment appears effective — monitoring"


async def test_adherence_pattern_b_not_suppressed_when_no_med_change() -> None:
    """Pattern B NOT suppressed when no recent med change — gate critical."""
    readings = [
        _reading_row(_days_ago(28), 158.0),
        _reading_row(_days_ago(14), 147.0),
        _reading_row(_days_ago(7),  138.0),
        _reading_row(_days_ago(1),  135.0),
    ]
    cc_obj = _cc(med_history=None, last_med_change=None)
    session = _session(
        _cc_scalar(cc_obj),
        _appt(),
        _one(20, 20),
        _scalar(148.0),
        _rows(*readings),
    )
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "B"
    assert result["interpretation"] == "treatment review warranted"


async def test_adherence_pattern_b_not_suppressed_when_med_change_too_old() -> None:
    """Pattern B NOT suppressed when med change is older than the default 42-day window."""
    readings = [
        _reading_row(_days_ago(28), 158.0),
        _reading_row(_days_ago(14), 147.0),
        _reading_row(_days_ago(7),  138.0),
        _reading_row(_days_ago(1),  135.0),
    ]
    old_change = date.today() - timedelta(days=50)
    cc_obj = _cc(med_history=None, last_med_change=old_change)
    session = _session(
        _cc_scalar(cc_obj),
        _appt(),
        _one(20, 20),
        _scalar(148.0),
        _rows(*readings),
    )
    result = await run_adherence_analyzer(session, "1091")
    assert result["pattern"] == "B"


async def test_adherence_pattern_b_med_history_used_for_suppression() -> None:
    """med_history JSONB most recent date used for days_since + drug-class window."""
    readings = [
        _reading_row(_days_ago(28), 158.0),
        _reading_row(_days_ago(21), 152.0),
        _reading_row(_days_ago(14), 146.0),
        _reading_row(_days_ago(7),  139.0),
        _reading_row(_days_ago(3),  134.0),
        _reading_row(_days_ago(1),  132.0),
    ]
    recent_date_str = (date.today() - timedelta(days=5)).isoformat()
    med_history = [
        {"name": "Amlodipine", "rxnorm": "17767", "date": recent_date_str, "activity": "start"},
    ]
    cc_obj = _cc(med_history=med_history, last_med_change=None)
    session = _session(
        _cc_scalar(cc_obj),      # cc first: amlodipine → 56d window, med change 5d ago
        _appt(),
        _one(20, 20),
        _scalar(148.0),
        _rows(*readings),
    )
    result = await run_adherence_analyzer(session, "1091")
    # slope ≈ -0.93 mmHg/day, 7d avg ≈ 135, med change 5d ago → suppressed
    assert result["pattern"] == "none"
    assert result["interpretation"] == "treatment appears effective — monitoring"


# ===========================================================================
# DETERIORATION DETECTOR
# Query order: ClinicalContext (scalar_one_or_none), then readings (iterable).
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


def _no_cc() -> MagicMock:
    """ClinicalContext not found → threshold falls back to 140."""
    return _scalar(None)


async def test_deterioration_worsening_trend() -> None:
    """Clear upward trend above threshold → deterioration=True, positive slope."""
    session = _session(_no_cc(), _appt(), _rows(*_worsening_readings()))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is True
    assert result["slope"] is not None and result["slope"] > 0
    assert result["recent_avg"] is not None
    assert result["baseline_avg"] is not None
    assert result["recent_avg"] > result["baseline_avg"]


async def test_deterioration_improving_trend() -> None:
    """Downward trend → deterioration=False (negative slope)."""
    session = _session(_no_cc(), _appt(), _rows(*_improving_readings()))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_fewer_than_7_readings() -> None:
    """Fewer than 7 readings → deterioration=False, no slope computed."""
    readings = [_reading_row(_days_ago(i), 155.0) for i in range(5)]
    session = _session(_no_cc(), _appt(), _rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False
    assert result["slope"] is None


async def test_deterioration_no_readings() -> None:
    """Zero readings → deterioration=False."""
    session = _session(_no_cc(), _appt(), _rows())
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_flat_trend() -> None:
    """Flat BP (zero slope) → deterioration=False."""
    readings = [_reading_row(_days_ago(14 - i), 150.0) for i in range(10)]
    session = _session(_no_cc(), _appt(), _rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False
    assert result["slope"] == 0.0


async def test_deterioration_positive_slope_but_recent_not_higher() -> None:
    """Positive overall slope but recent_avg <= baseline_avg → False (both signals required)."""
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
    session = _session(_no_cc(), _appt(), _rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_readings_only_in_recent_window() -> None:
    """All readings in last 3 days → no baseline values → deterioration=False."""
    readings = [_reading_row(_days_ago(i * 0.3), 160.0) for i in range(8)]
    session = _session(_no_cc(), _appt(), _rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_below_threshold_not_flagged() -> None:
    """Worsening trend but recent_avg < 140 → Signal 3 gate blocks → False."""
    # Rising from 115 to 125 — positive slope but normotensive
    readings = _worsening_readings(
        count=10, start_systolic=115.0, end_systolic=125.0, window_days=14
    )
    session = _session(_no_cc(), _appt(), _rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False


async def test_deterioration_step_change_detected() -> None:
    """Acute 15+ mmHg step in 7-day windows → deterioration=True via step-change."""
    # Readings span 28 days: old week at 130, recent week at 148 (delta=18 ≥ 15)
    old_week = [
        _reading_row(_days_ago(21), 130.0),
        _reading_row(_days_ago(20), 131.0),
        _reading_row(_days_ago(19), 130.0),
    ]
    recent_week = [
        _reading_row(_days_ago(5), 148.0),
        _reading_row(_days_ago(3), 150.0),
        _reading_row(_days_ago(1), 149.0),
        _reading_row(_days_ago(0.5), 147.0),
    ]
    # Add enough readings to meet _MIN_READINGS=7
    mid = [_reading_row(_days_ago(12), 138.0)]
    session = _session(_no_cc(), _appt(), _rows(*(old_week + mid + recent_week)))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is True


# ---------------------------------------------------------------------------
# Unit tests for the pure-Python slope helper (imported via backward-compat alias)
# ---------------------------------------------------------------------------


def test_slope_positive_trend() -> None:
    """Perfect linear upward trend → slope == exact rise/run."""
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


# ===========================================================================
# THRESHOLD UTILS unit tests
# ===========================================================================


def test_threshold_default_when_no_history() -> None:
    """No historic BP → threshold = 140.0, mode = default_no_history."""
    from app.services.pattern_engine.threshold_utils import compute_patient_threshold

    threshold, mode = compute_patient_threshold(None)
    assert threshold == 140.0
    assert mode == "default_no_history"


def test_threshold_default_when_fewer_than_3_readings() -> None:
    """Fewer than 3 readings → threshold falls back to 140."""
    from app.services.pattern_engine.threshold_utils import compute_patient_threshold

    threshold, mode = compute_patient_threshold([160, 170])
    assert threshold == 140.0
    assert mode == "default_no_history"


def test_threshold_adaptive_capped_at_145() -> None:
    """Very high historic BP → threshold capped at 145."""
    from app.services.pattern_engine.threshold_utils import compute_patient_threshold

    # mean=190, SD≈0 → mean+1.5*SD=190 → capped at 145
    threshold, mode = compute_patient_threshold([190, 191, 189])
    assert threshold == 145.0
    assert mode == "adaptive"


def test_threshold_adaptive_floored_at_130() -> None:
    """Very low historic BP → threshold floored at 130."""
    from app.services.pattern_engine.threshold_utils import compute_patient_threshold

    threshold, mode = compute_patient_threshold([100, 102, 101])
    assert threshold == 130.0
    assert mode == "adaptive"


def test_comorbidity_adjustment_when_severe_cardio_only() -> None:
    """CHF alone (severe-weight cardio, no metabolic) → -7 mmHg adjustment (Fix 5)."""
    from app.services.pattern_engine.threshold_utils import (
        apply_comorbidity_adjustment,
        classify_comorbidity_concern,
    )

    state = classify_comorbidity_concern(["I50.1"])  # CHF only
    adjusted, mode = apply_comorbidity_adjustment(140.0, state)
    assert adjusted == 133.0   # -7 mmHg: CHF alone is sufficient
    assert "comorbidity_adjusted" in mode


def test_comorbidity_no_adjustment_when_metabolic_only() -> None:
    """Metabolic alone (no severe cardio) → NO threshold adjustment."""
    from app.services.pattern_engine.threshold_utils import (
        apply_comorbidity_adjustment,
        classify_comorbidity_concern,
    )

    state = classify_comorbidity_concern(["E11.9"])  # T2DM only, no CHF/Stroke/TIA
    adjusted, _mode = apply_comorbidity_adjustment(140.0, state)
    assert adjusted == 140.0   # no adjustment — metabolic alone not sufficient


def test_comorbidity_adjustment_when_cardio_and_metabolic() -> None:
    """Both cardio AND metabolic → -7 mmHg adjustment (floor 130)."""
    from app.services.pattern_engine.threshold_utils import (
        apply_comorbidity_adjustment,
        classify_comorbidity_concern,
    )

    state = classify_comorbidity_concern(["I50.1", "E11.9"])  # CHF + T2DM
    adjusted, _mode = apply_comorbidity_adjustment(140.0, state)
    assert adjusted == 133.0


def test_comorbidity_adjustment_respects_floor() -> None:
    """Adjustment does not go below 130 mmHg floor."""
    from app.services.pattern_engine.threshold_utils import (
        apply_comorbidity_adjustment,
        classify_comorbidity_concern,
    )

    state = classify_comorbidity_concern(["I50.1", "E11.9"])
    adjusted, _mode = apply_comorbidity_adjustment(130.0, state)
    assert adjusted == 130.0


def test_get_last_med_change_date_uses_med_history() -> None:
    """get_last_med_change_date returns max date from med_history."""
    from app.services.pattern_engine.threshold_utils import get_last_med_change_date

    med_history = [
        {"name": "Lisinopril", "date": "2026-03-01", "activity": "start"},
        {"name": "Metoprolol", "date": "2026-04-10", "activity": "increased"},
    ]
    result = get_last_med_change_date(med_history, None)
    assert result == date(2026, 4, 10)


def test_get_last_med_change_date_falls_back_to_field() -> None:
    """get_last_med_change_date falls back to last_med_change when no med_history."""
    from app.services.pattern_engine.threshold_utils import get_last_med_change_date

    fallback = date(2025, 12, 1)
    result = get_last_med_change_date(None, fallback)
    assert result == fallback


def test_get_last_med_change_date_returns_none_when_both_empty() -> None:
    """No med_history and no fallback → returns None."""
    from app.services.pattern_engine.threshold_utils import get_last_med_change_date

    assert get_last_med_change_date(None, None) is None


def test_titration_window_beta_blocker() -> None:
    """Beta-blocker (-olol suffix) → 14-day window."""
    from app.services.pattern_engine.threshold_utils import get_titration_window

    history = [{"name": "Metoprolol", "date": "2026-04-10", "activity": "Refill"}]
    assert get_titration_window(history) == 14


def test_titration_window_ace_inhibitor() -> None:
    """ACE inhibitor (-pril suffix) → 28-day window."""
    from app.services.pattern_engine.threshold_utils import get_titration_window

    history = [{"name": "Lisinopril", "date": "2026-04-10", "activity": "add"}]
    assert get_titration_window(history) == 28


def test_titration_window_amlodipine() -> None:
    """Amlodipine → 56-day window (long-acting CCB)."""
    from app.services.pattern_engine.threshold_utils import get_titration_window

    history = [{"name": "Amlodipine 5mg", "date": "2026-04-10", "activity": "add"}]
    assert get_titration_window(history) == 56


def test_titration_window_default_when_no_history() -> None:
    """No med_history → default 42-day window."""
    from app.services.pattern_engine.threshold_utils import get_titration_window

    assert get_titration_window(None) == 42


def test_titration_window_uses_most_recent_drug() -> None:
    """Uses most recent entry's drug class, not earliest."""
    from app.services.pattern_engine.threshold_utils import get_titration_window

    history = [
        {"name": "Lisinopril", "date": "2025-01-01", "activity": "add"},   # ACE → 28d
        {"name": "Amlodipine", "date": "2026-04-10", "activity": "add"},   # CCB → 56d
    ]
    assert get_titration_window(history) == 56


# ===========================================================================
# INERTIA — titration window aware Condition 4 (Fix: beyond-window should fire)
# ===========================================================================


async def test_inertia_med_change_beyond_titration_window_still_fires() -> None:
    """Med change after first elevated but BEYOND titration window → inertia fires.

    First elevated reading 30 days ago; Metoprolol changed 20 days ago.
    Beta-blocker titration window = 14 days.  20 > 14 → beyond window → fires.
    """
    readings = _elevated_readings(count=6, days_back=30.0)
    change_date_str = (date.today() - timedelta(days=20)).isoformat()
    med_history = [
        {"name": "Metoprolol", "rxnorm": "41493", "date": change_date_str, "activity": "increased"},
    ]
    cc_obj = _cc(med_history=med_history, last_med_change=None)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is True


async def test_inertia_med_change_within_titration_window_still_blocks() -> None:
    """Med change after first elevated and WITHIN titration window → blocked.

    First elevated reading 30 days ago; Metoprolol changed 10 days ago.
    Beta-blocker titration window = 14 days.  10 < 14 → within window → blocked.
    """
    readings = _elevated_readings(count=6, days_back=30.0)
    change_date_str = (date.today() - timedelta(days=10)).isoformat()
    med_history = [
        {"name": "Metoprolol", "rxnorm": "41493", "date": change_date_str, "activity": "increased"},
    ]
    cc_obj = _cc(med_history=med_history, last_med_change=None)
    session = _session(_cc_scalar(cc_obj), _appt(), _rows(*readings))
    result = await run_inertia_detector(session, "1091")
    assert result["inertia_detected"] is False


# ===========================================================================
# DETERIORATION — minimum slope gate (Fix: slope >= 0.3 mmHg/day required)
# ===========================================================================


async def test_deterioration_barely_positive_slope_does_not_fire() -> None:
    """A barely positive slope (< 0.3 mmHg/day) is not clinically significant → False.

    Start 145 → end 147 over 14 days: slope ≈ 0.14 mmHg/day.
    All three main signals pass except the new minimum slope gate.
    Step-change sub-detector does not fire (delta < 15 mmHg).
    """
    readings = _worsening_readings(
        count=10, start_systolic=145.0, end_systolic=147.0, window_days=14
    )
    session = _session(_no_cc(), _appt(), _rows(*readings))
    result = await run_deterioration_detector(session, "1091")
    assert result["deterioration"] is False
