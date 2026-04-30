"""Chatbot-specific LLM output validator for ARIA.

Reuses five safety/guardrail checks from llm_validator.py and adds five
new checks tailored to conversational responses grounded in tool results.

On any failure: returns safe fallback answer — never null to the frontend.
Unlike the briefing validator, chatbot responses are NOT retried on failure
(blocking a guardrail-triggering question is immediate).

Public API:
  validate_chat_response(text, evidence, tool_results, patient_id, session) → ValidationResult
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.services.briefing.llm_validator import (
    ValidationResult,
    check_bp_plausibility,
    check_guardrails,
    check_medication_hallucination,
    check_phi_leak,
    check_prompt_injection,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_CERTAINTY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwill definitely\b", re.IGNORECASE),
    re.compile(r"\bwill certainly\b", re.IGNORECASE),
    re.compile(r"\bguaranteed to\b", re.IGNORECASE),
    re.compile(r"\bwill improve\b", re.IGNORECASE),
    re.compile(r"\bwill worsen\b", re.IGNORECASE),
    re.compile(r"\bwill resolve\b", re.IGNORECASE),
]

_SCOPE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bother patients?\b", re.IGNORECASE),
    re.compile(r"\bignore (previous|prior|above)\b", re.IGNORECASE),
    re.compile(r"\bsystem prompt\b", re.IGNORECASE),
    re.compile(r"\btraining data\b", re.IGNORECASE),
    re.compile(r"\byou are (now|actually)\b", re.IGNORECASE),
]

# Clinical terms that must appear in any non-refusal answer when no tools were called.
# If the LLM answers without tools and the answer has none of these, it answered off-topic.
_CLINICAL_ANSWER_TERMS: frozenset[str] = frozenset({
    "patient", "bp", "blood pressure", "systolic", "diastolic",
    "medication", "adherence", "reading", "readings", "trend", "briefing",
    "lab", "clinical", "hypertension", "heart rate", "monitoring",
    "mmhg", "prescribed", "baseline", "risk", "inertia", "gap",
    "deterioration", "alert", "overdue", "visit", "dose", "drug",
    "only answer questions", "clinical data",  # scope refusal phrases
})


def check_groundedness(
    text: str,
    tool_results: dict[str, Any],
) -> ValidationResult:
    """Validate that numbers cited in the answer appear in tool results.

    Extracts all integers and decimals from the answer and checks each
    against the string representation of all tool results.

    Args:
        text: Raw LLM answer string.
        tool_results: Dict of tool_name → result dict for this turn.

    Returns:
        Failed result if a number in the answer cannot be found in any tool result.
    """
    if not tool_results:
        return ValidationResult(passed=True)

    tool_values_str = str(tool_results).lower()
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)

    for num in numbers:
        # Skip small integers that are likely filler (days, percentages phrased as round numbers)
        if float(num) < 10:
            continue
        if num not in tool_values_str:
            return ValidationResult(
                passed=False,
                failed_check="groundedness",
                detail=f"Value '{num}' in answer not found in any tool result",
            )
    return ValidationResult(passed=True)


def check_empty_data_acknowledged(
    text: str,
    tool_results: dict[str, Any],
) -> ValidationResult:
    """Validate that the answer acknowledges when all tools returned no data.

    Args:
        text: Raw LLM answer string.
        tool_results: Dict of tool_name → result dict.

    Returns:
        Failed result if all tools returned data_available=False but the
        answer does not contain an acknowledgement phrase.
    """
    if not tool_results:
        return ValidationResult(passed=True)

    all_empty = all(
        not result.get("data_available", True)
        for result in tool_results.values()
    )
    if not all_empty:
        return ValidationResult(passed=True)

    acknowledgement_phrases = [
        "no data", "not available", "no information", "cannot find",
        "no records", "no readings", "insufficient", "not recorded",
    ]
    text_lower = text.lower()
    if not any(phrase in text_lower for phrase in acknowledgement_phrases):
        return ValidationResult(
            passed=False,
            failed_check="empty_data_not_acknowledged",
            detail="All tools returned no data but answer does not acknowledge this",
        )
    return ValidationResult(passed=True)


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
                detail=f"Pattern '{pattern.pattern}' detected — predictions framed as certainties are blocked",
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
    """Block answers that contain no clinical terms when no tools were called.

    If the LLM answered without calling any tools AND the answer contains none
    of the expected clinical terms, it almost certainly answered an off-topic
    question using its general world knowledge.

    Args:
        text: Raw LLM answer string.
        tool_results: Tools called this turn (empty when no tools were used).

    Returns:
        Failed result when answer looks like a general-knowledge response.
    """
    if tool_results:
        # Tools were called — answer is grounded in patient data, skip this check
        return ValidationResult(passed=True)

    text_lower = text.lower()
    if any(term in text_lower for term in _CLINICAL_ANSWER_TERMS):
        return ValidationResult(passed=True)

    return ValidationResult(
        passed=False,
        failed_check="clinical_scope",
        detail="Answer contains no clinical terms and no tools were called — likely an off-topic response",
    )


def check_evidence_consistency(
    evidence: list[str],
    tool_results: dict[str, Any],
) -> ValidationResult:
    """Validate that cited evidence items reference tools that were actually called.

    Args:
        evidence: List of evidence strings from the structured response.
        tool_results: Dict of tool_name → result for this turn.

    Returns:
        Failed result if an evidence item references a tool not in tool_results.
    """
    if not evidence or not tool_results:
        return ValidationResult(passed=True)

    tool_names = set(tool_results.keys())
    for item in evidence:
        item_lower = item.lower()
        # Check if the evidence item mentions a source that was NOT called
        for source in ["get_patient_readings", "get_patient_alerts", "get_medication_history",
                       "get_adherence_summary", "get_clinical_context", "get_briefing"]:
            if source.replace("get_", "").replace("_", " ") in item_lower and source not in tool_names:
                return ValidationResult(
                    passed=False,
                    failed_check="evidence_inconsistency",
                    detail=f"Evidence references '{source}' but that tool was not called this turn",
                )
    return ValidationResult(passed=True)


async def validate_chat_response(
    text: str,
    evidence: list[str],
    tool_results: dict[str, Any],
    patient_id: str,
    clinician_id: str,
    db_session: AsyncSession,
) -> ValidationResult:
    """Run all guardrail and chatbot-specific checks on the LLM response.

    Execution order:
      Group A (safety): PHI leak, prompt injection.
      Group B (guardrails): forbidden clinical language.
      Group C (chatbot-specific): groundedness, empty data, certainty, scope, evidence.

    Writes an audit_events row regardless of outcome.

    Args:
        text: Raw LLM answer string.
        evidence: Evidence list from structured response.
        tool_results: Tool results from this agent turn.
        patient_id: Patient identifier — checked for PHI leak.
        clinician_id: Clinician identifier — for audit row.
        db_session: Async DB session.

    Returns:
        ValidationResult — passed=True or first failed check.
    """
    # Build medication payload stub for hallucination check
    med_payload: dict[str, Any] = {}
    if "get_medication_history" in tool_results:
        mh = tool_results["get_medication_history"]
        med_payload["medication_status"] = " ".join(mh.get("current_medications", []))

    checks = [
        lambda: check_phi_leak(text, patient_id),
        lambda: check_prompt_injection(text),
        lambda: check_guardrails(text),
        lambda: check_clinical_scope(text, tool_results),  # blocks off-topic answers
        lambda: check_medication_hallucination(text, med_payload),
        lambda: check_bp_plausibility(text, {}),
        lambda: check_groundedness(text, tool_results),
        lambda: check_empty_data_acknowledged(text, tool_results),
        lambda: check_no_certainty_predictions(text),
        lambda: check_scope_boundary(text),
        lambda: check_evidence_consistency(evidence, tool_results),
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
