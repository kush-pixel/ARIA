"""Readings API routes for ARIA.

GET  /api/readings?patient_id={id}  — home BP readings for a patient (28 days)
POST /api/readings                  — ingest a single manual reading
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.audit_event import AuditEvent
from app.models.patient import Patient
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["readings"])


class ReadingIn(BaseModel):
    """Payload for manually submitted home BP reading."""

    patient_id: str
    systolic_1: int = Field(..., ge=60, le=250)
    diastolic_1: int = Field(..., ge=30, le=150)
    heart_rate_1: int | None = Field(None, ge=30, le=200)
    systolic_2: int | None = Field(None, ge=60, le=250)
    diastolic_2: int | None = Field(None, ge=30, le=150)
    heart_rate_2: int | None = Field(None, ge=30, le=200)
    effective_datetime: datetime
    session: Literal["morning", "evening", "ad_hoc"]
    bp_position: str | None = None
    bp_site: str | None = None
    medication_taken: Literal["yes", "no", "partial"] | None = None
    submitted_by: str = "patient"
    consent_version: str = "1.0"


@router.get("/readings")
async def list_readings(
    patient_id: str = Query(..., description="Patient ID to fetch readings for"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return all readings for a patient from the last 28 days, newest first."""
    cutoff = datetime.now(UTC) - timedelta(days=28)
    result = await session.execute(
        select(Reading)
        .where(Reading.patient_id == patient_id)
        .where(Reading.effective_datetime >= cutoff)
        .order_by(Reading.effective_datetime.desc())
    )
    return [_serialise(r) for r in result.scalars().all()]


@router.post("/readings", status_code=201)
async def create_reading(
    payload: ReadingIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ingest a single manually submitted home BP reading."""
    # Verify patient exists
    patient_result = await session.execute(
        select(Patient).where(Patient.patient_id == payload.patient_id)
    )
    if patient_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    sys2 = payload.systolic_2
    dia2 = payload.diastolic_2
    hr2 = payload.heart_rate_2

    sys_avg = (payload.systolic_1 + sys2) / 2 if sys2 else float(payload.systolic_1)
    dia_avg = (payload.diastolic_1 + dia2) / 2 if dia2 else float(payload.diastolic_1)
    hr_avg = (payload.heart_rate_1 + hr2) / 2 if payload.heart_rate_1 and hr2 else (
        float(payload.heart_rate_1) if payload.heart_rate_1 else None
    )

    reading = Reading(
        patient_id=payload.patient_id,
        systolic_1=payload.systolic_1,
        diastolic_1=payload.diastolic_1,
        heart_rate_1=payload.heart_rate_1,
        systolic_2=sys2,
        diastolic_2=dia2,
        heart_rate_2=hr2,
        systolic_avg=round(sys_avg, 1),
        diastolic_avg=round(dia_avg, 1),
        heart_rate_avg=round(hr_avg, 1) if hr_avg else None,
        effective_datetime=payload.effective_datetime,
        session=payload.session,
        source="manual",
        submitted_by=payload.submitted_by,
        bp_position=payload.bp_position,
        bp_site=payload.bp_site,
        medication_taken=payload.medication_taken,
        consent_version=payload.consent_version,
    )
    session.add(reading)

    session.add(AuditEvent(
        actor_type="clinician",
        patient_id=payload.patient_id,
        action="reading_ingested",
        resource_type="Reading",
        outcome="success",
    ))
    await session.commit()
    await session.refresh(reading)

    return _serialise(reading)


def _serialise(r: Reading) -> dict:
    return {
        "reading_id": r.reading_id,
        "patient_id": r.patient_id,
        "systolic_1": r.systolic_1,
        "diastolic_1": r.diastolic_1,
        "heart_rate_1": r.heart_rate_1,
        "systolic_2": r.systolic_2,
        "diastolic_2": r.diastolic_2,
        "heart_rate_2": r.heart_rate_2,
        "systolic_avg": float(r.systolic_avg),
        "diastolic_avg": float(r.diastolic_avg),
        "heart_rate_avg": float(r.heart_rate_avg) if r.heart_rate_avg else None,
        "effective_datetime": r.effective_datetime.isoformat(),
        "session": r.session,
        "source": r.source,
        "submitted_by": r.submitted_by,
        "bp_position": r.bp_position,
        "bp_site": r.bp_site,
        "medication_taken": r.medication_taken,
        "consent_version": r.consent_version,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
