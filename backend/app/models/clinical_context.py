"""ORM model for the ``clinical_context`` table.

One row per patient, pre-computed at FHIR ingestion.  Parallel arrays:
``active_problems[n]`` corresponds to ``problem_codes[n]``, and
``current_medications[n]`` corresponds to ``med_rxnorm_codes[n]``.

``historic_bp_dates`` stores dates as ISO-8601 strings (e.g. "2024-01-15")
because asyncpg has known issues with DATE[] arrays in async context.
"""


from datetime import date, datetime

from sqlalchemy import Date, DateTime, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClinicalContext(Base):
    """Pre-computed clinical context for a patient, sourced from FHIR ingestion."""

    __tablename__ = "clinical_context"

    patient_id: Mapped[str] = mapped_column(
        String, primary_key=True
    )
    active_problems: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    problem_codes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    current_medications: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    med_rxnorm_codes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    med_history: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    last_med_change: Mapped[date | None] = mapped_column(Date, nullable=True)
    allergies: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    last_visit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_clinic_systolic: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_clinic_diastolic: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    historic_bp_systolic: Mapped[list[int] | None] = mapped_column(
        ARRAY(SmallInteger), nullable=True
    )
    # Stored as ISO-8601 strings — asyncpg DATE[] async incompatibility workaround.
    historic_bp_dates: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    overdue_labs: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    social_context: Mapped[str | None] = mapped_column(String, nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
