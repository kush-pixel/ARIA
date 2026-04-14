"""Synthetic 28-day home BP reading generator for ARIA demo patients.

Anchors generation on real clinic BP values from ``clinical_context``
and applies the Patient A scenario rules defined in CLAUDE.md Section 5.
Returns a list of dicts ready to insert into the ``readings`` table.

Device outages are represented as absent rows — null values are never generated.
All datetimes are timezone-aware UTC.

Usage::

    readings = await generate_readings("1091", session, scenario="patient_a")
"""

from __future__ import annotations

import random
import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_context import ClinicalContext
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCENARIO_PATIENT_A: str = "patient_a"

# Patient A scenario anchor values
PATIENT_A_MORNING_MEAN: float = 163.0
PATIENT_A_MORNING_SD: float = 8.0

# Systolic clip bounds for morning Gaussian draw
MORNING_SYSTOLIC_CLIP_LOW: int = 145
MORNING_SYSTOLIC_CLIP_HIGH: int = 185

# UTC hours for session timing
MORNING_HOUR_UTC: int = 7
EVENING_HOUR_UTC: int = 21

# ±jitter applied to session start time (minutes)
SESSION_JITTER_MINUTES_LOW: int = -15
SESSION_JITTER_MINUTES_HIGH: int = 15

# Morning/evening systolic offset from scenario target
MORNING_OFFSET_LOW: float = 0.0
MORNING_OFFSET_HIGH: float = 3.0
EVENING_OFFSET_LOW: float = 6.0
EVENING_OFFSET_HIGH: float = 7.0

# Anti-rounding noise (prevents .0 endings)
ANTI_ROUND_LOW: float = -1.5
ANTI_ROUND_HIGH: float = 1.5

# Second reading is slightly lower than first
READING2_DROP_LOW: float = 2.0
READING2_DROP_HIGH: float = 6.0
READING2_NOISE_LOW: float = -0.5
READING2_NOISE_HIGH: float = 0.5

# Diastolic is a fraction of systolic
DIASTOLIC_RATIO_LOW: float = 0.60
DIASTOLIC_RATIO_HIGH: float = 0.66

# Heart rate range and beta-blocker correction
HR_BASE_LOW: float = 64.0
HR_BASE_HIGH: float = 82.0
HR_CLAMP_LOW: int = 52
HR_CLAMP_HIGH: int = 95
HR_BETA_BLOCKER_COEFF: float = 0.3
HR_BETA_BLOCKER_THRESHOLD: int = 140
METOPROLOL_KEYWORD: str = "metoprolol"

# Phase 2 (days 8-14): inertia drift parameters
PHASE2_DRIFT_LOW: float = 0.5
PHASE2_DRIFT_HIGH: float = 0.8
PHASE2_TARGET: float = 165.0

# Phase 3 (days 15-18): continued elevation band
PHASE3_MEAN_LOW: float = 164.0
PHASE3_MEAN_HIGH: float = 167.0

# Phase 4 (days 19-21): white-coat dip targets
PHASE4_TARGETS: list[float] = [158.0, 153.0, 149.0]

# Phase 5 (days 22-28): post-appointment return band
PHASE5_MEAN_LOW: float = 160.0
PHASE5_MEAN_HIGH: float = 166.0

# Total generation window
GENERATION_WINDOW_DAYS: int = 28

# Fixed column values for all generated readings
GENERATED_SOURCE: str = "generated"
GENERATED_SUBMITTED_BY: str = "generator"
GENERATED_SESSION_MORNING: str = "morning"
GENERATED_SESSION_EVENING: str = "evening"
GENERATED_BP_POSITION: str = "seated"
GENERATED_BP_SITE: str = "left_arm"
GENERATED_CONSENT_VERSION: str = "1.0"
GENERATED_MEDICATION_TAKEN: str = "yes"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _compute_baseline(historic_bp: list[int]) -> tuple[float, float]:
    """Compute mean and SD from real clinic systolic values.

    Args:
        historic_bp: List of historic clinic systolic readings.

    Returns:
        Tuple of (mean, sd). Falls back to Patient A defaults when the list
        has fewer than two values (``statistics.stdev`` requires n >= 2).
    """
    if len(historic_bp) >= 2:
        return statistics.mean(historic_bp), statistics.stdev(historic_bp)
    if len(historic_bp) == 1:
        return float(historic_bp[0]), PATIENT_A_MORNING_SD
    return PATIENT_A_MORNING_MEAN, PATIENT_A_MORNING_SD


