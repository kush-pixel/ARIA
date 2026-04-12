"""ORM model for the ``readings`` table.

Stores home BP readings — both generated synthetic data and manual or
BLE-auto readings.  ``systolic_avg`` / ``diastolic_avg`` are the primary
analysis values (mean of reading_1 and reading_2 if both present).

Device outages are represented as absent rows, never null values.
"""


from datetime import datetime

from sqlalchemy import DateTime, Numeric, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Reading(Base):
    """A single home blood-pressure measurement session."""

    __tablename__ = "readings"

    reading_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)

    # First reading in session (always present)
    systolic_1: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    diastolic_1: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    heart_rate_1: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Second reading in session (absent for ad_hoc single readings)
    systolic_2: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    diastolic_2: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    heart_rate_2: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Averages — primary analysis values
    systolic_avg: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    diastolic_avg: Mapped[float] = mapped_column(Numeric(5, 1), nullable=False)
    heart_rate_avg: Mapped[float | None] = mapped_column(Numeric(5, 1), nullable=True)

    effective_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    session: Mapped[str] = mapped_column(String, nullable=False)  # morning|evening|ad_hoc
    source: Mapped[str] = mapped_column(String, nullable=False)   # generated|manual|ble_auto
    submitted_by: Mapped[str] = mapped_column(String, nullable=False)  # patient|carer|generator

    bp_position: Mapped[str | None] = mapped_column(String, nullable=True)  # seated|standing
    bp_site: Mapped[str | None] = mapped_column(String, nullable=True)      # left_arm|right_arm

    consent_version: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'1.0'")
    )
    medication_taken: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # yes|no|partial|NULL

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
