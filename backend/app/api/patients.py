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
from app.models.briefing import Briefing
from app.models.clinical_context import ClinicalContext
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

    # Fix 23: determine which patients have at least one briefing row
    briefing_result = await session.execute(
        select(Briefing.patient_id).distinct()
    )
    patients_with_briefing: set[str] = {row[0] for row in briefing_result}

    def sort_key(p: Patient) -> tuple[int, float]:
        tier = _TIER_ORDER.get(p.risk_tier, 9)
        score = float(p.risk_score) if p.risk_score is not None else 0.0
        return (tier, -score)

    sorted_patients = sorted(patients, key=sort_key)
    return [_serialise(p, has_briefing=p.patient_id in patients_with_briefing) for p in sorted_patients]


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
    briefing_check = await session.execute(
        select(Briefing.patient_id).where(Briefing.patient_id == patient_id).limit(1)
    )
    has_briefing = briefing_check.scalar_one_or_none() is not None
    return _serialise(patient, has_briefing=has_briefing)


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
    briefing_check2 = await session.execute(
        select(Briefing.patient_id).where(Briefing.patient_id == patient_id).limit(1)
    )
    return _serialise(patient, has_briefing=briefing_check2.scalar_one_or_none() is not None)


@router.get("/patients/{patient_id}/baseline")
async def get_patient_baseline(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the patient's personal systolic baseline from clinic readings.

    Computes median of historic_bp_systolic stored in clinical_context.
    Falls back to 163.0 if fewer than 2 clinic readings are available.
    """
    result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = result.scalar_one_or_none()
    if ctx is None:
        raise HTTPException(status_code=404, detail="Clinical context not found")

    values: list[int] = ctx.historic_bp_systolic or []
    if len(values) < 2:
        return {"baseline_systolic": 163.0, "reading_count": len(values)}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    median = sorted_vals[mid] if n % 2 == 1 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return {"baseline_systolic": float(median), "reading_count": n}


def _serialise(p: Patient, has_briefing: bool = False) -> dict:
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
        "has_briefing": has_briefing,
    }
