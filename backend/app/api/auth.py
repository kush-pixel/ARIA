"""Authentication API routes for ARIA.

POST /api/auth/clinician-token  — username + password → JWT
POST /api/auth/patient-token    — research_id → JWT
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models.clinician import Clinician
from app.models.patient import Patient
from app.utils.auth_utils import create_access_token, verify_password
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["auth"])


class ClinicianLoginRequest(BaseModel):
    """Request body for clinician login."""

    username: str
    password: str


class PatientTokenRequest(BaseModel):
    """Request body for patient token."""

    research_id: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/auth/clinician-token", response_model=TokenResponse)
async def clinician_login(
    body: ClinicianLoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Authenticate a clinician and return a signed JWT.

    Args:
        body: Username and plaintext password.
        session: Async DB session.

    Returns:
        TokenResponse with access_token and expiry.

    Raises:
        HTTPException 401: On invalid credentials or inactive account.
    """
    result = await session.execute(
        select(Clinician).where(Clinician.username == body.username)
    )
    clinician = result.scalar_one_or_none()

    if clinician is None or not verify_password(body.password, clinician.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not clinician.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive",
        )

    await session.execute(
        update(Clinician)
        .where(Clinician.clinician_id == clinician.clinician_id)
        .values(last_login=datetime.now(UTC))
    )
    await session.commit()

    token = create_access_token(
        data={"sub": clinician.clinician_id, "username": clinician.username, "role": clinician.role},
        secret=settings.app_secret_key,
        expires_minutes=settings.jwt_expiry_minutes,
    )

    logger.info("Clinician login: username=%s role=%s", clinician.username, clinician.role)

    return TokenResponse(access_token=token, expires_in=settings.jwt_expiry_minutes * 60)


@router.post("/auth/patient-token", response_model=TokenResponse)
async def patient_token(
    body: PatientTokenRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Issue a patient JWT for a valid research ID.

    Args:
        body: research_id (maps to patient_id in the patients table).
        session: Async DB session.

    Returns:
        TokenResponse with access_token and 8-hour expiry.

    Raises:
        HTTPException 404: If research_id does not exist in patients table.
    """
    result = await session.execute(
        select(Patient).where(Patient.patient_id == body.research_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research ID not found",
        )

    secret = settings.patient_jwt_secret or settings.app_secret_key
    token = create_access_token(
        data={"sub": body.research_id, "role": "patient"},
        secret=secret,
        expires_minutes=480,  # 8 hours
    )

    logger.info("Patient token issued: research_id=%s", body.research_id)

    return TokenResponse(access_token=token, expires_in=480 * 60)
