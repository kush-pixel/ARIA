"""Conversation session management for the ARIA chatbot.

Maintains in-memory conversation history per (clinician_id, patient_id) pair.
Max 20 turns stored — oldest pair evicted when limit is exceeded.
Sessions are lost on server restart (no persistence needed without auth).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import get_logger

logger = get_logger(__name__)

_MAX_TURNS = 20

_memory: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _key(clinician_id: str, patient_id: str) -> tuple[str, str]:
    return (clinician_id, patient_id)


async def get_history(
    clinician_id: str,
    patient_id: str,
    db_session: AsyncSession,
) -> list[dict[str, Any]]:
    """Return in-memory conversation history.

    Args:
        clinician_id: Clinician identifier (may be a demo constant).
        patient_id: Patient identifier.
        db_session: Unused — kept for API compatibility.

    Returns:
        List of message dicts [{role, content}, ...].
    """
    return _memory.get(_key(clinician_id, patient_id), [])


async def append_messages(
    clinician_id: str,
    patient_id: str,
    new_messages: list[dict[str, Any]],
    db_session: AsyncSession,
) -> None:
    """Append messages to the in-memory session.

    Evicts the oldest user+assistant pair when history exceeds MAX_TURNS.

    Args:
        clinician_id: Clinician identifier.
        patient_id: Patient identifier.
        new_messages: List of {role, content} dicts to append.
        db_session: Unused — kept for API compatibility.
    """
    k = _key(clinician_id, patient_id)
    history = _memory.get(k, [])
    history.extend(new_messages)

    while len(history) > _MAX_TURNS * 2:
        history = history[2:]

    _memory[k] = history


async def clear_session(
    clinician_id: str,
    patient_id: str,
    db_session: AsyncSession,
) -> None:
    """Clear in-memory conversation history for a clinician-patient pair.

    Args:
        clinician_id: Clinician identifier.
        patient_id: Patient identifier.
        db_session: Unused — kept for API compatibility.
    """
    _memory.pop(_key(clinician_id, patient_id), None)
    logger.info("Chat session cleared: clinician=%s patient=%s", clinician_id, patient_id)


def get_raw_history(clinician_id: str, patient_id: str) -> list[dict[str, Any]]:
    """Return raw history for summary generation (synchronous, no DB needed).

    Args:
        clinician_id: Clinician identifier.
        patient_id: Patient identifier.

    Returns:
        List of message dicts [{role, content}, ...].
    """
    return list(_memory.get(_key(clinician_id, patient_id), []))
