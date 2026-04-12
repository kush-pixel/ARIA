"""ORM model for the ``medication_confirmations`` table.

Tracks whether a patient confirmed taking each scheduled medication dose.
A NULL ``confirmed_at`` means the dose was missed.  Used by Layer 1
adherence analysis to compute adherence rate per medication.
"""


from datetime import datetime

from sqlalchemy import DateTime, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MedicationConfirmation(Base):
    """A single scheduled medication dose and its confirmation status."""

    __tablename__ = "medication_confirmations"

    confirmation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    medication_name: Mapped[str] = mapped_column(String, nullable=False)
    rxnorm_code: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # NULL = missed dose
    confirmation_type: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # synthetic_demo|tap|photo|qr_scan
    confidence: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'self_report'")
    )
    minutes_from_schedule: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