def _anti_round(value: float) -> float:
    """Add small random noise so readings never land on a round number.

    Applies up to ±1.5 mmHg of noise and rounds to 1 decimal place.
    If the result still lands on a whole number (extremely rare), a 0.1
    nudge is applied to guarantee a non-round value.

    Args:
        value: Calculated BP or HR value.

    Returns:
        Value rounded to 1 decimal place, never ending in exactly .0.
    """
    result = round(value + random.uniform(ANTI_ROUND_LOW, ANTI_ROUND_HIGH), 1)
    if result % 1 == 0.0:
        result = round(result + 0.1, 1)
    return result


def _make_datetime(day_date: date, hour: int) -> datetime:
    """Build a timezone-aware UTC datetime with ±15-minute session jitter.

    Args:
        day_date: Calendar date for the reading.
        hour: Nominal UTC hour (7 for morning, 21 for evening).

    Returns:
        Timezone-aware UTC datetime.
    """
    base = datetime(
        day_date.year, day_date.month, day_date.day, hour, 0, 0, tzinfo=UTC
    )
    jitter = random.randint(SESSION_JITTER_MINUTES_LOW, SESSION_JITTER_MINUTES_HIGH)
    return base + timedelta(minutes=jitter)


def _build_reading(
    patient_id: str,
    scenario_sys: float,
    session_name: str,
    day_date: date,
    medications: list[str],
) -> dict[str, Any]:
    """Build a single reading dict from a scenario systolic target.

    Applies ESH/AHA two-reading protocol, anti-rounding, diastolic ratio,
    and beta-blocker heart rate correction. Pure function — no DB access.

    Args:
        patient_id: ARIA patient identifier.
        scenario_sys: Scenario systolic target (before morning/evening offset).
        session_name: ``"morning"`` or ``"evening"``.
        day_date: Calendar date for the reading.
        medications: Patient's current medication names (case-insensitive check
            for metoprolol beta-blocker effect on heart rate).

    Returns:
        Dict of all ``readings`` table columns except ``reading_id`` and
        ``created_at`` (both DB-generated).
    """
    # Apply morning/evening offset to the scenario target
    if session_name == GENERATED_SESSION_MORNING:
        sys_base = scenario_sys + random.uniform(MORNING_OFFSET_LOW, MORNING_OFFSET_HIGH)
    else:
        sys_base = scenario_sys - random.uniform(EVENING_OFFSET_LOW, EVENING_OFFSET_HIGH)

    # First reading — compute as float, then truncate to int for the SmallInteger column.
    # Keep the float value to compute averages with sub-integer precision.
    s1_float = _anti_round(sys_base)
    systolic_1 = int(s1_float)

    # Second reading — slightly lower than first (ESH protocol: reading 2 is lower)
    s2_raw = (
        sys_base
        - random.uniform(READING2_DROP_LOW, READING2_DROP_HIGH)
        + random.uniform(READING2_NOISE_LOW, READING2_NOISE_HIGH)
    )
    s2_float = _anti_round(s2_raw)
    systolic_2 = int(s2_float)
    # Guarantee reading 2 is strictly lower after int truncation
    if systolic_2 >= systolic_1:
        systolic_2 = systolic_1 - 1

    # Average computed from float values (retains precision) with anti-round applied
    # so the avg never lands on an exact round number.
    systolic_avg = _anti_round((s1_float + s2_float) / 2)

    # Diastolic using per-session ratio; same float-first pattern
    dia_ratio = random.uniform(DIASTOLIC_RATIO_LOW, DIASTOLIC_RATIO_HIGH)
    d1_float = _anti_round(s1_float * dia_ratio)
    d2_float = _anti_round(s2_float * dia_ratio)
    diastolic_1 = int(d1_float)
    diastolic_2 = int(d2_float)
    if diastolic_2 >= diastolic_1:
        diastolic_2 = diastolic_1 - 1
    diastolic_avg = _anti_round((d1_float + d2_float) / 2)

    # Heart rate with optional beta-blocker reduction
    hr_raw = random.uniform(HR_BASE_LOW, HR_BASE_HIGH)
    on_beta_blocker = any(
        METOPROLOL_KEYWORD in m.lower() for m in medications
    )
    if on_beta_blocker:
        hr_raw -= HR_BETA_BLOCKER_COEFF * max(0.0, systolic_avg - HR_BETA_BLOCKER_THRESHOLD)
    hr_1 = int(round(max(HR_CLAMP_LOW, min(HR_CLAMP_HIGH, hr_raw))))
    hr_2_raw = hr_raw + random.uniform(-1.5, 1.5)
    hr_2 = int(round(max(HR_CLAMP_LOW, min(HR_CLAMP_HIGH, hr_2_raw))))
    heart_rate_avg = round((hr_1 + hr_2) / 2, 1)

    hour = MORNING_HOUR_UTC if session_name == GENERATED_SESSION_MORNING else EVENING_HOUR_UTC

    return {
        "patient_id": patient_id,
        "systolic_1": systolic_1,
        "diastolic_1": diastolic_1,
        "heart_rate_1": hr_1,
        "systolic_2": systolic_2,
        "diastolic_2": diastolic_2,
        "heart_rate_2": hr_2,
        "systolic_avg": systolic_avg,
        "diastolic_avg": diastolic_avg,
        "heart_rate_avg": heart_rate_avg,
        "effective_datetime": _make_datetime(day_date, hour),
        "session": session_name,
        "source": GENERATED_SOURCE,
        "submitted_by": GENERATED_SUBMITTED_BY,
        "bp_position": GENERATED_BP_POSITION,
        "bp_site": GENERATED_BP_SITE,
        "consent_version": GENERATED_CONSENT_VERSION,
        "medication_taken": GENERATED_MEDICATION_TAKEN,
    }


