# ARIA Backend Context

## GIT POLICY
Never git push, commit, or add. Tell user what changed.

## Setup
conda activate aria
cd backend
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000

## Testing
python -m pytest tests/ -v -m "not integration"
python -m pytest tests/test_integration.py -v

## Linting
ruff check app/
ruff format app/

## Database
PostgreSQL via Supabase. DATABASE_URL in backend/.env
Async driver: asyncpg. ORM: SQLAlchemy 2.0 async.
Schema: 8 tables, 11 indexes (CREATE INDEX IF NOT EXISTS) + 1 ALTER TABLE
  migration — all managed by scripts/setup_db.py, safe to re-run.
clinical_context.med_history JSONB — added via ALTER TABLE migration in
  setup_db.py. Stores full medication timeline from iEMR visits.

## Critical Syntax
SQLAlchemy 2.0 async:
  async with AsyncSession(engine) as session:
      result = await session.execute(select(Model).where(...))

Pydantic v2:
  model_config = SettingsConfigDict(env_file=".env", extra="ignore")

## Service Layers
fhir/           iEMR adapter, FHIR ingestion, validator
generator/      synthetic readings + medication confirmations
pattern_engine/ gap, inertia, adherence, deterioration + risk_scorer (Layer 2)
briefing/       deterministic JSON composer + LLM summarizer (Layer 3)
worker/         processing_jobs poller + scheduler

## Audit Rule
Every bundle_import, reading_ingested, briefing_viewed,
alert_acknowledged must create an audit_events row.

## Layer Execution Order
Always: Layer 1 detectors -> Layer 2 risk_scorer -> Layer 3 summarizer
Never run Layer 3 before Layer 1 is complete and correct.
