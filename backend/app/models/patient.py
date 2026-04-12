"""ORM model for the ``patients`` table.

Each row represents one enrolled patient.  ``patient_id`` is the iEMR
MED_REC_NO (plain text), not a UUID.  ``risk_score`` is the Layer 2
weighted priority score (0.0–100.0); ``risk_tier`` is the coarse bucket
(high / medium / low).  The dashboard sorts by tier then by risk_score DESC.
"""


from datetime import datetime

from sqlalchemy import Boolean, DateTime, Numeric, SmallInteger, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Patient(Base):
    """Enrolled hypertensive patient record."""

    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String, primary_key=True)
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True)
    age: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    risk_tier: Mapped[str] = mapped_column(String, nullable=False)
    tier_override: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    monitoring_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    next_appointment: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    enrolled_by: Mapped[str | None] = mapped_column(String, nullable=True)
