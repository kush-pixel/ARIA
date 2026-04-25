"""Layer 3 LLM output validation and guardrails for ARIA.

Two checks run on every LLM response before readable_summary is stored:

Guardrails (absolute — payload irrelevant):
  Forbidden clinical language, PHI leak, prompt injection patterns.
  Any match blocks storage regardless of what the payload contains.

Faithfulness (contextual — compared against Layer 1 payload):
  Sentence count, risk score consistency, adherence pattern grounding,
  titration language, urgent flag support, overdue lab support,
  problem assessment grounding, data limitations, medication hallucination,
  BP plausibility, and contradiction detection.

Public API:
  validate_llm_output(text, payload, briefing_id, patient_id, session)
    → ValidationResult(passed, failed_check, detail)

On failure: caller in summarizer.py retries once, then stores readable_summary=None.
Layer 1 briefing is always the authoritative output — Layer 3 is additive.
audit_events row written on every call regardless of pass/fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of a single LLM output validation run."""

    passed: bool
    failed_check: str | None = None
    detail: str | None = None


# ── Guardrail patterns (clinical language boundary — CLAUDE.md) ───────────────

_GUARDRAIL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnon[- ]?adherent\b", re.IGNORECASE), "non_adherent"),
    (re.compile(r"\bnon[- ]?compliant\b", re.IGNORECASE), "non_compliant"),
    (re.compile(r"\bhypertensive crisis\b", re.IGNORECASE), "hypertensive_crisis"),
    (re.compile(r"\bmedication failure\b", re.IGNORECASE), "medication_failure"),
    (re.compile(r"\bprescribe\b", re.IGNORECASE), "prescribe"),
    (re.compile(r"\bincrease\b.{1,30}?mg\b", re.IGNORECASE), "dosage_increase"),
    (re.compile(r"\bdecrease\b.{1,30}?mg\b", re.IGNORECASE), "dosage_decrease"),
    (re.compile(r"\btell the patient\b", re.IGNORECASE), "patient_facing"),
    (re.compile(r"\bdiagnos", re.IGNORECASE), "diagnose"),
    (re.compile(r"\bemergency\b", re.IGNORECASE), "emergency"),
]

# ── Prompt injection patterns ─────────────────────────────────────────────────

_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore previous", re.IGNORECASE), "ignore_previous"),
    (re.compile(r"new instruction", re.IGNORECASE), "new_instruction"),
    (re.compile(r"\bsystem:", re.IGNORECASE), "system_colon"),
    (re.compile(r"\[INST\]"), "inst_tag"),
    (re.compile(r"<\|im_start\|>"), "im_start_tag"),
    (re.compile(r"\bAssistant:"), "assistant_colon"),
]

# ── Drug name detection ───────────────────────────────────────────────────────

# Searches for common drug class suffixes anywhere within a lowercase word.
# Used with re.search(pattern, word) — no word-boundary anchors needed.
_DRUG_SUFFIX_SEARCH_RE = re.compile(
    r"olol|pril|sartan|dipine|statin|mab|mide|zide|azide|"
    r"mycin|cillin|cycline|floxacin|azole",
    re.IGNORECASE,
)

# Known drug names without distinctive class suffixes, and beta-blockers whose
# suffix ("-lol") differs from the standard "-olol" pattern (e.g. carvedilol).
_KNOWN_UNSUFFIXED_DRUGS: frozenset[str] = frozenset({
    "aspirin", "warfarin", "insulin", "glucagon", "lantus", "novolog",
    "isosorbide", "omeprazole", "namenda", "lasix", "sular", "digoxin",
    "heparin", "clopidogrel", "metformin", "lithium",
    "carvedilol", "labetalol", "nebivolol",
})

# ── BP value plausibility ─────────────────────────────────────────────────────

_SYSTOLIC_MIN = 60
_SYSTOLIC_MAX = 250
_BP_PAYLOAD_TOLERANCE = 20

# Matches "X/Y" BP panel format — captures systolic (X)
_BP_PANEL_RE = re.compile(r"\b(\d{2,3})/\d{2,3}\b")

