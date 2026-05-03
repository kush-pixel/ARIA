"""Briefing API routes for ARIA.

GET /api/briefings/{patient_id}  — latest briefing for a patient; writes audit + read_at
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.patient import Patient
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["briefings"])


@router.get("/briefings/{patient_id}")
async def get_briefing(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the most recent briefing for a patient and mark it as read."""
    # Verify patient exists
    patient_result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    if patient_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    # Fetch most recent active briefing:
    # - appointment_date IS NULL → mini-briefing (between-visit urgent alert), always active
    # - appointment_date >= today → pre-visit briefing for today or a future appointment
    # Past appointment briefings are excluded — the clinician should see current state, not
    # stale pre-visit content from an appointment that has already taken place.
    today = datetime.now(UTC).date()
    result = await session.execute(
        select(Briefing)
        .where(
            Briefing.patient_id == patient_id,
            or_(
                Briefing.appointment_date.is_(None),
                Briefing.appointment_date >= today,
            ),
        )
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )
    briefing = result.scalar_one_or_none()
    if briefing is None:
        raise HTTPException(status_code=404, detail="No active briefing for this patient")

    now = datetime.now(UTC)

    # Mark as read if not already
    if briefing.read_at is None:
        await session.execute(
            update(Briefing)
            .where(Briefing.briefing_id == briefing.briefing_id)
            .values(read_at=now)
        )

    # Audit: briefing_viewed
    session.add(AuditEvent(
        actor_type="clinician",
        patient_id=patient_id,
        action="briefing_viewed",
        resource_type="Briefing",
        resource_id=briefing.briefing_id,
        outcome="success",
    ))
    await session.commit()

    return {
        "briefing_id": briefing.briefing_id,
        "patient_id": briefing.patient_id,
        "appointment_date": briefing.appointment_date.isoformat() if briefing.appointment_date else None,
        "llm_response": briefing.llm_response,
        "generated_at": briefing.generated_at.isoformat() if briefing.generated_at else None,
        "delivered_at": briefing.delivered_at.isoformat() if briefing.delivered_at else None,
        "read_at": now.isoformat(),
    }
