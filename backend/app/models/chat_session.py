"""ORM model for the ``chat_sessions`` table.

Persists clinician-patient conversation history so sessions survive server
restarts. Each row stores the full messages array as JSONB.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatSession(Base):
    """Persistent conversation history for one clinician + patient pair."""

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    clinician_id: Mapped[str] = mapped_column(String, nullable=False)
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