# Matches "X mmHg" standalone — captures X, skipping if preceded by "/" (diastolic)
_MMHG_RE = re.compile(r"\b(\d{2,3})\s*mmHg\b", re.IGNORECASE)

# Extracts systolic from trend_summary payload field
_TREND_SYSTOLIC_RE = re.compile(
    r"(?:avg|average|mean|systolic)[^\d]{0,20}(\d{2,3}(?:\.\d)?)",
    re.IGNORECASE,
)

# ── Negation helper ───────────────────────────────────────────────────────────

_NEGATION_WORDS: tuple[str, ...] = (
    "no ", "not ", "without ", "rather than", "instead of",
    "isn't", "is not", "rules out", "no evidence of",
)


def _is_negated(text: str, phrase: str) -> bool:
    """Return True if phrase appears in text preceded by a negation within 60 chars."""
    text_lower = text.lower()
    phrase_lower = phrase.lower()
    idx = text_lower.find(phrase_lower)
    if idx < 0:
        return False
    context = text_lower[max(0, idx - 60):idx]
    return any(neg in context for neg in _NEGATION_WORDS)


# ── Group A: Safety checks ────────────────────────────────────────────────────

def check_phi_leak(text: str, patient_id: str) -> ValidationResult:
    """Detect patient_id appearing verbatim in LLM output.

    Args:
        text: Raw LLM output string.
        patient_id: Patient identifier to scan for.

    Returns:
        Failed result if patient_id found in text, else passed.
    """
    if patient_id and patient_id in text:
        return ValidationResult(
            passed=False,
            failed_check="phi_leak",
            detail="patient_id appears verbatim in output",
        )
    return ValidationResult(passed=True)


def check_prompt_injection(text: str) -> ValidationResult:
    """Detect prompt injection patterns in LLM output.

    Patient-sourced text fields (problem_assessments, social_context) passed
    to the LLM could carry injected instructions that echo back in the summary.

    Args:
        text: Raw LLM output string.

    Returns:
        Failed result if an injection pattern is detected, else passed.
    """
    for pattern, name in _INJECTION_PATTERNS:
        if pattern.search(text):
            return ValidationResult(
                passed=False,
                failed_check=f"prompt_injection:{name}",
                detail=f"injection pattern '{name}' detected in output",
            )
    return ValidationResult(passed=True)


# ── Group B: Clinical language guardrails ─────────────────────────────────────

def check_guardrails(text: str) -> ValidationResult:
    """Block forbidden clinical language, dosage recommendations, and patient-facing language.

    Enforces the ARIA clinical boundary defined in CLAUDE.md. These phrases
    are always blocked — payload content is irrelevant.

    Args:
        text: Raw LLM output string.

    Returns:
        Failed result on first forbidden phrase match, else passed.
    """
    for pattern, name in _GUARDRAIL_PATTERNS:
        if pattern.search(text):
            return ValidationResult(
                passed=False,
                failed_check=f"guardrail:{name}",
                detail=f"forbidden phrase '{name}' detected in output",
            )
    return ValidationResult(passed=True)


# ── Group C: Faithfulness checks ──────────────────────────────────────────────

def check_sentence_count(text: str) -> ValidationResult:
    """Validate LLM output contains exactly 3 sentences per spec.

    Args:
        text: Raw LLM output string.

    Returns:
        Failed result if sentence count != 3, else passed.
    """
    # Split on sentence-ending punctuation followed by whitespace before an uppercase letter
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    n = len([p for p in parts if p.strip()])
    if n != 3:
        return ValidationResult(
            passed=False,
            failed_check="sentence_count",
            detail=f"got {n} sentences, expected 3",
        )
    return ValidationResult(passed=True)


