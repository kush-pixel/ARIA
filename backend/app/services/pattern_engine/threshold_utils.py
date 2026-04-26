"""Shared threshold and window utilities for Layer 1 pattern engine detectors.

All four detectors import from here.  Never hardcode 140 mmHg in a detector —
call compute_patient_threshold() and apply_comorbidity_adjustment() instead.
"""

from __future__ import annotations

import statistics
from datetime import date

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD = 140.0
_THRESHOLD_FLOOR = 130.0
_THRESHOLD_CEILING = 145.0
_COMORBIDITY_ADJUSTMENT = -7.0

# ICD-10 prefix groups (lowercased, dots/hyphens stripped)
_CARDIO_PREFIXES = ("i50", "i63", "g45")    # CHF, stroke, TIA
_METABOLIC_PREFIXES = ("e11", "n18")        # T2DM, CKD


# ---------------------------------------------------------------------------
# Slope helper (pure Python — no numpy)
# ---------------------------------------------------------------------------


def compute_slope(points: list[tuple[float, float]]) -> float:
    """Least-squares linear regression slope for (x, y) pairs.

    Args:
        points: (x, y) pairs where x is days from origin and y is systolic value.

    Returns:
        Slope in mmHg/day.  Returns 0.0 for degenerate or single-point input.
    """
    n = len(points)
    if n < 2:
        return 0.0

    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_x2 = sum(p[0] ** 2 for p in points)

    denom = n * sum_x2 - sum_x ** 2
    if denom == 0.0:
        return 0.0

    return (n * sum_xy - sum_x * sum_y) / denom


# ---------------------------------------------------------------------------
# Patient-adaptive threshold
# ---------------------------------------------------------------------------


def compute_patient_threshold(
    historic_bp_systolic: list[int] | None,
    problem_assessments: list | None = None,
) -> tuple[float, str]:
    """Compute patient-adaptive elevated BP threshold from clinic history.

    Uses historic clinic systolic readings to establish a personal baseline:
      threshold = max(FLOOR, min(CEILING, mean + 1.5 × population_SD))

    Falls back to 140 mmHg if fewer than 3 readings are available.

    Args:
        historic_bp_systolic: Array of historic clinic systolic readings.
        problem_assessments: Phase 1 JSONB (reserved — not yet used).

    Returns:
        (threshold_mmhg, mode) where mode is "adaptive" or "default_no_history".
    """
    if not historic_bp_systolic or len(historic_bp_systolic) < 3:
        return (_DEFAULT_THRESHOLD, "default_no_history")

    readings = [float(v) for v in historic_bp_systolic if v is not None]
    if len(readings) < 3:
        return (_DEFAULT_THRESHOLD, "default_no_history")

    mean = sum(readings) / len(readings)
    sd = statistics.pstdev(readings)
    threshold = max(_THRESHOLD_FLOOR, min(_THRESHOLD_CEILING, mean + 1.5 * sd))
    return (round(threshold, 1), "adaptive")


# ---------------------------------------------------------------------------
# Comorbidity concern classification and threshold adjustment
# ---------------------------------------------------------------------------


def classify_comorbidity_concern(
    problem_codes: list[str] | None,
    problem_assessments: list | None = None,
) -> dict:
    """Classify cardiovascular and metabolic comorbidity concern level.

    Degraded mode (problem_assessments=None): presence of an ICD-10 code
    implies elevated concern.  Full mode (Phase 1): would check
    problem_assessments JSONB for active/significant concern flags.

    Args:
        problem_codes: ICD-10 or SNOMED codes from clinical_context.
        problem_assessments: Phase 1 JSONB (reserved).

    Returns:
        Dict with: cardio (bool), metabolic (bool), elevated_concern (bool), mode (str).
    """
    if not problem_codes:
        return {
            "cardio": False,
            "metabolic": False,
            "elevated_concern": False,
            "mode": "degraded_no_codes",
        }

    def _norm(code: str) -> str:
        return code.lower().replace(".", "").replace("-", "")

    codes_norm = [_norm(c) for c in problem_codes]
    cardio = any(c.startswith(p) for c in codes_norm for p in _CARDIO_PREFIXES)
    metabolic = any(c.startswith(p) for c in codes_norm for p in _METABOLIC_PREFIXES)

    mode = "full_assessments" if problem_assessments is not None else "degraded_no_assessments"
    return {
        "cardio": cardio,
        "metabolic": metabolic,
        "elevated_concern": cardio or metabolic,
        "mode": mode,
    }


def apply_comorbidity_adjustment(
    patient_threshold: float,
    concern_state: dict,
) -> tuple[float, str]:
    """Lower the threshold by 7 mmHg (floor 130) when BOTH cardio AND metabolic
    comorbidities are simultaneously in elevated concern state.

    Args:
        patient_threshold: Unadjusted threshold in mmHg.
        concern_state: Output of classify_comorbidity_concern().

    Returns:
        (adjusted_threshold, threshold_adjustment_mode).
    """
    cardio = concern_state.get("cardio", False)
    metabolic = concern_state.get("metabolic", False)
    concern_mode = concern_state.get("mode", "unknown")

    if cardio and metabolic:
        adjusted = max(_THRESHOLD_FLOOR, patient_threshold + _COMORBIDITY_ADJUSTMENT)
        return (round(adjusted, 1), f"comorbidity_adjusted_{concern_mode}")
    return (patient_threshold, concern_mode)


# ---------------------------------------------------------------------------
# Medication change date helper
# ---------------------------------------------------------------------------


def get_last_med_change_date(
    med_history: list[dict] | None,
    last_med_change_fallback: date | None,
) -> date | None:
    """Return the most recent medication change date from med_history or fallback.

    med_history entries are {name, rxnorm, date, activity}.  Any entry counts
    as a change (including dose increases, which represent physician response).

    Args:
        med_history: JSONB medication history from clinical_context.
        last_med_change_fallback: Stale single-date field (used if no med_history).

    Returns:
        Most recent change date, or None if no change is recorded.
    """
    if med_history:
        dates: list[str] = []
        for entry in med_history:
            d = entry.get("date") or ""
            if d:
                dates.append(d)
        if dates:
            latest_str = max(dates)
            try:
                parts = latest_str.split("-")
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            except (ValueError, IndexError):
                pass
    return last_med_change_fallback
