"""30-day outcome verification tracker (Fix 42 L3).

When a clinician dismisses an alert (disposition='disagree'), a 30-day check
is scheduled. The daily worker sweep inspects whether a concerning event
occurred within that window and prompts a retrospective label.

No automatic decisions — every retrospective prompt requires clinician response
via POST /api/admin/outcome-verifications/{id}/respond.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.outcome_verification import OutcomeVerification
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_OUTCOME_WINDOW_DAYS: int = 30
_CONCERNING_ALERT_TYPES: frozenset[str] = frozenset({"gap_urgent", "deterioration"})


async def schedule_outcome_check(
    session: AsyncSession,
    feedback_id: str,
    alert_id: str,
    patient_id: str,
    dismissed_at: datetime,
) -> OutcomeVerification:
    """Create a pending outcome verification for a dismissed alert.

    Args:
        session: Active async SQLAlchemy session.
        feedback_id: The alert_feedback row that recorded the dismissal.
        alert_id: The alert that was dismissed.
        patient_id: Patient the alert belongs to.
        dismissed_at: Timestamp when the clinician dismissed the alert.

    Returns:
        The newly created OutcomeVerification row (not yet flushed).
    """
    verification = OutcomeVerification(
        feedback_id=feedback_id,
        alert_id=alert_id,
        patient_id=patient_id,
        dismissed_at=dismissed_at,
        check_after=dismissed_at + timedelta(days=_OUTCOME_WINDOW_DAYS),
        outcome_type="pending",
    )
    session.add(verification)
    logger.info(
        "outcome_check scheduled: patient=%s alert=%s check_after=%s",
        patient_id,
        alert_id,
        (dismissed_at + timedelta(days=_OUTCOME_WINDOW_DAYS)).date(),
    )
    return verification


async def run_outcome_checks(session: AsyncSession) -> int:
    """Check due outcome verifications and resolve outcome_type.

    Runs in the daily worker sweep. For each pending verification where
    check_after <= now, inspects the alerts table for concerning events
    after dismissed_at. Sets outcome_type and prompted_at.

    Returns:
        Number of verifications resolved in this sweep.
    """
    now = datetime.now(UTC)

    result = await session.execute(
        select(OutcomeVerification).where(
            OutcomeVerification.outcome_type == "pending",
            OutcomeVerification.check_after <= now,
        )
    )
    due = result.scalars().all()

    resolved = 0
    for verification in due:
        concerning_result = await session.execute(
            select(Alert)
            .where(
                Alert.patient_id == verification.patient_id,
                Alert.alert_type.in_(_CONCERNING_ALERT_TYPES),
                Alert.triggered_at > verification.dismissed_at,
                Alert.triggered_at <= verification.check_after,
            )
            .limit(1)
        )
        concerning_alert = concerning_result.scalar_one_or_none()

        verification.outcome_type = (
            "deterioration_cluster" if concerning_alert else "none"
        )
        verification.prompted_at = now
        resolved += 1

        logger.info(
            "outcome_check resolved: patient=%s alert=%s outcome=%s",
            verification.patient_id,
            verification.alert_id,
            verification.outcome_type,
        )

    if resolved:
        await session.flush()

    return resolved
