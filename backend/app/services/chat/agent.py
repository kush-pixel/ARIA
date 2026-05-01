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

# TEMP: OpenAI testing override — revert to `import anthropic` when switching back
from openai import BadRequestError as OpenAIBadRequestError
from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.briefing.llm_validator import ValidationResult
from app.services.chat import session as session_store
from app.services.chat.formatter import ChatResponse, make_blocked_response, parse_response
from app.services.chat.tools import TOOL_SCHEMAS, dispatch_tool, get_briefing, get_clinical_context
from app.services.chat.validator import validate_chat_response
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

# TEMP: OpenAI gpt-4o-mini — revert to "claude-sonnet-4-20250514" when switching back
_MODEL = "gpt-4o-mini"
_MAX_TOOL_ROUNDS = 3
_PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "chat_system_prompt.md"

# Keywords that indicate a patient-clinical question — at least one must be present
_CLINICAL_KEYWORDS: frozenset[str] = frozenset({
    "patient", "bp", "blood pressure", "systolic", "diastolic",
    "medication", "med", "drug", "adherence", "dose", "dosage",
    "reading", "readings", "trend", "alert", "briefing", "lab",
    "clinical", "appointment", "condition", "problem", "hypertension",
    "heart rate", "pulse", "monitoring", "monitor", "mmhg",
    "prescribed", "prescription", "missed", "average", "baseline",
    "risk", "inertia", "gap", "deterioration", "flag", "urgent",
    "overdue", "visit", "summary", "compare", "history", "result",
    "score", "tier", "last week", "last month", "past week", "past month",
    "recent", "current", "today", "yesterday", "days", "weeks", "months",
    "how is", "how has", "what is", "what are", "what was", "what were",
    "when did", "when was", "why did", "why is", "why was", "show me",
    "tell me", "explain", "describe", "any", "latest", "highest", "lowest",
    "morning", "evening", "session", "confirmed", "pattern",
})

_OFF_TOPIC_RESPONSE = json.dumps({
    "answer": "I can only answer questions about this patient's clinical data in ARIA. Please ask about BP trends, medications, adherence, lab results, or clinical alerts.",
    "evidence": [],
    "confidence": "no_data",
    "data_gaps": [],
})


def _to_groq_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-format tool schemas to OpenAI/Groq function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        }
        for s in schemas
    ]


def _is_off_topic(question: str) -> bool:
    """Return True if the question has no clinical keywords — block before hitting LLM.

    Uses a conservative allowlist: if any clinical keyword appears, the question
    is allowed through. Only pure off-topic questions (politics, geography, general
    knowledge) will have zero matches.
    """
    q_lower = question.lower()
    return not any(kw in q_lower for kw in _CLINICAL_KEYWORDS)


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


def _generate_followup_questions(tools_used: list[str]) -> list[str]:
    """Generate 2-3 contextual follow-up question chips based on tools called.

    Args:
        tools_used: List of tool names called during this agent turn.

    Returns:
        Up to 3 follow-up question strings.
    """
    seen = set(tools_used)
    questions: list[str] = []
    if "get_patient_readings" in seen:
        questions.append("Were there any monitoring gaps during this period?")
        questions.append("How does this compare to 3 months ago?")
    if "get_medication_history" in seen:
        questions.append("What is the current adherence rate?")
    if "get_adherence_summary" in seen:
        questions.append("Which medication had the most missed doses?")
    if "get_briefing" in seen:
        questions.append("Are there any overdue labs flagged?")
    if "get_clinical_context" in seen:
        questions.append("When was the last clinic visit?")
    return questions[:3]


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
    # Pre-flight: block off-topic questions before hitting the LLM
    if _is_off_topic(question):
        logger.info("Off-topic question blocked pre-flight: patient=%s", patient_id)
        yield _sse("done", {
            "answer": "My role is to support pre-visit clinical review for this patient. I'm not able to answer general questions outside that scope.",
            "evidence": [],
            "confidence": "no_data",
            "data_gaps": [],
            "tools_used": [],
            "blocked": True,
            "block_reason": "off_topic",
        })
        return

    client = OpenAI(api_key=settings.openai_api_key)
    groq_tools = _to_groq_tools(TOOL_SCHEMAS)

    system_prompt = _load_system_prompt()
    patient_context = await _build_patient_context(patient_id, db_session)
    system_content = system_prompt + "\n\n" + patient_context

    history = await session_store.get_history(clinician_id, patient_id, db_session)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_content},
        *list(history),
        {"role": "user", "content": question},
    ]

    tool_results_accumulator: dict[str, Any] = {}
    tools_used: list[str] = []
    final_text = ""

    # ── Tool-use loop ──────────────────────────────────────────────────────────
    for round_num in range(_MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                max_tokens=1024,
                messages=messages,
                tools=groq_tools,
                tool_choice="auto",
            )
        except OpenAIBadRequestError as exc:
            # Model emitted a malformed tool call (e.g. <function=null>) — fall back
            # to a plain completion so the turn still produces an answer.
            logger.warning("Groq tool_use_failed (round %d) — retrying without tools: %s", round_num + 1, exc)
            fallback = client.chat.completions.create(
                model=_MODEL,
                max_tokens=1024,
                messages=messages,
            )
            final_text = fallback.choices[0].message.content or ""
            break

        message = response.choices[0].message
        tool_calls = message.tool_calls or []

        if not tool_calls:
            final_text = message.content or ""
            break

        # Notify frontend which tools are being called
        for tc in tool_calls:
            yield _sse("thinking", {"tool": tc.function.name, "round": round_num + 1})
            tools_used.append(tc.function.name)

        # Append assistant's tool_call turn to history
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        # Execute tools and append results
        for tc in tool_calls:
            try:
                result = await dispatch_tool(
                    tool_name=tc.function.name,
                    tool_input=json.loads(tc.function.arguments),
                    patient_id=patient_id,
                    session=db_session,
                )
                tool_results_accumulator[tc.function.name] = result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
            except Exception as exc:
                logger.error("Tool %s failed: %s", tc.function.name, exc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": str(exc), "data_available": False}),
                })

    # ── Synthesise after tool rounds if no text yet ────────────────────────────
    if not final_text:
        final_response = client.chat.completions.create(
            model=_MODEL,
            max_tokens=1024,
            messages=messages,
        )
        final_text = final_response.choices[0].message.content or ""

    # ── Parse + validate BEFORE streaming — blocked answers must not reach the screen ──
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
        # Do NOT persist blocked turns — prevents LLM from "learning" to comply on repeat asks
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

    # ── Stream validated answer tokens ────────────────────────────────────────
    words = parsed.answer.split(" ")
    for i, word in enumerate(words):
        chunk = word if i == len(words) - 1 else word + " "
        yield _sse("token", {"token": chunk})

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
        "follow_up_questions": _generate_followup_questions(tools_used),
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

    client = OpenAI(api_key=settings.openai_api_key)

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
        response = client.chat.completions.create(
            model=_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = response.choices[0].message.content.strip()
        # Basic safety check — skip if it contains forbidden phrases
        forbidden = ["prescribe", "diagnos", "non-adherent", "emergency", "mg"]
        if any(f in suggestion.lower() for f in forbidden):
            return None
        return suggestion
    except Exception as exc:
        logger.warning("Proactive suggestion generation failed: %s", exc)
        return None
