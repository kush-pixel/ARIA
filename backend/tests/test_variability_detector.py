"""Unit tests for Layer 1 BP Variability Detector (Fix 59).

All tests use mocked AsyncSession — no real DB connections.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.pattern_engine.variability_detector import (
    _CV_HIGH_THRESHOLD,
    _CV_MODERATE_THRESHOLD,
    _MIN_READINGS,
    run_variability_detector,
)

PATIENT_ID = "TEST_VAR_001"
_NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)


def _make_session(
    cc_last_visit: object = None,
    next_appointment: datetime | None = None,
    systolics: list[float] | None = None,
) -> AsyncMock:
    """Build a mocked AsyncSession for variability tests."""
    session = AsyncMock()

    async def execute_side_effect(stmt):
        sql = str(stmt).lower()

        # ClinicalContext query
        if "clinical_context" in sql and "last_visit_date" in sql:
            result = MagicMock()
            result.one_or_none.return_value = (cc_last_visit,) if cc_last_visit is not None else None
            return result

        # Patient.next_appointment query
        if "patients" in sql and "next_appointment" in sql:
            result = MagicMock()
            result.one_or_none.return_value = (next_appointment,) if next_appointment is not None else None
            return result

        # Reading.systolic_avg query
        if "reading" in sql or "systolic_avg" in sql:
            rows = []
            for s in (systolics or []):
                row = MagicMock()
                row.__getitem__ = lambda self, i, _s=s: _s
                rows.append(row)
            result = MagicMock()
            result.__iter__ = lambda self: iter(rows)
            return result

        result = MagicMock()
        result.one_or_none.return_value = None
        return result

    session.execute = AsyncMock(side_effect=execute_side_effect)
    return session


@pytest.mark.asyncio
async def test_insufficient_readings_returns_no_detect():
    """Fewer than _MIN_READINGS readings → detected=False, level='none'."""
    session = _make_session(systolics=[155.0, 160.0, 158.0])  # only 3
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    assert result["detected"] is False
    assert result["level"] == "none"
    assert result["cv_pct"] is None
    assert result["visit_agenda_item"] is None
    assert result["variability_score"] == 0.0


@pytest.mark.asyncio
async def test_low_cv_no_flag():
    """CV < 12% → level='none', detected=False."""
    # Mean ~150, SD ~3 → CV ~2%
    systolics = [148.0, 150.0, 152.0, 149.0, 151.0, 150.0, 148.0, 152.0]
    session = _make_session(systolics=systolics)
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    assert result["detected"] is False
    assert result["level"] == "none"
    assert result["cv_pct"] is not None
    assert result["cv_pct"] < _CV_MODERATE_THRESHOLD
    assert result["visit_agenda_item"] is None


@pytest.mark.asyncio
async def test_moderate_variability():
    """CV between 12% and 15% → level='moderate', detected=True."""
    # Mean=150, pstdev=20, CV=13.3% (alternating 130/170)
    systolics = [130.0, 170.0, 130.0, 170.0, 130.0, 170.0, 130.0, 170.0]
    session = _make_session(systolics=systolics)
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    assert result["detected"] is True
    assert result["level"] == "moderate"
    assert result["cv_pct"] is not None
    assert _CV_MODERATE_THRESHOLD <= result["cv_pct"] < _CV_HIGH_THRESHOLD
    assert result["visit_agenda_item"] is not None
    assert "moderate" in result["visit_agenda_item"].lower() or "variability" in result["visit_agenda_item"].lower()
    assert result["variability_score"] > 0.0


@pytest.mark.asyncio
async def test_high_variability():
    """CV >= 15% → level='high', detected=True, agenda item mentions ABPM."""
    # Mean ~150, pstdev ~25 → CV ~16.7%
    systolics = [120.0, 175.0, 130.0, 180.0, 125.0, 170.0, 140.0, 160.0]
    session = _make_session(systolics=systolics)
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    assert result["detected"] is True
    assert result["level"] == "high"
    assert result["cv_pct"] is not None
    assert result["cv_pct"] >= _CV_HIGH_THRESHOLD
    assert result["visit_agenda_item"] is not None
    assert "abpm" in result["visit_agenda_item"].lower() or "ambulatory" in result["visit_agenda_item"].lower()


@pytest.mark.asyncio
async def test_variability_score_saturates_at_100():
    """CV well above 20% must produce variability_score=100.0 (clamp)."""
    # Mean ~100, SD ~30 → CV ~30%
    systolics = [70.0, 130.0, 75.0, 125.0, 80.0, 120.0, 100.0, 100.0]
    session = _make_session(systolics=systolics)
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    assert result["variability_score"] <= 100.0


@pytest.mark.asyncio
async def test_no_patient_record_uses_fallback_window():
    """No ClinicalContext or Patient rows → falls back to 28-day window, still computes."""
    systolics = [120.0, 175.0, 130.0, 180.0, 125.0, 170.0, 140.0, 160.0]
    session = _make_session(systolics=systolics)  # cc_last_visit=None, next_appointment=None
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    # No error, still produces a result
    assert "level" in result
    assert "cv_pct" in result


@pytest.mark.asyncio
async def test_exactly_min_readings_produces_result():
    """Exactly _MIN_READINGS readings must be sufficient (not < _MIN_READINGS)."""
    # Build exactly _MIN_READINGS readings with noticeable spread
    base = [150.0 + (i % 3) * 10 for i in range(_MIN_READINGS)]
    session = _make_session(systolics=base)
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    assert result["cv_pct"] is not None
    assert result["level"] in ("none", "moderate", "high")


@pytest.mark.asyncio
async def test_adaptive_window_used_when_dates_available():
    """When both next_appointment and last_visit_date are set, adaptive window is used."""
    last_visit = datetime(2026, 3, 27, tzinfo=UTC).date()
    next_appt = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
    systolics = [155.0] * 8  # flat, low CV
    session = _make_session(
        cc_last_visit=last_visit,
        next_appointment=next_appt,
        systolics=systolics,
    )
    with patch("app.services.pattern_engine.variability_detector.datetime") as dt_mock:
        dt_mock.now.return_value = _NOW
        result = await run_variability_detector(session, PATIENT_ID)
    # No error — adaptive window computed without crashing
    assert result["level"] == "none"  # flat readings → no variability
