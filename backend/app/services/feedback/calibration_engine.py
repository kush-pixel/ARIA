"""Layer 2 feedback calibration engine (Fix 42 L2).

Queries alert_feedback for dismissal patterns and surfaces recommendations
when a detector accumulates 4+ disagree dispositions for the same
patient/detector pair.

No automatic threshold changes — every recommendation requires explicit
clinician approval via POST /api/admin/calibration-rules.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_feedback import AlertFeedback
from app.models.calibration_rule import CalibrationRule
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_DISMISSAL_THRESHOLD: int = 4


async def get_calibration_recommendations(session: AsyncSession) -> list[dict]:
    """Return patient/detector pairs with 4+ disagree dispositions and no active rule.

    Returns:
        List of recommendation dicts: patient_id, detector_type,
        dismissal_count, threshold.
    """
    result = await session.execute(
        select(
            AlertFeedback.patient_id,
            AlertFeedback.detector_type,
            func.count(AlertFeedback.feedback_id).label("dismissal_count"),
        )
        .where(AlertFeedback.disposition == "disagree")
        .group_by(AlertFeedback.patient_id, AlertFeedback.detector_type)
        .having(func.count(AlertFeedback.feedback_id) >= _DISMISSAL_THRESHOLD)
    )
    rows = result.all()

    # Filter out pairs that already have an active approved rule
    active_rules_result = await session.execute(
        select(CalibrationRule.patient_id, CalibrationRule.detector_type)
        .where(CalibrationRule.active.is_(True))
    )
    active_pairs = {(r.patient_id, r.detector_type) for r in active_rules_result.all()}

    recommendations = [
        {
            "patient_id": row.patient_id,
            "detector_type": row.detector_type,
            "dismissal_count": row.dismissal_count,
            "threshold": _DISMISSAL_THRESHOLD,
        }
        for row in rows
        if (row.patient_id, row.detector_type) not in active_pairs
    ]

    logger.info("calibration_engine: %d recommendation(s) surfaced", len(recommendations))
    return recommendations


async def approve_calibration_rule(
    session: AsyncSession,
    patient_id: str,
    detector_type: str,
    dismissal_count: int,
    approved_by: str | None,
    notes: str | None,
) -> CalibrationRule:
    """Persist a clinician-approved calibration rule.

    Deactivates any previous rule for the same (patient_id, detector_type)
    before inserting the new active one.

    Args:
        session: Active async SQLAlchemy session.
        patient_id: Patient the rule applies to.
        detector_type: Detector being calibrated (gap|inertia|deterioration|adherence).
        dismissal_count: Number of dismissals that triggered this recommendation.
        approved_by: Clinician or admin ID authorising the rule.
        notes: Optional free-text justification.

    Returns:
        The new CalibrationRule row (not yet committed).
    """
    await session.execute(
        update(CalibrationRule)
        .where(
            CalibrationRule.patient_id == patient_id,
            CalibrationRule.detector_type == detector_type,
            CalibrationRule.active.is_(True),
        )
        .values(active=False)
    )

    rule = CalibrationRule(
        patient_id=patient_id,
        detector_type=detector_type,
        dismissal_count=dismissal_count,
        approved_by=approved_by,
        approved_at=datetime.now(UTC),
        notes=notes,
        active=True,
    )
    session.add(rule)
    await session.flush()
    logger.info(
        "calibration_rule approved: patient=%s detector=%s by=%s",
        patient_id,
        detector_type,
        approved_by,
    )
    return rule
