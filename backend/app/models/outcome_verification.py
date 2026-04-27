"""ORM model for the outcome_verifications table (Fix 42 L3).

Tracks 30-day outcomes after a clinician dismisses an alert (disposition='disagree').
The daily worker sweep checks for concerning events within the window and
prompts a retrospective label from the clinician.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutcomeVerification(Base):
    """Pending or completed 30-day outcome check for a dismissed alert."""

    __tablename__ = "outcome_verifications"

    verification_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    feedback_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alert_feedback.feedback_id"),
        nullable=False,
    )
    alert_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alerts.alert_id"),
        nullable=False,
    )
    patient_id: Mapped[str] = mapped_column(String, nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # pending → deterioration_cluster | urgent_visit | none (set when check runs)
    outcome_type: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    prompted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # relevant | not_relevant | unsure (clinician retrospective response)
    clinician_response: Mapped[str | None] = mapped_column(String, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
