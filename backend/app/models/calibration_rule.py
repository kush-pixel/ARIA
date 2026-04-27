"""ORM model for the calibration_rules table (Fix 42 L2).

Clinician-approved detector calibration rules created after 4+ disagree
dispositions accumulate for the same patient/detector pair.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CalibrationRule(Base):
    """A clinician-approved detector calibration rule."""

    __tablename__ = "calibration_rules"

    rule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    detector_type: Mapped[str] = mapped_column(String, nullable=False)
    dismissal_count: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
