"""ARIA clinical chatbot agent.

Multi-turn tool-use loop with Anthropic prompt caching and SSE streaming.
The agent runs up to MAX_TOOL_ROUNDS of tool calls per question, then
streams the final answer back token-by-token.

Public API:
  run_agent(question, patient_id, clinician_id, db_session) → AsyncGenerator[str, None]
    Yields SSE-formatted event strings.

  generate_suggested_questions(briefing_payload) → list[str]
    Returns 3-4 suggested question strings based on Layer 1 signals.

  generate_proactive_suggestion(briefing_payload, db_session, patient_id) → str | None
    Returns one proactive question the clinician should consider, or None.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.briefing.llm_validator import ValidationResult
from app.services.chat import session as session_store
from app.services.chat.formatter import ChatResponse, make_blocked_response, parse_response
from app.services.chat.tools import TOOL_SCHEMAS, dispatch_tool, get_briefing, get_clinical_context
from app.services.chat.validator import validate_chat_response
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_MODEL = "claude-sonnet-4-20250514"
_MAX_TOOL_ROUNDS = 3
_PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "chat_system_prompt.md"


def _load_system_prompt() -> str:
    """Load the chatbot system prompt from prompts/chat_system_prompt.md."""
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Chat system prompt not found at {_PROMPT_PATH}. "
            "Ensure prompts/chat_system_prompt.md exists."
        )
    return _PROMPT_PATH.read_text(encoding="utf-8")


async def _build_patient_context(
    patient_id: str,
    db_session: AsyncSession,
) -> str:
    """Pre-load patient context for prompt caching.

    Fetches briefing + clinical context once per session and formats as a
    structured string block injected into the cached system content.

    Args:
        patient_id: Patient identifier.
        db_session: Async DB session.

    Returns:
        Formatted patient context string.
    """
    briefing = await get_briefing(db_session, patient_id)
    ctx = await get_clinical_context(db_session, patient_id)

    lines = ["## Patient Context — ID: [REDACTED]"]

    if briefing.get("data_available"):
        lines.append(f"Risk score: {briefing.get('risk_score')}")
        lines.append(f"Urgent flags: {', '.join(briefing.get('urgent_flags', [])) or 'None'}")
        lines.append(f"Active problems: {', '.join(briefing.get('active_problems', [])) or 'None'}")
        lines.append(f"Trend summary: {briefing.get('trend_summary', 'N/A')}")
        lines.append(f"Medication status: {briefing.get('medication_status', 'N/A')}")
        lines.append(f"Visit agenda: {'; '.join(briefing.get('visit_agenda', [])) or 'None'}")
        lines.append(f"Overdue labs: {', '.join(briefing.get('overdue_labs', [])) or 'None'}")

    if ctx.get("data_available"):
        lines.append(f"Last visit: {ctx.get('last_visit_date', 'unknown')}")
        lines.append(f"Last clinic BP: {ctx.get('last_clinic_systolic')}/{ctx.get('last_clinic_diastolic')} mmHg")

    return "\n".join(lines)


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def run_agent(
    question: str,
    patient_id: str,
    clinician_id: str,
    db_session: AsyncSession,
) -> AsyncGenerator[str, None]:
    """Run the multi-turn chatbot agent and yield SSE events.

    Flow:
      1. Load conversation history from session store.
      2. Build system prompt + cached patient context.
      3. Tool-use loop (max MAX_TOOL_ROUNDS): call tools, accumulate results.
      4. Stream final answer tokens.
      5. Validate response. On failure: yield blocked event.
      6. Persist turn to session store.
      7. Write audit event.

    Args:
        question: Clinician's natural language question.
        patient_id: Patient scope — all tool calls locked to this ID.
        clinician_id: From JWT — used for session key and audit.
        db_session: Async DB session.

    Yields:
        SSE-formatted strings: thinking | token | done | error events.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    system_prompt = _load_system_prompt()
    patient_context = await _build_patient_context(patient_id, db_session)

    # System with prompt caching: system prompt + patient context both cached
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": patient_context,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    history = await session_store.get_history(clinician_id, patient_id, db_session)
    messages: list[dict[str, Any]] = list(history) + [{"role": "user", "content": question}]

    tool_results_accumulator: dict[str, Any] = {}
    tools_used: list[str] = []
    final_text = ""

    # ── Tool-use loop ──────────────────────────────────────────────────────────
    for round_num in range(_MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=system_blocks,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        # Check if this round produced tool calls
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            # No tool calls — extract text and break
            for block in response.content:
                if block.type == "text":
                    final_text = block.text
            break

        # Notify frontend which tools are being called
        for tool_use in tool_uses:
            yield _sse("thinking", {"tool": tool_use.name, "round": round_num + 1})
            tools_used.append(tool_use.name)

        # Execute tools
        tool_result_blocks = []
        for tool_use in tool_uses:
            try:
                result = await dispatch_tool(
                    tool_name=tool_use.name,
                    tool_input=tool_use.input,
                    patient_id=patient_id,
                    session=db_session,
                )
                tool_results_accumulator[tool_use.name] = result
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps(result),
                })
            except Exception as exc:
                logger.error("Tool %s failed: %s", tool_use.name, exc)
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": json.dumps({"error": str(exc), "data_available": False}),
                    "is_error": True,
                })

        # Append assistant tool_use turn + tool results to message history
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_result_blocks})

    # ── Stream final answer ────────────────────────────────────────────────────
    if not final_text:
        # Need one more LLM call to synthesise after tools (no more tool calls allowed)
        final_response = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=system_blocks,
            messages=messages,
        )
        for block in final_response.content:
            if block.type == "text":
                final_text = block.text
                break

    # Stream tokens character by character (simulate streaming for UX)
    # In production swap for client.messages.stream() when tool loop is done
    words = final_text.split(" ")
    for i, word in enumerate(words):
        chunk = word if i == len(words) - 1 else word + " "
        yield _sse("token", {"token": chunk})

    # ── Parse + validate ───────────────────────────────────────────────────────
    parsed: ChatResponse = parse_response(final_text, tools_used=tools_used)

    validation: ValidationResult = await validate_chat_response(
        text=parsed.answer,
        evidence=parsed.evidence,
        tool_results=tool_results_accumulator,
        patient_id=patient_id,
        clinician_id=clinician_id,
        db_session=db_session,
    )

    if not validation.passed:
        blocked = make_blocked_response(validation.failed_check or "unknown")
        yield _sse("done", {
            "answer": blocked.answer,
            "evidence": [],
            "confidence": "blocked",
            "data_gaps": [],
            "tools_used": tools_used,
            "blocked": True,
            "block_reason": blocked.block_reason,
        })
        return

    # ── Persist conversation turn ──────────────────────────────────────────────
    await session_store.append_messages(
        clinician_id=clinician_id,
        patient_id=patient_id,
        new_messages=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": parsed.answer},
        ],
        db_session=db_session,
    )

    yield _sse("done", {
        "answer": parsed.answer,
        "evidence": parsed.evidence,
        "confidence": parsed.confidence,
        "data_gaps": parsed.data_gaps,
        "tools_used": tools_used,
        "blocked": False,
    })


