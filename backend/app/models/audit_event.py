"""ORM model for the ``audit_events`` table.

Every significant system action must produce an audit row.  Required actions:
- bundle_import (resource_type="Bundle")
- reading_ingested (resource_type="Reading")
- briefing_viewed (resource_type="Briefing") — also updates briefings.read_at
- alert_acknowledged (resource_type="Alert")

``outcome`` must always be "success" or "failure" — never omitted.
"""


from datetime import datetime

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditEvent(Base):
    """Immutable audit trail entry for a system or clinician action."""

    __tablename__ = "audit_events"

    audit_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    actor_type: Mapped[str] = mapped_column(String, nullable=False)  # system|clinician|admin
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    patient_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(
        String, nullable=False
    )  # bundle_import|reading_ingested|briefing_viewed|alert_acknowledged
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)  # success|failure
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    details: Mapped[str | None] = mapped_column(String, nullable=True)
