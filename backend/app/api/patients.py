"""Patient API routes for ARIA.

GET  /api/patients                          — list all enrolled patients, sorted by tier then risk_score DESC
GET  /api/patients/{id}                     — single patient record
PATCH /api/patients/{id}/appointment        — update next_appointment datetime
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.patient import Patient
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["patients"])

_TIER_ORDER = {"high": 0, "medium": 1, "low": 2}


class AppointmentUpdateRequest(BaseModel):
    """Request body for PATCH /patients/{patient_id}/appointment."""

    next_appointment: datetime


@router.get("/patients")
async def list_patients(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Return all enrolled patients sorted by risk tier then risk_score DESC."""
    result = await session.execute(select(Patient))
    patients = result.scalars().all()

    def sort_key(p: Patient) -> tuple[int, float]:
        tier = _TIER_ORDER.get(p.risk_tier, 9)
        score = float(p.risk_score) if p.risk_score is not None else 0.0
        return (tier, -score)

    sorted_patients = sorted(patients, key=sort_key)
    return [_serialise(p) for p in sorted_patients]


@router.get("/patients/{patient_id}")
async def get_patient(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return a single patient record by ID."""
    result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _serialise(patient)


@router.patch("/patients/{patient_id}/appointment")
async def update_appointment(
    patient_id: str,
    body: AppointmentUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Update next_appointment for a patient.

    Called after each clinic visit (manually in demo; via EHR webhook in
    production) so the 7:30 AM briefing scheduler and adaptive detection
    window always see a current appointment date.

    Args:
        patient_id: The patient's MED_REC_NO / FHIR Patient.id.
        body: JSON body with ``next_appointment`` as an ISO 8601 datetime.

    Returns:
        Updated patient record dict.

    Raises:
        HTTPException 404: Patient not found.
    """
    result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient.next_appointment = body.next_appointment
    await session.commit()
    logger.info(
        "next_appointment updated: patient=%s next_appointment=%s",
        patient_id,
        body.next_appointment.isoformat(),
    )
    return _serialise(patient)


def _serialise(p: Patient) -> dict:
    return {
        "patient_id": p.patient_id,
        "gender": p.gender,
        "age": p.age,
        "risk_tier": p.risk_tier,
        "tier_override": p.tier_override,
        "risk_score": float(p.risk_score) if p.risk_score is not None else None,
        "risk_score_computed_at": p.risk_score_computed_at.isoformat() if p.risk_score_computed_at else None,
        "monitoring_active": p.monitoring_active,
        "next_appointment": p.next_appointment.isoformat() if p.next_appointment else None,
        "enrolled_at": p.enrolled_at.isoformat() if p.enrolled_at else None,
        "enrolled_by": p.enrolled_by,
    }
