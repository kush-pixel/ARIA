"""Clinical chatbot API routes for ARIA.

POST /api/chat                                  — SSE streaming Q&A
GET  /api/chat/suggested-questions/{patient_id} — dynamic question chips + proactive suggestion
DELETE /api/chat/session                        — clear conversation history
POST /api/chat/summary/{patient_id}             — bullet-point conversation summary
POST /api/chat/feedback                         — clinician thumbs-up/down feedback
"""

from __future__ import annotations

from typing import Any

import anthropic
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_session
from app.models.audit_event import AuditEvent
from app.models.briefing import Briefing
from app.models.patient import Patient
from app.services.chat import session as session_store
from app.services.chat.agent import (
    generate_proactive_suggestion,
    generate_suggested_questions,
    run_agent,
)
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["chat"])

_DEFAULT_CLINICIAN_ID = "demo-clinician"


class ChatRequest(BaseModel):
    """Request body for a chat question."""

    patient_id: str
    question: str


class ClearSessionRequest(BaseModel):
    """Request body for clearing a chat session."""

    patient_id: str


@router.post("/chat")
async def chat(
    body: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Stream a chatbot response for a question about a patient."""
    result = await session.execute(
        select(Patient).where(Patient.patient_id == body.patient_id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    logger.info("Chat request: patient=%s question_len=%d", body.patient_id, len(body.question))

    return StreamingResponse(
        run_agent(
            question=body.question,
            patient_id=body.patient_id,
            clinician_id=_DEFAULT_CLINICIAN_ID,
            db_session=session,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/suggested-questions/{patient_id}")
async def suggested_questions(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return suggested question chips and one proactive suggestion."""
    result = await session.execute(
        select(Briefing)
        .where(Briefing.patient_id == patient_id)
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )
    briefing = result.scalar_one_or_none()
    payload: dict[str, Any] = briefing.llm_response if briefing else {}

    questions = generate_suggested_questions(payload)
    proactive = await generate_proactive_suggestion(payload, session, patient_id)

    return {"questions": questions, "proactive": proactive}


@router.delete("/chat/session")
async def clear_session(
    body: ClearSessionRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Clear the conversation history for a patient session."""
    await session_store.clear_session(_DEFAULT_CLINICIAN_ID, body.patient_id, session)
    return {"cleared": True}


class ChatFeedbackRequest(BaseModel):
    """Request body for chat response feedback."""

    patient_id: str
    message_index: int
    rating: str  # "up" | "down"


@router.post("/chat/summary/{patient_id}")
async def chat_summary(
    patient_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | None]:
    """Generate a bullet-point summary of the current conversation."""
    history = session_store.get_raw_history(_DEFAULT_CLINICIAN_ID, patient_id)
    if len(history) < 2:
        return {"summary": None}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    convo = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history[-20:]
    )
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "Summarise this clinical consultation in 3-5 concise bullet points "
                    "suitable for clinical notes. Be factual, no recommendations.\n\n"
                    + convo
                ),
            }],
        )
        return {"summary": response.content[0].text.strip()}
    except Exception as exc:
        logger.warning("Summary generation failed: %s", exc)
        return {"summary": None}


@router.post("/chat/feedback")
async def chat_feedback(
    body: ChatFeedbackRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Record clinician thumbs-up/down feedback on a chat response."""
    session.add(AuditEvent(
        actor_type="clinician",
        actor_id=_DEFAULT_CLINICIAN_ID,
        patient_id=body.patient_id,
        action="chat_feedback",
        resource_type="Chat",
        resource_id=body.patient_id,
        outcome="success",
        details=f"rating={body.rating} msg_index={body.message_index}",
    ))
    await session.commit()
    return {"recorded": True}
