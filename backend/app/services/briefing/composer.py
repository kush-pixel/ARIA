"""Deterministic Layer 1 briefing composer for ARIA.

Queries the database to assemble the structured pre-visit briefing JSON for a
patient's appointment day.  This module implements Layer 1 of the three-layer
AI architecture — pure deterministic logic, no LLM.

The Layer 3 LLM summariser (summarizer.py) may optionally be called AFTER this
composer has produced and persisted a verified briefing.  Never call Layer 3
before Layer 1 is complete.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.patient import Patient
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ── Clinical thresholds ────────────────────────────────────────────────────────
_ELEVATED_SYSTOLIC: float = 140.0       # mmHg — ESH/AHA hypertension threshold
_ADHERENCE_THRESHOLD: float = 80.0     # % — clinical low-adherence flag
_INERTIA_DAYS: int = 7                  # days of elevation before inertia flag
_TREND_MIN_READINGS: int = 7            # minimum readings needed for trend comparison


# ── Internal helpers ───────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    """Return current UTC-aware datetime."""
    return datetime.now(tz=UTC)


def _bp_category(systolic: float) -> str:
    """Return a plain-English BP category label for a systolic value.

    Args:
        systolic: Mean systolic BP in mmHg.

    Returns:
        Human-readable category string.
    """
    if systolic < 120:
        return "normal range"
    if systolic < 130:
        return "elevated range"
    if systolic < 140:
        return "Stage 1 hypertension range"
    return "Stage 2 hypertension range"


def _build_trend_summary(
    readings: list[Reading],
    last_clinic_systolic: int | None,
    last_clinic_diastolic: int | None,
    monitoring_active: bool,
    window_days: int = 28,
) -> str:
    """Describe the home BP trend in plain clinical language.

    Args:
        readings: List of Reading rows from the window, sorted ascending.
        last_clinic_systolic: Most recent in-clinic systolic from EHR.
        last_clinic_diastolic: Most recent in-clinic diastolic from EHR.
        monitoring_active: Whether the patient has home monitoring active.
        window_days: Size of the trend window in days (default 28).

    Returns:
        Trend summary string for the briefing payload.
    """
    if not monitoring_active:
        if last_clinic_systolic and last_clinic_diastolic:
            return (
                f"No home monitoring. Last clinic reading: "
                f"{last_clinic_systolic}/{last_clinic_diastolic} mmHg."
            )
        return "No home monitoring data available. EHR data only."

    if not readings:
        return f"Home monitoring active but no readings received in the past {window_days} days."

    systolics = [float(r.systolic_avg) for r in readings]
    diastolics = [float(r.diastolic_avg) for r in readings]

    avg_sys = statistics.mean(systolics)
    avg_dia = statistics.mean(diastolics)
    n = len(readings)
    category = _bp_category(avg_sys)

    # Trend direction — compare first 7 vs last 7 sessions
    trend = ""
    if n >= _TREND_MIN_READINGS:
        early_mean = statistics.mean(systolics[:7])
        late_mean = statistics.mean(systolics[-7:])
        delta = late_mean - early_mean
        if delta >= 5:
            trend = " Readings show an upward trend over the period."
        elif delta <= -5:
            trend = " Readings show a downward trend over the period."
        else:
            trend = " Readings have been relatively stable."

    return (
        f"{window_days}-day home average: {avg_sys:.0f}/{avg_dia:.0f} mmHg "
        f"({category}) based on {n} reading sessions.{trend}"
    )


def _build_long_term_trajectory(
    historic_bp_systolic: list[int] | None,
    historic_bp_dates: list[str] | None,
) -> str | None:
    """Compute a 90-day clinic BP trajectory from the historic EHR reading arrays.

    Anchors the 90-day window on the most recent clinic date (not today), so
    patients with older EHR data still produce a meaningful trajectory.

    Returns a sentence for appending to trend_summary, or None if fewer than
    two clinic readings exist in the 90-day window.

    Args:
        historic_bp_systolic: Array of clinic systolic values (parallel to dates).
        historic_bp_dates: ISO-8601 date strings, parallel to historic_bp_systolic.

    Returns:
        Trajectory sentence string, or None when insufficient data.
    """
    if not historic_bp_systolic or not historic_bp_dates:
        return None
    if len(historic_bp_systolic) != len(historic_bp_dates):
        return None

    # Parse all dates, dropping any unparseable entries
    pairs: list[tuple[date, int]] = []
    for sys_val, date_str in zip(historic_bp_systolic, historic_bp_dates, strict=False):
        try:
            d = date.fromisoformat(date_str)
            pairs.append((d, sys_val))
        except (ValueError, AttributeError):
            continue

    if not pairs:
        return None

    # Anchor window on the most recent clinic date (handles historical patients)
    max_date = max(d for d, _ in pairs)
    cutoff = max_date - timedelta(days=90)
    window_pairs = [(d, s) for d, s in pairs if d >= cutoff]

    if len(window_pairs) < 2:
        return None

    window_pairs.sort(key=lambda x: x[0])
    earliest_date, earliest_sys = window_pairs[0]
    _, latest_sys = window_pairs[-1]

    month_name = earliest_date.strftime("%B")
    delta = latest_sys - earliest_sys

    if delta <= -5:
        return (
            f"3-month trajectory: declining from {earliest_sys} in {month_name} "
            f"— improvement trend."
        )
    if delta >= 5:
        return (
            f"3-month trajectory: rising from {earliest_sys} in {month_name} "
            f"— worsening trend."
        )
    return f"3-month trajectory: stable elevation since {month_name}."


_PROBLEM_PRIORITY: dict[str, int] = {
    "I50": 0,  # CHF — highest clinical priority
    "I10": 1,  # Hypertension
    "E11": 2,  # Type 2 Diabetes
    "I25": 3,  # CAD
}


def _sort_problems(problems: list[str], codes: list[str]) -> list[str]:
    """Sort active problems by clinical priority, then alphabetically.

    Args:
        problems: Human-readable problem names from clinical_context.active_problems.
        codes: Parallel ICD-10/SNOMED codes from clinical_context.problem_codes.

    Returns:
        Problem names reordered so high-priority conditions appear first.
    """
    paired = list(zip(problems, codes, strict=False))

    def _key(pair: tuple[str, str]) -> tuple[int, str]:
        _, code = pair
        prefix = code[:3] if code else ""
        return (_PROBLEM_PRIORITY.get(prefix, 99), pair[0])

    return [p for p, _ in sorted(paired, key=_key)]


def _human_duration(days: int) -> str:
    """Convert a day count into a human-readable duration string.

    Args:
        days: Number of days elapsed.

    Returns:
        Natural language string such as "about 3 months ago".
    """
    if days < 1:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    if days < 60:
        return "about a month ago"
    if days < 365:
        months = days // 30
        return f"about {months} months ago"
    years = days // 365
    months = (days % 365) // 30
    if months > 0:
        return f"about {years} year{'s' if years > 1 else ''} and {months} month{'s' if months > 1 else ''} ago"
    return f"about {years} year{'s' if years > 1 else ''} ago"


def _build_medication_status(
    current_medications: list[str] | None,
    last_med_change: date | None,
) -> str:
    """Describe the current medication regimen and last recorded change date.

    Args:
        current_medications: List of medication name strings from clinical_context.
        last_med_change: Date of most recent MedicationRequest change from EHR.

    Returns:
        Medication status string for the briefing payload.
    """
    if not current_medications:
        return "No current medications recorded in EHR."

    meds = ", ".join(current_medications)
    if last_med_change:
        days_since = (date.today() - last_med_change).days
        return (
            f"Current regimen: {meds}. "
            f"Last recorded medication change: {last_med_change.isoformat()} "
            f"({_human_duration(days_since)})."
        )
    return f"Current regimen: {meds}. No medication change date recorded in EHR."


def _compute_adherence(
    confirmations: list[MedicationConfirmation],
) -> dict[str, dict[str, Any]]:
    """Compute per-medication adherence rates from confirmation records.

    Args:
        confirmations: All MedicationConfirmation rows for the period.

    Returns:
        Dict keyed by medication_name with keys: scheduled, confirmed, rate_pct.
    """
    by_med: dict[str, dict[str, int]] = {}
    for conf in confirmations:
        med = conf.medication_name
        if med not in by_med:
            by_med[med] = {"scheduled": 0, "confirmed": 0}
        by_med[med]["scheduled"] += 1
        if conf.confirmed_at is not None:
            by_med[med]["confirmed"] += 1

    result: dict[str, dict[str, Any]] = {}
    for med, counts in by_med.items():
        scheduled = counts["scheduled"]
        confirmed = counts["confirmed"]
        rate = (confirmed / scheduled * 100.0) if scheduled > 0 else 0.0
        result[med] = {
            "scheduled": scheduled,
            "confirmed": confirmed,
            "rate_pct": round(rate, 1),
        }
    return result


def _build_adherence_summary(
    confirmations: list[MedicationConfirmation],
    readings: list[Reading],
    monitoring_active: bool,
) -> str:
    """Describe per-medication adherence rates using clinical language.

    Uses "possible adherence concern" not "non-adherent" per CLAUDE.md.

    Args:
        confirmations: Medication confirmation rows for the 28-day period.
        readings: Reading rows for the 28-day period (used for pattern context).
        monitoring_active: Whether home monitoring is active for this patient.

    Returns:
        Adherence summary string for the briefing payload.
    """
    if not monitoring_active:
        return "Medication confirmation data not available (home monitoring not active)."

    if not confirmations:
        return "No medication confirmation data available for this period."

    adherence = _compute_adherence(confirmations)
    lines = [
        f"{med}: {data['rate_pct']:.0f}% "
        f"({data['confirmed']}/{data['scheduled']} doses confirmed)"
        for med, data in adherence.items()
    ]
    overall_scheduled = sum(d["scheduled"] for d in adherence.values())
    overall_confirmed = sum(d["confirmed"] for d in adherence.values())
    overall_rate = (
        (overall_confirmed / overall_scheduled * 100.0) if overall_scheduled > 0 else 0.0
    )

    summary = "; ".join(lines) + f". Overall confirmation rate: {overall_rate:.0f}%."

    # Pattern interpretation — clinical language enforced
    if readings:
        avg_sys = statistics.mean(float(r.systolic_avg) for r in readings)
        if overall_rate < _ADHERENCE_THRESHOLD and avg_sys >= _ELEVATED_SYSTOLIC:
            summary += (
                " Pattern suggests possible adherence concern alongside elevated readings."
            )
        elif overall_rate < _ADHERENCE_THRESHOLD and avg_sys < _ELEVATED_SYSTOLIC:
            summary += " Low confirmation rate with controlled readings — contextual review."
        elif overall_rate >= _ADHERENCE_THRESHOLD and avg_sys >= _ELEVATED_SYSTOLIC:
            summary += (
                " High confirmation rate with sustained elevated readings — "
                "possible treatment review warranted."
            )

    return summary


def _build_urgent_flags(alerts: list[Alert]) -> list[str]:
    """Convert unacknowledged Alert rows into human-readable flag strings.

    Args:
        alerts: Unacknowledged Alert ORM instances for this patient.

    Returns:
        List of flag strings for the urgent_flags briefing field.
    """
    flags: list[str] = []
    for alert in alerts:
        if alert.alert_type == "gap_urgent":
            days = alert.gap_days or "unknown number of"
            flags.append(f"Reading gap: {days} days without home BP data (urgent threshold).")
        elif alert.alert_type == "gap_briefing":
            days = alert.gap_days or "unknown number of"
            flags.append(f"Reading gap: {days} days without home BP data.")
        elif alert.alert_type == "inertia":
            sys_str = f"{alert.systolic_avg:.0f}" if alert.systolic_avg else "elevated"
            flags.append(
                f"Therapeutic inertia: sustained {sys_str} mmHg average systolic "
                f"with no medication change recorded."
            )
        elif alert.alert_type == "deterioration":
            flags.append(
                "Deterioration flag: sustained worsening trend relative to personal baseline."
            )
    return flags


def _build_visit_agenda(
    urgent_flags: list[str],
    readings: list[Reading],
    confirmations: list[MedicationConfirmation],
    active_problems: list[str] | None,
    overdue_labs: list[str] | None,
    last_med_change: date | None,
    monitoring_active: bool,
) -> list[str]:
    """Build a prioritised visit agenda (up to 6 items).

    Priority order per CLAUDE.md:
    1. Urgent alerts
    2. Inertia flag (elevated BP + no med change > 7 days)
    3. Adherence concern
    4. Overdue labs
    5. Active problems review
    6. Next appointment recommendation

    Args:
        urgent_flags: Pre-built urgent flag strings from _build_urgent_flags.
        readings: 28-day reading rows.
        confirmations: 28-day medication confirmation rows.
        active_problems: Problem list from clinical context.
        overdue_labs: Overdue lab list from clinical context.
        last_med_change: Date of last medication change from clinical context.
        monitoring_active: Whether home monitoring is active.

    Returns:
        Ordered list of visit agenda item strings (max 6).
    """
    agenda: list[str] = []

    # 1. Urgent alerts
    for flag in urgent_flags:
        agenda.append(f"URGENT: {flag}")
        if len(agenda) >= 6:
            return agenda

    # 2. Therapeutic inertia
    if monitoring_active and readings:
        avg_sys = statistics.mean(float(r.systolic_avg) for r in readings)
        if avg_sys >= _ELEVATED_SYSTOLIC and last_med_change is not None:
            days_since = (date.today() - last_med_change).days
            if days_since > _INERTIA_DAYS:
                agenda.append(
                    f"Review treatment plan: 28-day average systolic {avg_sys:.0f} mmHg "
                    f"with no recorded medication change ({_human_duration(days_since)})."
                )
        elif avg_sys >= _ELEVATED_SYSTOLIC and last_med_change is None:
            agenda.append(
                f"Review treatment plan: 28-day average systolic {avg_sys:.0f} mmHg "
                f"with no medication change date recorded."
            )

    # 3. Adherence concern
    if monitoring_active and confirmations:
        adherence = _compute_adherence(confirmations)
        overall_scheduled = sum(d["scheduled"] for d in adherence.values())
        overall_confirmed = sum(d["confirmed"] for d in adherence.values())
        overall_rate = (
            (overall_confirmed / overall_scheduled * 100.0) if overall_scheduled > 0 else 0.0
        )
        if overall_rate < _ADHERENCE_THRESHOLD:
            agenda.append(
                f"Discuss possible adherence concern: {overall_rate:.0f}% overall "
                f"confirmation rate across all medications in the past 28 days."
            )

    # 4. Overdue labs / pending clinical follow-ups
    # This list includes actual lab orders and other clinical follow-up items
    # (referrals, protocols) — use neutral language that covers both.
    if overdue_labs:
        for lab in overdue_labs:
            if len(agenda) >= 5:
                break
            agenda.append(f"Pending follow-up: {lab}.")

    # 5. Active problems review
    if active_problems and len(agenda) < 5:
        problems_str = ", ".join(active_problems)
        agenda.append(f"Review active conditions: {problems_str}.")

    # 6. Next appointment recommendation
    if len(agenda) < 6:
        agenda.append("Confirm next monitoring review date with patient.")

    return agenda[:6]


def _build_data_limitations(
    readings: list[Reading],
    monitoring_active: bool,
    window_days: int = 28,
    mini_briefing: bool = False,
) -> str:
    """Describe any data quality or availability limitations for this briefing.

    Args:
        readings: Reading rows for the window period (may be empty).
        monitoring_active: Whether home monitoring is active.
        window_days: Size of the trend window in days (default 28).
        mini_briefing: True when this is a between-visit alert briefing.

    Returns:
        Data limitations string for the briefing payload.
    """
    if not monitoring_active:
        return "Patient is on EHR-only pathway. No home monitoring data available."
    if not readings:
        return f"Home monitoring active but no readings received in past {window_days} days."
    n = len(readings)
    _synthetic_notice = (
        " Home BP readings are synthetic, generated for demonstration purposes "
        "from real iEMR baseline data."
    )
    suffix = " This is a between-visit alert briefing — not a scheduled pre-visit summary." if mini_briefing else ""
    if n < 14:
        return (
            f"Limited home monitoring data: {n} sessions in past {window_days} days. "
            f"Trend interpretation should be treated with caution.{_synthetic_notice}{suffix}"
        )
    return f"Home monitoring data available: {n} sessions over {window_days} days.{_synthetic_notice}{suffix}"


# ── Public API ─────────────────────────────────────────────────────────────────

async def compose_briefing(
    session: AsyncSession,
    patient_id: str,
    appointment_date: date,
) -> Briefing:
    """Compose and persist a deterministic Layer 1 pre-visit briefing.

    Queries the database for the patient's readings, alerts, medication
    confirmations, and clinical context, then assembles the 9-field structured
    briefing JSON and writes a Briefing row to the database.

    This is a Layer 1 operation — pure deterministic logic, no LLM.
    Layer 3 (summarizer.py) must NOT be called before this function completes.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: The patient's unique identifier.
        appointment_date: The appointment date this briefing covers.

    Returns:
        The persisted Briefing ORM instance.

    Raises:
        ValueError: If the patient or clinical context row is not found.
    """
    logger.info(
        "Composing Layer 1 briefing for patient=%s appointment=%s",
        patient_id,
        appointment_date,
    )

    # ── Fetch patient ──────────────────────────────────────────────────────────
    patient_result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    patient = patient_result.scalar_one_or_none()
    if patient is None:
        raise ValueError(f"Patient {patient_id!r} not found in database.")

    # ── Fetch clinical context ─────────────────────────────────────────────────
    ctx_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = ctx_result.scalar_one_or_none()
    if ctx is None:
        raise ValueError(f"Clinical context for patient {patient_id!r} not found.")

    # ── Fetch last 28 days of readings (ascending by time) ────────────────────
    since = _now_utc() - timedelta(days=28)
    readings_result = await session.execute(
        select(Reading)
        .where(
            and_(
                Reading.patient_id == patient_id,
                Reading.effective_datetime >= since,
            )
        )
        .order_by(Reading.effective_datetime.asc())
    )
    readings = list(readings_result.scalars().all())

    # ── Fetch unacknowledged alerts ───────────────────────────────────────────
    alerts_result = await session.execute(
        select(Alert)
        .where(
            and_(
                Alert.patient_id == patient_id,
                Alert.acknowledged_at.is_(None),
            )
        )
        .order_by(Alert.triggered_at.desc())
    )
    alerts = list(alerts_result.scalars().all())

    # ── Fetch 28-day medication confirmations ─────────────────────────────────
    confs_result = await session.execute(
        select(MedicationConfirmation)
        .where(
            and_(
                MedicationConfirmation.patient_id == patient_id,
                MedicationConfirmation.scheduled_time >= since,
            )
        )
        .order_by(MedicationConfirmation.scheduled_time.asc())
    )
    confirmations = list(confs_result.scalars().all())

    # ── Build all 9 briefing fields ───────────────────────────────────────────
    trend_summary = _build_trend_summary(
        readings=readings,
        last_clinic_systolic=ctx.last_clinic_systolic,
        last_clinic_diastolic=ctx.last_clinic_diastolic,
        monitoring_active=patient.monitoring_active,
    )
    trajectory = _build_long_term_trajectory(
        ctx.historic_bp_systolic,
        ctx.historic_bp_dates,
    )
    if trajectory:
        trend_summary = f"{trend_summary} {trajectory}"
    medication_status = _build_medication_status(
        current_medications=ctx.current_medications,
        last_med_change=ctx.last_med_change,
    )
    adherence_summary = _build_adherence_summary(
        confirmations=confirmations,
        readings=readings,
        monitoring_active=patient.monitoring_active,
    )
    urgent_flags = _build_urgent_flags(alerts)
    visit_agenda = _build_visit_agenda(
        urgent_flags=urgent_flags,
        readings=readings,
        confirmations=confirmations,
        active_problems=ctx.active_problems,
        overdue_labs=ctx.overdue_labs,
        last_med_change=ctx.last_med_change,
        monitoring_active=patient.monitoring_active,
    )
    data_limitations = _build_data_limitations(
        readings=readings,
        monitoring_active=patient.monitoring_active,
    )

    payload: dict[str, Any] = {
        "trend_summary": trend_summary,
        "medication_status": medication_status,
        "adherence_summary": adherence_summary,
        "active_problems": _sort_problems(ctx.active_problems or [], ctx.problem_codes or []),
        "overdue_labs": ctx.overdue_labs or [],
        "visit_agenda": visit_agenda,
        "urgent_flags": urgent_flags,
        "risk_score": float(patient.risk_score) if patient.risk_score is not None else None,
        "data_limitations": data_limitations,
    }

    # ── Persist briefing row ──────────────────────────────────────────────────
    briefing = Briefing(
        patient_id=patient_id,
        appointment_date=appointment_date,
        llm_response=payload,
    )
    session.add(briefing)
    await session.flush()  # populate briefing_id before writing audit row

    # ── Write audit event ─────────────────────────────────────────────────────
    audit = AuditEvent(
        actor_type="system",
        actor_id="briefing_composer",
        patient_id=patient_id,
        action="briefing_generation",
        resource_type="Briefing",
        resource_id=briefing.briefing_id,
        outcome="success",
        details=f"Layer 1 briefing composed for appointment {appointment_date.isoformat()}",
    )
    session.add(audit)
    await session.commit()

    logger.info(
        "Briefing %s composed for patient=%s appointment=%s",
        briefing.briefing_id,
        patient_id,
        appointment_date,
    )
    return briefing


async def compose_mini_briefing(
    session: AsyncSession,
    patient_id: str,
    trigger_alert_type: str,
) -> Briefing:
    """Compose and persist a between-visit mini-briefing for an urgent alert.

    Called when a gap_urgent or deterioration alert fires between appointments.
    Uses a 7-day reading window instead of 28 days.  Layer 3 LLM summary is
    never generated for mini-briefings.  appointment_date is stored as None.

    Deduplicated by calendar day: if a mini-briefing already exists for this
    patient today (appointment_date IS NULL, DATE(generated_at) = today), the
    existing row is returned without a new insert.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: The patient's unique identifier.
        trigger_alert_type: ``"gap_urgent"`` or ``"deterioration"``.

    Returns:
        The persisted (or pre-existing same-day) Briefing ORM instance.

    Raises:
        ValueError: If the patient or clinical context row is not found.
    """
    from sqlalchemy import Date as SADate
    from sqlalchemy import cast

    logger.info(
        "Composing mini-briefing for patient=%s trigger=%s",
        patient_id,
        trigger_alert_type,
    )

    # ── Same-day dedup ─────────────────────────────────────────────────────────
    today_utc = _now_utc().date()
    existing_result = await session.execute(
        select(Briefing).where(
            and_(
                Briefing.patient_id == patient_id,
                Briefing.appointment_date.is_(None),
                cast(Briefing.generated_at, SADate) == today_utc,
            )
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        logger.info(
            "Mini-briefing already exists for patient=%s today — skipping duplicate",
            patient_id,
        )
        return existing

    # ── Fetch patient ──────────────────────────────────────────────────────────
    patient_result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    patient = patient_result.scalar_one_or_none()
    if patient is None:
        raise ValueError(f"Patient {patient_id!r} not found in database.")

    # ── Fetch clinical context ─────────────────────────────────────────────────
    ctx_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = ctx_result.scalar_one_or_none()
    if ctx is None:
        raise ValueError(f"Clinical context for patient {patient_id!r} not found.")

    # ── Fetch last 7 days of readings ─────────────────────────────────────────
    mini_window_days = 7
    since = _now_utc() - timedelta(days=mini_window_days)
    readings_result = await session.execute(
        select(Reading)
        .where(
            and_(
                Reading.patient_id == patient_id,
                Reading.effective_datetime >= since,
            )
        )
        .order_by(Reading.effective_datetime.asc())
    )
    readings = list(readings_result.scalars().all())

    # ── Fetch unacknowledged alerts ───────────────────────────────────────────
    alerts_result = await session.execute(
        select(Alert)
        .where(
            and_(
                Alert.patient_id == patient_id,
                Alert.acknowledged_at.is_(None),
            )
        )
        .order_by(Alert.triggered_at.desc())
    )
    alerts = list(alerts_result.scalars().all())

    # ── Build payload (same 9-field structure as full briefing) ───────────────
    urgent_flags = _build_urgent_flags(alerts)
    trend_summary = _build_trend_summary(
        readings=readings,
        last_clinic_systolic=ctx.last_clinic_systolic,
        last_clinic_diastolic=ctx.last_clinic_diastolic,
        monitoring_active=patient.monitoring_active,
        window_days=mini_window_days,
    )
    medication_status = _build_medication_status(
        current_medications=ctx.current_medications,
        last_med_change=ctx.last_med_change,
    )
    visit_agenda = _build_visit_agenda(
        urgent_flags=urgent_flags,
        readings=readings,
        confirmations=[],
        active_problems=ctx.active_problems,
        overdue_labs=ctx.overdue_labs,
        last_med_change=ctx.last_med_change,
        monitoring_active=patient.monitoring_active,
    )
    data_limitations = _build_data_limitations(
        readings=readings,
        monitoring_active=patient.monitoring_active,
        window_days=mini_window_days,
        mini_briefing=True,
    )

    payload: dict[str, Any] = {
        "trend_summary": trend_summary,
        "medication_status": medication_status,
        "adherence_summary": "",
        "active_problems": _sort_problems(ctx.active_problems or [], ctx.problem_codes or []),
        "overdue_labs": ctx.overdue_labs or [],
        "visit_agenda": visit_agenda,
        "urgent_flags": urgent_flags,
        "risk_score": float(patient.risk_score) if patient.risk_score is not None else None,
        "data_limitations": data_limitations,
    }

    # ── Persist briefing row (appointment_date=None marks as mini-briefing) ───
    briefing = Briefing(
        patient_id=patient_id,
        appointment_date=None,
        llm_response=payload,
    )
    session.add(briefing)
    await session.flush()

    # ── Write audit event ─────────────────────────────────────────────────────
    audit = AuditEvent(
        actor_type="system",
        actor_id="briefing_composer",
        patient_id=patient_id,
        action="briefing_generation",
        resource_type="Briefing",
        resource_id=briefing.briefing_id,
        outcome="success",
        details=f"Mini-briefing for {trigger_alert_type}",
    )
    session.add(audit)
    await session.commit()

    logger.info(
        "Mini-briefing %s composed for patient=%s trigger=%s",
        briefing.briefing_id,
        patient_id,
        trigger_alert_type,
    )
    return briefing
