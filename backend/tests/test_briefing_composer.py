"""Unit tests for backend/app/services/briefing/composer.py and summarizer.py.

Unit tests use only fixture data and mocks — no real database or API calls.

Run unit tests only (CI-safe):
    cd backend && python -m pytest tests/test_briefing_composer.py -v -m "not integration"
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.briefing.composer import (
    _bp_category,
    _build_adherence_summary,
    _build_data_limitations,
    _build_long_term_trajectory,
    _build_medication_status,
    _build_trend_summary,
    _build_urgent_flags,
    _build_visit_agenda,
    _compute_adherence,
    compose_briefing,
    compose_mini_briefing,
)
from app.services.briefing.summarizer import (
    _build_user_message,
    _compute_prompt_hash,
    _load_prompt_template,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_reading(systolic_avg: float, diastolic_avg: float, session: str = "morning") -> MagicMock:
    """Create a mock Reading with the given averages."""
    r = MagicMock()
    r.systolic_avg = systolic_avg
    r.diastolic_avg = diastolic_avg
    r.session = session
    r.effective_datetime = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    return r


def _make_confirmation(medication_name: str, confirmed: bool) -> MagicMock:
    """Create a mock MedicationConfirmation with optional confirmed_at."""
    c = MagicMock()
    c.medication_name = medication_name
    c.confirmed_at = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc) if confirmed else None
    c.scheduled_time = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    return c


def _make_alert(alert_type: str, gap_days: int | None = None, systolic_avg: float | None = None) -> MagicMock:
    """Create a mock Alert of the given type."""
    a = MagicMock()
    a.alert_type = alert_type
    a.gap_days = gap_days
    a.systolic_avg = systolic_avg
    a.acknowledged_at = None
    a.triggered_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return a


def _make_patient(
    monitoring_active: bool = True,
    risk_score: float | None = 72.5,
    risk_tier: str = "high",
) -> MagicMock:
    """Create a mock Patient ORM instance."""
    p = MagicMock()
    p.patient_id = "1091"
    p.monitoring_active = monitoring_active
    p.risk_score = risk_score
    p.risk_tier = risk_tier
    p.next_appointment = datetime(2026, 4, 14, 9, 0, tzinfo=timezone.utc)
    p.enrolled_at = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    return p


def _make_clinical_context(
    medications: list[str] | None = None,
    problems: list[str] | None = None,
    problem_codes: list[str] | None = None,
    overdue_labs: list[str] | None = None,
    last_med_change: date | None = None,
    last_clinic_systolic: int | None = 185,
    last_clinic_diastolic: int | None = 72,
    historic_bp_systolic: list[int] | None = None,
    historic_bp_dates: list[str] | None = None,
) -> MagicMock:
    """Create a mock ClinicalContext ORM instance."""
    ctx = MagicMock()
    ctx.patient_id = "1091"
    ctx.current_medications = medications or ["Metoprolol 50mg BID", "Lisinopril 10mg QD", "Lasix 40mg QD"]
    ctx.active_problems = problems or ["Hypertension", "CHF", "T2DM"]
    ctx.problem_codes = problem_codes or ["I10", "I50.9", "E11.9"]
    ctx.overdue_labs = overdue_labs or []
    ctx.last_med_change = last_med_change or date(2026, 1, 15)
    ctx.last_clinic_systolic = last_clinic_systolic
    ctx.last_clinic_diastolic = last_clinic_diastolic
    ctx.historic_bp_systolic = historic_bp_systolic
    ctx.historic_bp_dates = historic_bp_dates
    ctx.social_context = "Lives alone, retired."
    return ctx


# ---------------------------------------------------------------------------
# _bp_category
# ---------------------------------------------------------------------------

class TestBpCategory:
    """Tests for _bp_category()."""

    def test_normal_range(self) -> None:
        assert _bp_category(110.0) == "normal range"

    def test_elevated_range(self) -> None:
        assert _bp_category(125.0) == "elevated range"

    def test_stage1_range(self) -> None:
        assert _bp_category(135.0) == "Stage 1 hypertension range"

    def test_stage2_range(self) -> None:
        assert _bp_category(155.0) == "Stage 2 hypertension range"

    def test_boundary_120(self) -> None:
        assert _bp_category(120.0) == "elevated range"

    def test_boundary_140(self) -> None:
        assert _bp_category(140.0) == "Stage 2 hypertension range"


# ---------------------------------------------------------------------------
# _build_trend_summary
# ---------------------------------------------------------------------------

class TestBuildTrendSummary:
    """Tests for _build_trend_summary()."""

    def test_ehr_only_with_clinic_bp(self) -> None:
        result = _build_trend_summary([], 185, 72, monitoring_active=False)
        assert "185/72" in result
        assert "No home monitoring" in result

    def test_ehr_only_no_clinic_bp(self) -> None:
        result = _build_trend_summary([], None, None, monitoring_active=False)
        assert "EHR data only" in result

    def test_active_no_readings(self) -> None:
        result = _build_trend_summary([], None, None, monitoring_active=True)
        assert "no readings" in result.lower()

    def test_28day_average_included(self) -> None:
        readings = [_make_reading(163.0, 98.0) for _ in range(10)]
        result = _build_trend_summary(readings, 185, 72, monitoring_active=True)
        assert "163" in result
        assert "10 reading sessions" in result

    def test_upward_trend_detected(self) -> None:
        # First 7 readings low, last 7 high — delta > 5
        readings = (
            [_make_reading(150.0, 90.0) for _ in range(7)]
            + [_make_reading(160.0, 96.0) for _ in range(7)]
        )
        result = _build_trend_summary(readings, None, None, monitoring_active=True)
        assert "upward trend" in result

    def test_downward_trend_detected(self) -> None:
        readings = (
            [_make_reading(165.0, 99.0) for _ in range(7)]
            + [_make_reading(150.0, 90.0) for _ in range(7)]
        )
        result = _build_trend_summary(readings, None, None, monitoring_active=True)
        assert "downward trend" in result

    def test_stable_trend(self) -> None:
        readings = [_make_reading(163.0, 98.0) for _ in range(14)]
        result = _build_trend_summary(readings, None, None, monitoring_active=True)
        assert "stable" in result


# ---------------------------------------------------------------------------
# _build_long_term_trajectory — Fix 47
# ---------------------------------------------------------------------------

class TestBuildLongTermTrajectory:
    """Tests for _build_long_term_trajectory()."""

    def test_none_when_both_arrays_none(self) -> None:
        assert _build_long_term_trajectory(None, None) is None

    def test_none_when_systolic_none(self) -> None:
        assert _build_long_term_trajectory(None, ["2026-01-15"]) is None

    def test_none_when_dates_none(self) -> None:
        assert _build_long_term_trajectory([160], None) is None

    def test_none_when_mismatched_lengths(self) -> None:
        assert _build_long_term_trajectory([160, 155], ["2026-01-15"]) is None

    def test_none_when_fewer_than_two_in_window(self) -> None:
        # Only one reading in the 90-day window anchored on max date
        assert _build_long_term_trajectory([160], ["2026-01-15"]) is None

    def test_improvement_trend_large_decline(self) -> None:
        # 170 -> 155 over ~60 days = delta -15, improvement
        result = _build_long_term_trajectory(
            [170, 155],
            ["2026-01-15", "2026-03-15"],
        )
        assert result is not None
        assert "improvement trend" in result
        assert "170" in result
        assert "January" in result

    def test_worsening_trend_large_rise(self) -> None:
        # 140 -> 162 over ~60 days = delta +22, worsening
        result = _build_long_term_trajectory(
            [140, 162],
            ["2026-01-15", "2026-03-15"],
        )
        assert result is not None
        assert "worsening trend" in result
        assert "140" in result

    def test_stable_elevation_small_delta(self) -> None:
        # 158 -> 160 = delta +2, stable
        result = _build_long_term_trajectory(
            [158, 160],
            ["2026-01-15", "2026-03-15"],
        )
        assert result is not None
        assert "stable elevation" in result
        assert "January" in result

    def test_window_anchored_on_max_date_not_today(self) -> None:
        # All readings in 2013 — well outside 90 days of today (2026-04-27)
        # Should still compute trajectory relative to 2013-09-26
        systolics = [170, 165, 160, 158]
        dates = ["2013-01-15", "2013-04-20", "2013-07-10", "2013-09-26"]
        result = _build_long_term_trajectory(systolics, dates)
        # max date is 2013-09-26; 90 days back = 2013-06-27; so 2013-07-10 and 2013-09-26 included
        assert result is not None
        assert "trajectory" in result

    def test_ignores_readings_outside_90day_window(self) -> None:
        # Oldest reading is 200 days before max — should be excluded from window
        # Only last 2 readings (within 90 days) should count
        systolics = [180, 158, 156]
        dates = ["2026-01-01", "2026-03-20", "2026-04-15"]
        # max = 2026-04-15; cutoff = 2026-01-15; so 2026-01-01 is excluded
        result = _build_long_term_trajectory(systolics, dates)
        # Only 2026-03-20 (158) and 2026-04-15 (156) are in window: delta = -2 → stable
        assert result is not None
        assert "stable" in result
        assert "180" not in result  # first reading excluded

    def test_unparseable_dates_skipped(self) -> None:
        # One bad date, two good ones — should still compute
        result = _build_long_term_trajectory(
            [170, 165, 155],
            ["not-a-date", "2026-02-15", "2026-04-15"],
        )
        assert result is not None

    def test_output_starts_with_3_month_trajectory(self) -> None:
        result = _build_long_term_trajectory(
            [170, 155],
            ["2026-01-15", "2026-03-15"],
        )
        assert result is not None
        assert result.startswith("3-month trajectory:")


# ---------------------------------------------------------------------------
# _build_medication_status
# ---------------------------------------------------------------------------

class TestBuildMedicationStatus:
    """Tests for _build_medication_status()."""

    def test_no_medications(self) -> None:
        result = _build_medication_status(None, None)
        assert "No current medications" in result

    def test_medications_with_change_date(self) -> None:
        result = _build_medication_status(
            ["Metoprolol 50mg", "Lisinopril 10mg"], date(2026, 1, 15)
        )
        assert "Metoprolol" in result
        assert "Lisinopril" in result
        assert "2026-01-15" in result
        assert "months ago" in result

    def test_medications_no_change_date(self) -> None:
        result = _build_medication_status(["Aspirin 81mg"], None)
        assert "Aspirin" in result
        assert "No medication change date" in result

    def test_multiple_medications_joined(self) -> None:
        meds = ["Med A", "Med B", "Med C"]
        result = _build_medication_status(meds, None)
        assert "Med A" in result
        assert "Med B" in result
        assert "Med C" in result


# ---------------------------------------------------------------------------
# _compute_adherence
# ---------------------------------------------------------------------------

class TestComputeadherence:
    """Tests for _compute_adherence()."""

    def test_full_adherence(self) -> None:
        confs = [_make_confirmation("Metoprolol", confirmed=True) for _ in range(5)]
        result = _compute_adherence(confs)
        assert result["Metoprolol"]["rate_pct"] == 100.0
        assert result["Metoprolol"]["scheduled"] == 5
        assert result["Metoprolol"]["confirmed"] == 5

    def test_zero_adherence(self) -> None:
        confs = [_make_confirmation("Lisinopril", confirmed=False) for _ in range(4)]
        result = _compute_adherence(confs)
        assert result["Lisinopril"]["rate_pct"] == 0.0

    def test_partial_adherence(self) -> None:
        confs = (
            [_make_confirmation("Metoprolol", confirmed=True) for _ in range(3)]
            + [_make_confirmation("Metoprolol", confirmed=False) for _ in range(1)]
        )
        result = _compute_adherence(confs)
        assert result["Metoprolol"]["rate_pct"] == 75.0

    def test_multiple_medications(self) -> None:
        confs = [
            _make_confirmation("Med A", confirmed=True),
            _make_confirmation("Med A", confirmed=False),
            _make_confirmation("Med B", confirmed=True),
        ]
        result = _compute_adherence(confs)
        assert result["Med A"]["rate_pct"] == 50.0
        assert result["Med B"]["rate_pct"] == 100.0

    def test_empty_confirmations(self) -> None:
        result = _compute_adherence([])
        assert result == {}


# ---------------------------------------------------------------------------
# _build_adherence_summary
# ---------------------------------------------------------------------------

class TestBuildAdherenceSummary:
    """Tests for _build_adherence_summary()."""

    def test_no_monitoring(self) -> None:
        result = _build_adherence_summary([], [], monitoring_active=False)
        assert "not available" in result

    def test_no_confirmations(self) -> None:
        result = _build_adherence_summary([], [], monitoring_active=True)
        assert "No medication confirmation" in result

    def test_high_adherence_high_bp_flags_treatment_review(self) -> None:
        confs = [_make_confirmation("Metoprolol", confirmed=True) for _ in range(10)]
        readings = [_make_reading(163.0, 98.0) for _ in range(10)]
        result = _build_adherence_summary(confs, readings, monitoring_active=True)
        assert "treatment review warranted" in result

    def test_low_adherence_high_bp_flags_adherence_concern(self) -> None:
        confs = (
            [_make_confirmation("Metoprolol", confirmed=True) for _ in range(2)]
            + [_make_confirmation("Metoprolol", confirmed=False) for _ in range(8)]
        )
        readings = [_make_reading(163.0, 98.0) for _ in range(10)]
        result = _build_adherence_summary(confs, readings, monitoring_active=True)
        assert "possible adherence concern" in result

    def test_low_adherence_normal_bp_contextual_review(self) -> None:
        confs = (
            [_make_confirmation("Metoprolol", confirmed=True) for _ in range(2)]
            + [_make_confirmation("Metoprolol", confirmed=False) for _ in range(8)]
        )
        readings = [_make_reading(118.0, 72.0) for _ in range(10)]
        result = _build_adherence_summary(confs, readings, monitoring_active=True)
        assert "contextual review" in result

    def test_clinical_language_never_non_adherent(self) -> None:
        confs = [_make_confirmation("Metoprolol", confirmed=False) for _ in range(10)]
        readings = [_make_reading(163.0, 98.0) for _ in range(10)]
        result = _build_adherence_summary(confs, readings, monitoring_active=True)
        assert "non-adherent" not in result

    def test_rates_included_in_output(self) -> None:
        confs = [_make_confirmation("Metoprolol", confirmed=True) for _ in range(10)]
        result = _build_adherence_summary(confs, [], monitoring_active=True)
        assert "100%" in result
        assert "Metoprolol" in result


# ---------------------------------------------------------------------------
# _build_urgent_flags
# ---------------------------------------------------------------------------

class TestBuildUrgentFlags:
    """Tests for _build_urgent_flags()."""

    def test_empty_alerts(self) -> None:
        assert _build_urgent_flags([]) == []

    def test_gap_urgent(self) -> None:
        flags = _build_urgent_flags([_make_alert("gap_urgent", gap_days=4)])
        assert len(flags) == 1
        assert "4 days" in flags[0]
        assert "urgent threshold" in flags[0]

    def test_gap_briefing(self) -> None:
        flags = _build_urgent_flags([_make_alert("gap_briefing", gap_days=2)])
        assert "2 days" in flags[0]

    def test_inertia_includes_systolic(self) -> None:
        flags = _build_urgent_flags([_make_alert("inertia", systolic_avg=163.5)])
        assert "mmHg" in flags[0]
        assert "no medication change" in flags[0].lower()

    def test_deterioration(self) -> None:
        flags = _build_urgent_flags([_make_alert("deterioration")])
        assert "Deterioration" in flags[0]
        assert "baseline" in flags[0]

    def test_multiple_alerts(self) -> None:
        alerts = [
            _make_alert("gap_urgent", gap_days=5),
            _make_alert("inertia", systolic_avg=160.0),
        ]
        flags = _build_urgent_flags(alerts)
        assert len(flags) == 2


# ---------------------------------------------------------------------------
# _build_visit_agenda
# ---------------------------------------------------------------------------

class TestBuildVisitAgenda:
    """Tests for _build_visit_agenda()."""

    def test_urgent_flags_come_first(self) -> None:
        agenda = _build_visit_agenda(
            urgent_flags=["Gap alert"],
            readings=[_make_reading(163.0, 98.0)],
            confirmations=[],
            active_problems=["Hypertension"],
            overdue_labs=[],
            last_med_change=date(2026, 1, 1),
            monitoring_active=True,
        )
        assert agenda[0].startswith("URGENT")

    def test_max_six_items(self) -> None:
        agenda = _build_visit_agenda(
            urgent_flags=["Flag 1", "Flag 2", "Flag 3"],
            readings=[_make_reading(163.0, 98.0) for _ in range(10)],
            confirmations=[_make_confirmation("Med", confirmed=False) for _ in range(10)],
            active_problems=["HTN", "CHF", "T2DM"],
            overdue_labs=["HbA1c", "Renal panel"],
            last_med_change=date(2026, 1, 1),
            monitoring_active=True,
        )
        assert len(agenda) <= 6

    def test_inertia_flagged_in_agenda(self) -> None:
        readings = [_make_reading(163.0, 98.0) for _ in range(10)]
        agenda = _build_visit_agenda(
            urgent_flags=[],
            readings=readings,
            confirmations=[],
            active_problems=[],
            overdue_labs=[],
            last_med_change=date(2025, 12, 1),  # > 7 days ago
            monitoring_active=True,
        )
        assert any("treatment plan" in item.lower() for item in agenda)

    def test_overdue_labs_included(self) -> None:
        agenda = _build_visit_agenda(
            urgent_flags=[],
            readings=[],
            confirmations=[],
            active_problems=[],
            overdue_labs=["HbA1c"],
            last_med_change=None,
            monitoring_active=False,
        )
        assert any("HbA1c" in item for item in agenda)

    def test_next_appointment_always_present(self) -> None:
        agenda = _build_visit_agenda(
            urgent_flags=[],
            readings=[],
            confirmations=[],
            active_problems=[],
            overdue_labs=[],
            last_med_change=None,
            monitoring_active=False,
        )
        assert any("review date" in item.lower() for item in agenda)

    def test_ehr_only_no_inertia_flag(self) -> None:
        # No readings so inertia cannot be flagged
        agenda = _build_visit_agenda(
            urgent_flags=[],
            readings=[],
            confirmations=[],
            active_problems=["Hypertension"],
            overdue_labs=[],
            last_med_change=date(2025, 1, 1),
            monitoring_active=False,
        )
        assert not any("treatment plan" in item.lower() for item in agenda)


# ---------------------------------------------------------------------------
# _build_data_limitations
# ---------------------------------------------------------------------------

class TestBuildDataLimitations:
    """Tests for _build_data_limitations()."""

    def test_ehr_only(self) -> None:
        result = _build_data_limitations([], monitoring_active=False)
        assert "EHR-only" in result

    def test_active_no_readings(self) -> None:
        result = _build_data_limitations([], monitoring_active=True)
        assert "no readings" in result.lower()

    def test_limited_readings(self) -> None:
        readings = [MagicMock() for _ in range(8)]
        result = _build_data_limitations(readings, monitoring_active=True)
        assert "8 sessions" in result
        assert "caution" in result
        assert "synthetic" in result

    def test_sufficient_readings(self) -> None:
        readings = [MagicMock() for _ in range(20)]
        result = _build_data_limitations(readings, monitoring_active=True)
        assert "20 sessions" in result
        assert "caution" not in result
        assert "synthetic" in result


# ---------------------------------------------------------------------------
# compose_briefing (async, mocked DB)
# ---------------------------------------------------------------------------

class TestComposeBriefing:
    """Tests for compose_briefing() with mocked AsyncSession."""

    def _make_session(
        self,
        patient: MagicMock,
        ctx: MagicMock,
        readings: list,
        alerts: list,
        confirmations: list,
    ) -> AsyncMock:
        """Build a mock AsyncSession with pre-configured execute side effects."""
        session = AsyncMock()

        def _scalar(val: MagicMock) -> MagicMock:
            result = MagicMock()
            result.scalar_one_or_none.return_value = val
            return result

        def _scalars(vals: list) -> MagicMock:
            result = MagicMock()
            scalars = MagicMock()
            scalars.all.return_value = vals
            result.scalars.return_value = scalars
            return result

        session.execute.side_effect = [
            _scalar(patient),    # Patient query
            _scalar(ctx),        # ClinicalContext query
            _scalars(readings),  # Readings query
            _scalars(alerts),    # Alerts query
            _scalars(confirmations),  # Confirmations query
        ]
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_returns_briefing_with_all_9_fields(self) -> None:
        patient = _make_patient()
        ctx = _make_clinical_context()
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))

        payload = briefing.llm_response
        assert "trend_summary" in payload
        assert "medication_status" in payload
        assert "adherence_summary" in payload
        assert "active_problems" in payload
        assert "overdue_labs" in payload
        assert "visit_agenda" in payload
        assert "urgent_flags" in payload
        assert "risk_score" in payload
        assert "data_limitations" in payload

    @pytest.mark.asyncio
    async def test_raises_if_patient_not_found(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute.return_value = result

        with pytest.raises(ValueError, match="not found"):
            await compose_briefing(session, "MISSING", date(2026, 4, 14))

    @pytest.mark.asyncio
    async def test_raises_if_clinical_context_missing(self) -> None:
        session = AsyncMock()
        patient = _make_patient()

        def _scalar(val: MagicMock) -> MagicMock:
            r = MagicMock()
            r.scalar_one_or_none.return_value = val
            return r

        session.execute.side_effect = [_scalar(patient), _scalar(None)]

        with pytest.raises(ValueError, match="Clinical context"):
            await compose_briefing(session, "1091", date(2026, 4, 14))

    @pytest.mark.asyncio
    async def test_risk_score_in_payload(self) -> None:
        patient = _make_patient(risk_score=87.3)
        ctx = _make_clinical_context()
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))
        assert briefing.llm_response["risk_score"] == pytest.approx(87.3)

    @pytest.mark.asyncio
    async def test_null_risk_score_handled(self) -> None:
        patient = _make_patient(risk_score=None)
        ctx = _make_clinical_context()
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))
        assert briefing.llm_response["risk_score"] is None

    @pytest.mark.asyncio
    async def test_audit_event_written(self) -> None:
        patient = _make_patient()
        ctx = _make_clinical_context()
        session = self._make_session(patient, ctx, [], [], [])

        await compose_briefing(session, "1091", date(2026, 4, 14))

        assert session.flush.called
        assert session.commit.called
        # Two adds: briefing + audit
        assert session.add.call_count == 2

    @pytest.mark.asyncio
    async def test_ehr_only_patient_briefing(self) -> None:
        patient = _make_patient(monitoring_active=False)
        ctx = _make_clinical_context()
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))

        assert "No home monitoring" in briefing.llm_response["trend_summary"]
        assert "EHR-only" in briefing.llm_response["data_limitations"]

    @pytest.mark.asyncio
    async def test_active_problems_list_in_payload(self) -> None:
        patient = _make_patient()
        ctx = _make_clinical_context(problems=["Hypertension", "CHF"], problem_codes=["I10", "I50.9"])
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))
        assert "Hypertension" in briefing.llm_response["active_problems"]
        assert "CHF" in briefing.llm_response["active_problems"]

    @pytest.mark.asyncio
    async def test_overdue_labs_list_in_payload(self) -> None:
        patient = _make_patient()
        ctx = _make_clinical_context(overdue_labs=["HbA1c", "Renal panel"])
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))
        assert "HbA1c" in briefing.llm_response["overdue_labs"]

    @pytest.mark.asyncio
    async def test_long_term_trajectory_appended_when_historic_data_present(self) -> None:
        """Fix 47: 3-month trajectory from clinic BP history appears in trend_summary."""
        patient = _make_patient()
        ctx = _make_clinical_context(
            historic_bp_systolic=[170, 158],
            historic_bp_dates=["2026-01-15", "2026-03-20"],
        )
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))
        assert "3-month trajectory" in briefing.llm_response["trend_summary"]

    @pytest.mark.asyncio
    async def test_no_trajectory_when_historic_data_absent(self) -> None:
        """Fix 47: trend_summary is unchanged when historic_bp_systolic is None."""
        patient = _make_patient()
        ctx = _make_clinical_context(
            historic_bp_systolic=None,
            historic_bp_dates=None,
        )
        session = self._make_session(patient, ctx, [], [], [])

        briefing = await compose_briefing(session, "1091", date(2026, 4, 14))
        assert "3-month trajectory" not in briefing.llm_response["trend_summary"]


# ---------------------------------------------------------------------------
# Summarizer helpers
# ---------------------------------------------------------------------------

class TestSummarizerHelpers:
    """Tests for summarizer.py helper functions."""

    def test_load_prompt_template_returns_string(self) -> None:
        prompt = _load_prompt_template()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_prompt_contains_clinical_language_rules(self) -> None:
        prompt = _load_prompt_template()
        assert "possible adherence concern" in prompt
        assert "treatment review warranted" in prompt

    def test_compute_prompt_hash_is_64_chars(self) -> None:
        h = _compute_prompt_hash("test prompt")
        assert len(h) == 64

    def test_compute_prompt_hash_is_deterministic(self) -> None:
        h1 = _compute_prompt_hash("same prompt")
        h2 = _compute_prompt_hash("same prompt")
        assert h1 == h2

    def test_compute_prompt_hash_differs_on_change(self) -> None:
        h1 = _compute_prompt_hash("prompt v1")
        h2 = _compute_prompt_hash("prompt v2")
        assert h1 != h2

    def test_build_user_message_includes_all_fields(self) -> None:
        payload = {
            "trend_summary": "163/98 average",
            "medication_status": "Metoprolol, Lisinopril",
            "adherence_summary": "91% overall",
            "active_problems": ["Hypertension", "CHF"],
            "overdue_labs": ["HbA1c"],
            "urgent_flags": ["Inertia flag"],
            "risk_score": 82.5,
            "data_limitations": "20 sessions",
        }
        msg = _build_user_message(payload)
        assert "163/98" in msg
        assert "Metoprolol" in msg
        assert "91%" in msg
        assert "Hypertension" in msg
        assert "HbA1c" in msg
        assert "Inertia" in msg
        assert "82.5" in msg

    def test_build_user_message_handles_empty_lists(self) -> None:
        payload = {
            "trend_summary": "No data",
            "medication_status": "None",
            "adherence_summary": "None",
            "active_problems": [],
            "overdue_labs": [],
            "urgent_flags": [],
            "risk_score": None,
            "data_limitations": "EHR only",
        }
        msg = _build_user_message(payload)
        assert "None" in msg
        assert "not calculated" in msg


# ---------------------------------------------------------------------------
# compose_mini_briefing — Fix 46
# ---------------------------------------------------------------------------

def _make_async_session_for_mini(
    patient: MagicMock,
    ctx: MagicMock,
    readings: list,
    alerts: list,
    existing_briefing: MagicMock | None = None,
) -> AsyncMock:
    """Build an AsyncMock session that returns fixture data for compose_mini_briefing."""
    session = AsyncMock()

    def _make_scalar_result(value):
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        return r

    def _make_scalars_result(items):
        r = MagicMock()
        inner = MagicMock()
        inner.all = MagicMock(return_value=items)
        r.scalars = MagicMock(return_value=inner)
        return r

    # execute() is called 5 times: dedup check, patient, ctx, readings, alerts
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(existing_briefing),  # dedup check
        _make_scalar_result(patient),            # patient fetch
        _make_scalar_result(ctx),                # ctx fetch
        _make_scalars_result(readings),          # readings
        _make_scalars_result(alerts),            # alerts
    ])
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


class TestComposeMinieBriefing:
    """Tests for compose_mini_briefing() — Fix 46."""

    @pytest.mark.asyncio
    async def test_compose_mini_briefing_sets_appointment_date_none(self) -> None:
        """appointment_date must be None on the persisted Briefing row."""
        patient = _make_patient()
        ctx = _make_clinical_context()
        readings = [_make_reading(158.0, 96.0)]
        alerts = [_make_alert("gap_urgent", gap_days=4)]
        session = _make_async_session_for_mini(patient, ctx, readings, alerts)

        result = await compose_mini_briefing(session, "1091", "gap_urgent")

        assert result.appointment_date is None

    @pytest.mark.asyncio
    async def test_compose_mini_briefing_trigger_appears_in_urgent_flags(self) -> None:
        """The triggering gap_urgent alert must appear in urgent_flags payload."""
        patient = _make_patient()
        ctx = _make_clinical_context()
        readings = [_make_reading(162.0, 99.0)]
        alerts = [_make_alert("gap_urgent", gap_days=5)]
        session = _make_async_session_for_mini(patient, ctx, readings, alerts)

        result = await compose_mini_briefing(session, "1091", "gap_urgent")

        payload = result.llm_response
        assert any("gap" in f.lower() for f in payload["urgent_flags"])

    @pytest.mark.asyncio
    async def test_compose_mini_briefing_deduplicates_same_day(self) -> None:
        """Second call on the same day returns the existing row, no new insert."""
        patient = _make_patient()
        ctx = _make_clinical_context()
        existing = MagicMock()
        existing.briefing_id = "existing-mini-001"

        session = AsyncMock()

        def _make_scalar_result(value):
            r = MagicMock()
            r.scalar_one_or_none = MagicMock(return_value=value)
            return r

        # Only the dedup check execute is needed — returns existing row
        session.execute = AsyncMock(return_value=_make_scalar_result(existing))
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.add = MagicMock()

        result = await compose_mini_briefing(session, "1091", "gap_urgent")

        assert result is existing
        session.add.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_compose_mini_briefing_uses_7day_window(self) -> None:
        """trend_summary must reference 7-day window, not 28-day."""
        patient = _make_patient()
        ctx = _make_clinical_context()
        readings = [_make_reading(160.0, 97.0) for _ in range(5)]
        alerts = [_make_alert("deterioration")]
        session = _make_async_session_for_mini(patient, ctx, readings, alerts)

        result = await compose_mini_briefing(session, "1091", "deterioration")

        payload = result.llm_response
        assert "7-day" in payload["trend_summary"]
        assert "28-day" not in payload["trend_summary"]
