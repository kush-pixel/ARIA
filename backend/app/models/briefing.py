"""ORM model for the ``briefings`` table.

A briefing is generated at 07:30 on appointment days and contains the
structured pre-visit summary for the clinician.  ``llm_response`` holds
the deterministic Layer 1 JSON payload.  ``model_version`` and
``prompt_hash`` are populated only when the optional Layer 3 LLM summary
is generated; they are NULL for Layer 1-only briefings.
"""


from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Briefing(Base):
    """Pre-visit briefing generated for a patient's appointment day."""

    __tablename__ = "briefings"

    briefing_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    appointment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    llm_response: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Layer 3 audit columns — NULL when only Layer 1 briefing was generated
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
