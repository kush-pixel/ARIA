# ARIA v4.3 — Project Status
Last updated: 2026-04-13 by Nesh Rochwani

---

## Pipeline Status

```
iEMR JSON → [DONE] FHIR Bundle → [DONE] PostgreSQL tables → [NEXT] Synthetic readings → [ ] Pattern engine → [ ] Briefing → [ ] Dashboard
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
- backend/app/services/fhir/validator.py — validate_fhir_bundle() returns list[str], never raises; checks resourceType, Patient presence, Patient.id non-empty
- backend/app/services/fhir/ingestion.py — ingest_fhir_bundle() populates patients, clinical_context, readings, audit_events; idempotent (patients ON CONFLICT DO NOTHING, clinical_context ON CONFLICT DO UPDATE, readings COUNT guard); 65 clinic readings inserted for patient 1091; risk_tier=high set via CHF override (I50.9 in problem codes); audit event always written in finally block
- scripts/run_ingestion.py — CLI: reads FHIR Bundle, validates, ingests to PostgreSQL, prints summary; accepts --bundle flag (default: data/fhir/bundles/1091_bundle.json)
- backend/tests/test_ingestion.py — 36 unit tests passing, 1 integration test (@pytest.mark.integration); covers success path, idempotency, CHF tier override, failure audit event, all summary fields
- backend/app/services/worker/processor.py — WorkerProcessor async polling class; polls processing_jobs every 30s; status flow queued→running→succeeded|failed; atomic claim via conditional UPDATE (rowcount guard); finished_at always written; three handlers: bundle_import (fully implemented, calls ingest_fhir_bundle), pattern_recompute (stub, Week 2), briefing_generation (stub, Week 3); session_factory injectable for tests
- backend/app/services/worker/scheduler.py — enqueue_briefing_jobs() finds monitoring_active patients with next_appointment::DATE = today and no existing briefing, inserts briefing_generation jobs; idempotent via ON CONFLICT DO NOTHING on idempotency_key ("briefing_generation:{patient_id}:{YYYY-MM-DD}"); mirrors spec Section 7.4 query exactly; callable from POST /api/admin/trigger-scheduler for demo mode
- scripts/run_worker.py — CLI entry point; starts WorkerProcessor + APScheduler cron at 07:30Z daily; graceful Ctrl+C shutdown
- backend/tests/test_worker.py — 19 unit tests passing, 1 integration test (@pytest.mark.integration); covers processor status transitions, claim guard, error handling, unknown job type, both handler stubs, scheduler enqueue logic, idempotency key format

### IN PROGRESS
- backend/app/services/generator/reading_generator.py — Task 4: synthetic 28-day home BP readings for Patient A scenario (starting)

### NOT STARTED
- backend/app/services/generator/ (reading_generator, confirmation_generator)
- backend/app/services/pattern_engine/ (all detectors + risk_scorer)
- backend/app/services/briefing/ (composer, summarizer)
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
- adapter.py `_age` extension key (`_age`) is non-standard FHIR. ingestion.py must read this same constant from adapter.py (`_PATIENT_AGE_EXT`) rather than hardcoding the string, to keep both files in sync. Current state: both files define `_PATIENT_AGE_EXT = "_age"` independently with matching values — silent divergence risk if adapter.py changes the key. Fix before Task 5: import the constant in ingestion.py rather than redefining it.

---

## Supabase Connection String
Host: db.xxxxxxxxxxxx.supabase.co
Database: postgres
Port: 5432
Team members get the connection string from Kush directly.
Never commit the password.
