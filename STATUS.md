# ARIA v4.3 — Project Status
Last updated: 2026-04-20 by Sahil Khalsa + Krishna

---

## Pipeline Status

```
iEMR JSON → [DONE] FHIR Bundle → [DONE] PostgreSQL tables → [DONE] Synthetic readings → [DONE] Pattern engine → [DONE] Briefing → [DONE] Dashboard shell
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
- backend/app/services/fhir/adapter.py — iEMR JSON → FHIR R4 Bundle; 6 resource types (Patient, Condition, MedicationRequest, Observation, AllergyIntolerance, ServiceRequest); most-recent-wins deduplication keyed by iEMR code for all types except Observation; VITALS_DATETIME used for effectiveDateTime (never ADMIT_DATE); non-standard `_age` extension passes age to ingestion layer; _build_med_history() collects full medication timeline (104 events for patient 1091) deduplicated by (name, date, activity), passed as _aria_med_history metadata on bundle dict
- backend/tests/test_fhir_adapter.py — 36 tests (35 unit + 1 integration); all passing; includes TestBuildMedHistory (7 cases) and test_bundle_contains_aria_med_history_key
- scripts/run_adapter.py — CLI: reads iEMR JSON, writes FHIR Bundle to data/fhir/bundles/<id>_bundle.json, prints per-type resource counts; accepts --patient (required) and --patient-id (optional, defaults to filename stem) for generalizability across patients
- backend/app/services/fhir/validator.py — validate_fhir_bundle() returns list[str], never raises; checks resourceType, Patient presence, Patient.id non-empty
- backend/app/services/fhir/ingestion.py — ingest_fhir_bundle() populates patients, clinical_context, readings, audit_events; idempotent (patients ON CONFLICT DO NOTHING, clinical_context ON CONFLICT DO UPDATE, readings COUNT guard); 65 clinic readings inserted for patient 1091; risk_tier=high set via CHF override (I50.9 in problem codes); audit event always written in finally block; med_history extracted from _aria_med_history and written to clinical_context upsert
- scripts/run_ingestion.py — CLI: reads FHIR Bundle, validates, ingests to PostgreSQL, prints summary; accepts --bundle flag (default: data/fhir/bundles/1091_bundle.json)
- backend/tests/test_ingestion.py — 36 unit tests passing, 1 integration test (@pytest.mark.integration); covers success path, idempotency, CHF tier override, failure audit event, all summary fields
- backend/app/services/worker/processor.py — WorkerProcessor async polling class; polls processing_jobs every 30s; status flow queued→running→succeeded|failed; atomic claim via conditional UPDATE (rowcount guard); finished_at always written; three handlers fully wired: bundle_import (ingest_fhir_bundle), pattern_recompute (all 4 Layer 1 detectors in order then compute_risk_score), briefing_generation (compose_briefing + generate_llm_summary; Layer 3 failure caught and logged, job still succeeds); session_factory injectable for tests
- backend/app/services/worker/scheduler.py — enqueue_briefing_jobs() finds monitoring_active patients with next_appointment::DATE = today and no existing briefing, inserts briefing_generation jobs; idempotent via ON CONFLICT DO NOTHING on idempotency_key ("briefing_generation:{patient_id}:{YYYY-MM-DD}"); mirrors spec Section 7.4 query exactly; callable from POST /api/admin/trigger-scheduler for demo mode
- scripts/run_worker.py — CLI entry point; starts WorkerProcessor + APScheduler cron at 07:30Z daily; graceful Ctrl+C shutdown
- backend/tests/test_worker.py — 23 unit tests passing, 1 integration test (@pytest.mark.integration, deselected in unit-only run); covers processor status transitions, claim guard, error handling, unknown job type, pattern_recompute (all 4 detectors + scorer), briefing_generation (Layer 1+3 success, Layer 3 graceful failure, missing patient_id, bad date in key), scheduler enqueue logic, idempotency key format
- backend/tests/test_ingestion.py — 37 unit tests passing, 1 integration test (@pytest.mark.integration); covers success path, idempotency, CHF tier override, failure audit event, all summary fields, med_history stored in upsert
- backend/app/services/generator/reading_generator.py — Patient A 28-day scenario; 47 readings (14+13+4+6+10); all clinical rules pass; 14 unit tests + 1 integration test; ruff clean
- backend/app/services/generator/confirmation_generator.py — synthetic medication confirmation events for all active medications over 28 days; 1092 scheduled doses for patient 1091; 977 confirmed (89.5%), 115 missed; weekday rate 0.95, weekend rate 0.78 (blended ~90% matches Patient A spec 91%); 20 unit tests passing; ruff clean
- scripts/run_generator.py — updated: independent idempotency checks for readings (source='generated') and confirmations (confidence='simulated'); both data types generated and reported in a single CLI run; prints adherence summary (total/confirmed/missed) when confirmations are inserted

- backend/app/services/briefing/composer.py — compose_briefing() async function; queries DB for 28-day readings, unacknowledged alerts, medication confirmations, clinical context; assembles all 9 deterministic briefing fields (trend_summary, medication_status, adherence_summary, active_problems, overdue_labs, visit_agenda, urgent_flags, risk_score, data_limitations); persists Briefing row + audit_event row; clinical language enforced at code level ("possible adherence concern", "treatment review warranted"); Layer 1 only — no LLM
- backend/app/services/briefing/summarizer.py — generate_llm_summary() async function; loads prompt from prompts/briefing_summary_prompt.md; computes SHA-256 prompt_hash; calls claude-sonnet-4-20250514 for 3-sentence readable summary; writes readable_summary into llm_response JSONB; populates model_version, prompt_hash, generated_at on briefing row for audit; must only run after Layer 1 is verified
- backend/app/services/briefing/__init__.py — exports compose_briefing, generate_llm_summary
- prompts/briefing_summary_prompt.md — Layer 3 system prompt; enforces 3-sentence output, clinical language rules, no medication recommendations
- backend/tests/test_briefing_composer.py — 61 unit tests (all passing); covers all helper functions, all 9 briefing fields, clinical language enforcement, async compose_briefing with mocked session, error handling, summarizer helpers

- backend/app/services/pattern_engine/risk_scorer.py — Layer 2 compute_risk_score(patient_id, session); verifies patient exists, queries 28-day readings, clinical_context, medication_confirmations directly; computes weighted 0.0–100.0 priority score from systolic-vs-baseline, medication inertia, inverted adherence, reading gap, and comorbidity count; handles missing data with neutral/default signals; rounds to 2 decimals and persists patients.risk_score; no audit_event required for this computation
- backend/tests/test_risk_scorer.py — 14 unit tests passing with mocked AsyncSession; covers high/low risk scenarios, no readings, no confirmations, NULL last_med_change, clinic systolic fallback, patient not found, clamping, persistence, rounding/commit behavior, and per-signal weight verification

- backend/app/services/pattern_engine/gap_detector.py — Layer 1 run_gap_detector(session, patient_id) → GapResult; tier-aware thresholds (high: flag≥1d/urgent≥3d, medium: flag≥3d/urgent≥5d, low: flag≥7d/urgent≥14d); returns gap_days, flagged, urgent booleans; no readings → gap_days=None, flagged=False
- backend/app/services/pattern_engine/inertia_detector.py — Layer 1 run_inertia_detector(session, patient_id) → InertiaResult; all 4 conditions required simultaneously: avg systolic≥140, ≥5 elevated readings, elevated span >7 days, no med change on/after first elevated reading; fail-safe: any unmet condition → detected=False
- backend/app/services/pattern_engine/adherence_analyzer.py — Layer 1 run_adherence_analyzer(session, patient_id) → AdherenceResult; 28-day adherence rate from confirmations vs avg systolic; Pattern A (elevated+low adherence)="possible adherence concern", Pattern B (elevated+high adherence)="treatment review warranted", Pattern C (normal+low adherence)="contextual review", none=no concern; clinical language enforced at code level
- backend/app/services/pattern_engine/deterioration_detector.py — Layer 1 run_deterioration_detector(session, patient_id) → DeteriorationResult; dual-signal: least-squares positive slope over 14 days AND recent 3-day avg > days 4–10 baseline avg; both signals required to reduce false positives; <7 readings → detected=False; pure Python slope (no numpy)
- backend/tests/test_pattern_engine.py — 39 unit tests (all passing); covers all 4 Layer 1 detectors and their edge cases (no readings, sparse data, borderline thresholds, all pattern types)
- frontend/src/ — dashboard shell with mock data (Krishna)
  - All 24 components and pages created (PatientList, BriefingCard, SparklineChart, AdherenceSummary, VisitAgenda, AlertInbox, RiskTierBadge, RiskScoreBar, Sidebar, ThemeToggle, Admin page)
  - API stubs in place in src/lib/api.ts — mock data returns match real API response shape exactly
  - npm run build passing with 0 TypeScript errors, 0 lint errors
  - Dark mode, Inter font, teal/sage colour system, clinical language enforced

### IN PROGRESS
- backend/app/api/ (all routes) — Task 7

### NOT STARTED
- backend/app/api/ (patients, readings, briefings, alerts, ingest, admin routes)

---

## Schema Changes Since v4.3 Spec
- briefings table: added model_version TEXT (nullable) and prompt_hash TEXT (nullable) columns for Layer 3 LLM audit trail (per CLAUDE.md: "Log: model_version, prompt_hash, generated_at in briefing row")
- readings rows inserted by ingestion.py for clinic BP readings from FHIR Observations use session="ad_hoc" and source="clinic" to distinguish them from home monitoring readings (source="generated" | "manual" | "ble_auto")
- clinical_context table: added med_history JSONB column (ALTER TABLE … ADD COLUMN IF NOT EXISTS); stores full medication timeline as list[{name, rxnorm, date, activity}] sorted chronologically; 104 entries for patient 1091

---

## Plan Changes
- Layer 2 risk scorer was implemented before Layer 1 detectors. Now that all 4 Layer 1 detectors are complete, risk_scorer.py should be updated to consume Layer 1 outputs (GapResult, InertiaResult, AdherenceResult, DeteriorationResult) instead of running its own direct DB queries. processor.py pattern_recompute handler should call detectors in order before compute_risk_score().
- Frontend built with mock data first. API wiring happens after Sahil completes routes. All mock shapes match expected API response format exactly.

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
- Supabase project may be paused (free tier). Un-pause at supabase.com before running `python scripts/setup_db.py`. Last connection attempt: 2026-04-12, error: DNS resolution failure.
- adapter.py `_age` extension key (`_age`) is non-standard FHIR. ingestion.py must read this same constant from adapter.py (`_PATIENT_AGE_EXT`) rather than hardcoding the string, to keep both files in sync. Current state: both files define `_PATIENT_AGE_EXT = "_age"` independently with matching values — silent divergence risk if adapter.py changes the key. Fix before Task 5: import the constant in ingestion.py rather than redefining it.
- API routes not yet created — frontend uses mock data. Switch to real data: change one line in src/lib/api.ts per function when routes are ready.
- Some iEMR medication entries have null MED_ACTIVITY (e.g. ASPIRIN 81, METOPROLOL in patient 1091's earliest visits). The activity field in med_history will be null for these entries. Acceptable for now — briefing composer should handle null activity gracefully.

---

## Supabase Connection String
Host: db.xxxxxxxxxxxx.supabase.co
Database: postgres
Port: 5432
Team members get the connection string from Kush directly.
Never commit the password.
