"""JWT authentication utilities for ARIA.

Provides token creation, decoding, and FastAPI dependencies for
clinician and patient authentication.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer_scheme = HTTPBearer()

ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    """Return bcrypt hash of a plaintext password."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the bcrypt hash."""
    return _pwd_context.verify(plain, hashed)


def create_access_token(
    data: dict[str, Any],
    secret: str,
    expires_minutes: int,
) -> str:
    """Encode a JWT with an expiry claim.

    Args:
        data: Claims to encode (must include ``sub``).
        secret: HMAC signing secret.
        expires_minutes: Token lifetime in minutes.

    Returns:
        Signed JWT string.
    """
    payload = data.copy()
    payload["exp"] = datetime.now(tz=UTC) + timedelta(minutes=expires_minutes)
    payload["iat"] = datetime.now(tz=UTC)
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def _decode_token(token: str, secret: str) -> dict[str, Any]:
    """Decode and verify a JWT, raising HTTPException on failure.

    Args:
        token: Raw JWT string.
        secret: HMAC signing secret.

    Returns:
        Decoded payload dict.

    Raises:
        HTTPException 401: On expired or invalid token.
    """
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except JWTError as exc:
        logger.warning("JWT decode failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_clinician(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency — validate clinician JWT and return claims.

    Expects ``role == 'clinician'`` or ``role == 'admin'`` in the token.

    Returns:
        Decoded JWT payload dict with at least ``sub`` and ``role``.

    Raises:
        HTTPException 401: On invalid token.
        HTTPException 403: On wrong role.
    """
    payload = _decode_token(credentials.credentials, settings.app_secret_key)
    role = payload.get("role", "")
    if role not in ("clinician", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clinician access required",
        )
    return payload


def get_current_patient(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency — validate patient JWT and return claims.

    Uses the separate PATIENT_JWT_SECRET for blast-radius isolation.
    Falls back to app_secret_key if patient_jwt_secret is not configured.

    Returns:
        Decoded JWT payload dict with at least ``sub`` and ``role``.

    Raises:
        HTTPException 401: On invalid token.
        HTTPException 403: On wrong role.
    """
    secret = settings.patient_jwt_secret or settings.app_secret_key
    payload = _decode_token(credentials.credentials, secret)
    if payload.get("role") != "patient":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Patient access required",
        )
    return payload
