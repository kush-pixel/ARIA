"""Unit tests for the Layer 2 risk scorer.

These tests use mocked AsyncSession objects only. They verify query-driven
scoring behavior without requiring a live database.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.clinical_context import ClinicalContext
from app.services.pattern_engine.risk_scorer import compute_risk_score

_DEFAULT_LAST_READING = object()


def _context(
    *,
    historic_bp_systolic: list[int] | None = None,
    last_clinic_systolic: int | None = 140,
    last_med_change: date | None = date.today(),
    problem_codes: list[str] | None = None,
) -> ClinicalContext:
    """Build a minimal ClinicalContext ORM instance for scorer tests."""
    context = ClinicalContext()
    context.patient_id = "1091"
    context.historic_bp_systolic = historic_bp_systolic
    context.last_clinic_systolic = last_clinic_systolic
    context.last_med_change = last_med_change
    context.problem_codes = problem_codes if problem_codes is not None else []
    return context


def _scalar_result(value: object) -> MagicMock:
    """Return a mocked SQLAlchemy result for scalar_one_or_none()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _row_result(*values: object) -> MagicMock:
    """Return a mocked SQLAlchemy result for one()."""
    result = MagicMock()
    result.one.return_value = values
    return result


def _session_for(
    *,
    patient_exists: bool = True,
    context: ClinicalContext | None = None,
    avg_systolic: float | Decimal | None = 140.0,
    last_reading_at: datetime | None | object = _DEFAULT_LAST_READING,
    total_confirmations: int = 28,
    confirmed_count: int = 28,
) -> AsyncMock:
    """Build a mocked session with scorer query responses in call order."""
    if last_reading_at is _DEFAULT_LAST_READING:
        last_reading_at = datetime.now(UTC)

    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _scalar_result("1091" if patient_exists else None),
            _scalar_result(context),
            _scalar_result(avg_systolic),
            _scalar_result(last_reading_at),
            _row_result(total_confirmations, confirmed_count),
            MagicMock(),
        ]
    )
    session.commit = AsyncMock(return_value=None)
    return session


async def test_high_risk_patient() -> None:
    """Elevated BP, long inertia, low adherence produces a high score."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[130, 132, 134],
            last_med_change=date.today() - timedelta(days=180),
            problem_codes=["I10", "E11.9", "I50.9", "N18.3", "E78.5"],
        ),
        avg_systolic=170.0,
        last_reading_at=datetime.now(UTC) - timedelta(days=14),
        total_confirmations=20,
        confirmed_count=1,
    )

    score = await compute_risk_score("1091", session)

    assert score > 70.0


async def test_low_risk_patient() -> None:
    """Controlled BP, recent med change, high adherence produces a low score."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[140, 140, 140],
            last_med_change=date.today(),
            problem_codes=[],
        ),
        avg_systolic=128.0,
        last_reading_at=datetime.now(UTC),
        total_confirmations=28,
        confirmed_count=28,
    )

    score = await compute_risk_score("1091", session)

    assert score < 30.0


async def test_no_readings_neutral_bp() -> None:
    """No recent readings uses neutral BP signal and max gap signal."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[140],
            last_med_change=date.today(),
            problem_codes=[],
        ),
        avg_systolic=None,
        last_reading_at=None,
        total_confirmations=28,
        confirmed_count=28,
    )

    score = await compute_risk_score("1091", session)

    assert score == 30.0


async def test_no_confirmations_neutral_adherence() -> None:
    """No medication confirmations uses neutral adherence signal."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[140],
            last_med_change=date.today(),
            problem_codes=[],
        ),
        avg_systolic=140.0,
        last_reading_at=datetime.now(UTC),
        total_confirmations=0,
        confirmed_count=0,
    )

    score = await compute_risk_score("1091", session)

    assert score == 10.0


async def test_null_last_med_change() -> None:
    """NULL last_med_change is treated as max inertia signal."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[140],
            last_med_change=None,
            problem_codes=[],
        ),
        avg_systolic=140.0,
        last_reading_at=datetime.now(UTC),
        total_confirmations=28,
        confirmed_count=28,
    )

    score = await compute_risk_score("1091", session)

    assert score == 25.0


async def test_no_historic_bp_falls_back_to_clinic() -> None:
    """Missing historic BP uses last_clinic_systolic as the baseline."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=None,
            last_clinic_systolic=150,
            last_med_change=date.today(),
            problem_codes=[],
        ),
        avg_systolic=165.0,
        last_reading_at=datetime.now(UTC),
        total_confirmations=28,
        confirmed_count=28,
    )

    score = await compute_risk_score("1091", session)

    assert score == 15.0


async def test_patient_not_found() -> None:
    """Absent patient_id raises ValueError."""
    session = _session_for(patient_exists=False, context=None)

    with pytest.raises(ValueError, match="Patient not found"):
        await compute_risk_score("missing", session)

    session.commit.assert_not_called()


async def test_score_clamped_0_100() -> None:
    """Extreme inputs still produce a final score within the valid range."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[100],
            last_med_change=date.today() - timedelta(days=1000),
            problem_codes=["A", "B", "C", "D", "E", "F", "G"],
        ),
        avg_systolic=300.0,
        last_reading_at=datetime.now(UTC) - timedelta(days=1000),
        total_confirmations=10,
        confirmed_count=0,
    )

    score = await compute_risk_score("1091", session)

    assert 0.0 <= score <= 100.0
    assert score == 100.0


async def test_persists_to_patients_table() -> None:
    """Score computation updates patients.risk_score and commits."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[130, 130, 130],
            last_med_change=date.today() - timedelta(days=45),
            problem_codes=["I10"],
        ),
        avg_systolic=140.0,
        last_reading_at=datetime.now(UTC) - timedelta(days=7),
        total_confirmations=3,
        confirmed_count=2,
    )

    score = await compute_risk_score("1091", session)

    assert score == 38.67
    assert session.execute.call_count == 6
    update_statement = session.execute.call_args_list[-1].args[0]
    assert "UPDATE patients SET risk_score=:risk_score" in str(update_statement)
    assert update_statement.compile().params["risk_score"] == 38.67
    session.commit.assert_called_once()


@pytest.mark.parametrize(
    ("avg_systolic", "last_med_change", "total_confirmations", "confirmed_count", "last_reading_days", "problem_codes", "expected"),
    [
        (170.0, date.today(), 1, 1, 0, [], 30.0),
        (140.0, date.today() - timedelta(days=90), 1, 1, 0, [], 25.0),
        (140.0, date.today(), 1, 0, 0, [], 20.0),
        (140.0, date.today(), 1, 1, 14, [], 15.0),
        (140.0, date.today(), 1, 1, 0, ["A", "B", "C", "D", "E"], 10.0),
    ],
)
async def test_signal_weights(
    avg_systolic: float,
    last_med_change: date,
    total_confirmations: int,
    confirmed_count: int,
    last_reading_days: int,
    problem_codes: list[str],
    expected: float,
) -> None:
    """A single maxed signal contributes exactly its configured weight."""
    session = _session_for(
        context=_context(
            historic_bp_systolic=[140],
            last_med_change=last_med_change,
            problem_codes=problem_codes,
        ),
        avg_systolic=avg_systolic,
        last_reading_at=datetime.now(UTC) - timedelta(days=last_reading_days),
        total_confirmations=total_confirmations,
        confirmed_count=confirmed_count,
    )

    score = await compute_risk_score("1091", session)

    assert score == expected
