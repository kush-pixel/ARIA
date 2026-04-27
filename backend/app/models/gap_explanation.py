"""ORM model for the gap_explanations table (Fix 41).

Records clinician- or patient-supplied reasons for a reading gap,
distinguishing device issues from non-compliance or other causes.
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GapExplanation(Base):
    """A recorded explanation for a patient reading gap."""

    __tablename__ = "gap_explanations"

    explanation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    # Inclusive date range of the gap being explained
    gap_start: Mapped[date] = mapped_column(Date, nullable=False)
    gap_end: Mapped[date] = mapped_column(Date, nullable=False)
    # device_issue | travel | illness | unknown | non_compliance
    reason: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # clinician | patient | system
    reported_by: Mapped[str] = mapped_column(String, nullable=False, default="clinician")
    reporter_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
