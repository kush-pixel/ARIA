"""Medication confirmation endpoints for the ARIA patient PWA.

GET  /api/confirmations/pending          — today's unconfirmed doses
POST /api/confirmations/confirm          — tap-confirm a set of doses
GET  /api/confirmations/ics/{patient_id} — download .ics reminder file

All endpoints require a valid patient JWT (patient_required dependency).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Path
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models.audit_event import AuditEvent
from app.models.medication_confirmation import MedicationConfirmation
from app.utils.ics_generator import generate_ics
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["confirmations"])


# ---------------------------------------------------------------------------
# Patient JWT dependency
# ---------------------------------------------------------------------------


async def patient_required(authorization: str = Header(...)) -> str:
    """Decode and validate a patient Bearer JWT; return patient_id (sub claim).

    Args:
        authorization: HTTP Authorization header value (Bearer <token>).

    Returns:
        patient_id extracted from the token's ``sub`` claim.

    Raises:
        HTTPException 401: Missing, malformed, or expired token.
        HTTPException 403: Token does not carry role="patient".
        HTTPException 500: patient_jwt_secret not configured.
    """
    if not settings.patient_jwt_secret:
        raise HTTPException(status_code=500, detail="Patient auth not configured")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.patient_jwt_secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if payload.get("role") != "patient":
        raise HTTPException(status_code=403, detail="Patient token required")

    return payload["sub"]


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PendingConfirmation(BaseModel):
    """A single unconfirmed scheduled dose."""

    confirmation_id: str
    medication_name: str
    rxnorm_code: str | None
    scheduled_time: str


class ConfirmRequest(BaseModel):
    """Batch tap-confirmation request."""

    patient_id: str
    confirmation_ids: list[str]


class ConfirmResponse(BaseModel):
    """Number of doses confirmed."""

    confirmed: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/confirmations/pending", response_model=list[PendingConfirmation])
async def pending_confirmations(
    patient_id: str = Depends(patient_required),
    db: AsyncSession = Depends(get_session),
) -> list[PendingConfirmation]:
    """Return today's unconfirmed scheduled doses for the authenticated patient.

    Args:
        patient_id: Injected from the patient JWT sub claim.
        db: Async database session.

    Returns:
        List of unconfirmed doses scheduled for today (UTC).
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    result = await db.execute(
        select(MedicationConfirmation)
        .where(
            MedicationConfirmation.patient_id == patient_id,
            MedicationConfirmation.scheduled_time >= today_start,
            MedicationConfirmation.scheduled_time < today_end,
            MedicationConfirmation.confirmed_at.is_(None),
        )
        .order_by(MedicationConfirmation.scheduled_time.asc())
    )
    rows = result.scalars().all()
    return [
        PendingConfirmation(
            confirmation_id=r.confirmation_id,
            medication_name=r.medication_name,
            rxnorm_code=r.rxnorm_code,
            scheduled_time=r.scheduled_time.isoformat(),
        )
        for r in rows
    ]


@router.post("/confirmations/confirm", response_model=ConfirmResponse)
async def confirm_doses(
    body: ConfirmRequest,
    patient_id: str = Depends(patient_required),
    db: AsyncSession = Depends(get_session),
) -> ConfirmResponse:
    """Tap-confirm a set of medication doses.

    Only confirms rows that belong to the authenticated patient — the
    patient_id guard prevents cross-patient confirmation even if a valid
    token is reused with a spoofed patient_id in the body.

    Args:
        body: Confirmation request with patient_id and confirmation_ids.
        patient_id: Injected from the patient JWT (authoritative).
        db: Async database session.

    Returns:
        Number of rows updated.
    """
    if not body.confirmation_ids:
        return ConfirmResponse(confirmed=0)

    now = datetime.now(UTC)

    # Fetch rows to compute minutes_from_schedule
    rows_result = await db.execute(
        select(MedicationConfirmation).where(
            MedicationConfirmation.confirmation_id.in_(body.confirmation_ids),
            MedicationConfirmation.patient_id == patient_id,
            MedicationConfirmation.confirmed_at.is_(None),
        )
    )
    rows = rows_result.scalars().all()

    confirmed = 0
    for row in rows:
        delta_minutes = int((now - row.scheduled_time).total_seconds() / 60)
        await db.execute(
            update(MedicationConfirmation)
            .where(MedicationConfirmation.confirmation_id == row.confirmation_id)
            .values(
                confirmed_at=now,
                confirmation_type="tap",
                minutes_from_schedule=delta_minutes,
            )
        )
        confirmed += 1

    if confirmed:
        db.add(AuditEvent(
            actor_type="system",
            patient_id=patient_id,
            action="reading_ingested",
            resource_type="MedicationConfirmation",
            outcome="success",
            details=f"tap-confirmed {confirmed} dose(s)",
        ))
        await db.commit()

    logger.info(
        "confirm_doses: patient=%s confirmed=%d", patient_id, confirmed
    )
    return ConfirmResponse(confirmed=confirmed)


@router.get("/confirmations/ics/{patient_id}")
async def download_ics(
    patient_id: str = Path(..., description="Patient ID"),
    _auth_patient_id: str = Depends(patient_required),
    db: AsyncSession = Depends(get_session),
) -> Response:
    """Generate and return an iCalendar (.ics) medication reminder file.

    The authenticated patient can only download their own .ics file —
    the path patient_id is validated against the JWT sub claim.

    Args:
        patient_id: From the URL path.
        _auth_patient_id: Injected from patient JWT (validated below).
        db: Async database session.

    Returns:
        text/calendar response with Content-Disposition attachment header.
    """
    if _auth_patient_id != patient_id:
        raise HTTPException(status_code=403, detail="Access denied")

    pwa_base_url = settings.patient_app_url or "http://localhost:3001"
    ics_content = await generate_ics(patient_id, db, pwa_base_url)

    return Response(
        content=ics_content,
        media_type="text/calendar",
        headers={
            "Content-Disposition": 'attachment; filename="aria-medications.ics"',
        },
    )
