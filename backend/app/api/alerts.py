"""Alert API routes for ARIA.

GET  /api/alerts[?patient_id=]          — unacknowledged alerts (optionally scoped)
POST /api/alerts/{alert_id}/acknowledge — acknowledge an alert; optional disposition
                                          + reason_text written to alert_feedback
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.limiter import limiter
from app.models.alert import Alert
from app.models.alert_feedback import AlertFeedback
from app.models.audit_event import AuditEvent
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["alerts"])


# ---------------------------------------------------------------------------
# Detector-type derivation (alert_type → detector_type)
# ---------------------------------------------------------------------------

_DETECTOR_TYPE_MAP: dict[str, str] = {
    "gap_urgent": "gap",
    "gap_briefing": "gap",
    "inertia": "inertia",
    "deterioration": "deterioration",
    "adherence": "adherence",
}


class AcknowledgeRequest(BaseModel):
    """Optional clinician feedback on an alert acknowledgement.

    Empty body → backwards-compatible acknowledge-only behaviour.
    Providing `disposition` writes a row to alert_feedback (Fix 42 L1).
    """

    disposition: Literal["agree_acting", "agree_monitoring", "disagree"] | None = None
    reason_text: str | None = Field(default=None, max_length=2000)
    clinician_id: str | None = None


@router.get("/alerts")
@limiter.limit("30/minute")
async def list_alerts(
    request: Request,
    patient_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return unacknowledged alerts ordered by triggered_at DESC.

    Args:
        patient_id: Optional filter — when provided, restricts results to a single
            patient. Omit to receive all unacknowledged alerts (inbox behaviour).
    """
    stmt = select(Alert).where(Alert.acknowledged_at.is_(None))
    if patient_id is not None:
        stmt = stmt.where(Alert.patient_id == patient_id)
    result = await session.execute(stmt.order_by(Alert.triggered_at.desc()))
    alerts = result.scalars().all()
    return [_serialise(a) for a in alerts]


_ACKNOWLEDGED_HISTORY_DAYS = 7
_UNDO_WINDOW_MINUTES = 24 * 60


@router.get("/alerts/acknowledged")
@limiter.limit("30/minute")
async def list_acknowledged_alerts(
    request: Request,
    patient_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return alerts acknowledged within the last 7 days, newest first.

    Args:
        patient_id: Optional filter to scope results to one patient.
    """
    cutoff = datetime.now(UTC) - timedelta(days=_ACKNOWLEDGED_HISTORY_DAYS)
    stmt = select(Alert).where(
        Alert.acknowledged_at.is_not(None),
        Alert.acknowledged_at >= cutoff,
    )
    if patient_id is not None:
        stmt = stmt.where(Alert.patient_id == patient_id)
    result = await session.execute(stmt.order_by(Alert.acknowledged_at.desc()))
    alerts = result.scalars().all()
    return [_serialise(a) for a in alerts]


@router.post("/alerts/{alert_id}/unacknowledge")
async def unacknowledge_alert(
    alert_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reverse an acknowledgement if within the 24-hour undo window.

    Clears acknowledged_at so the alert re-appears in the active inbox.
    Blocked after 24 hours to preserve audit integrity.
    """
    result = await session.execute(
        select(Alert).where(Alert.alert_id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.acknowledged_at is None:
        return {"status": "not_acknowledged"}

    undo_cutoff = datetime.now(UTC) - timedelta(minutes=_UNDO_WINDOW_MINUTES)
    if alert.acknowledged_at < undo_cutoff:
        raise HTTPException(
            status_code=409,
            detail="Undo window expired — alerts can only be reversed within 24 hours of acknowledgement.",
        )

    await session.execute(
        update(Alert)
        .where(Alert.alert_id == alert_id)
        .values(acknowledged_at=None)
    )
    session.add(AuditEvent(
        actor_type="clinician",
        patient_id=alert.patient_id,
        action="alert_unacknowledged",
        resource_type="Alert",
        resource_id=alert_id,
        outcome="success",
    ))
    await session.commit()
    return {"status": "unacknowledged"}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    payload: AcknowledgeRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Acknowledge an alert and write an audit event.

    When `payload.disposition` is supplied, a row is also inserted into the
    `alert_feedback` table — this is Fix 42 Layer 1 of the feedback loop and
    the input signal for Layer 2 calibration recommendations.
    """
    result = await session.execute(
        select(Alert).where(Alert.alert_id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.acknowledged_at is not None:
        return {"status": "already_acknowledged"}

    now = datetime.now(UTC)
    await session.execute(
        update(Alert)
        .where(Alert.alert_id == alert_id)
        .values(acknowledged_at=now)
    )

    session.add(AuditEvent(
        actor_type="clinician",
        patient_id=alert.patient_id,
        action="alert_acknowledged",
        resource_type="Alert",
        resource_id=alert_id,
        outcome="success",
    ))

    feedback_recorded = False
    if payload is not None and payload.disposition is not None:
        feedback = AlertFeedback(
            alert_id=alert_id,
            patient_id=alert.patient_id,
            detector_type=_DETECTOR_TYPE_MAP.get(alert.alert_type, alert.alert_type),
            disposition=payload.disposition,
            reason_text=payload.reason_text,
            clinician_id=payload.clinician_id,
        )
        session.add(feedback)
        await session.flush()  # materialise feedback_id before outcome scheduling
        feedback_recorded = True

        # Fix 42 L3: schedule 30-day outcome check when clinician disagrees
        if payload.disposition == "disagree":
            from app.services.feedback.outcome_tracker import schedule_outcome_check
            await schedule_outcome_check(
                session,
                feedback_id=feedback.feedback_id,
                alert_id=alert_id,
                patient_id=alert.patient_id,
                dismissed_at=now,
            )

    await session.commit()

    return {
        "status": "acknowledged",
        "acknowledged_at": now.isoformat(),
        "feedback_recorded": feedback_recorded,
    }


def _serialise(a: Alert) -> dict:
    return {
        "alert_id": a.alert_id,
        "patient_id": a.patient_id,
        "alert_type": a.alert_type,
        "gap_days": a.gap_days,
        "systolic_avg": float(a.systolic_avg) if a.systolic_avg is not None else None,
        "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
        "delivered_at": a.delivered_at.isoformat() if a.delivered_at else None,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "off_hours": a.off_hours,
        "escalated": a.escalated,
    }
