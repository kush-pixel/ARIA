"""ORM model for the ``alert_feedback`` table (Fix 42 Layer 1).

Records the clinician's disposition when an alert is acknowledged so the
feedback loop has labelled signal for Layer 2 calibration recommendations.
One row per acknowledgement that includes a disposition; alerts dismissed
without disposition information leave no feedback row.
"""


from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AlertFeedback(Base):
    """Clinician disposition attached to an acknowledged alert."""

    __tablename__ = "alert_feedback"

    feedback_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    alert_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alerts.alert_id"),
        nullable=False,
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    detector_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # gap | inertia | deterioration | adherence
    disposition: Mapped[str] = mapped_column(
        String, nullable=False
    )  # agree_acting | agree_monitoring | disagree
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    clinician_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
