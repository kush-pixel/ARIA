"""Alert API routes for ARIA.

GET  /api/alerts                       — all unacknowledged alerts
POST /api/alerts/{alert_id}/acknowledge — acknowledge an alert (writes audit event)
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.alert import Alert
from app.models.audit_event import AuditEvent
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def list_alerts(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Return all unacknowledged alerts ordered by triggered_at DESC."""
    result = await session.execute(
        select(Alert)
        .where(Alert.acknowledged_at.is_(None))
        .order_by(Alert.triggered_at.desc())
    )
    alerts = result.scalars().all()
    return [_serialise(a) for a in alerts]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Acknowledge an alert and write an audit event."""
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
    await session.commit()

    return {"status": "acknowledged", "acknowledged_at": now.isoformat()}


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
    }
