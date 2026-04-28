"""Shared threshold and window utilities for Layer 1 pattern engine detectors.

All four detectors import from here.  Never hardcode 140 mmHg in a detector —
call compute_patient_threshold() and apply_comorbidity_adjustment() instead.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD = 140.0
_THRESHOLD_FLOOR = 130.0
_THRESHOLD_CEILING = 145.0
_COMORBIDITY_ADJUSTMENT = -7.0

# ICD-10 prefix groups (lowercased, dots/hyphens stripped)
_CARDIO_PREFIXES = ("i50", "i63", "g45")    # CHF, stroke, TIA  (all severe-weight)
_METABOLIC_PREFIXES = ("e11", "n18")        # T2DM, CKD

# Drug-class-aware titration window (days after a medication change before full response expected)
# Source: AUDIT.md Fix 3/26; aligned with CLAUDE.md TITRATION_WINDOWS spec
_TITRATION_WINDOWS: dict[str, int] = {
    "diuretic":      14,
    "beta_blocker":  14,
    "ace_inhibitor": 28,
    "arb":           28,
    "amlodipine":    56,
    "default":       42,
}

_DIURETIC_NAMES = frozenset({
    "furosemide", "lasix", "hydrochlorothiazide", "hctz", "chlorthalidone",
    "indapamide", "torsemide", "spironolactone", "eplerenone", "metolazone",
})


# ---------------------------------------------------------------------------
# Adaptive detection window
# ---------------------------------------------------------------------------


def compute_window_days(
    next_appointment: datetime | None,
    last_visit_date: date | None,
    fallback: int = 28,
) -> tuple[int, str]:
    """Compute adaptive detection window in days.

    Uses (next_appointment − last_visit_date) clamped to [14, 90].
    Falls back to ``fallback`` (28) when either date is missing or the
    computed interval is non-positive.

    Args:
        next_appointment: Patient's next scheduled appointment datetime.
        last_visit_date: Date of the most recent clinical visit.
        fallback: Default window when adaptive computation is unavailable.

    Returns:
        (window_days, source) where source is "adaptive" or "fallback_default".
    """
    if next_appointment is None or last_visit_date is None:
        return (fallback, "fallback_default")

    next_appt_date = (
        next_appointment.date() if isinstance(next_appointment, datetime) else next_appointment
    )
    lv_date = (
        last_visit_date.date() if isinstance(last_visit_date, datetime) else last_visit_date
    )

    interval = (next_appt_date - lv_date).days
    if interval <= 0:
        return (fallback, "fallback_default")

    return (min(90, max(14, interval)), "adaptive")


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
    """Lower the threshold by 7 mmHg (floor 130) when EITHER:
      (a) cardiovascular AND metabolic comorbidities both in elevated concern, OR
      (b) any single severe-weight comorbidity (CHF/Stroke/TIA) in elevated concern.

    Rule (b) covers CHF-only and stroke-only patients missed by the original
    cardio+metabolic rule (AUDIT.md Fix 5).  Since _CARDIO_PREFIXES already maps
    only to CHF/Stroke/TIA, cardio=True is sufficient to trigger the adjustment.

    Args:
        patient_threshold: Unadjusted threshold in mmHg.
        concern_state: Output of classify_comorbidity_concern().

    Returns:
        (adjusted_threshold, threshold_adjustment_mode).
    """
    cardio = concern_state.get("cardio", False)
    concern_mode = concern_state.get("mode", "unknown")

    if cardio:
        adjusted = max(_THRESHOLD_FLOOR, patient_threshold + _COMORBIDITY_ADJUSTMENT)
        return (round(adjusted, 1), f"comorbidity_adjusted_{concern_mode}")
    return (patient_threshold, concern_mode)


# ---------------------------------------------------------------------------
# Drug-class inference and titration window
# ---------------------------------------------------------------------------


def _infer_drug_class(drug_name: str) -> str:
    """Infer drug class from medication name using suffix and exact-match patterns."""
    name = drug_name.lower().strip()
    if "amlodipine" in name:
        return "amlodipine"
    if name.endswith("olol"):
        return "beta_blocker"
    if name.endswith("pril"):
        return "ace_inhibitor"
    if name.endswith("sartan"):
        return "arb"
    if any(d in name for d in _DIURETIC_NAMES):
        return "diuretic"
    return "default"


def get_titration_window(
    med_history: list[dict] | None,
    last_med_change_fallback: date | None = None,
) -> int:
    """Return drug-class-aware titration window (days) for the most recently changed drug.

    Derives the window from the most recent med_history entry's drug class using
    _TITRATION_WINDOWS.  Falls back to default (42 days) when history is absent
    or the drug class cannot be inferred.

    Args:
        med_history: JSONB medication history from clinical_context.
        last_med_change_fallback: Unused — kept for API symmetry with
            get_last_med_change_date().

    Returns:
        Titration window in days.
    """
    if not med_history:
        return _TITRATION_WINDOWS["default"]

    best_date = ""
    best_name = ""
    for entry in med_history:
        d = entry.get("date") or ""
        if d > best_date:
            best_date = d
            best_name = entry.get("name") or ""

    if not best_name:
        return _TITRATION_WINDOWS["default"]

    drug_class = _infer_drug_class(best_name)
    return _TITRATION_WINDOWS.get(drug_class, _TITRATION_WINDOWS["default"])


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
