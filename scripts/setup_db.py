"""Create all ARIA database tables and indexes in PostgreSQL (Supabase).

Run from the project root with the aria conda environment active:

    conda activate aria
    python scripts/setup_db.py

The script is safe to re-run: tables use CREATE TABLE IF NOT EXISTS
(via SQLAlchemy's checkfirst=True) and indexes use CREATE INDEX IF NOT EXISTS.

.env is loaded from backend/.env relative to this script's parent directory.
"""

import asyncio
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: add backend/ to sys.path and load .env before importing app code
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402 — must come after sys.path patch

# Try backend/.env first (canonical), then backend/backend/.env (common misplacement)
_candidates = [BACKEND_DIR / ".env", BACKEND_DIR / "backend" / ".env"]
env_path = next((p for p in _candidates if p.exists()), None)
if env_path is None:
    print("[WARN] .env not found in backend/ or backend/backend/ — falling back to environment variables")
else:
    load_dotenv(env_path)
    print(f"[OK] Loaded .env from {env_path}")

# ---------------------------------------------------------------------------
# Import app modules (after .env is loaded so Settings() resolves correctly)
# ---------------------------------------------------------------------------
from sqlalchemy import text  # noqa: E402

from app.db.base import Base, engine  # noqa: E402
import app.models  # noqa: E402, F401 — registers all 8 models with Base.metadata

# ---------------------------------------------------------------------------
# Index definitions (all use IF NOT EXISTS so safe to re-run)
# ---------------------------------------------------------------------------
INDEXES: list[tuple[str, str]] = [
    (
        "idx_processing_jobs_idempotency",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_jobs_idempotency "
        "ON processing_jobs (idempotency_key)",
    ),
    (
        "idx_readings_patient_datetime",
        "CREATE INDEX IF NOT EXISTS idx_readings_patient_datetime "
        "ON readings (patient_id, effective_datetime DESC)",
    ),
    (
        "idx_readings_patient_session",
        "CREATE INDEX IF NOT EXISTS idx_readings_patient_session "
        "ON readings (patient_id, session, effective_datetime DESC)",
    ),
    (
        "idx_alerts_undelivered",
        "CREATE INDEX IF NOT EXISTS idx_alerts_undelivered "
        "ON alerts (patient_id, delivered_at) "
        "WHERE delivered_at IS NULL",
    ),
    (
        "idx_patients_appointment",
        "CREATE INDEX IF NOT EXISTS idx_patients_appointment "
        "ON patients (next_appointment) "
        "WHERE monitoring_active = TRUE",
    ),
    (
        "idx_cc_problem_codes",
        "CREATE INDEX IF NOT EXISTS idx_cc_problem_codes "
        "ON clinical_context USING GIN (problem_codes)",
    ),
    (
        "idx_confirmations_patient_scheduled",
        "CREATE INDEX IF NOT EXISTS idx_confirmations_patient_scheduled "
        "ON medication_confirmations (patient_id, scheduled_time DESC)",
    ),
    (
        "idx_confirmations_missed",
        "CREATE INDEX IF NOT EXISTS idx_confirmations_missed "
        "ON medication_confirmations (patient_id, scheduled_time) "
        "WHERE confirmed_at IS NULL",
    ),
    (
        "idx_processing_jobs_status_type",
        "CREATE INDEX IF NOT EXISTS idx_processing_jobs_status_type "
        "ON processing_jobs (status, job_type, queued_at)",
    ),
    (
        "idx_audit_events_patient_time",
        "CREATE INDEX IF NOT EXISTS idx_audit_events_patient_time "
        "ON audit_events (patient_id, event_timestamp DESC)",
    ),
    (
        "idx_patients_risk_score",
        "CREATE INDEX IF NOT EXISTS idx_patients_risk_score "
        "ON patients (risk_tier, risk_score DESC)",
    ),
    (
        "clinical_context_med_history_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS med_history JSONB",
    ),
    (
        "idx_readings_patient_datetime_source",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_patient_datetime_source "
        "ON readings (patient_id, effective_datetime, source)",
    ),
    (
        "idx_confirmations_patient_med_scheduled",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_confirmations_patient_med_scheduled "
        "ON medication_confirmations (patient_id, medication_name, scheduled_time)",
    ),
    (
        "patients_risk_score_computed_at_col",
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS risk_score_computed_at TIMESTAMPTZ",
    ),
    (
        "idx_alert_feedback_patient_detector",
        "CREATE INDEX IF NOT EXISTS idx_alert_feedback_patient_detector "
        "ON alert_feedback (patient_id, detector_type, created_at DESC)",
    ),
    (
        "idx_alert_feedback_alert",
        "CREATE INDEX IF NOT EXISTS idx_alert_feedback_alert "
        "ON alert_feedback (alert_id)",
    ),
    # Fix 45 — escalation + off-hours columns on alerts
    (
        "alerts_off_hours_col",
        "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS off_hours BOOLEAN DEFAULT FALSE",
    ),
    (
        "alerts_escalated_col",
        "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS escalated BOOLEAN DEFAULT FALSE",
    ),
    # Fix 42 L2 — calibration_rules index
    (
        "idx_calibration_rules_patient_detector",
        "CREATE INDEX IF NOT EXISTS idx_calibration_rules_patient_detector "
        "ON calibration_rules (patient_id, detector_type, active)",
    ),
    # Fix 42 L3 — outcome_verifications indexes
    (
        "idx_outcome_verifications_pending",
        "CREATE INDEX IF NOT EXISTS idx_outcome_verifications_pending "
        "ON outcome_verifications (outcome_type, check_after) "
        "WHERE outcome_type = 'pending'",
    ),
    (
        "idx_outcome_verifications_patient",
        "CREATE INDEX IF NOT EXISTS idx_outcome_verifications_patient "
        "ON outcome_verifications (patient_id, prompted_at DESC)",
    ),
]


async def create_all() -> None:
    """Create tables then indexes, printing a confirmation line for each."""
    async with engine.begin() as conn:
        # Create all tables registered with Base.metadata
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    # Print table confirmations
    for table_name in Base.metadata.tables:
        print(f"[OK] Table: {table_name}")

    # Create indexes individually so we can print per-index confirmation
    async with engine.begin() as conn:
        for index_name, ddl in INDEXES:
            await conn.execute(text(ddl))
            print(f"[OK] Index: {index_name}")

    print()
    print(
        f"Done. {len(Base.metadata.tables)} tables, {len(INDEXES)} indexes "
        f"created or already exist."
    )


if __name__ == "__main__":
    asyncio.run(create_all())
