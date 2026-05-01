"""Patient authentication endpoint for ARIA.

POST /api/auth/patient-token — exchange a research ID for a short-lived JWT.

Patient tokens are signed with a separate secret (patient_jwt_secret) so a
compromised patient token cannot be used on clinician endpoints.
"""

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.limiter import limiter
from app.models.patient import Patient
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["auth"])

_TOKEN_EXPIRY_SECONDS: int = 8 * 60 * 60  # 8 hours


class PatientTokenRequest(BaseModel):
    """Request body for patient token exchange."""

    research_id: str


class PatientTokenResponse(BaseModel):
    """Response containing the patient JWT."""

    access_token: str
    expires_in: int


@router.post("/auth/patient-token", response_model=PatientTokenResponse)
@limiter.limit("5/minute")
async def patient_token(
    request: Request,
    body: PatientTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> PatientTokenResponse:
    """Exchange a research ID for a patient JWT.

    Returns a signed JWT valid for 8 hours. The token carries role="patient"
    and is signed with patient_jwt_secret — separate from the clinician secret
    so blast radius is isolated.

    Args:
        request: FastAPI request (required by slowapi rate limiter).
        body: JSON body containing research_id.
        session: Async database session.

    Raises:
        HTTPException 404: research_id not found in patients table.
        HTTPException 500: patient_jwt_secret not configured.
    """
    if not settings.patient_jwt_secret:
        logger.error("patient_jwt_secret is not configured")
        raise HTTPException(status_code=500, detail="Patient auth not configured")

    result = await session.execute(
        select(Patient).where(Patient.patient_id == body.research_id)
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Research ID not found")

    now = datetime.now(UTC)
    payload = {
        "sub": body.research_id,
        "role": "patient",
        "iat": now,
        "exp": now + timedelta(seconds=_TOKEN_EXPIRY_SECONDS),
    }
    token = jwt.encode(payload, settings.patient_jwt_secret, algorithm="HS256")

    logger.info("patient_token issued for patient=%s", body.research_id)
    return PatientTokenResponse(access_token=token, expires_in=_TOKEN_EXPIRY_SECONDS)
