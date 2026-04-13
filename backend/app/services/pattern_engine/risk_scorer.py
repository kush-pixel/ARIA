"""Layer 2 weighted priority score computation.

The scorer runs after Layer 1 pattern detection in the ARIA architecture.
Until the Layer 1 detectors are implemented, it queries the database directly
for each signal and persists the resulting 0.0-100.0 score to patients.risk_score.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.patient import Patient
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_LOOKBACK_DAYS = 28
_DEFAULT_BASELINE_SYSTOLIC = 140.0

_SYSTOLIC_WEIGHT = 0.30
_INERTIA_WEIGHT = 0.25
_ADHERENCE_WEIGHT = 0.20
_GAP_WEIGHT = 0.15
_COMORBIDITY_WEIGHT = 0.10


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    """Clamp value to the inclusive minimum and maximum range."""
    return max(minimum, min(value, maximum))


def _as_float(value: Decimal | float | int | None) -> float | None:
    """Return a numeric value as float, preserving None."""
    if value is None:
        return None
    return float(value)


def _baseline_systolic(context: ClinicalContext | None) -> float:
    """Choose the patient's systolic baseline using the required fallback order."""
    if context is None:
        return _DEFAULT_BASELINE_SYSTOLIC

    historic = context.historic_bp_systolic or []
    if historic:
        return float(median(historic))

    if context.last_clinic_systolic is not None:
        return float(context.last_clinic_systolic)

    return _DEFAULT_BASELINE_SYSTOLIC


def _days_since_med_change(context: ClinicalContext | None) -> int:
    """Return days since last medication change, defaulting NULL to 180 days."""
    if context is None or context.last_med_change is None:
        return 180

    med_change = context.last_med_change
    if isinstance(med_change, datetime):
        med_change = med_change.date()

    return max((date.today() - med_change).days, 0)


def _days_since_reading(last_reading_at: datetime | None, now: datetime) -> int:
    """Return whole days since most recent reading; absent readings count as 28 days."""
    if last_reading_at is None:
        return _LOOKBACK_DAYS

    if last_reading_at.tzinfo is None:
        last_reading_at = last_reading_at.replace(tzinfo=UTC)

    return max((now - last_reading_at).days, 0)


def _comorbidity_count(context: ClinicalContext | None) -> int:
    """Return the number of coded problems available for the patient."""
    if context is None or context.problem_codes is None:
        return 0
    return len(context.problem_codes)


async def compute_risk_score(patient_id: str, session: AsyncSession) -> float:
    """Compute and persist the Layer 2 risk score for a patient.

    Args:
        patient_id: ARIA patient identifier.
        session: Async SQLAlchemy session.

    Returns:
        The rounded 0.0-100.0 priority score.

    Raises:
        ValueError: The patient_id does not exist in patients.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=_LOOKBACK_DAYS)

    patient_result = await session.execute(
        select(Patient.patient_id).where(Patient.patient_id == patient_id)
    )
    if patient_result.scalar_one_or_none() is None:
        raise ValueError(f"Patient not found: {patient_id}")

    context_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    context = context_result.scalar_one_or_none()

    avg_systolic_result = await session.execute(
        select(func.avg(Reading.systolic_avg)).where(
            Reading.patient_id == patient_id,
            Reading.effective_datetime >= window_start,
        )
    )
    avg_systolic = _as_float(avg_systolic_result.scalar_one_or_none())

    last_reading_result = await session.execute(
        select(func.max(Reading.effective_datetime)).where(Reading.patient_id == patient_id)
    )
    last_reading_at = last_reading_result.scalar_one_or_none()

    confirmations_result = await session.execute(
        select(
            func.count(MedicationConfirmation.confirmation_id),
            func.count(MedicationConfirmation.confirmed_at),
        ).where(
            MedicationConfirmation.patient_id == patient_id,
            MedicationConfirmation.scheduled_time >= window_start,
        )
    )
    total_confirmations, confirmed_count = confirmations_result.one()

    baseline = _baseline_systolic(context)
    sig_systolic = (
        50.0
        if avg_systolic is None
        else _clamp((avg_systolic - baseline) / 30.0 * 100.0)
    )

    sig_inertia = _clamp(_days_since_med_change(context) / 90.0 * 100.0)

    sig_adherence = (
        50.0
        if total_confirmations == 0
        else 100.0 - (float(confirmed_count) / float(total_confirmations) * 100.0)
    )

    gap_days = _days_since_reading(last_reading_at, now)
    sig_gap = _clamp(gap_days / 14.0 * 100.0)

    sig_comorbidity = _clamp(_comorbidity_count(context) / 5.0 * 100.0)

    score = _clamp(
        sig_systolic * _SYSTOLIC_WEIGHT
        + sig_inertia * _INERTIA_WEIGHT
        + sig_adherence * _ADHERENCE_WEIGHT
        + sig_gap * _GAP_WEIGHT
        + sig_comorbidity * _COMORBIDITY_WEIGHT
    )
    rounded_score = round(score, 2)

    # Risk scoring does not emit audit_events; SOP audit requirements omit this action.
    await session.execute(
        update(Patient)
        .where(Patient.patient_id == patient_id)
        .values(risk_score=rounded_score)
    )
    await session.commit()

    logger.info("risk_score computed patient=%s score=%.2f", patient_id, rounded_score)
    return rounded_score
