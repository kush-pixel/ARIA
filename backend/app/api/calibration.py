"""Admin endpoints for feedback loop Layers 2 and 3.

Fix 42 L2 — Calibration recommendations (4+ dismissals → admin review):
  GET  /api/admin/calibration-recommendations
  POST /api/admin/calibration-rules

Fix 42 L3 — Outcome verification retrospective prompts (30-day follow-up):
  GET  /api/admin/outcome-verifications
  POST /api/admin/outcome-verifications/{id}/respond
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.outcome_verification import OutcomeVerification
from app.services.feedback.calibration_engine import (
    approve_calibration_rule,
    get_calibration_recommendations,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Fix 42 L2 — Calibration recommendations
# ---------------------------------------------------------------------------


@router.get("/admin/calibration-recommendations")
async def list_calibration_recommendations(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return detector/patient pairs with 4+ dismissals and no active rule."""
    return await get_calibration_recommendations(session)


class ApproveRuleRequest(BaseModel):
    """Body for approving a calibration recommendation."""

    patient_id: str
    detector_type: str
    dismissal_count: int = Field(ge=4)
    approved_by: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/admin/calibration-rules", status_code=201)
async def create_calibration_rule(
    payload: ApproveRuleRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Approve a calibration recommendation and persist it as an active rule."""
    rule = await approve_calibration_rule(
        session,
        patient_id=payload.patient_id,
        detector_type=payload.detector_type,
        dismissal_count=payload.dismissal_count,
        approved_by=payload.approved_by,
        notes=payload.notes,
    )
    await session.commit()
    return {
        "rule_id": rule.rule_id,
        "patient_id": rule.patient_id,
        "detector_type": rule.detector_type,
        "dismissal_count": rule.dismissal_count,
        "active": rule.active,
        "approved_at": rule.approved_at.isoformat() if rule.approved_at else None,
    }


# ---------------------------------------------------------------------------
# Fix 42 L3 — Outcome verification retrospective prompts
# ---------------------------------------------------------------------------


@router.get("/admin/outcome-verifications")
async def list_outcome_verifications(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return outcome verifications that are due for clinician response."""
    result = await session.execute(
        select(OutcomeVerification)
        .where(
            OutcomeVerification.prompted_at.is_not(None),
            OutcomeVerification.clinician_response.is_(None),
        )
        .order_by(OutcomeVerification.prompted_at.desc())
    )
    verifications = result.scalars().all()
    return [_serialise_verification(v) for v in verifications]


class OutcomeResponseRequest(BaseModel):
    """Clinician retrospective response to an outcome verification."""

    clinician_response: str = Field(pattern="^(relevant|not_relevant|unsure)$")
    response_notes: str | None = Field(default=None, max_length=2000)


@router.post("/admin/outcome-verifications/{verification_id}/respond")
async def respond_to_outcome_verification(
    verification_id: str,
    payload: OutcomeResponseRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record a clinician's retrospective response to an outcome verification."""
    result = await session.execute(
        select(OutcomeVerification).where(
            OutcomeVerification.verification_id == verification_id
        )
    )
    verification = result.scalar_one_or_none()
    if verification is None:
        raise HTTPException(status_code=404, detail="Outcome verification not found")

    if verification.clinician_response is not None:
        return {"status": "already_responded"}

    now = datetime.now(UTC)
    verification.clinician_response = payload.clinician_response
    verification.response_notes = payload.response_notes
    verification.responded_at = now
    await session.commit()

    logger.info(
        "outcome_verification responded: id=%s response=%s",
        verification_id,
        payload.clinician_response,
    )
    return {
        "status": "responded",
        "verification_id": verification_id,
        "clinician_response": payload.clinician_response,
        "responded_at": now.isoformat(),
    }


def _serialise_verification(v: OutcomeVerification) -> dict:
    return {
        "verification_id": v.verification_id,
        "feedback_id": v.feedback_id,
        "alert_id": v.alert_id,
        "patient_id": v.patient_id,
        "dismissed_at": v.dismissed_at.isoformat(),
        "check_after": v.check_after.isoformat(),
        "outcome_type": v.outcome_type,
        "prompted_at": v.prompted_at.isoformat() if v.prompted_at else None,
        "clinician_response": v.clinician_response,
        "responded_at": v.responded_at.isoformat() if v.responded_at else None,
    }
