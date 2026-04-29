"""Clinical chatbot API routes for ARIA.

POST /api/chat                                  — SSE streaming Q&A
GET  /api/chat/suggested-questions/{patient_id} — dynamic question chips + proactive suggestion
DELETE /api/chat/session                        — clear conversation history
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
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