def _patient_a_schedule() -> list[tuple[int, str, float]]:
    """Build the Patient A 28-day generation schedule.

    Returns a flat list of ``(day_num, session_name, sys_target)`` tuples.
    Days with device outages or missed sessions produce no entries — the
    absent rows represent the outage/miss (never null values).

    Schedule summary:
      Days  1-7:  Baseline. Both sessions. 14 entries.
      Days  8-14: Inertia drift toward 165. One evening missed. 13 entries.
      Days 15-18: Elevation 164-167. Days 16-17 absent (device outage).
                  Days 15 and 18 both sessions. 4 entries.
      Days 19-21: White-coat dip [158, 153, 149]. Both sessions. 6 entries.
      Days 22-28: Return 160-166. Days 25-26 absent (weekend miss).
                  Days 22-24 and 27-28 both sessions. 10 entries.
      Total: 47 entries.

    Returns:
        List of ``(day_num, session_name, sys_target)`` tuples.
    """
    schedule: list[tuple[int, str, float]] = []

    # ── Phase 1: Days 1-7 — Baseline establishment ─────────────────────────
    # CLAUDE.md specifies Gaussian(163, SD=8) explicitly for this scenario.
    for d in range(1, 8):
        sys_target = random.gauss(PATIENT_A_MORNING_MEAN, PATIENT_A_MORNING_SD)
        sys_target = max(MORNING_SYSTOLIC_CLIP_LOW, min(MORNING_SYSTOLIC_CLIP_HIGH, sys_target))
        schedule.append((d, GENERATED_SESSION_MORNING, sys_target))
        schedule.append((d, GENERATED_SESSION_EVENING, sys_target))

    # ── Phase 2: Days 8-14 — Inertia develops (drift upward) ───────────────
    missed_evening_day = random.choice([12, 13])
    current_sys = PATIENT_A_MORNING_MEAN
    for d in range(8, 15):
        drift = random.uniform(PHASE2_DRIFT_LOW, PHASE2_DRIFT_HIGH)
        current_sys = min(PHASE2_TARGET, current_sys + drift)
        schedule.append((d, GENERATED_SESSION_MORNING, current_sys))
        if d != missed_evening_day:
            schedule.append((d, GENERATED_SESSION_EVENING, current_sys))

    # ── Phase 3: Days 15-18 — Continued elevation; device outage 16-17 ─────
    # Days 16 and 17 produce no entries (absent rows = device outage).
    for d in [15, 18]:
        sys_target = random.uniform(PHASE3_MEAN_LOW, PHASE3_MEAN_HIGH)
        schedule.append((d, GENERATED_SESSION_MORNING, sys_target))
        schedule.append((d, GENERATED_SESSION_EVENING, sys_target))

    # ── Phase 4: Days 19-21 — White-coat dip ───────────────────────────────
    for i, d in enumerate(range(19, 22)):
        sys_target = PHASE4_TARGETS[i]
        schedule.append((d, GENERATED_SESSION_MORNING, sys_target))
        schedule.append((d, GENERATED_SESSION_EVENING, sys_target))

    # ── Phase 5: Days 22-28 — Post-appointment return ──────────────────────
    # Days 25 and 26 produce no entries (absent rows = weekend miss).
    for d in range(22, 29):
        if d in (25, 26):
            continue
        sys_target = random.uniform(PHASE5_MEAN_LOW, PHASE5_MEAN_HIGH)
        schedule.append((d, GENERATED_SESSION_MORNING, sys_target))
        schedule.append((d, GENERATED_SESSION_EVENING, sys_target))

    return schedule


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_readings(
    patient_id: str,
    session: AsyncSession,
    scenario: str = SCENARIO_PATIENT_A,
) -> list[dict[str, Any]]:
    """Generate 28 days of synthetic home BP readings for a patient.

    Anchors generation on real clinic BP data from ``clinical_context``,
    then applies the named scenario rules.  The caller owns the session
    lifecycle; this function does not commit.

    Args:
        patient_id: ARIA patient identifier (e.g. ``"1091"``).
        session: SQLAlchemy async session.
        scenario: Scenario key controlling the generation profile.
            Currently only ``"patient_a"`` is supported.

    Returns:
        List of dicts, one per reading, containing all ``readings`` table
        columns except ``reading_id`` and ``created_at`` (DB-generated).

    Raises:
        ValueError: If an unknown scenario name is given.
        sqlalchemy.exc.SQLAlchemyError: On database query failure.
    """
    if scenario != SCENARIO_PATIENT_A:
        raise ValueError(
            f"Unknown scenario: {scenario!r}. Supported scenarios: {SCENARIO_PATIENT_A!r}"
        )

    # ── Step 1: Query clinical context to anchor generation ─────────────────
    result = await session.execute(
        select(ClinicalContext).where(ClinicalContext.patient_id == patient_id)
    )
    ctx = result.scalar_one_or_none()

    if ctx is None:
        logger.warning(
            "No ClinicalContext found for patient %s — using default baseline", patient_id
        )

    historic_bp: list[int] = (ctx.historic_bp_systolic or []) if ctx else []
    medications: list[str] = (ctx.current_medications or []) if ctx else []

    baseline_mean, baseline_sd = _compute_baseline(historic_bp)
    logger.info(
        "Patient %s: baseline mean=%.1f sd=%.1f from %d historic clinic readings",
        patient_id,
        baseline_mean,
        baseline_sd,
        len(historic_bp),
    )

    # ── Step 2: Compute start date (day 1 = today − 27 days, day 28 = today) ─
    start_date = date.today() - timedelta(days=GENERATION_WINDOW_DAYS - 1)

    # ── Step 3: Build scenario schedule ─────────────────────────────────────
    schedule = _patient_a_schedule()

    # ── Step 4: Generate readings ────────────────────────────────────────────
    readings: list[dict[str, Any]] = []
    for day_num, session_name, sys_target in schedule:
        day_date = start_date + timedelta(days=day_num - 1)
        reading = _build_reading(patient_id, sys_target, session_name, day_date, medications)
        readings.append(reading)

    logger.info(
        "Generated %d synthetic readings for patient %s (scenario=%s)",
        len(readings),
        patient_id,
        scenario,
    )
    return readings
