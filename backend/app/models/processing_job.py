"""ORM model for the ``processing_jobs`` table.

Background jobs are enqueued here and polled every 30 seconds by the worker.
``idempotency_key`` enforces at-most-once processing — insert fails if the
same key is submitted twice.  Status flow: queued → running → succeeded | failed.
"""


from datetime import datetime

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProcessingJob(Base):
    """An async background job in the ARIA processing queue."""

    __tablename__ = "processing_jobs"

    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    job_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # pattern_recompute|briefing_generation|bundle_import
    patient_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False
    )  # queued|running|succeeded|failed
    payload_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'system'")
    )
