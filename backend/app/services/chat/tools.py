"""Read-only DB tool executors and Anthropic tool schemas for the ARIA chatbot.

All tools are scoped to a single patient_id — the agent cannot query other patients.
Every function returns a typed dict; empty results return structured empty responses
so the agent can acknowledge data gaps rather than fabricate answers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.briefing import Briefing
from app.models.clinical_context import ClinicalContext
from app.models.medication_confirmation import MedicationConfirmation
from app.models.reading import Reading
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ── Tool executors ─────────────────────────────────────────────────────────────

async def get_patient_readings(
    session: AsyncSession,
    patient_id: str,
    days: int = 28,
) -> dict[str, Any]:
    """Fetch summarised home BP readings for the past N days.

    Args:
        session: Async DB session.
        patient_id: Patient identifier (scoped).
        days: Lookback window in days.

    Returns:
        Dict with avg_systolic, avg_diastolic, reading_count, trend_direction,
        morning_avg, evening_avg, date_range, and raw readings list.
    """
    from datetime import UTC, datetime, timedelta
    cutoff = datetime.now(UTC) - timedelta(days=days)

    result = await session.execute(
        select(Reading)
        .where(Reading.patient_id == patient_id)
        .where(Reading.effective_datetime >= cutoff)
        .order_by(Reading.effective_datetime.asc())
    )
    readings = list(result.scalars().all())

    if not readings:
        return {
            "data_available": False,
            "message": f"No home BP readings found in the past {days} days.",
            "reading_count": 0,
        }

    systolics = [float(r.systolic_avg) for r in readings]
    diastolics = [float(r.diastolic_avg) for r in readings]

    morning = [float(r.systolic_avg) for r in readings if r.session == "morning"]
    evening = [float(r.systolic_avg) for r in readings if r.session == "evening"]

    # Simple trend: compare first third vs last third
    n = len(systolics)
    third = max(1, n // 3)
    early_avg = sum(systolics[:third]) / third
    late_avg = sum(systolics[-third:]) / third
    if late_avg > early_avg + 3:
        trend = "increasing"
    elif late_avg < early_avg - 3:
        trend = "decreasing"
    else:
        trend = "stable"

    return {
        "data_available": True,
        "reading_count": n,
        "avg_systolic": round(sum(systolics) / n, 1),
        "avg_diastolic": round(sum(diastolics) / n, 1),
        "trend_direction": trend,
        "morning_avg_systolic": round(sum(morning) / len(morning), 1) if morning else None,
        "evening_avg_systolic": round(sum(evening) / len(evening), 1) if evening else None,
        "date_range": {
            "from": readings[0].effective_datetime.date().isoformat(),
            "to": readings[-1].effective_datetime.date().isoformat(),
        },
        "window_days": days,
    }


async def get_patient_alerts(
    session: AsyncSession,
    patient_id: str,
) -> dict[str, Any]:
    """Fetch active unacknowledged alerts.

    Args:
        session: Async DB session.
        patient_id: Patient identifier (scoped).

    Returns:
        Dict with alert list and count.
    """
    result = await session.execute(
        select(Alert)
        .where(Alert.patient_id == patient_id)
        .where(Alert.acknowledged_at.is_(None))
        .order_by(Alert.triggered_at.desc())
    )
    alerts = list(result.scalars().all())

    if not alerts:
        return {"data_available": False, "message": "No active unacknowledged alerts.", "alert_count": 0}

    return {
        "data_available": True,
        "alert_count": len(alerts),
        "alerts": [
            {
                "alert_type": a.alert_type,
                "gap_days": a.gap_days,
                "systolic_avg": float(a.systolic_avg) if a.systolic_avg else None,
                "triggered_at": a.triggered_at.date().isoformat(),
                "off_hours": a.off_hours,
                "escalated": a.escalated,
            }
            for a in alerts
        ],
    }


async def get_medication_history(
    session: AsyncSession,
    patient_id: str,
) -> dict[str, Any]:
    """Fetch full medication timeline from clinical_context.med_history.

    Args:
        session: Async DB session.
        patient_id: Patient identifier (scoped).

    Returns:
        Dict with med_history list and last_med_change date.
    """
    result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = result.scalar_one_or_none()

    if ctx is None:
        return {"data_available": False, "message": "No clinical context found."}

    med_history = ctx.med_history or []
    if not med_history:
        return {
            "data_available": False,
            "message": "No medication history recorded.",
            "current_medications": ctx.current_medications or [],
        }

    # Find most recent change
    dates = [entry.get("date") for entry in med_history if entry.get("date")]
    last_change = max(dates) if dates else None

    return {
        "data_available": True,
        "current_medications": ctx.current_medications or [],
        "last_med_change": last_change,
        "last_med_change_recorded": ctx.last_med_change.isoformat() if ctx.last_med_change else None,
        "med_history": sorted(med_history, key=lambda x: x.get("date", ""), reverse=True)[:20],
    }


async def get_adherence_summary(
    session: AsyncSession,
    patient_id: str,
    days: int = 28,
) -> dict[str, Any]:
    """Compute per-medication adherence rates from confirmation records.

    Args:
        session: Async DB session.
        patient_id: Patient identifier (scoped).
        days: Lookback window in days.

    Returns:
        Dict with per-med adherence rates and overall rate.
    """
    from datetime import UTC, datetime, timedelta
    cutoff = datetime.now(UTC) - timedelta(days=days)

    result = await session.execute(
        select(MedicationConfirmation)
        .where(MedicationConfirmation.patient_id == patient_id)
        .where(MedicationConfirmation.scheduled_time >= cutoff)
    )
    confirmations = list(result.scalars().all())

    if not confirmations:
        return {
            "data_available": False,
            "message": f"No medication confirmation data in the past {days} days.",
            "window_days": days,
        }

    by_med: dict[str, dict[str, int]] = {}
    for conf in confirmations:
        med = conf.medication_name
        if med not in by_med:
            by_med[med] = {"scheduled": 0, "confirmed": 0}
        by_med[med]["scheduled"] += 1
        if conf.confirmed_at is not None:
            by_med[med]["confirmed"] += 1

    per_med = {
        med: {
            "scheduled": counts["scheduled"],
            "confirmed": counts["confirmed"],
            "rate_pct": round(counts["confirmed"] / counts["scheduled"] * 100, 1),
        }
        for med, counts in by_med.items()
    }

    total_scheduled = sum(c["scheduled"] for c in by_med.values())
    total_confirmed = sum(c["confirmed"] for c in by_med.values())
    overall_pct = round(total_confirmed / total_scheduled * 100, 1) if total_scheduled else 0.0

    return {
        "data_available": True,
        "overall_adherence_pct": overall_pct,
        "per_medication": per_med,
        "window_days": days,
        "total_doses_scheduled": total_scheduled,
        "total_doses_confirmed": total_confirmed,
    }


async def get_clinical_context(
    session: AsyncSession,
    patient_id: str,
) -> dict[str, Any]:
    """Fetch clinical context — problems, labs, allergies, vitals.

    Args:
        session: Async DB session.
        patient_id: Patient identifier (scoped).

    Returns:
        Dict with active_problems, overdue_labs, recent_labs, last clinic vitals.
    """
    result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = result.scalar_one_or_none()

    if ctx is None:
        return {"data_available": False, "message": "No clinical context found."}

    return {
        "data_available": True,
        "active_problems": ctx.active_problems or [],
        "problem_codes": ctx.problem_codes or [],
        "overdue_labs": ctx.overdue_labs or [],
        "recent_labs": ctx.recent_labs or {},
        "allergies": ctx.allergies or [],
        "allergy_reactions": ctx.allergy_reactions or [],
        "last_visit_date": ctx.last_visit_date.isoformat() if ctx.last_visit_date else None,
        "last_clinic_systolic": ctx.last_clinic_systolic,
        "last_clinic_diastolic": ctx.last_clinic_diastolic,
        "last_clinic_pulse": ctx.last_clinic_pulse,
        "social_context": ctx.social_context,
    }


async def get_briefing(
    session: AsyncSession,
    patient_id: str,
) -> dict[str, Any]:
    """Fetch the latest Layer 1 briefing payload.

    Args:
        session: Async DB session.
        patient_id: Patient identifier (scoped).

    Returns:
        Dict with briefing payload fields.
    """
    result = await session.execute(
        select(Briefing)
        .where(Briefing.patient_id == patient_id)
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )
    briefing = result.scalar_one_or_none()

    if briefing is None:
        return {"data_available": False, "message": "No briefing found for this patient."}

    payload = briefing.llm_response or {}
    return {
        "data_available": True,
        "generated_at": briefing.generated_at.date().isoformat() if briefing.generated_at else None,
        "trend_summary": payload.get("trend_summary"),
        "medication_status": payload.get("medication_status"),
        "adherence_summary": payload.get("adherence_summary"),
        "active_problems": payload.get("active_problems", []),
        "overdue_labs": payload.get("overdue_labs", []),
        "visit_agenda": payload.get("visit_agenda", []),
        "urgent_flags": payload.get("urgent_flags", []),
        "risk_score": payload.get("risk_score"),
        "data_limitations": payload.get("data_limitations"),
    }


# ── Anthropic tool schemas ─────────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_patient_readings",
        "description": (
            "Fetch summarised home BP readings for a patient over the past N days. "
            "Returns average systolic/diastolic, trend direction, morning vs evening split, "
            "reading count, and date range. Use when the clinician asks about BP trends, "
            "recent readings, or patterns over a specific period."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back. Default 28. Use 90 for 3-month view, 7 for last week.",
                    "default": 28,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_patient_alerts",
        "description": (
            "Fetch active unacknowledged alerts for the patient. "
            "Returns alert types (gap_urgent, inertia, deterioration, adherence), "
            "triggered dates, and escalation status. Use when asked about flags, alerts, or why something was flagged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_medication_history",
        "description": (
            "Fetch the patient's full medication timeline from the EHR. "
            "Returns current medications, last change date, and chronological history of adds/modifies/removes. "
            "Use when asked about medication changes, current regimen, or when a drug was started or stopped."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_adherence_summary",
        "description": (
            "Compute per-medication adherence rates from confirmation records over the past N days. "
            "Returns overall adherence percentage and per-medication breakdown. "
            "Use when asked about missed doses, adherence rates, or medication compliance patterns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look back. Default 28.",
                    "default": 28,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_clinical_context",
        "description": (
            "Fetch the patient's clinical context: active problems, overdue labs, recent lab values, "
            "allergies, and last clinic vitals. Use when asked about comorbidities, lab results, "
            "allergies, or clinical background."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_briefing",
        "description": (
            "Fetch the latest pre-visit briefing payload generated by the ARIA Layer 1 rule engine. "
            "Returns trend summary, medication status, adherence summary, visit agenda, urgent flags, "
            "and risk score. Use when asked why something was flagged or what ARIA's assessment is."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ── Tool dispatcher ────────────────────────────────────────────────────────────

async def dispatch_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    patient_id: str,
    session: AsyncSession,
) -> dict[str, Any]:
    """Execute a named tool and return its result.

    Args:
        tool_name: One of the six tool names.
        tool_input: Parameters from the LLM tool_use block.
        patient_id: Patient scope — all tools are locked to this ID.
        session: Async DB session.

    Returns:
        Tool result dict.

    Raises:
        ValueError: If tool_name is not recognised.
    """
    days = int(tool_input.get("days", 28))

    if tool_name == "get_patient_readings":
        return await get_patient_readings(session, patient_id, days=days)
    if tool_name == "get_patient_alerts":
        return await get_patient_alerts(session, patient_id)
    if tool_name == "get_medication_history":
        return await get_medication_history(session, patient_id)
    if tool_name == "get_adherence_summary":
        return await get_adherence_summary(session, patient_id, days=days)
    if tool_name == "get_clinical_context":
        return await get_clinical_context(session, patient_id)
    if tool_name == "get_briefing":
        return await get_briefing(session, patient_id)

    raise ValueError(f"Unknown tool: {tool_name}")
