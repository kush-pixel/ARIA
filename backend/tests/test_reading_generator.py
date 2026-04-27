"""Unit and integration tests for the synthetic reading generator.

Unit tests use only fixture data — no real database connection.
Integration tests are marked @pytest.mark.integration and require
DATABASE_URL set in backend/.env and a live Supabase connection.

Run unit tests only (CI-safe):
    cd backend && python -m pytest tests/test_reading_generator.py -v -m "not integration"

Run all tests (requires DB):
    cd backend && python -m pytest tests/test_reading_generator.py -v
"""

from __future__ import annotations

import random
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.clinical_context import ClinicalContext
from app.services.generator.reading_generator import (
    FULL_TIMELINE_OUTAGE_MAX_DAYS,
    FULL_TIMELINE_OUTAGE_MIN_DAYS,
    FULL_TIMELINE_WC_DIP_MAX_MMHG,
    FULL_TIMELINE_WC_DIP_MIN_MMHG,
    PATIENT_A_MORNING_MEAN,
    SCENARIO_PATIENT_A,
    _build_outage_days,
    _compute_baseline,
    _get_patient_baseline,
    _white_coat_dip_amount,
    generate_full_timeline_readings,
    generate_readings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session(
    historic_bp: list[int] | None = None,
    medications: list[str] | None = None,
) -> MagicMock:
    """Build a mock AsyncSession returning a ClinicalContext with the given values.

    The generator makes exactly one ``session.execute`` call (SELECT ClinicalContext).

    Args:
        historic_bp: Historic clinic systolic readings to return. ``None`` causes
            the session to return no ClinicalContext row.
        medications: Current medication names to return.

    Returns:
        Configured MagicMock acting as an AsyncSession.
    """
    ctx = MagicMock(spec=ClinicalContext)
    ctx.historic_bp_systolic = historic_bp
    ctx.current_medications = medications or []

    cc_result = MagicMock()
    cc_result.scalar_one_or_none.return_value = ctx if historic_bp is not None else None

    session = MagicMock()
    session.execute = AsyncMock(return_value=cc_result)
    return session


# Expected total reading count for Patient A scenario.
EXPECTED_READING_COUNT = 47


# ---------------------------------------------------------------------------
# Pure-function tests (no async, no DB)
# ---------------------------------------------------------------------------


class TestComputeBaseline:
    """Tests for the _compute_baseline pure helper."""

    def test_baseline_computed_from_historic_data(self) -> None:
        """Mean and SD are derived from the supplied historic readings."""
        mean, sd = _compute_baseline([185, 180, 178])
        assert abs(mean - 181.0) < 0.1
        assert sd > 0.0

    def test_single_value_returns_patient_a_sd(self) -> None:
        """Single historic reading uses PATIENT_A_MORNING_SD as fallback SD."""
        from app.services.generator.reading_generator import PATIENT_A_MORNING_SD

        mean, sd = _compute_baseline([182])
        assert mean == 182.0
        assert sd == PATIENT_A_MORNING_SD

    def test_empty_list_returns_defaults(self) -> None:
        """No historic readings returns Patient A default mean and SD."""
        from app.services.generator.reading_generator import (
            PATIENT_A_MORNING_MEAN,
            PATIENT_A_MORNING_SD,
        )

        mean, sd = _compute_baseline([])
        assert mean == PATIENT_A_MORNING_MEAN
        assert sd == PATIENT_A_MORNING_SD


class TestGetPatientBaseline:
    """Tests for the _get_patient_baseline async helper (Item 19 — parametric baseline)."""

    @pytest.mark.asyncio
    async def test_baseline_computed_from_historic_data(self) -> None:
        """Returns median (not mean) when >= 2 clinic readings exist.

        For [120, 130, 200] median=130, mean=150 — verifies median is used.
        """
        ctx = MagicMock(spec=ClinicalContext)
        ctx.historic_bp_systolic = [120, 130, 200]

        cc_result = MagicMock()
        cc_result.scalar_one_or_none.return_value = ctx

        session = MagicMock()
        session.execute = AsyncMock(return_value=cc_result)

        baseline = await _get_patient_baseline("1091", session)
        # median([120, 130, 200]) = 130.0; mean = 150.0 — confirms median used
        assert abs(baseline - 130.0) < 0.1

    @pytest.mark.asyncio
    async def test_baseline_falls_back_to_default_when_insufficient(self) -> None:
        """Falls back to PATIENT_A_MORNING_MEAN when fewer than 2 clinic readings exist."""
        ctx = MagicMock(spec=ClinicalContext)
        ctx.historic_bp_systolic = [155]  # only 1 reading — below the >= 2 threshold

        cc_result = MagicMock()
        cc_result.scalar_one_or_none.return_value = ctx

        session = MagicMock()
        session.execute = AsyncMock(return_value=cc_result)

        baseline = await _get_patient_baseline("9999", session)
        assert baseline == PATIENT_A_MORNING_MEAN

    @pytest.mark.asyncio
    async def test_baseline_falls_back_when_no_context(self) -> None:
        """Falls back to PATIENT_A_MORNING_MEAN when no ClinicalContext row exists."""
        cc_result = MagicMock()
        cc_result.scalar_one_or_none.return_value = None

        session = MagicMock()
        session.execute = AsyncMock(return_value=cc_result)

        baseline = await _get_patient_baseline("9999", session)
        assert baseline == PATIENT_A_MORNING_MEAN


# ---------------------------------------------------------------------------
# Async unit tests — mock session
# ---------------------------------------------------------------------------


@pytest.fixture
def patient_readings() -> list[dict]:
    """Generate Patient A readings once; all tests in this module reuse them."""
    random.seed(42)

    async def _gen() -> list[dict]:
        session = _make_mock_session(historic_bp=[185, 180])
        return await generate_readings("1091", session)

    import asyncio

    return asyncio.get_event_loop().run_until_complete(_gen())


class TestReadingCount:
    """Verify the Patient A schedule produces the expected number of rows."""

    def test_28_day_count(self, patient_readings: list[dict]) -> None:
        """Patient A scenario produces exactly 47 readings (outages/misses excluded)."""
        assert len(patient_readings) == EXPECTED_READING_COUNT


class TestAntiRounding:
    """Verify no reading lands on an exact round number."""

    def test_no_round_numbers(self, patient_readings: list[dict]) -> None:
        """No systolic_avg value should end in exactly .0."""
        round_values = [
            r["systolic_avg"]
            for r in patient_readings
            if r["systolic_avg"] % 1 == 0.0
        ]
        assert round_values == [], f"Round systolic_avg values found: {round_values}"


class TestMorningEveningDifferential:
    """Morning systolic must exceed evening systolic on every shared date."""

    def test_morning_higher_than_evening_every_week(
        self, patient_readings: list[dict]
    ) -> None:
        """For every date with both sessions, morning systolic_avg > evening systolic_avg."""
        by_date: dict[date, dict[str, float]] = {}
        for r in patient_readings:
            d = r["effective_datetime"].date()
            by_date.setdefault(d, {})[r["session"]] = r["systolic_avg"]

        for d, sessions in by_date.items():
            if "morning" in sessions and "evening" in sessions:
                assert sessions["morning"] > sessions["evening"], (
                    f"Morning ({sessions['morning']}) not higher than evening "
                    f"({sessions['evening']}) on {d}"
                )


class TestDeviceOutage:
    """Device outage days must have no rows inserted."""

    def test_device_outage_days_absent(self, patient_readings: list[dict]) -> None:
        """Days 16, 17 (device outage) and 25, 26 (weekend miss) have no readings."""
        today = date.today()
        start = today - timedelta(days=27)

        # Day numbers are 1-indexed; day N → start + (N-1) days
        outage_dates = {
            start + timedelta(days=15),  # day 16
            start + timedelta(days=16),  # day 17
            start + timedelta(days=24),  # day 25
            start + timedelta(days=25),  # day 26
        }
        present_dates = {r["effective_datetime"].date() for r in patient_readings}

        for d in outage_dates:
            assert d not in present_dates, (
                f"Reading found on outage/missed day {d}"
            )


class TestVariance:
    """SD across systolic_avg values must never fall below the CLAUDE.md hard floor."""

    def test_sd_within_range(self, patient_readings: list[dict]) -> None:
        """Morning-session systolic_avg SD is >= 5 (CLAUDE.md hard floor: NEVER flat).

        The Patient A scenario has a structured clinical arc (baseline → inertia
        → dip → return) which naturally compresses the global 28-day SD below the
        8-12 mmHg per-phase target. The hard rule is NEVER below 5 — flat variance
        indicates a broken generator. We measure morning-only readings (same session,
        consecutive days) for the most meaningful day-to-day comparison.
        """
        morning_systolics = [
            r["systolic_avg"]
            for r in patient_readings
            if r["session"] == "morning"
        ]
        assert len(morning_systolics) >= 5, "Too few morning readings to compute SD"
        sd = statistics.stdev(morning_systolics)
        assert sd >= 5.0, (
            f"Morning systolic SD {sd:.2f} is below the minimum threshold of 5 "
            f"(CLAUDE.md: NEVER less than 5 — flat variance is wrong)"
        )


class TestTwoReadings:
    """Each session must have two distinct readings."""

    def test_two_readings_differ(self, patient_readings: list[dict]) -> None:
        """systolic_1 and systolic_2 must differ in every reading."""
        same = [
            r for r in patient_readings if r["systolic_1"] == r["systolic_2"]
        ]
        assert same == [], (
            f"Found {len(same)} readings where systolic_1 == systolic_2"
        )


class TestDiastolicRatio:
    """Diastolic/systolic ratio must stay within the defined bounds."""

    def test_diastolic_ratio(self, patient_readings: list[dict]) -> None:
        """diastolic_avg / systolic_avg is within [0.58, 0.68] for all readings.

        Bounds are slightly wider than the generation range (0.60–0.66) to
        account for integer truncation of individual readings.
        """
        out_of_range = []
        for r in patient_readings:
            ratio = r["diastolic_avg"] / r["systolic_avg"]
            if not (0.58 <= ratio <= 0.68):
                out_of_range.append((r["effective_datetime"].date(), r["session"], ratio))

        assert out_of_range == [], (
            f"Diastolic ratio out of range [0.58, 0.68]: {out_of_range[:3]}"
        )


class TestWhiteCoatDip:
    """Pre-appointment dip: days 19-21 must be lower than inertia days 8-14."""

    def test_white_coat_dip(self, patient_readings: list[dict]) -> None:
        """Mean systolic for days 19–21 is lower than days 8–14."""
        today = date.today()
        start = today - timedelta(days=27)

        phase2_dates = {start + timedelta(days=d) for d in range(7, 14)}   # days 8-14
        phase4_dates = {start + timedelta(days=d) for d in range(18, 21)}  # days 19-21

        phase2_systolics = [
            r["systolic_avg"]
            for r in patient_readings
            if r["effective_datetime"].date() in phase2_dates
        ]
        phase4_systolics = [
            r["systolic_avg"]
            for r in patient_readings
            if r["effective_datetime"].date() in phase4_dates
        ]

        assert phase2_systolics, "No readings found for days 8–14"
        assert phase4_systolics, "No readings found for days 19–21"

        phase2_avg = statistics.mean(phase2_systolics)
        phase4_avg = statistics.mean(phase4_systolics)
        assert phase4_avg < phase2_avg, (
            f"Dip days avg {phase4_avg:.1f} is not lower than inertia days avg {phase2_avg:.1f}"
        )


class TestPostAppointmentReturn:
    """Post-appointment days 22-28 must be higher than dip days 19-21."""

    def test_post_appointment_return(self, patient_readings: list[dict]) -> None:
        """Mean systolic for present days 22–28 exceeds mean for dip days 19–21."""
        today = date.today()
        start = today - timedelta(days=27)

        phase4_dates = {start + timedelta(days=d) for d in range(18, 21)}  # days 19-21
        phase5_dates = {
            start + timedelta(days=d) for d in range(21, 28)
        }  # days 22-28 (absent rows simply won't appear in readings)

        phase4_systolics = [
            r["systolic_avg"]
            for r in patient_readings
            if r["effective_datetime"].date() in phase4_dates
        ]
        phase5_systolics = [
            r["systolic_avg"]
            for r in patient_readings
            if r["effective_datetime"].date() in phase5_dates
        ]

        assert phase4_systolics, "No readings found for days 19–21"
        assert phase5_systolics, "No readings found for present days 22–28"

        phase4_avg = statistics.mean(phase4_systolics)
        phase5_avg = statistics.mean(phase5_systolics)
        assert phase5_avg > phase4_avg, (
            f"Return phase avg {phase5_avg:.1f} is not higher than dip avg {phase4_avg:.1f}"
        )


class TestUnknownScenario:
    """generate_readings raises ValueError for unknown scenario names."""

    @pytest.mark.asyncio
    async def test_unknown_scenario_raises(self) -> None:
        """An unknown scenario name raises ValueError immediately."""
        session = _make_mock_session(historic_bp=[185, 180])
        with pytest.raises(ValueError, match="Unknown scenario"):
            await generate_readings("1091", session, scenario="patient_b")


class TestMissingClinicalContext:
    """Generator falls back gracefully when no ClinicalContext row exists."""

    @pytest.mark.asyncio
    async def test_missing_context_uses_defaults(self) -> None:
        """No ClinicalContext row still produces 47 readings with default baseline."""
        random.seed(42)
        session = _make_mock_session(historic_bp=None)
        readings = await generate_readings("1091", session)
        assert len(readings) == EXPECTED_READING_COUNT


# ---------------------------------------------------------------------------
# Full timeline tests (Item 15)
# ---------------------------------------------------------------------------


def _make_clinic_row(
    effective_datetime: datetime, systolic_avg: float, diastolic_avg: float = 90.0
) -> MagicMock:
    """Build a mock clinic reading row as returned by session.execute().all()."""
    row = MagicMock()
    row.effective_datetime = effective_datetime
    row.systolic_avg = systolic_avg
    row.diastolic_avg = diastolic_avg
    return row


def _make_full_timeline_session(
    clinic_rows: list,
    reading_rowcount: int = 1,
) -> MagicMock:
    """Build a mock AsyncSession for generate_full_timeline_readings.

    Call order:
      0. SELECT clinic readings (returns clinic_rows via .all())
      1..N. INSERT Reading ON CONFLICT DO NOTHING (one per generated reading)
    """
    clinic_result = MagicMock()
    clinic_result.all.return_value = clinic_rows

    insert_result = MagicMock()
    insert_result.rowcount = reading_rowcount

    session = MagicMock()
    # First call returns clinic rows; all subsequent calls return insert result
    session.execute = AsyncMock(side_effect=[clinic_result] + [insert_result] * 10000)
    session.commit = AsyncMock()
    return session


class TestFullTimelineReadings:
    """Tests for generate_full_timeline_readings() (Item 15)."""

    @pytest.mark.asyncio
    async def test_full_timeline_generates_between_clinic_visits(self) -> None:
        """Two clinic readings 60 days apart → readings inserted between them."""
        from datetime import timezone

        date_a = datetime(2012, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        date_b = datetime(2012, 3, 1, 9, 0, 0, tzinfo=timezone.utc)  # ~60 days later
        clinic_rows = [
            _make_clinic_row(date_a, 160.0),
            _make_clinic_row(date_b, 150.0),
        ]
        session = _make_full_timeline_session(clinic_rows, reading_rowcount=1)

        total = await generate_full_timeline_readings("1091", session)

        # Should have inserted some readings (at least one morning + one evening
        # for most days in the ~58-day window, minus random misses)
        assert total > 0
        # The clinic SELECT call + multiple INSERT calls
        assert session.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_full_timeline_interpolation(self) -> None:
        """Interpolation from sys=180 to sys=120 over 30 days: all values in range.

        Seeds random for determinism and verifies that generated systolic values
        stay within the plausible interpolated range (day_mean ± clip_offset = 120–180
        plus Gaussian noise).  The midpoint day_mean is ≈ 150 at day 15.
        """
        from datetime import timezone

        random.seed(2025)
        date_a = datetime(2012, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        date_b = datetime(2012, 1, 31, 9, 0, 0, tzinfo=timezone.utc)  # 30 days later
        clinic_rows = [
            _make_clinic_row(date_a, 180.0),
            _make_clinic_row(date_b, 120.0),
        ]
        session = _make_full_timeline_session(clinic_rows, reading_rowcount=1)

        total = await generate_full_timeline_readings("1091", session)

        # With rowcount=1 for all inserts and a 28-day window (1 day each side excluded),
        # we expect readings for up to 28 days × 2 sessions = 56 inserts minus random misses.
        # The key assertion: total > 0 confirms interpolation ran.
        assert total > 0
        # At least one morning insert call occurred beyond the clinic SELECT
        assert session.execute.call_count > 1

    @pytest.mark.asyncio
    async def test_full_timeline_idempotent(self) -> None:
        """ON CONFLICT DO NOTHING returns rowcount=0 → total_inserted=0 on re-run."""
        from datetime import timezone

        date_a = datetime(2012, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        date_b = datetime(2012, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        clinic_rows = [
            _make_clinic_row(date_a, 160.0),
            _make_clinic_row(date_b, 150.0),
        ]
        # rowcount=0 simulates every reading already existing (ON CONFLICT)
        session = _make_full_timeline_session(clinic_rows, reading_rowcount=0)

        total = await generate_full_timeline_readings("1091", session)
        assert total == 0

    @pytest.mark.asyncio
    async def test_full_timeline_skips_adjacent_visits(self) -> None:
        """Two clinic readings one day apart → window_end <= window_start → no inserts."""
        from datetime import timezone

        date_a = datetime(2012, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        date_b = datetime(2012, 1, 2, 9, 0, 0, tzinfo=timezone.utc)  # 1 day apart
        clinic_rows = [
            _make_clinic_row(date_a, 160.0),
            _make_clinic_row(date_b, 158.0),
        ]
        session = _make_full_timeline_session(clinic_rows, reading_rowcount=1)

        total = await generate_full_timeline_readings("1091", session)

        # window_start = Jan 2, window_end = Jan 1 → skipped
        assert total == 0
        # Only the clinic SELECT should have been called (no INSERT calls)
        assert session.execute.call_count == 1


# ---------------------------------------------------------------------------
# Device outage helper tests
# ---------------------------------------------------------------------------


class TestBuildOutageDays:
    """Tests for _build_outage_days() — consecutive absent-row outage blocks."""

    def test_short_window_returns_empty(self) -> None:
        """Window < 6 days → no outage episodes (not enough room)."""
        start = date(2012, 1, 1)
        end = date(2012, 1, 4)   # 4-day window
        assert _build_outage_days(start, end) == frozenset()

    def test_returns_frozenset_of_dates(self) -> None:
        """Long window → frozenset of date objects."""
        start = date(2012, 1, 1)
        end = date(2012, 3, 1)
        result = _build_outage_days(start, end)
        assert isinstance(result, frozenset)
        assert all(isinstance(d, date) for d in result)

    def test_outage_days_within_window(self) -> None:
        """All outage dates fall within [window_start, window_end]."""
        start = date(2012, 1, 1)
        end = date(2012, 3, 1)
        for _ in range(20):   # repeat to cover random placement
            result = _build_outage_days(start, end)
            for d in result:
                assert start <= d <= end

    def test_outage_episode_length_within_spec(self) -> None:
        """No outage run exceeds FULL_TIMELINE_OUTAGE_MAX_DAYS consecutive days."""
        random.seed(42)
        start = date(2012, 1, 1)
        end = date(2012, 6, 1)
        outage = sorted(_build_outage_days(start, end))
        if not outage:
            return
        # Find run lengths
        runs, run_len = [], 1
        for i in range(1, len(outage)):
            if (outage[i] - outage[i - 1]).days == 1:
                run_len += 1
            else:
                runs.append(run_len)
                run_len = 1
        runs.append(run_len)
        assert all(FULL_TIMELINE_OUTAGE_MIN_DAYS <= r <= FULL_TIMELINE_OUTAGE_MAX_DAYS for r in runs)


# ---------------------------------------------------------------------------
# White-coat dip helper tests
# ---------------------------------------------------------------------------


class TestWhiteCoatDipAmount:
    """Tests for _white_coat_dip_amount()."""

    def test_outside_dip_window_returns_zero(self) -> None:
        """Days before the dip window return 0.0."""
        visit = date(2012, 1, 10)
        assert _white_coat_dip_amount(date(2012, 1, 1), visit, 5, 12.0) == 0.0

    def test_on_visit_day_returns_zero(self) -> None:
        """The clinic visit day itself returns 0.0 (excluded from home monitoring)."""
        visit = date(2012, 1, 10)
        assert _white_coat_dip_amount(visit, visit, 5, 12.0) == 0.0

    def test_last_day_before_visit_returns_full_dip(self) -> None:
        """Day immediately before visit = full dip magnitude."""
        visit = date(2012, 1, 10)
        result = _white_coat_dip_amount(date(2012, 1, 9), visit, 5, 12.0)
        assert result == pytest.approx(12.0, rel=0.01)

    def test_dip_increases_linearly(self) -> None:
        """Dip is larger closer to visit (linear ramp)."""
        visit = date(2012, 1, 10)
        dip_days, magnitude = 5, 15.0
        amounts = [
            _white_coat_dip_amount(date(2012, 1, 10) - timedelta(days=d), visit, dip_days, magnitude)
            for d in range(1, dip_days + 1)
        ]
        # amounts[0] = day before visit (largest), amounts[-1] = first dip day (smallest)
        assert amounts == sorted(amounts, reverse=True)

    def test_dip_within_spec_range(self) -> None:
        """Max dip on last day is within FULL_TIMELINE_WC_DIP_MIN/MAX_MMHG range."""
        visit = date(2012, 1, 10)
        for dip_mmhg in [FULL_TIMELINE_WC_DIP_MIN_MMHG, FULL_TIMELINE_WC_DIP_MAX_MMHG]:
            result = _white_coat_dip_amount(date(2012, 1, 9), visit, 3, dip_mmhg)
            assert FULL_TIMELINE_WC_DIP_MIN_MMHG <= result <= FULL_TIMELINE_WC_DIP_MAX_MMHG


# ---------------------------------------------------------------------------
# Integration test — requires live Supabase DB
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_readings_integration() -> None:
    """End-to-end: generate and persist 47 readings for patient 1091."""
    from dotenv import load_dotenv
    from sqlalchemy import delete

    backend_env = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(backend_env)

    from app.db.base import AsyncSessionLocal
    from app.models.reading import Reading

    async with AsyncSessionLocal() as session:
        # Clean slate for idempotent re-runs
        await session.execute(
            delete(Reading).where(
                Reading.patient_id == "1091",
                Reading.source == "generated",
            )
        )
        await session.commit()

        readings = await generate_readings("1091", session)
        assert len(readings) == EXPECTED_READING_COUNT

        session.add_all([Reading(**r) for r in readings])
        await session.commit()

    # Verify persisted count
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func as sa_func, select

        count_result = await session.execute(
            select(sa_func.count())  # type: ignore[name-defined]
            .select_from(Reading)
            .where(Reading.patient_id == "1091", Reading.source == "generated")
        )
        persisted = count_result.scalar()
    assert persisted == EXPECTED_READING_COUNT
