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
        "clinical_context_problem_assessments_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS problem_assessments JSONB",
    ),
    (
        "clinical_context_allergy_reactions_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS allergy_reactions TEXT[]",
    ),
    (
        "clinical_context_last_clinic_pulse_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_pulse SMALLINT",
    ),
    (
        "clinical_context_last_clinic_weight_kg_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_weight_kg NUMERIC(5,1)",
    ),
    (
        "clinical_context_last_clinic_spo2_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_spo2 NUMERIC(4,1)",
    ),
    (
        "clinical_context_historic_spo2_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS historic_spo2 NUMERIC[]",
    ),
    (
        "clinical_context_recent_labs_col",
        "ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS recent_labs JSONB",
    ),
    (
        "patients_risk_score_computed_at_col",
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS risk_score_computed_at TIMESTAMPTZ",
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
]

# ---------------------------------------------------------------------------
# DB-level audit trigger for readings table (Fix 38)
# asyncpg cannot execute multiple statements in one call, so we split the
# trigger setup into three separate statements executed individually.
# ---------------------------------------------------------------------------
_TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION aria_readings_audit_trigger()
RETURNS TRIGGER AS $$
DECLARE
    v_actor_id   TEXT;
    v_request_id TEXT;
BEGIN
    v_actor_id   := current_setting('aria.actor_id',   true);
    v_request_id := current_setting('aria.request_id', true);
    INSERT INTO audit_events (
        actor_type,
        actor_id,
        patient_id,
        action,
        resource_type,
        resource_id,
        outcome,
        request_id,
        details
    ) VALUES (
        COALESCE(NULLIF(v_actor_id, ''), 'system'),
        NULLIF(v_actor_id, ''),
        NEW.patient_id,
        'reading_ingested',
        'Reading',
        NEW.reading_id::TEXT,
        'success',
        NULLIF(v_request_id, ''),
        'DB trigger audit record'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_DROP_TRIGGER_SQL = "DROP TRIGGER IF EXISTS trg_readings_audit ON readings"

_CREATE_TRIGGER_SQL = (
    "CREATE TRIGGER trg_readings_audit "
    "AFTER INSERT ON readings "
    "FOR EACH ROW EXECUTE FUNCTION aria_readings_audit_trigger()"
)


async def create_all() -> None:
    """Create tables, indexes, column migrations, and DB triggers."""
    async with engine.begin() as conn:
        # Create all tables registered with Base.metadata
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    # Print table confirmations
    for table_name in Base.metadata.tables:
        print(f"[OK] Table: {table_name}")

    # Create indexes and column migrations individually (safe to re-run)
    async with engine.begin() as conn:
        for index_name, ddl in INDEXES:
            await conn.execute(text(ddl))
            print(f"[OK] Index/migration: {index_name}")

    # Install DB-level audit trigger for readings table (Fix 38)
    # Three separate statements — asyncpg cannot execute multi-statement SQL in one call.
    async with engine.begin() as conn:
        await conn.execute(text(_TRIGGER_FUNCTION_SQL))
    async with engine.begin() as conn:
        await conn.execute(text(_DROP_TRIGGER_SQL))
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_TRIGGER_SQL))
    print("[OK] Trigger: trg_readings_audit on readings")

    print()
    print(
        f"Done. {len(Base.metadata.tables)} tables, {len(INDEXES)} indexes/migrations, "
        f"1 audit trigger created or already exist."
    )


if __name__ == "__main__":
    asyncio.run(create_all())
