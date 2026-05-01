"""ICS calendar file generator for ARIA medication reminders.

Generates one VEVENT per dosing session (morning / evening) with a
RRULE:FREQ=DAILY recurrence and a 5-minute VALARM so the patient's
phone fires a native alarm at dose time. Grouping by session (not per
drug) means one notification per dosing time regardless of how many
medications the patient takes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.services.generator.confirmation_generator import (
    _determine_hours,
    _is_medication,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# UTC hour boundaries for session grouping
_MORNING_START: int = 6
_MORNING_END: int = 12   # exclusive


def _fmt_dt(dt: datetime) -> str:
    """Format a UTC datetime to iCalendar YYYYMMDDTHHMMSSZ."""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _build_vevent(
    uid: str,
    summary: str,
    dtstart: datetime,
    description: str,
) -> str:
    """Return a VEVENT block as a string."""
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DTSTART:{_fmt_dt(dtstart)}\r\n"
        "RRULE:FREQ=DAILY\r\n"
        f"DESCRIPTION:{description}\r\n"
        "BEGIN:VALARM\r\n"
        "TRIGGER:-PT5M\r\n"
        "ACTION:DISPLAY\r\n"
        f"DESCRIPTION:{summary}\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
    )


async def generate_ics(
    patient_id: str,
    session: AsyncSession,
    pwa_base_url: str,
) -> str:
    """Generate an iCalendar file with daily medication reminder events.

    Creates one VEVENT per dosing session that has at least one medication
    scheduled. Each event recurs daily and includes a 5-minute VALARM.

    Args:
        patient_id: Patient whose medications to include.
        session: Active async SQLAlchemy session.
        pwa_base_url: Base URL of the patient PWA (used in the deep link).

    Returns:
        iCalendar file content as a string (text/calendar).
    """
    cc_result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    cc = cc_result.scalar_one_or_none()

    medications: list[str] = []
    if cc and cc.current_medications:
        medications = [m for m in cc.current_medications if _is_medication(m)]

    # Group medications by session based on their dosing hours
    morning_meds: list[str] = []
    evening_meds: list[str] = []

    for med in medications:
        hours = _determine_hours(med)
        for hour in hours:
            if _MORNING_START <= hour < _MORNING_END:
                if med not in morning_meds:
                    morning_meds.append(med)
            else:
                if med not in evening_meds:
                    evening_meds.append(med)

    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    vevents: list[str] = []

    if morning_meds:
        dtstart = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 8, 0, 0, tzinfo=UTC)
        med_list = "\\n".join(f"• {m}" for m in morning_meds)
        deep_link = f"{pwa_base_url}/confirm?session=morning"
        description = f"{med_list}\\n\\nTap to confirm:\\n{deep_link}"
        vevents.append(_build_vevent(
            uid=f"aria-morning-{patient_id}@aria.local",
            summary="Morning medications (ARIA)",
            dtstart=dtstart,
            description=description,
        ))

    if evening_meds:
        dtstart = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 20, 0, 0, tzinfo=UTC)
        med_list = "\\n".join(f"• {m}" for m in evening_meds)
        deep_link = f"{pwa_base_url}/confirm?session=evening"
        description = f"{med_list}\\n\\nTap to confirm:\\n{deep_link}"
        vevents.append(_build_vevent(
            uid=f"aria-evening-{patient_id}@aria.local",
            summary="Evening medications (ARIA)",
            dtstart=dtstart,
            description=description,
        ))

    logger.info(
        "ics_generator: patient=%s morning_meds=%d evening_meds=%d",
        patient_id,
        len(morning_meds),
        len(evening_meds),
    )

    now_str = _fmt_dt(datetime.now(UTC))
    calendar = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//ARIA//Medication Reminders//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        f"DTSTAMP:{now_str}\r\n"
        + "".join(vevents)
        + "END:VCALENDAR\r\n"
    )
    return calendar
