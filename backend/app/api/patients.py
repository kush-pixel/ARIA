"""Patient API routes for ARIA.

GET  /api/patients                          — paginated patient list, server-side search/filter
GET  /api/patients/{id}                     — single patient record
PATCH /api/patients/{id}/appointment        — update next_appointment datetime
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.audit_event import AuditEvent
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


class TierOverrideRequest(BaseModel):
    """Request body for PATCH /patients/{patient_id}/tier."""

    risk_tier: Literal["high", "medium", "low"]
    reason: str = Field(..., min_length=1, max_length=500)


# How long a clinician demotion suppresses nightly reclassification
_CLINICIAN_SUPPRESSION_DAYS: int = 28


@router.get("/patients")
async def list_patients(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    search: str = Query(default=""),
    tier: Literal["all", "high", "medium", "low"] = Query(default="all"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return enrolled patients with server-side pagination, search, and tier filter.

    Search matches against patient_id, name, and active_problems (case-insensitive).
    Tier filter is applied after search. Results are sorted: risk_tier first
    (high > medium > low), then risk_score DESC within tier.

    Args:
        page: 1-based page number.
        page_size: Rows per page (1–100, default 25).
        search: Substring match against patient_id, name, active_problems.
        tier: Restrict to a single risk tier, or "all" for no filter.
        session: Async database session.

    Returns:
        Dict with keys: patients, total, page, page_size, total_pages, counts.
        counts reflects search-filtered totals per tier (ignores the tier param)
        so the tab badges stay accurate while a tier filter is active.
    """
    today = datetime.now(UTC).date()

    # ── Active-briefing subquery (reused for trend_avg_systolic) ─────────────
    latest_sq = (
        select(
            Briefing.patient_id.label("pid"),
            func.max(Briefing.generated_at).label("latest_at"),
        )
        .where(
            or_(
                Briefing.appointment_date.is_(None),
                Briefing.appointment_date >= today,
            )
        )
        .group_by(Briefing.patient_id)
        .subquery()
    )
    briefing_result = await session.execute(
        select(Briefing.patient_id, Briefing.llm_response)
        .join(
            latest_sq,
            and_(
                Briefing.patient_id == latest_sq.c.pid,
                Briefing.generated_at == latest_sq.c.latest_at,
            ),
        )
    )
    briefing_map: dict[str, float | None] = {}
    for row in briefing_result:
        pid, llm = row[0], row[1] or {}
        briefing_map[pid] = llm.get("trend_avg_systolic")

    # ── Base search query (search filter only, no tier) ───────────────────────
    base_stmt = (
        select(Patient)
        .outerjoin(ClinicalContext, Patient.patient_id == ClinicalContext.patient_id)
    )
    if search:
        q = f"%{search.lower()}%"
        base_stmt = base_stmt.where(
            or_(
                func.lower(Patient.patient_id).like(q),
                func.lower(Patient.name).like(q),
                func.lower(
                    func.array_to_string(ClinicalContext.active_problems, " ")
                ).like(q),
            )
        )

    # ── Per-tier counts (search-filtered, tier-agnostic) ─────────────────────
    counts_stmt = (
        select(Patient.risk_tier, func.count(Patient.patient_id))
        .outerjoin(ClinicalContext, Patient.patient_id == ClinicalContext.patient_id)
    )
    if search:
        counts_stmt = counts_stmt.where(
            or_(
                func.lower(Patient.patient_id).like(q),
                func.lower(Patient.name).like(q),
                func.lower(
                    func.array_to_string(ClinicalContext.active_problems, " ")
                ).like(q),
            )
        )
    counts_stmt = counts_stmt.group_by(Patient.risk_tier)
    counts_result = await session.execute(counts_stmt)
    tier_counts: dict[str, int] = {row[0]: row[1] for row in counts_result}
    total_all = sum(tier_counts.values())
    counts = {
        "all": total_all,
        "high": tier_counts.get("high", 0),
        "medium": tier_counts.get("medium", 0),
        "low": tier_counts.get("low", 0),
    }

    # ── Apply tier filter, sort, paginate ────────────────────────────────────
    filtered_stmt = base_stmt
    if tier != "all":
        filtered_stmt = filtered_stmt.where(Patient.risk_tier == tier)

    total = counts.get(tier, total_all) if tier != "all" else total_all
    total_pages = max(1, math.ceil(total / page_size))
    safe_page = min(page, total_pages)
    offset = (safe_page - 1) * page_size

    # Sort: tier order then risk_score DESC — done in Python to reuse _TIER_ORDER
    all_filtered_result = await session.execute(filtered_stmt)
    all_filtered = list(all_filtered_result.scalars().all())

    def _sort_key(p: Patient) -> tuple[int, float]:
        return (_TIER_ORDER.get(p.risk_tier, 9), -(float(p.risk_score) if p.risk_score is not None else 0.0))

    sorted_patients = sorted(all_filtered, key=_sort_key)
    page_slice = sorted_patients[offset: offset + page_size]

    # ── Fetch active_problems for the page slice only ─────────────────────────
    if page_slice:
        page_ids = [p.patient_id for p in page_slice]
        cc_result = await session.execute(
            select(ClinicalContext.patient_id, ClinicalContext.active_problems)
            .where(ClinicalContext.patient_id.in_(page_ids))
        )
        problems_map: dict[str, list[str]] = {row[0]: row[1] or [] for row in cc_result}
    else:
        problems_map = {}

    patients_with_briefing = set(briefing_map.keys())

    return {
        "patients": [
            _serialise(
                p,
                has_briefing=p.patient_id in patients_with_briefing,
                trend_avg_systolic=briefing_map.get(p.patient_id),
                active_problems=problems_map.get(p.patient_id, []),
            )
            for p in page_slice
        ],
        "total": total,
        "page": safe_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "counts": counts,
    }


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
    today_get = datetime.now(UTC).date()
    briefing_check = await session.execute(
        select(Briefing.llm_response)
        .where(
            Briefing.patient_id == patient_id,
            or_(
                Briefing.appointment_date.is_(None),
                Briefing.appointment_date >= today_get,
            ),
        )
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )
    llm_response = briefing_check.scalar_one_or_none()
    return _serialise(
        patient,
        has_briefing=llm_response is not None,
        trend_avg_systolic=llm_response.get("trend_avg_systolic") if llm_response else None,
    )


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
    today_appt = datetime.now(UTC).date()
    briefing_check2 = await session.execute(
        select(Briefing.llm_response)
        .where(
            Briefing.patient_id == patient_id,
            or_(
                Briefing.appointment_date.is_(None),
                Briefing.appointment_date >= today_appt,
            ),
        )
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )
    llm2 = briefing_check2.scalar_one_or_none()
    return _serialise(
        patient,
        has_briefing=llm2 is not None,
        trend_avg_systolic=llm2.get("trend_avg_systolic") if llm2 else None,
    )


