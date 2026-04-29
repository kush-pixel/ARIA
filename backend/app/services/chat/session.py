"""Conversation session management for the ARIA chatbot.

Maintains in-memory conversation history per (clinician_id, patient_id) pair,
backed by the chat_sessions table for persistence across server restarts.
Max 20 turns stored — oldest pair evicted when limit is exceeded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession
from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_MAX_TURNS = 20

# In-memory cache: (clinician_id, patient_id) → list of message dicts
_memory: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _key(clinician_id: str, patient_id: str) -> tuple[str, str]:
    return (clinician_id, patient_id)


async def get_history(
    clinician_id: str,
    patient_id: str,
    db_session: AsyncSession,
) -> list[dict[str, Any]]:
    """Return conversation history, loading from DB on first access.

    Args:
        clinician_id: Clinician identifier from JWT.
        patient_id: Patient identifier.
        db_session: Async DB session for DB fallback load.

    Returns:
        List of message dicts [{role, content}, ...].
    """
    k = _key(clinician_id, patient_id)
    if k in _memory:
        return _memory[k]

    # Load from DB
    result = await db_session.execute(
        select(ChatSession)
        .where(ChatSession.clinician_id == clinician_id)
        .where(ChatSession.patient_id == patient_id)
        .order_by(ChatSession.last_active.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    messages: list[dict[str, Any]] = row.messages if row else []
    _memory[k] = messages
    return messages


async def append_messages(
    clinician_id: str,
    patient_id: str,
    new_messages: list[dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    """Append one or more messages to the session and persist to DB.

    Evicts the oldest user+assistant pair when history exceeds MAX_TURNS.

    Args:
        clinician_id: Clinician identifier.
        patient_id: Patient identifier.
        new_messages: List of {role, content} dicts to append.
        db_session: Async DB session.
    """
    k = _key(clinician_id, patient_id)
    history = _memory.get(k, [])
    history.extend(new_messages)

    # Evict oldest pair when over limit
    while len(history) > _MAX_TURNS * 2:
        history = history[2:]

    _memory[k] = history

    # Upsert to DB
    result = await db_session.execute(
        select(ChatSession)
        .where(ChatSession.clinician_id == clinician_id)
        .where(ChatSession.patient_id == patient_id)
    )
    row = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if row is None:
        db_session.add(ChatSession(
            session_id=str(uuid.uuid4()),
            clinician_id=clinician_id,
            patient_id=patient_id,
            messages=history,
            last_active=now,
        ))
    else:
        row.messages = history
        row.last_active = now
        db_session.add(row)

    await db_session.commit()


async def clear_session(
    clinician_id: str,
    patient_id: str,
    db_session: AsyncSession,
) -> None:
    """Clear conversation history for a clinician-patient pair.

    Args:
        clinician_id: Clinician identifier.
        patient_id: Patient identifier.
        db_session: Async DB session.
    """
    k = _key(clinician_id, patient_id)
    _memory.pop(k, None)

    result = await db_session.execute(
        select(ChatSession)
        .where(ChatSession.clinician_id == clinician_id)
        .where(ChatSession.patient_id == patient_id)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        row.messages = []
        row.last_active = datetime.now(UTC)
        db_session.add(row)
        await db_session.commit()

    logger.info("Chat session cleared: clinician=%s patient=%s", clinician_id, patient_id)