def check_risk_score_consistency(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate any risk score mentioned is within ±10 of the Layer 2 payload value.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result on score mismatch > 10 points, else passed.
    """
    risk_score = payload.get("risk_score")
    if risk_score is None:
        return ValidationResult(passed=True)

    matches = re.findall(
        r"(?:risk|score|priority)[^\d]{0,20}(\d{1,3}(?:\.\d)?)",
        text,
        re.IGNORECASE,
    )
    for match in matches:
        mentioned = float(match)
        if abs(mentioned - float(risk_score)) > 10:
            return ValidationResult(
                passed=False,
                failed_check="risk_score_mismatch",
                detail=f"LLM says {mentioned:.0f}, payload has {float(risk_score):.1f}",
            )
    return ValidationResult(passed=True)


def check_adherence_language(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate adherence and treatment-review claims are grounded in payload pattern.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result when adherence claim contradicts payload adherence_summary.
    """
    text_lower = text.lower()
    summary_lower = (payload.get("adherence_summary") or "").lower()

    if "adherence concern" in text_lower and not _is_negated(text, "adherence concern"):
        if "adherence concern" not in summary_lower:
            return ValidationResult(
                passed=False,
                failed_check="adherence_unsupported",
                detail="LLM claims adherence concern but payload adherence_summary does not support Pattern A",
            )

    if "treatment review" in text_lower and not _is_negated(text, "treatment review"):
        if "treatment" not in summary_lower:
            return ValidationResult(
                passed=False,
                failed_check="treatment_review_unsupported",
                detail="LLM claims treatment review but payload adherence_summary does not support Pattern B",
            )

    return ValidationResult(passed=True)


def check_titration_window(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate titration language is grounded in medication_status (Fix 34).

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result if text mentions titration but payload medication_status has no titration notice.
    """
    if "titration" not in text.lower():
        return ValidationResult(passed=True)
    med_status = (payload.get("medication_status") or "").lower()
    if "titration" not in med_status:
        return ValidationResult(
            passed=False,
            failed_check="titration_unsupported",
            detail="LLM mentions titration but payload medication_status has no titration window notice",
        )
    return ValidationResult(passed=True)


def check_urgent_flags(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate urgency claims are grounded in payload urgent_flags.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result if text asserts urgency but urgent_flags is empty.
    """
    text_lower = text.lower()
    urgent_asserted = bool(
        re.search(r"(?<!no )(?<!without )\burgent\b", text_lower)
    ) and not _is_negated(text, "urgent")
    if urgent_asserted and not payload.get("urgent_flags"):
        return ValidationResult(
            passed=False,
            failed_check="urgent_flags_unsupported",
            detail="LLM asserts urgency but payload urgent_flags is empty",
        )
    return ValidationResult(passed=True)


def check_overdue_labs(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate overdue lab references are grounded in payload overdue_labs.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result if text mentions overdue labs but payload overdue_labs is empty.
    """
    text_lower = text.lower()
    mentions_overdue_labs = bool(
        re.search(r"\boverdue\b.{0,20}\blab", text_lower)
        or re.search(r"\blab.{0,20}\boverdue\b", text_lower)
    )
    if mentions_overdue_labs and not payload.get("overdue_labs"):
        return ValidationResult(
            passed=False,
            failed_check="overdue_labs_unsupported",
            detail="LLM mentions overdue labs but payload overdue_labs is empty",
        )
    return ValidationResult(passed=True)


def check_problem_assessments(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate medical condition references are grounded in payload active_problems.

    Only fires when active_problems is explicitly empty — guards against the LLM
    inventing conditions when no problem data exists.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result if conditions mentioned with no active_problems in payload.
    """
    active_problems = payload.get("active_problems")
    if active_problems:
        return ValidationResult(passed=True)  # problems exist — no hallucination to catch

    condition_names = [
        "hypertension", "chf", "heart failure", "diabetes", "t2dm",
        "ckd", "renal", "cad", "stroke", "tia", "atrial fibrillation",
    ]
    text_lower = text.lower()
    for condition in condition_names:
        if condition in text_lower:
            return ValidationResult(
                passed=False,
                failed_check="problem_hallucination",
                detail=f"LLM mentions '{condition}' but payload active_problems is empty",
            )
    return ValidationResult(passed=True)


def check_data_limitations(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate data limitation claims are grounded in payload data_limitations.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result if text claims data limitations but payload field is empty.
    """
    text_lower = text.lower()
    if re.search(r"\binsufficient data\b|\bno home monitoring\b|\blimited data\b", text_lower):
        if not payload.get("data_limitations"):
            return ValidationResult(
                passed=False,
                failed_check="data_limitations_unsupported",
                detail="LLM mentions data limitations but payload data_limitations is empty",
            )
    return ValidationResult(passed=True)


def check_medication_hallucination(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Detect drug names in LLM output that are absent from payload medication_status.

    Uses common drug class suffixes and a curated list of known unsuffixed drugs.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result if an unrecognised drug name is found, else passed.
    """
    med_status = (payload.get("medication_status") or "").lower()
    if not med_status:
        return ValidationResult(passed=True)

    for word in re.findall(r"\b[a-z]+\b", text.lower()):
        if len(word) < 5:
            continue
        is_drug_like = (
            bool(_DRUG_SUFFIX_SEARCH_RE.search(word))
            or word in _KNOWN_UNSUFFIXED_DRUGS
        )
        if is_drug_like and word not in med_status:
            return ValidationResult(
                passed=False,
                failed_check="medication_hallucination",
                detail=f"LLM mentions '{word}' which is not in payload medication_status",
            )
    return ValidationResult(passed=True)


def check_bp_plausibility(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Validate BP systolic values in LLM output are plausible and consistent with payload.

    Checks two things:
      1. All extracted systolic values are within physiological range 60–250 mmHg.
      2. If payload trend_summary contains a systolic value, text values are within ±20.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result on out-of-range or inconsistent BP value, else passed.
    """
    systolics_in_text: list[int] = []

    for m in _BP_PANEL_RE.finditer(text):
        systolics_in_text.append(int(m.group(1)))

    for m in _MMHG_RE.finditer(text):
        val_start = m.start(1)
        if val_start > 0 and text[val_start - 1] == "/":
            continue  # skip diastolic part of X/Y pattern
        systolics_in_text.append(int(m.group(1)))

    if not systolics_in_text:
        return ValidationResult(passed=True)

    for val in systolics_in_text:
        if not (_SYSTOLIC_MIN <= val <= _SYSTOLIC_MAX):
            return ValidationResult(
                passed=False,
                failed_check="bp_value_implausible",
                detail=f"BP value {val} mmHg is outside physiological range {_SYSTOLIC_MIN}–{_SYSTOLIC_MAX}",
            )

    trend_summary = payload.get("trend_summary") or ""
    payload_systolics = [
        float(m.group(1)) for m in _TREND_SYSTOLIC_RE.finditer(trend_summary)
    ]
    if not payload_systolics:
        payload_systolics = [float(m.group(1)) for m in _BP_PANEL_RE.finditer(trend_summary)]

    if payload_systolics:
        payload_mean = sum(payload_systolics) / len(payload_systolics)
        for val in systolics_in_text:
            if abs(val - payload_mean) > _BP_PAYLOAD_TOLERANCE:
                return ValidationResult(
                    passed=False,
                    failed_check="bp_value_implausible",
                    detail=(
                        f"BP value {val} mmHg deviates more than {_BP_PAYLOAD_TOLERANCE} "
                        f"from payload mean {payload_mean:.1f}"
                    ),
                )

    return ValidationResult(passed=True)


def check_contradiction(text: str, payload: dict[str, Any]) -> ValidationResult:
    """Detect cases where LLM output is more alarming than the payload supports.

    Catches the inverse of faithfulness — the LLM over-asserting risk when
    the payload data does not support it.

    Checks:
      - Specific conditions mentioned when active_problems is explicitly empty.
      - Adherence concern asserted when adherence_summary shows only Pattern C.

    Args:
        text: Raw LLM output string.
        payload: Layer 1 briefing payload dict.

    Returns:
        Failed result on first contradiction detected, else passed.
    """
    text_lower = text.lower()

    # Condition claims when active_problems is explicitly empty list
    if payload.get("active_problems") == []:
        condition_names = [
            "hypertension", "chf", "heart failure", "diabetes", "t2dm",
            "ckd", "cad", "stroke", "tia", "atrial fibrillation",
        ]
        for condition in condition_names:
            if condition in text_lower:
                return ValidationResult(
                    passed=False,
                    failed_check="contradiction_problems",
                    detail=f"LLM mentions '{condition}' but payload active_problems is empty",
                )

    # Adherence concern when payload shows only Pattern C (contextual)
    adherence_summary = (payload.get("adherence_summary") or "").lower()
    if "contextual" in adherence_summary and not _is_negated(text, "adherence concern"):
        if "adherence concern" in text_lower:
            return ValidationResult(
                passed=False,
                failed_check="contradiction_adherence",
                detail="LLM claims adherence concern but payload adherence_summary shows Pattern C only",
            )

    return ValidationResult(passed=True)


# ── Audit event writer ─────────────────────────────────────────────────────────

def _write_audit_event(
    session: AsyncSession,
    briefing_id: str,
    patient_id: str,
    result: ValidationResult,
) -> None:
    """Add a llm_validation audit_events row to the session. Does not commit.

    Args:
        session: Active async SQLAlchemy session.
        briefing_id: UUID of the Briefing being validated.
        patient_id: Patient identifier for the audit row.
        result: Outcome of the validation run.
    """
    outcome = "success" if result.passed else "failure"
    details: str | None = None
    if not result.passed:
        parts = [result.failed_check or "unknown"]
        if result.detail:
            parts.append(result.detail)
        details = ": ".join(parts)

    session.add(
        AuditEvent(
            actor_type="system",
            actor_id="llm_validator",
            patient_id=patient_id,
            action="llm_validation",
            resource_type="Briefing",
            resource_id=briefing_id,
            outcome=outcome,
            details=details,
        )
    )


# ── Public API ─────────────────────────────────────────────────────────────────

async def validate_llm_output(
    text: str,
    payload: dict[str, Any],
    briefing_id: str,
    patient_id: str,
    session: AsyncSession,
) -> ValidationResult:
    """Run all guardrail and faithfulness checks on the LLM output.

    Execution order:
      Group A (safety — hard blocks): PHI leak, prompt injection.
      Group B (guardrails): forbidden clinical language.
      Group C (faithfulness): consistency with Layer 1 payload.

    Returns on first failed check. Adds an audit_events row regardless of outcome.

    Args:
        text: Raw LLM output string from summarizer.py.
        payload: Layer 1 briefing payload (10-field dict from composer.py).
        briefing_id: UUID string of the persisted Briefing row.
        patient_id: Patient identifier — checked for PHI leak.
        session: Active async SQLAlchemy session. audit_events row added, not committed.

    Returns:
        ValidationResult with passed=True, or the first failed check details.
    """
    checks = [
        # Group A — safety
        lambda: check_phi_leak(text, patient_id),
        lambda: check_prompt_injection(text),
        # Group B — clinical language guardrails
        lambda: check_guardrails(text),
        # Group C — faithfulness against Layer 1 payload
        lambda: check_sentence_count(text),
        lambda: check_risk_score_consistency(text, payload),
        lambda: check_adherence_language(text, payload),
        lambda: check_titration_window(text, payload),
        lambda: check_urgent_flags(text, payload),
        lambda: check_overdue_labs(text, payload),
        lambda: check_problem_assessments(text, payload),
        lambda: check_data_limitations(text, payload),
        lambda: check_medication_hallucination(text, payload),
        lambda: check_bp_plausibility(text, payload),
        lambda: check_contradiction(text, payload),
    ]

    for check in checks:
        result = check()
        if not result.passed:
            logger.warning(
                "LLM validation failed: briefing=%s check=%s detail=%s",
                briefing_id,
                result.failed_check,
                result.detail,
            )
            _write_audit_event(session, briefing_id, patient_id, result)
            return result

    passed_result = ValidationResult(passed=True)
    _write_audit_event(session, briefing_id, patient_id, passed_result)
    return passed_result
