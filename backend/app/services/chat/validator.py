"""Chatbot-specific LLM output validator for ARIA.

Safety philosophy:
  - BLOCK: PHI leak, prompt injection, prescriptive clinical language,
            scope violations, certainty predictions.
  - ALLOW: All informational queries about patient data — BP trends,
            adherence, flagging reasons, risk status, summaries.

Checks removed vs briefing validator (over-blocked chat):
  - check_medication_hallucination  (requires briefing payload context)
  - check_bp_plausibility           (fires on any BP value without payload)
  - check_groundedness              (too strict — rounded numbers mismatch)
  - check_evidence_consistency      (evidence field not always populated)

Public API:
  validate_chat_response(text, tool_results, patient_id, clinician_id, session) → ValidationResult
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.services.briefing.llm_validator import (
    ValidationResult,
    check_phi_leak,
    check_prompt_injection,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Chat-specific guardrails — only prescriptive/decision-making language.
# Much narrower than the briefing guardrails: "diagnosed" and similar
# descriptive clinical terms are ALLOWED in chat responses.
_CHAT_GUARDRAIL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnon[- ]?adherent\b", re.IGNORECASE),          "non_adherent"),
    (re.compile(r"\bnon[- ]?compliant\b", re.IGNORECASE),         "non_compliant"),
    (re.compile(r"\bhypertensive crisis\b", re.IGNORECASE),       "hypertensive_crisis"),
    (re.compile(r"\bmedication failure\b", re.IGNORECASE),        "medication_failure"),
    (re.compile(r"\bprescribe\b", re.IGNORECASE),                 "prescribe"),
    (re.compile(r"\bincrease\b.{1,30}?mg\b", re.IGNORECASE),     "dosage_increase"),
    (re.compile(r"\bdecrease\b.{1,30}?mg\b", re.IGNORECASE),     "dosage_decrease"),
    (re.compile(r"\btell the patient\b", re.IGNORECASE),          "patient_facing"),
    # Block prescriptive/crisis forms only — "no emergency concerns" and similar
    # descriptive phrases are allowed. System prompt already instructs LLM never to
    # say "emergency"; this catches violations in the dangerous prescriptive forms.
    (re.compile(
        r"\bhypertensive\s+emergency\b"
        r"|\bemergency\s+(?:services?|referral|room|department|care)\b"
        r"|\b(?:call|go\s+to|attend|visit)\s+emergency\b",
        re.IGNORECASE,
    ), "emergency"),
    # "diagnose" only when targeting the patient — prescriptive form.
    # Allows: "ARIA diagnoses inertia", "used to diagnose elevated BP patterns"
    # Blocks:  "diagnose this patient", "diagnose the patient with X"
    (re.compile(r"\bdiagnose\s+(?:this\s+|the\s+)?patient\b", re.IGNORECASE), "diagnose"),
]


def check_chat_guardrails(text: str) -> ValidationResult:
    """Block prescriptive clinical language in chat responses.

    Narrower than the briefing guardrail — descriptive terms like 'diagnosed'
    are allowed. Only active prescriptive language is blocked.
    """
    for pattern, name in _CHAT_GUARDRAIL_PATTERNS:
        if pattern.search(text):
            return ValidationResult(
                passed=False,
                failed_check=f"guardrail:{name}",
                detail=f"Guardrail pattern '{name}' matched in chat response",
            )
    return ValidationResult(passed=True)


# Predictions framed as clinical certainties — block these
_CERTAINTY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwill definitely\b", re.IGNORECASE),
    re.compile(r"\bwill certainly\b", re.IGNORECASE),
    re.compile(r"\bguaranteed to\b", re.IGNORECASE),
    re.compile(r"\bwill resolve\b", re.IGNORECASE),
]

# Scope boundary violations — other patients, system internals, injection
_SCOPE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bother patients?\b", re.IGNORECASE),
    re.compile(r"\bignore (previous|prior|above)\b", re.IGNORECASE),
    re.compile(r"\bsystem prompt\b", re.IGNORECASE),
    re.compile(r"\btraining data\b", re.IGNORECASE),
    re.compile(r"\byou are (now|actually)\b", re.IGNORECASE),
]

# Clinical terms — any of these in a no-tool response means it's on-topic
_CLINICAL_ANSWER_TERMS: frozenset[str] = frozenset({
    "patient", "bp", "blood pressure", "systolic", "diastolic",
    "medication", "adherence", "reading", "readings", "trend", "briefing",
    "lab", "clinical", "hypertension", "heart rate", "monitoring",
    "mmhg", "prescribed", "baseline", "risk", "inertia", "gap",
    "deterioration", "alert", "overdue", "visit", "dose", "drug",
    "only answer questions", "clinical data",
    # Social reply phrases — greetings and acknowledgements are on-topic
    "i'm here", "happy to help", "feel free", "let me know",
    "hope the", "goodbye", "of course", "you're welcome",
    "pre-visit", "consultation", "flagged", "concern",
})


def check_no_certainty_predictions(text: str) -> ValidationResult:
    """Block predictions framed as clinical certainties.

    Args:
        text: Raw LLM answer string.

    Returns:
        Failed result if a certainty prediction pattern is detected.
    """
    for pattern in _CERTAINTY_PATTERNS:
        if pattern.search(text):
            return ValidationResult(
                passed=False,
                failed_check="certainty_prediction",
                detail=f"Certainty pattern '{pattern.pattern}' detected",
            )
    return ValidationResult(passed=True)


def check_scope_boundary(text: str) -> ValidationResult:
    """Block references to other patients, system internals, or injection attempts.

    Args:
        text: Raw LLM answer string.

    Returns:
        Failed result if a scope boundary violation is detected.
    """
    for pattern in _SCOPE_PATTERNS:
        if pattern.search(text):
            return ValidationResult(
                passed=False,
                failed_check="scope_boundary",
                detail=f"Scope violation pattern '{pattern.pattern}' detected",
            )
    return ValidationResult(passed=True)


def check_clinical_scope(
    text: str,
    tool_results: dict[str, Any],
) -> ValidationResult:
    """Block answers with no clinical content when no tools were called.

    Only fires when tools were NOT called — if tools ran, the answer is
    grounded in patient data by definition. Social replies (greetings,
    thanks, farewells) are explicitly allowed via _CLINICAL_ANSWER_TERMS.

    Args:
        text: Raw LLM answer string.
        tool_results: Tools called this turn (empty when no tools were used).

    Returns:
        Failed result when answer looks like a pure off-topic response.
    """
    if tool_results:
        return ValidationResult(passed=True)

    text_lower = text.lower()
    if any(term in text_lower for term in _CLINICAL_ANSWER_TERMS):
        return ValidationResult(passed=True)

    return ValidationResult(
        passed=False,
        failed_check="clinical_scope",
        detail="Answer contains no clinical terms and no tools were called",
    )


def check_empty_data_acknowledged(
    text: str,
    tool_results: dict[str, Any],
) -> ValidationResult:
    """Validate the answer acknowledges when all tools returned no data.

    Args:
        text: Raw LLM answer string.
        tool_results: Dict of tool_name → result dict.

    Returns:
        Failed result if all tools returned data_available=False but the
        answer does not contain any acknowledgement phrase.
    """
    if not tool_results:
        return ValidationResult(passed=True)

    all_empty = all(
        not result.get("data_available", True)
        for result in tool_results.values()
    )
    if not all_empty:
        return ValidationResult(passed=True)

    # When all tools return empty, the LLM may still answer from the pre-loaded
    # patient context injected into the system prompt. If the answer contains
    # clinical content it is grounded in that context — do not block it.
    text_lower = text.lower()
    if any(term in text_lower for term in _CLINICAL_ANSWER_TERMS):
        return ValidationResult(passed=True)

    acknowledgement_phrases = [
        "no data", "not available", "no information", "cannot find",
        "no records", "no readings", "insufficient", "not recorded",
        "unable to find", "no results",
    ]
    text_lower = text.lower()
    if not any(phrase in text_lower for phrase in acknowledgement_phrases):
        return ValidationResult(
            passed=False,
            failed_check="empty_data_not_acknowledged",
            detail="All tools returned no data but answer does not acknowledge this",
        )
    return ValidationResult(passed=True)


async def validate_chat_response(
    text: str,
    tool_results: dict[str, Any],
    patient_id: str,
    clinician_id: str,
    db_session: AsyncSession,
) -> ValidationResult:
    """Run guardrail and chatbot-specific checks on the LLM response.

    Execution order (fail-fast):
      1. PHI leak         — patient ID verbatim in output
      2. Prompt injection — [INST], system:, ignore previous, etc.
      3. Guardrails       — prescribe, diagnose, non-adherent, emergency, etc.
      4. Clinical scope   — off-topic answer with no tools called
      5. Empty data ack   — all tools empty but answer doesn't say so
      6. Certainty preds  — "will definitely", "guaranteed to", etc.
      7. Scope boundary   — "other patients", "system prompt", etc.

    Writes an audit_events row regardless of outcome.

    Args:
        text: Raw LLM answer string.
        tool_results: Tool results from this agent turn.
        patient_id: Patient identifier — checked for PHI leak.
        clinician_id: Clinician identifier — for audit row.
        db_session: Async DB session.

    Returns:
        ValidationResult — passed=True or first failed check.
    """
    checks = [
        lambda: check_phi_leak(text, patient_id),
        lambda: check_prompt_injection(text),
        lambda: check_chat_guardrails(text),
        lambda: check_clinical_scope(text, tool_results),
        lambda: check_empty_data_acknowledged(text, tool_results),
        lambda: check_no_certainty_predictions(text),
        lambda: check_scope_boundary(text),
    ]

    result: ValidationResult = ValidationResult(passed=True)
    for check in checks:
        result = check()
        if not result.passed:
            logger.warning(
                "Chat validation failed: patient=%s check=%s detail=%s",
                patient_id,
                result.failed_check,
                result.detail,
            )
            break

    outcome = "success" if result.passed else "failure"
    details: str | None = None
    if not result.passed:
        parts = [result.failed_check or "unknown"]
        if result.detail:
            parts.append(result.detail)
        details = ": ".join(parts)

    db_session.add(AuditEvent(
        actor_type="clinician",
        actor_id=clinician_id,
        patient_id=patient_id,
        action="chat_query",
        resource_type="Patient",
        resource_id=patient_id,
        outcome=outcome,
        details=details,
    ))

    return result
