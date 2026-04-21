"""Adherence API route for ARIA.

GET /api/adherence/{patient_id}  — per-medication adherence breakdown over 28 days
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.medication_confirmation import MedicationConfirmation
from app.models.patient import Patient
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["adherence"])

_WINDOW_DAYS = 28


@router.get("/adherence/{patient_id}")
async def get_adherence(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return per-medication adherence stats for the last 28 days.

    Each entry contains medication_name, rxnorm_code, adherence_pct,
    total_doses, and confirmed_doses — matching the frontend AdherenceData type.
    """
    patient_result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    if patient_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    cutoff = datetime.now(UTC) - timedelta(days=_WINDOW_DAYS)

    result = await session.execute(
        select(
            MedicationConfirmation.medication_name,
            MedicationConfirmation.rxnorm_code,
            func.count(MedicationConfirmation.confirmation_id).label("total_doses"),
            func.count(MedicationConfirmation.confirmed_at).label("confirmed_doses"),
        )
        .where(
            MedicationConfirmation.patient_id == patient_id,
            MedicationConfirmation.scheduled_time >= cutoff,
        )
        .group_by(
            MedicationConfirmation.medication_name,
            MedicationConfirmation.rxnorm_code,
        )
        .order_by(MedicationConfirmation.medication_name)
    )

    rows = result.all()
    output = []
    for row in rows:
        total = row.total_doses or 0
        confirmed = row.confirmed_doses or 0
        adherence_pct = round((confirmed / total * 100), 1) if total > 0 else 0.0
        output.append({
            "medication_name": row.medication_name,
            "rxnorm_code": row.rxnorm_code,
            "adherence_pct": adherence_pct,
            "total_doses": total,
            "confirmed_doses": confirmed,
        })

    return output
