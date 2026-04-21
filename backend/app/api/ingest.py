"""FHIR Bundle ingestion API route for ARIA.

POST /api/ingest  — accept a FHIR R4 Bundle JSON body and ingest into PostgreSQL.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.fhir.ingestion import ingest_fhir_bundle
from app.services.fhir.validator import validate_fhir_bundle
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["ingest"])


@router.post("/ingest", status_code=201)
async def ingest_bundle(
    bundle: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Validate and ingest a FHIR R4 Bundle into ARIA PostgreSQL tables.

    Idempotent — safe to call multiple times with the same bundle.
    Returns a summary of what was inserted or skipped.
    """
    errors = validate_fhir_bundle(bundle)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Invalid FHIR Bundle", "errors": errors},
        )

    try:
        summary = await ingest_fhir_bundle(bundle, session)
    except Exception as exc:
        logger.exception("Bundle ingestion failed: %s", exc)
        raise HTTPException(status_code=500, detail="Ingestion failed — see server logs")

    return summary