def generate_suggested_questions(briefing_payload: dict[str, Any]) -> list[str]:
    """Generate 3-4 suggested question chips based on Layer 1 briefing signals.

    Args:
        briefing_payload: The llm_response JSONB dict from a Briefing row.

    Returns:
        List of question strings for the frontend chips.
    """
    questions: list[str] = []
    urgent_flags = briefing_payload.get("urgent_flags") or []
    adherence_summary = (briefing_payload.get("adherence_summary") or "").lower()
    overdue_labs = briefing_payload.get("overdue_labs") or []
    flags_str = " ".join(urgent_flags).lower()

    if "inertia" in flags_str:
        questions.append("Why was treatment review flagged?")
    if "deterioration" in flags_str:
        questions.append("When did readings start worsening?")
    if "gap" in flags_str:
        questions.append("How long was the monitoring gap and when did it start?")
    if "adherence concern" in adherence_summary:
        questions.append("Which medications had the most missed doses?")
    if overdue_labs:
        questions.append("What labs are overdue and when were they last done?")

    # Always include the baseline comparison question
    questions.append("How does current BP compare to this patient's historical baseline?")

    return questions[:4]


async def generate_proactive_suggestion(
    briefing_payload: dict[str, Any],
    db_session: AsyncSession,
    patient_id: str,
) -> str | None:
    """Generate one proactive question the clinician should consider.

    Uses a lightweight LLM call (no tool use) to read the briefing payload
    and suggest the single most clinically important question not already
    in the suggested chips.

    Args:
        briefing_payload: The llm_response JSONB dict from a Briefing row.
        db_session: Async DB session (unused here, kept for future use).
        patient_id: Patient identifier (unused here, kept for future use).

    Returns:
        One proactive question string, or None if briefing has no data.
    """
    if not briefing_payload:
        return None

    urgent = briefing_payload.get("urgent_flags") or []
    agenda = briefing_payload.get("visit_agenda") or []
    if not urgent and not agenda:
        return None

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    prompt = (
        "You are assisting a GP reviewing a patient briefing before a consultation.\n"
        "Based on the briefing below, suggest ONE specific question the clinician should ask "
        "that would be most clinically valuable. Return only the question, no preamble.\n\n"
        f"Urgent flags: {', '.join(urgent) or 'None'}\n"
        f"Visit agenda: {'; '.join(agenda) or 'None'}\n"
        f"Trend: {briefing_payload.get('trend_summary', 'N/A')}\n"
        f"Adherence: {briefing_payload.get('adherence_summary', 'N/A')}"
    )

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = response.content[0].text.strip()
        # Basic safety check — skip if it contains forbidden phrases
        forbidden = ["prescribe", "diagnos", "non-adherent", "emergency", "mg"]
        if any(f in suggestion.lower() for f in forbidden):
            return None
        return suggestion
    except Exception as exc:
        logger.warning("Proactive suggestion generation failed: %s", exc)
        return None
