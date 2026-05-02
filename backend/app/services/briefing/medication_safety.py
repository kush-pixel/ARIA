"""Hypertension drug interaction detector for ARIA briefing generation.

Detects four evidence-based interaction rules using data already present in
clinical_context (no additional database queries required).

Called exclusively from composer.py at briefing generation time.
All output is deterministic — no LLM involvement.
"""

from __future__ import annotations

from app.services.pattern_engine.threshold_utils import infer_drug_class

# ---------------------------------------------------------------------------
# Comorbidity ICD-10 prefixes
# ---------------------------------------------------------------------------

_CHF_PREFIX = "i50"
_CKD_PREFIX = "n18"

# ---------------------------------------------------------------------------
# Drug class groupings for interaction rules
# ---------------------------------------------------------------------------

_ANTIHYPERTENSIVE_CLASSES = frozenset({
    "ace_inhibitor", "arb", "beta_blocker", "amlodipine", "dhp_ccb",
    "non_dhp_ccb", "loop_diuretic", "thiazide", "k_sparing_diuretic", "diuretic",
})

_DIURETIC_CLASSES = frozenset({
    "loop_diuretic", "thiazide", "k_sparing_diuretic", "diuretic",
})


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _has_comorbidity(problem_codes: list[str] | None, prefix: str) -> bool:
    """Return True if any problem code starts with the given ICD-10 prefix.

    Normalises codes to lowercase with dots and hyphens stripped before
    comparison (e.g. "I50.9" → "i509" matches prefix "i50").

    Args:
        problem_codes: ICD-10 code list from clinical_context.
        prefix: Lowercase normalised prefix to match (e.g. "i50", "n18").

    Returns:
        True if any code matches the prefix.
    """
    if not problem_codes:
        return False
    for code in problem_codes:
        normalised = code.lower().replace(".", "").replace("-", "")
        if normalised.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_interactions(ctx: object) -> list[dict]:
    """Detect hypertension drug interactions from a patient's clinical context.

    Applies four deterministic rules against ctx.current_medications and
    ctx.problem_codes. No database queries are performed — all data is read
    from the already-fetched clinical_context object.

    Rules evaluated (Rule 2 checked before Rule 1 to suppress duplicate flags):
      1. NSAID + any antihypertensive → warning; escalate to concern if CHF or CKD.
      2. Triple whammy: NSAID + ACE/ARB + any diuretic → concern; escalate to
         critical if both CHF and CKD present. Supersedes Rule 1.
      3. K-sparing diuretic + ACE inhibitor or ARB → warning; escalate to
         concern if CKD present.
      4. Beta-blocker + non-DHP CCB (verapamil or diltiazem) → always concern.

    Args:
        ctx: ClinicalContext ORM instance (or compatible duck type). Must expose
             current_medications (list[str] | None) and problem_codes (list[str] | None).

    Returns:
        List of interaction dicts, each containing rule (str), severity (str),
        drugs_involved (list[str]), description (str), and comorbidity_amplified (bool).
        Returns [] when no interactions are found. Never returns None.
    """
    medications: list[str] = ctx.current_medications or []  # type: ignore[attr-defined]
    problem_codes: list[str] | None = ctx.problem_codes  # type: ignore[attr-defined]

    classified: list[tuple[str, str]] = [
        (med, infer_drug_class(med)) for med in medications
    ]

    has_chf = _has_comorbidity(problem_codes, _CHF_PREFIX)
    has_ckd = _has_comorbidity(problem_codes, _CKD_PREFIX)

    def _names_of_class(*classes: str) -> list[str]:
        return [name for name, cls in classified if cls in classes]

    nsaid_names = _names_of_class("nsaid")
    ace_names = _names_of_class("ace_inhibitor")
    arb_names = _names_of_class("arb")
    ace_or_arb_names = ace_names + arb_names
    diuretic_names = _names_of_class(*_DIURETIC_CLASSES)
    antihypertensive_names = _names_of_class(*_ANTIHYPERTENSIVE_CLASSES)
    k_sparing_names = _names_of_class("k_sparing_diuretic")
    beta_blocker_names = _names_of_class("beta_blocker")
    non_dhp_ccb_names = _names_of_class("non_dhp_ccb")

    interactions: list[dict] = []
    triple_whammy_fired = False

    # ── Rule 2: Triple whammy (checked before Rule 1 to suppress duplicate) ──
    if nsaid_names and ace_or_arb_names and diuretic_names:
        triple_whammy_fired = True
        severity = "concern"
        comorbidity_amplified = False
        if has_chf and has_ckd:
            severity = "critical"
            comorbidity_amplified = True
        interactions.append({
            "rule": "triple_whammy",
            "severity": severity,
            "drugs_involved": nsaid_names + ace_or_arb_names + diuretic_names,
            "description": (
                "Triple whammy combination: NSAID with ACE inhibitor/ARB and diuretic — "
                "significantly elevated acute kidney injury risk."
                + (" Comorbidities (CHF + CKD) escalate severity to critical." if comorbidity_amplified else "")
            ),
            "comorbidity_amplified": comorbidity_amplified,
        })

    # ── Rule 1: NSAID + antihypertensive (skip when triple whammy already fired) ──
    if not triple_whammy_fired and nsaid_names and antihypertensive_names:
        severity = "warning"
        comorbidity_amplified = False
        if has_chf or has_ckd:
            severity = "concern"
            comorbidity_amplified = True
        interactions.append({
            "rule": "nsaid_antihypertensive",
            "severity": severity,
            "drugs_involved": nsaid_names + antihypertensive_names,
            "description": (
                "NSAID co-prescribed with antihypertensive agent — "
                "may attenuate BP control and increase cardiovascular risk."
                + (" Comorbidity (CHF/CKD) escalates severity." if comorbidity_amplified else "")
            ),
            "comorbidity_amplified": comorbidity_amplified,
        })

    # ── Rule 3: K-sparing diuretic + ACE inhibitor or ARB ──
    if k_sparing_names and ace_or_arb_names:
        severity = "warning"
        comorbidity_amplified = False
        if has_ckd:
            severity = "concern"
            comorbidity_amplified = True
        interactions.append({
            "rule": "k_sparing_ace_arb",
            "severity": severity,
            "drugs_involved": k_sparing_names + ace_or_arb_names,
            "description": (
                "K-sparing diuretic co-prescribed with ACE inhibitor or ARB — "
                "risk of hyperkalaemia."
                + (" CKD escalates hyperkalaemia risk to concern level." if comorbidity_amplified else "")
            ),
            "comorbidity_amplified": comorbidity_amplified,
        })

    # ── Rule 4: Beta-blocker + non-DHP CCB ──
    if beta_blocker_names and non_dhp_ccb_names:
        interactions.append({
            "rule": "bb_non_dhp_ccb",
            "severity": "concern",
            "drugs_involved": beta_blocker_names + non_dhp_ccb_names,
            "description": (
                "Beta-blocker co-prescribed with rate-limiting CCB (verapamil or diltiazem) — "
                "risk of bradycardia and AV block."
            ),
            "comorbidity_amplified": False,
        })

    return interactions
