"""Gap explanation API routes (Fix 41).

Clinicians can record why a reading gap occurred — device issue, travel,
illness, unknown, or non-compliance — to distinguish benign gaps from
genuine monitoring failures in the briefing and alert context.

GET  /api/gap-explanations?patient_id=   — list explanations for a patient
POST /api/gap-explanations               — record a new gap explanation
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from app.db.session import get_session
from app.models.gap_explanation import GapExplanation
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["gap-explanations"])


class GapExplanationRequest(BaseModel):
    """Body for recording a gap explanation."""

    patient_id: str
    gap_start: date
    gap_end: date
    reason: Literal["device_issue", "travel", "illness", "unknown", "non_compliance"]
    notes: str | None = Field(default=None, max_length=2000)
    reported_by: Literal["clinician", "patient", "system"] = "clinician"
    reporter_id: str | None = None

    @model_validator(mode="after")
    def gap_end_after_start(self) -> "GapExplanationRequest":
        if self.gap_end < self.gap_start:
            raise ValueError("gap_end must be on or after gap_start")
        return self


@router.get("/gap-explanations")
async def list_gap_explanations(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return all gap explanations for a patient, newest first."""
    result = await session.execute(
        select(GapExplanation)
        .where(GapExplanation.patient_id == patient_id)
        .order_by(GapExplanation.gap_start.desc())
    )
    explanations = result.scalars().all()
    return [_serialise(e) for e in explanations]


@router.post("/gap-explanations", status_code=201)
async def create_gap_explanation(
    payload: GapExplanationRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record a gap explanation for a patient reading gap."""
    explanation = GapExplanation(
        patient_id=payload.patient_id,
        gap_start=payload.gap_start,
        gap_end=payload.gap_end,
        reason=payload.reason,
        notes=payload.notes,
        reported_by=payload.reported_by,
        reporter_id=payload.reporter_id,
    )
    session.add(explanation)
    await session.commit()

    logger.info(
        "gap_explanation created: patient=%s gap=%s–%s reason=%s",
        payload.patient_id,
        payload.gap_start,
        payload.gap_end,
        payload.reason,
    )
    return _serialise(explanation)


@router.delete("/gap-explanations/{explanation_id}", status_code=200)
async def delete_gap_explanation(
    explanation_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Delete a gap explanation by ID."""
    result = await session.execute(
        select(GapExplanation).where(GapExplanation.explanation_id == explanation_id)
    )
    explanation = result.scalar_one_or_none()
    if explanation is None:
        raise HTTPException(status_code=404, detail="Gap explanation not found")

    await session.delete(explanation)
    await session.commit()
    return {"status": "deleted", "explanation_id": explanation_id}


def _serialise(e: GapExplanation) -> dict:
    return {
        "explanation_id": e.explanation_id,
        "patient_id": e.patient_id,
        "gap_start": e.gap_start.isoformat(),
        "gap_end": e.gap_end.isoformat(),
        "reason": e.reason,
        "notes": e.notes,
        "reported_by": e.reported_by,
        "reporter_id": e.reporter_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
