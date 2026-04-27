"""BLE device webhook endpoint (Fix 44).

Accepts BP readings from manufacturer cloud webhooks (Omron Connect,
Withings Health) and inserts them as ARIA readings with source='ble_auto'.

POST /api/ble-webhook
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.audit_event import AuditEvent
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["ble"])


class BleReadingPayload(BaseModel):
    """Vendor-normalised BLE BP reading payload.

    Vendor-specific fields (Omron Connect, Withings) are mapped to this
    schema before calling POST /api/ble-webhook. The endpoint accepts this
    normalised form — no per-vendor parsing happens server-side.
    """

    patient_id: str
    systolic: int = Field(ge=60, le=250)
    diastolic: int = Field(ge=40, le=150)
    heart_rate: int | None = Field(default=None, ge=30, le=220)
    measured_at: datetime
    session: str = Field(default="ad_hoc", pattern="^(morning|evening|ad_hoc)$")
    device_serial: str | None = None


@router.post("/ble-webhook", status_code=201)
async def receive_ble_reading(
    payload: BleReadingPayload,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Accept a BLE device reading and insert it as source='ble_auto'.

    Idempotent: ON CONFLICT DO NOTHING on (patient_id, effective_datetime, source).
    Returns inserted=True on new row, inserted=False on duplicate.
    """
    stmt = (
        pg_insert(Reading)
        .values(
            patient_id=payload.patient_id,
            systolic_1=payload.systolic,
            diastolic_1=payload.diastolic,
            heart_rate_1=payload.heart_rate,
            systolic_avg=payload.systolic,
            diastolic_avg=payload.diastolic,
            heart_rate_avg=payload.heart_rate,
            effective_datetime=payload.measured_at,
            session=payload.session,
            source="ble_auto",
            submitted_by="ble_device",
        )
        .on_conflict_do_nothing(
            index_elements=["patient_id", "effective_datetime", "source"]
        )
    )
    result = await session.execute(stmt)
    inserted = result.rowcount == 1

    if inserted:
        session.add(
            AuditEvent(
                actor_type="system",
                patient_id=payload.patient_id,
                action="reading_ingested",
                resource_type="Reading",
                outcome="success",
                details=f"source=ble_auto device={payload.device_serial or 'unknown'}",
            )
        )

    await session.commit()

    return {
        "inserted": inserted,
        "patient_id": payload.patient_id,
        "measured_at": payload.measured_at.isoformat(),
    }