@router.patch("/patients/{patient_id}/tier")
async def override_tier(
    patient_id: str,
    body: TierOverrideRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Clinician override for a patient's risk tier.

    Allows a clinician to manually promote or demote a patient's tier with a
    mandatory reason string.  The override is blocked for patients whose tier
    is controlled by a clinical safety auto-override (CHF, stroke, TIA,
    haemorrhagic stroke) — those require updating the EHR problem list and
    re-ingesting.

    Demotion (e.g. high → medium, medium → low) sets a 28-day suppression
    window during which the nightly reclassification job will not promote the
    patient back, unless the risk score exceeds 85 (break-glass).
    28 days aligns with NICE NG136 §1.6.3 (4-week review standard).

    Args:
        patient_id: Patient's MED_REC_NO.
        body: JSON body with ``risk_tier`` and ``reason``.

    Returns:
        Updated patient record dict.

    Raises:
        HTTPException 404: Patient not found.
        HTTPException 409: Auto-override is active — EHR update required.
    """
    result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    if patient.tier_override_source == "system":
        raise HTTPException(
            status_code=409,
            detail=(
                "Auto-override active (CHF / Stroke / TIA / haemorrhagic stroke). "
                "Update the EHR problem list and re-ingest the FHIR bundle to change tier."
            ),
        )

    old_tier = patient.risk_tier
    new_tier = body.risk_tier
    now = datetime.now(UTC)

    patient.risk_tier = new_tier
    patient.tier_override = body.reason
    patient.tier_override_source = "clinician"

    is_demotion = _TIER_ORDER.get(new_tier, 9) > _TIER_ORDER.get(old_tier, 9)
    patient.tier_override_suppressed_until = (
        now + timedelta(days=_CLINICIAN_SUPPRESSION_DAYS) if is_demotion else None
    )

    session.add(
        AuditEvent(
            actor_type="clinician",
            patient_id=patient_id,
            action="tier_override",
            resource_type="Patient",
            resource_id=patient_id,
            outcome="success",
            details=f"tier={old_tier}→{new_tier} reason={body.reason}",
        )
    )
    await session.commit()
    logger.info(
        "tier_override: patient=%s %s→%s by clinician suppressed_until=%s",
        patient_id,
        old_tier,
        new_tier,
        patient.tier_override_suppressed_until.isoformat()
        if patient.tier_override_suppressed_until
        else "none",
    )

    today_tier = datetime.now(UTC).date()
    briefing_check = await session.execute(
        select(Briefing.llm_response)
        .where(
            Briefing.patient_id == patient_id,
            or_(
                Briefing.appointment_date.is_(None),
                Briefing.appointment_date >= today_tier,
            ),
        )
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )
    llm_tier = briefing_check.scalar_one_or_none()
    return _serialise(
        patient,
        has_briefing=llm_tier is not None,
        trend_avg_systolic=llm_tier.get("trend_avg_systolic") if llm_tier else None,
    )


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


def _serialise(
    p: Patient,
    has_briefing: bool = False,
    trend_avg_systolic: float | None = None,
    active_problems: list[str] | None = None,
) -> dict:
    return {
        "patient_id": p.patient_id,
        "name": p.name,
        "gender": p.gender,
        "age": p.age,
        "risk_tier": p.risk_tier,
        "tier_override": p.tier_override,
        "tier_override_source": p.tier_override_source,
        "tier_override_suppressed_until": (
            p.tier_override_suppressed_until.isoformat()
            if p.tier_override_suppressed_until
            else None
        ),
        "risk_score": float(p.risk_score) if p.risk_score is not None else None,
        "risk_score_computed_at": p.risk_score_computed_at.isoformat() if p.risk_score_computed_at else None,
        "monitoring_active": p.monitoring_active,
        "next_appointment": p.next_appointment.isoformat() if p.next_appointment else None,
        "enrolled_at": p.enrolled_at.isoformat() if p.enrolled_at else None,
        "enrolled_by": p.enrolled_by,
        "has_briefing": has_briefing,
        "trend_avg_systolic": trend_avg_systolic,
        "active_problems": active_problems or [],
    }
