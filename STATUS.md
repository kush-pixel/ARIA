# ARIA v4.3 — Project Status
Last updated: 2026-04-12 by Kush Patel

---

## Pipeline Status

```
iEMR JSON → [DONE] FHIR Bundle → [NEXT] PostgreSQL tables → [ ] Pattern engine → [ ] Briefing → [ ] Dashboard
```

---

## Implementation State

### COMPLETE
- Project scaffold and folder structure
- All AGENTS.md files for Claude Code and Codex
- All Claude Code hooks, skills, and agents
- backend/pyproject.toml, requirements.txt, requirements-dev.txt
- backend/app/config.py — Pydantic v2 Settings, 7 fields (DATABASE_URL, ANTHROPIC_API_KEY, APP_SECRET_KEY, APP_ENV, APP_DEBUG, DEMO_MODE, BRIEFING_TRIGGER)
- backend/app/db/base.py — async engine (auto-converts postgresql:// → postgresql+asyncpg://), AsyncSessionLocal factory, DeclarativeBase
- backend/app/db/session.py — get_session() FastAPI dependency
- backend/app/models/ — all 8 ORM models (patients, clinical_context, readings, medication_confirmations, alerts, briefings, processing_jobs, audit_events)
- scripts/setup_db.py — creates 8 tables + 11 indexes; safe to re-run (IF NOT EXISTS)
- backend/app/utils/logging_utils.py — get_logger(name) returns a named stdlib Logger with ISO timestamp format; used by all backend modules
- backend/app/services/fhir/adapter.py — iEMR JSON → FHIR R4 Bundle; 6 resource types (Patient, Condition, MedicationRequest, Observation, AllergyIntolerance, ServiceRequest); most-recent-wins deduplication keyed by iEMR code for all types except Observation; VITALS_DATETIME used for effectiveDateTime (never ADMIT_DATE); non-standard `_age` extension passes age to ingestion layer
- backend/tests/test_fhir_adapter.py — 28 tests (27 unit + 1 integration); all passing; integration test marked @pytest.mark.integration, skipped if real data absent
- scripts/run_adapter.py — CLI: reads iEMR JSON, writes FHIR Bundle to data/fhir/bundles/<id>_bundle.json, prints per-type resource counts; accepts --patient (required) and --patient-id (optional, defaults to filename stem) for generalizability across patients

### IN PROGRESS
- backend/app/services/fhir/ingestion.py — Task 3: FHIR Bundle → 8 PostgreSQL tables (starting)
- backend/app/services/fhir/validator.py — Task 3: validate FHIR Bundle structure before ingestion (starting)
- scripts/run_ingestion.py — Task 3: CLI to ingest a FHIR Bundle file into PostgreSQL (starting)

### NOT STARTED
- backend/app/services/generator/ (reading_generator, confirmation_generator)
- backend/app/services/pattern_engine/ (all detectors + risk_scorer)
- backend/app/services/briefing/ (composer, summarizer)
- backend/app/services/worker/ (processor, scheduler)
- backend/app/api/ (all routes)
- All frontend components

---

## Schema Changes Since v4.3 Spec
- briefings table: added model_version TEXT (nullable) and prompt_hash TEXT (nullable) columns for Layer 3 LLM audit trail (per CLAUDE.md: "Log: model_version, prompt_hash, generated_at in briefing row")
- readings rows inserted by ingestion.py for clinic BP readings from FHIR Observations use session="ad_hoc" and source="clinic" to distinguish them from home monitoring readings (source="generated" | "manual" | "ble_auto")

---

## Plan Changes
None yet.

---

## API Endpoints Status

| Endpoint | Status | Notes |
|---|---|---|
| POST /api/ingest | NOT STARTED | |
| POST /api/readings | NOT STARTED | |
| GET /api/patients | NOT STARTED | |
| GET /api/briefings/{id} | NOT STARTED | |
| GET /api/alerts | NOT STARTED | |
| POST /api/admin/trigger-scheduler | NOT STARTED | |

---

## Known Issues
- `.env` is currently at `backend/backend/.env` instead of canonical `backend/.env`. setup_db.py handles both paths. Move with: `Move-Item backend\backend\.env backend\.env` before running uvicorn.
- Supabase project may be paused (free tier). Un-pause at supabase.com before running `python scripts/setup_db.py`. Last connection attempt: 2026-04-12, error: DNS resolution failure.
- adapter.py `_age` extension key (`_age`) is non-standard FHIR. ingestion.py must read this same constant from adapter.py (`_PATIENT_AGE_EXT`) rather than hardcoding the string, to keep both files in sync.

---

## Supabase Connection String
Host: db.xxxxxxxxxxxx.supabase.co
Database: postgres
Port: 5432
Team members get the connection string from Kush directly.
Never commit the password.
