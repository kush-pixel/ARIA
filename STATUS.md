# ARIA v4.3 — Project Status
Last updated: 2026-04-27 by Kush (Phase 1 ingestion data fixes + Phase 8 security infrastructure — AUDIT.md Fixes 6, 7, 8, 9, 12, 16, 35, 36, 37, 38)
Previous: 2026-04-27 by Sahil (Phase 5 complete + Phase 7 complete except Fix 43; 426 unit tests passing, ruff clean)
Previous: 2026-04-26 by Yash (AUDIT.md Fixes 25, 58, 61 implemented — severity-weighted comorbidity score, adaptive gap/inertia normalization, risk_score_computed_at staleness indicator)

---

## Phase 0 — Standalone Correctness Fixes — 2026-04-27

**Author:** Yash Sharma
**Files changed:** `backend/app/services/worker/processor.py`, `backend/tests/test_worker.py`, `scripts/run_scheduler.py` (new)
**Tests:** 388 → 392 unit tests, all passing. `ruff check app/` — all checks passed.

### Fix 11 — Adherence alert row never written in processor
`_handle_pattern_recompute()` wrote alert rows for gap, inertia, and deterioration but silently dropped the adherence result. Pattern A (`adherence["pattern"] == "A"` — high BP + low adherence) never produced a row in the `alerts` table, so the adherence alert type was absent from the clinician inbox even when the detector fired. Fixed by adding `await _upsert_alert(session, pid, "adherence")` when `adherence["pattern"] == "A"`, consistent with how the other three detector results are handled.

New test: `test_handle_pattern_recompute_writes_adherence_alert_for_pattern_a` — verifies an Alert with `alert_type="adherence"` is added when the adherence analyzer returns pattern A.

### Fix 30 — `delivered_at` never set on alert insert
`_upsert_alert()` in `processor.py` never set `delivered_at` — the API serializer always returned `null` for this field, making every alert appear undelivered. Fixed by capturing `datetime.now(UTC)` once into `now` and setting both `triggered_at=now` and `delivered_at=now` on the `Alert` object at insert time. The alert is "delivered" at creation — the moment the system has written the finding for the clinician to see.

New tests: `test_upsert_alert_sets_delivered_at_on_insert` (verifies `delivered_at` is non-null and within 1 second of `triggered_at`), `test_upsert_alert_skips_insert_when_alert_already_exists` (deduplication still works).

### Fix 20 — Briefing appointment date parsed from idempotency key
`_handle_briefing_generation()` parsed the appointment date from the last 10 characters of the idempotency key (`job.idempotency_key[-10:]`). If the admin trigger fired on a day other than the patient's actual appointment, `briefings.appointment_date` recorded the trigger date, not the true appointment date.

Fixed by replacing the string parse with a DB query: `select(Patient).where(Patient.patient_id == job.patient_id)`, then using `patient.next_appointment.date()` as the appointment date. Falls back to `date.today()` when `next_appointment` is `None` or the patient row is absent, preserving demo-mode behaviour. `Patient` model is now imported at module level in `processor.py`.

Updated tests: `test_handle_briefing_generation_calls_composer_and_summarizer` and `test_handle_briefing_generation_layer3_failure_does_not_fail_job` both mock the Patient query. Replaced `test_handle_briefing_generation_raises_on_bad_date_in_key` (obsolete) with `test_handle_briefing_generation_falls_back_to_today_when_no_appointment` and `test_handle_briefing_generation_falls_back_to_today_when_patient_not_found`.

### scripts/run_scheduler.py — standalone manual scheduler trigger
The last remaining NOT STARTED item from the project scaffold. Provides a CLI equivalent to `POST /api/admin/trigger-scheduler` that can be invoked directly from the terminal without the API server running. `main()` parses an optional `--date YYYY-MM-DD` argument (defaults to today UTC). `_run()` calls `enqueue_briefing_jobs(target_date=target_date)` and prints the count of enqueued jobs. Invalid `--date` format exits with code 1.

Usage:
```
conda activate aria
python scripts/run_scheduler.py                    # target today UTC
python scripts/run_scheduler.py --date 2026-05-01  # target specific date
```

---

## Phase 1 + Phase 8 — Ingestion Data Fixes + Security Infrastructure — 2026-04-27

**Author:** Kush Patel
**Files changed:** `backend/app/services/fhir/adapter.py`, `backend/app/services/fhir/ingestion.py`, `backend/app/models/clinical_context.py`, `scripts/setup_db.py`, `backend/app/limiter.py` (new), `backend/app/config.py`, `backend/app/main.py`, `backend/app/api/readings.py`, `backend/app/api/ingest.py`, `backend/app/api/alerts.py`, `backend/requirements.txt`, `backend/tests/test_fhir_adapter.py`, `backend/tests/test_ingestion.py`
**Tests:** 35 new adapter tests + 18 new ingestion tests, all passing. `ruff check app/` — all checks passed.

### Fix 6 — Vital signs other than BP silently dropped
PULSE, WEIGHT, PULSEOXYGEN, and TEMPERATURE were present in iEMR VITALS but never emitted as Observations. `_build_simple_observation()` added for scalar-value vitals; each visit now emits up to 4 additional Observations (LOINC 8867-4/29463-7/59408-5/8310-5). Weight converted lb → kg (× 0.453592). SpO2 84% for CHF patient (Nov 2011) is now captured. Observation count: 65 → 190 for patient 1091. `ingestion.py` routes by LOINC code: `_get_obs_loinc()` dispatches to `last_clinic_pulse`, `last_clinic_weight_kg`, `last_clinic_spo2`, and `historic_spo2[]` in `clinical_context`.

### Fix 7 — Physician assessment texts discarded
`_build_problem_assessments()` collects per-visit `PROBLEM_ASSESSMENT_TEXT`, `PROBLEM_STATUS2_FLAG`, and `PROBLEM_STATUS2` across all visits. Passed as `_aria_problem_assessments` → `clinical_context.problem_assessments` JSONB (221 entries for patient 1091). Required by Phase 2 comorbidity-adjusted threshold (replaces degraded-mode presence check with actual physician labels).

### Fix 8 — Social history never populated
`_build_social_context()` joins `SOCIAL_HX_DESCRIPTION + SOCIAL_HX_COMMENT` from the most recent visit with SOCIAL_HX entries. Passed as `_aria_social_context` → `clinical_context.social_context`.

### Fix 9 — Inactive allergies included; reactions not captured
`_build_allergy_intolerances()` now filters `ALLERGY_STATUS != "Active"`. Reaction text from `ALLERGY_DETAIL[0].ALLERGY_REACTION` stored in `reaction[0].manifestation[0].text` → `clinical_context.allergy_reactions[]` parallel to `allergies[]`.

### Fix 12 — `last_visit_date` only reflected BP clinic dates
`_build_visit_dates()` collects all 124 ADMIT_DATE values as ISO date strings. `last_visit_date` set from `max(_aria_visit_dates)` — was previously missing 71 non-vitals visits. Corrected to 2015-11-24 for patient 1091.

### Fix 16 — Lab values skeleton
`recent_labs` JSONB column added to `clinical_context`. `ingestion.py` reads `_aria_recent_labs` if present; NULL for patient 1091 (iEMR has no structured lab LOINC observations). Infrastructure ready for future structured lab data.

### Fix 35 — HMAC pseudonymization prep
`_pseudonymize_patient_id(med_rec_no, key)` returns a 16-char HMAC-SHA256 hex prefix. Wired into `convert_iemr_to_fhir(pseudonym_key=...)`. Inactive until `PATIENT_PSEUDONYM_KEY` set in `.env` + DB reset + re-ingestion.

### Fix 36 — JWT expiry config field
`jwt_expiry_minutes: int = 60` added to `Settings` in `config.py` (documents JWT max lifetime requirement).

### Fix 37 — API rate limiting (slowapi)
`backend/app/limiter.py` (new) — shared slowapi `Limiter` instance. Applied: `readings.py` (60/min), `ingest.py` (5/min), `alerts.py` (30/min). Wired in `main.py` via `app.state.limiter` + `RateLimitExceeded` exception handler.

### Fix 38 — DB-level audit trigger on readings
`trg_readings_audit` AFTER INSERT trigger on `readings` table writes `audit_events` row on every insert (safety net for direct DB writes bypassing the API). Three separate asyncpg-compatible `execute()` calls in `setup_db.py` (`CREATE FUNCTION`, `DROP TRIGGER IF EXISTS`, `CREATE TRIGGER`).

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
- backend/app/config.py — Pydantic v2 Settings, 9 fields (DATABASE_URL, ANTHROPIC_API_KEY, APP_SECRET_KEY, APP_ENV, APP_DEBUG, DEMO_MODE, BRIEFING_TRIGGER, PATIENT_PSEUDONYM_KEY, JWT_EXPIRY_MINUTES)
- backend/app/db/base.py — async engine (auto-converts postgresql:// → postgresql+asyncpg://), AsyncSessionLocal factory, DeclarativeBase
- backend/app/db/session.py — get_session() FastAPI dependency
- backend/app/models/ — all 12 ORM models (patients, clinical_context, readings, medication_confirmations, alerts, alert_feedback, briefings, processing_jobs, audit_events, gap_explanations, calibration_rules, outcome_verifications); patients model includes risk_score_computed_at TIMESTAMPTZ (Fix 61); alerts model includes off_hours + escalated BOOLEAN (Fix 45); clinical_context model includes 7 Phase 1 columns (problem_assessments, allergy_reactions, last_clinic_pulse, last_clinic_weight_kg, last_clinic_spo2, historic_spo2, recent_labs)
- scripts/setup_db.py — creates 12 tables + 26 indexes/migrations + 1 DB audit trigger; safe to re-run (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS); includes all Phase 1, Phase 5, Phase 7, and Phase 8 migrations; trg_readings_audit installed on readings table (Fix 38)
- backend/app/utils/logging_utils.py — get_logger(name) returns a named stdlib Logger with ISO timestamp format; used by all backend modules
- backend/app/limiter.py — NEW (Phase 8 Fix 37): shared slowapi Limiter instance (key_func=get_remote_address); imported by main.py and the 3 rate-limited API route files to avoid circular imports
- backend/app/services/fhir/adapter.py — iEMR JSON → FHIR R4 Bundle; Phase 1 complete (2026-04-27): _build_simple_observation() emits PULSE (8867-4) / WEIGHT (29463-7, lb→kg) / PULSEOXYGEN (59408-5) / TEMP (8310-5) per vitals row; _build_allergy_intolerances() filters ALLERGY_STATUS != "Active" and captures ALLERGY_REACTION → reaction[0].manifestation[0].text; _build_problem_assessments() collects per-visit physician assessment texts → _aria_problem_assessments; _build_visit_dates() collects all 124 ADMIT_DATE values → _aria_visit_dates; _build_social_context() joins SOCIAL_HX entries → _aria_social_context; _pseudonymize_patient_id() HMAC-SHA256 helper added (Fix 35, inactive). Observation count: 65 → 190 for patient 1091. AllergyIntolerance: 6 Active-only (inactive filtered). Earlier data quality fixes: Z00.xx codes filtered, discontinued medications excluded via sentinel tombstone, supplies/tests/device scripts filtered from MedicationRequests, non-clinical PLAN items filtered from ServiceRequests.
- backend/tests/test_fhir_adapter.py — 77 tests (76 unit + 1 integration); all passing; includes TestBuildProblemAssessments, TestBuildVisitDates, TestBuildSocialContext, vitals observation tests, allergy filter/reaction tests, and earlier TestBuildMedHistory + data quality fix tests
- scripts/run_adapter.py — CLI: reads iEMR JSON, writes FHIR Bundle to data/fhir/bundles/<id>_bundle.json, prints per-type resource counts
- backend/app/services/fhir/validator.py — validate_fhir_bundle() returns list[str], never raises; checks resourceType, Patient presence, Patient.id non-empty
- backend/app/services/fhir/ingestion.py — Phase 1 complete (2026-04-27): _get_obs_loinc() + _get_obs_scalar_value() helpers; first observation pass routes by LOINC (55284-4→BP arrays, 8867-4→last_clinic_pulse, 29463-7→last_clinic_weight_kg, 59408-5→last_clinic_spo2+historic_spo2); Fix 12: last_visit_date from max(_aria_visit_dates); Fix 7: problem_assessments from _aria_problem_assessments; Fix 8: social_context from _aria_social_context; Fix 9: allergy_reactions[] parallel to allergies[]; Fix 16: recent_labs skeleton; readings INSERT pre-filtered to BP-panel-only; ingest_fhir_bundle() populates patients, clinical_context, readings, audit_events; idempotent; 65 clinic readings for patient 1091; risk_tier=high set via CHF override; audit event always written in finally block
- scripts/run_ingestion.py — CLI: reads FHIR Bundle, validates, ingests to PostgreSQL, prints summary
- backend/tests/test_ingestion.py — 55 unit tests passing, 1 integration test; covers Phase 1 new fields + success path, idempotency, CHF tier override, failure audit event
- backend/app/services/worker/processor.py — WorkerProcessor async polling class; polls processing_jobs every 30s; status flow queued→running→succeeded|failed; atomic claim via conditional UPDATE (rowcount guard); finished_at always written; three handlers fully wired: bundle_import, pattern_recompute (all 4 Layer 1 detectors + compute_risk_score + adherence alert for pattern A — Fix 11), briefing_generation (compose_briefing + generate_llm_summary; Layer 3 failure caught, job still succeeds; appointment date from patient.next_appointment — Fix 20; delivered_at set on insert — Fix 30)
- backend/app/services/worker/scheduler.py — enqueue_briefing_jobs() finds monitoring_active patients with next_appointment::DATE = today and no existing briefing; idempotent via ON CONFLICT DO NOTHING
- scripts/run_worker.py — CLI entry point; starts WorkerProcessor + APScheduler cron at 07:30Z daily; graceful Ctrl+C shutdown
- backend/tests/test_worker.py — 27 unit tests passing; covers all processor status transitions, handlers, Fix 11/20/30

- backend/app/services/briefing/composer.py — compose_briefing() async; all 9 deterministic briefing fields; clinical language enforced at code level
- backend/app/services/briefing/summarizer.py — generate_llm_summary(); retry loop (max 2 attempts): validate_llm_output() after each; stores readable_summary or None; model_version, prompt_hash, generated_at written to briefing row
- backend/app/services/briefing/llm_validator.py — Layer 3 output validation and guardrails; 14 checks (Group A: PHI leak, prompt injection; Group B: 10 guardrails; Group C: 11 faithfulness checks); validate_llm_output() returns on first failure; always writes audit_events row. check_problem_assessments() updated 2026-04-25: synonym map (_CONDITION_SYNONYMS, 15 entries) prevents false positives; scans all conditions regardless of active_problems list size
- backend/tests/test_llm_validator.py — 57 unit tests (all passing)

- backend/app/services/pattern_engine/risk_scorer.py — Layer 2 compute_risk_score(patient_id, session); severity-weighted comorbidity (Fix 25); adaptive gap normalization (Fix 58); inertia saturates at 180d (Fix 58); persists risk_score AND risk_score_computed_at=now() (Fix 61)
- backend/tests/test_risk_scorer.py — 14 unit tests; updated for Fix 25/58/61

- backend/app/services/pattern_engine/threshold_utils.py — compute_slope(), compute_patient_threshold(), classify_comorbidity_concern(), apply_comorbidity_adjustment() (Fix 5: CHF-alone triggers adjustment), get_last_med_change_date(), _TITRATION_WINDOWS, get_titration_window() (Fix 3/26)
- backend/app/services/pattern_engine/gap_detector.py, inertia_detector.py, adherence_analyzer.py, deterioration_detector.py — all 4 Layer 1 detectors complete; adaptive threshold, comorbidity adjustment, med_history traversal, white-coat exclusion
- backend/tests/test_pattern_engine.py — 64 unit tests (all passing)

- frontend/src/ — full dashboard wired to real backend (Krishna)
- backend/app/api/patients.py — GET /api/patients (sorted tier→score DESC), GET /api/patients/{id}; _serialise() includes risk_score_computed_at (Fix 61)
- backend/app/api/briefings.py — GET /api/briefings/{patient_id}; marks read_at; writes audit event
- backend/app/api/alerts.py — GET /api/alerts[?patient_id=] (Fix 24), POST /api/alerts/{id}/acknowledge with optional AcknowledgeRequest (Fix 42 L1); @limiter.limit("30/minute") (Fix 37)
- backend/app/api/readings.py — GET /api/readings?patient_id=, POST /api/readings; @limiter.limit("60/minute") (Fix 37)
- backend/app/api/ingest.py — POST /api/ingest; @limiter.limit("5/minute") (Fix 37)
- backend/app/api/admin.py — POST /api/admin/trigger-scheduler (DEMO_MODE guard)
- backend/app/api/adherence.py — GET /api/adherence/{patient_id}
- backend/tests/test_api.py — 24 unit tests (all passing)
- backend/tests/test_phase5_phase7.py — 34 unit tests (all passing); covers Fix 41, 44, 45, 42 L2, 42 L3

- backend/app/services/generator/reading_generator.py — full-timeline home BP generation (Fix 15); parametric baseline from median(historic_bp_systolic); device outage + white-coat dip
- backend/app/services/generator/confirmation_generator.py — full-timeline confirmations (Fix 15); _active_meds_at() parses med_history; Beta(6.5, 0.65) per-interval adherence; ON CONFLICT DO NOTHING
- scripts/run_generator.py — --mode full-timeline calls both generators

- Item 6, 7, 8, 9, 12 COMPLETE (Phase 1 — Kush, 2026-04-27): re-ingestion run completed; clinical_context verified: last_visit_date=2015-11-24, problem_assessments=221 entries, last_clinic_pulse=71 bpm, last_clinic_weight_kg=76.2 kg, last_clinic_spo2=93.0% (7 historic), allergy_reactions=['?','?','?','Generalized Bad Feeling...','MYALGIAS','LEG CRAMPS'], social_context=CONSULTANTS list
- Items 35, 36, 37, 38 COMPLETE (Phase 8 — Kush, 2026-04-27): slowapi live on 3 endpoints; HMAC function wired (inactive); jwt_expiry_minutes added; DB audit trigger installed

### IN PROGRESS
- None

### NOT STARTED
- None

---

## Schema Changes Since v4.3 Spec
- briefings table: added model_version TEXT (nullable) and prompt_hash TEXT (nullable) columns for Layer 3 LLM audit trail
- readings rows inserted by ingestion.py use session="ad_hoc" and source="clinic"
- clinical_context table: added med_history JSONB column (ALTER TABLE … ADD COLUMN IF NOT EXISTS); 104 entries for patient 1091
- clinical_context table (Phase 1): 7 new columns — problem_assessments JSONB, allergy_reactions TEXT[], last_clinic_pulse SMALLINT, last_clinic_weight_kg NUMERIC(5,1), last_clinic_spo2 NUMERIC(4,1), historic_spo2 NUMERIC[], recent_labs JSONB
- patients table: added risk_score_computed_at TIMESTAMPTZ (Fix 61); set on every risk_score update
- new table alert_feedback (Phase 5 Fix 42 L1): feedback_id UUID PK, alert_id UUID FK → alerts, patient_id, detector_type, disposition, reason_text, clinician_id, created_at; two indexes
- DB trigger (Phase 8 Fix 38): trg_readings_audit AFTER INSERT on readings — safety-net audit record; reads aria.actor_id and aria.request_id SET LOCAL session vars

---

## Plan Changes
- Layer 2 risk scorer was implemented before Layer 1 detectors. Now that all 4 Layer 1 detectors are complete, risk_scorer.py should be updated to consume Layer 1 outputs instead of running its own direct DB queries.
- Frontend built with mock data first (Krishna), then wired to real backend (Sahil). api.ts now hits real endpoints — mock data no longer used at runtime.

---

## API Endpoints Status

| Endpoint | Status | Notes |
|---|---|---|
| GET /api/patients | DONE | |
| GET /api/patients/{id} | DONE | |
| GET /api/briefings/{patient_id} | DONE | marks read_at, writes audit |
| GET /api/readings?patient_id= | DONE | 28-day window |
| POST /api/readings | DONE | manual entry; rate limited 60/min (Fix 37) |
| GET /api/alerts | DONE | unacknowledged only; optional ?patient_id= filter (Fix 24); rate limited 30/min (Fix 37) |
| POST /api/alerts/{id}/acknowledge | DONE | writes audit; optional disposition payload writes alert_feedback (Fix 42 L1); disagree → schedules outcome verification |
| GET /api/adherence/{patient_id} | DONE | per-medication breakdown |
| POST /api/ingest | DONE | validates then ingests; rate limited 5/min (Fix 37) |
| POST /api/admin/trigger-scheduler | DONE | DEMO_MODE guard |
| GET /api/shadow-mode/results | DONE | serves pre-computed JSON results file |
| POST /api/ble-webhook | DONE | BLE device readings, source=ble_auto, idempotent (Fix 44) |
| GET /api/gap-explanations | DONE | list explanations for patient (Fix 41) |
| POST /api/gap-explanations | DONE | record gap explanation (Fix 41) |
| DELETE /api/gap-explanations/{id} | DONE | remove explanation (Fix 41) |
| GET /api/admin/calibration-recommendations | DONE | 4+ dismissal pairs (Fix 42 L2) |
| POST /api/admin/calibration-rules | DONE | approve calibration rule (Fix 42 L2) |
| GET /api/admin/outcome-verifications | DONE | due retrospective prompts (Fix 42 L3) |
| POST /api/admin/outcome-verifications/{id}/respond | DONE | clinician response (Fix 42 L3) |
| GET /health | DONE | |

---

## Known Issues
- Supabase project may be paused (free tier). Un-pause at supabase.com before running `python scripts/setup_db.py`.
- adapter.py `_age` extension key (`_age`) is non-standard FHIR. Both files define `_PATIENT_AGE_EXT = "_age"` independently — silent divergence risk if adapter.py changes the key.
- ANTHROPIC_API_KEY in backend/.env is placeholder — Layer 3 LLM summary will be skipped until real key is set.
- Some iEMR medication entries have null MED_ACTIVITY. The activity field in med_history will be null for these entries.
- risk_score for patient 1091 (69.48) was computed before the medication list was corrected. Should be recomputed via a pattern_recompute job.

---

## Shadow Mode Development — 2026-04-22

### Overview
Shadow mode replays all 124 iEMR clinic visits in chronological order and asks: if ARIA had been running during this patient's care, would it have fired (or not fired) at the same moments the physician was concerned?

**Final result: 94.3% agreement (33/35 labelled), PASSED ✓** (target was ≥80%)
`random.seed(1)` is set in the script for reproducible results.

### What the script does (`scripts/run_shadow_mode.py`)

**Step 1 — Clinic BP extraction (53 visits)**
Loads all clinic BP readings from the Supabase DB (`source='clinic'`). Deduplicates by date keeping the chronologically last reading per day.

**Step 2 — Synthetic home reading generation (28-day windows)**
For each BP visit, generates a 28-day window of synthetic home readings using the same `ReadingGenerator` rules as production.

**Step 3 — Patient-adaptive threshold**
Computes a personal BP threshold from prior clinic visits only: `max(130, stable_baseline_mean + 1.5×SD)` capped at 145 mmHg. Falls back to 140 mmHg if fewer than 3 stable readings exist.

**Step 4 — Continuous monitoring (between-visit alerts)**
Scans every 7 days through each inter-visit gap using a rolling 28-day synthetic window.

**Step 5 — Four Layer 1 detectors (shadow versions)**
All four detectors re-implemented locally (no DB calls) operating on in-memory synthetic window.

**Step 6 — Scoring and agreement**
`aria_fired = gap_urgent OR inertia OR deterioration OR adherence`.

**Step 7 — Best demo window identification**
Finds the contiguous run of visits with the highest density of `agree` results.

**Step 8 — Output**
Results written to `data/shadow_mode_results.json`.

### Result breakdown (patient 1091)
```
Total evaluation points:  53
Skipped (< 4 readings):    1
Clinic BP points:          52
With ground truth:         35
  Concerned (flag 1/2):    27
  Stable (flag 3):          8
No ground truth:           17 (excluded from scoring)

Agreements:   33
False negatives: 0
False positives: 2  (ARIA fired, physician stable) — documented in AUDIT.md Fix 1 and Fix 3
Agreement rate: 33/35 = 94.3%  PASSED ✓

Between-visit alerts generated: 9 (across all inter-visit gaps)
```

---

## Generator — Fix 15 Confirmations Half + Fix 15 Reading Gaps — 2026-04-26

**Author:** Kush Patel
**Files changed:** `confirmation_generator.py`, `reading_generator.py`, `run_generator.py`, `tests/test_confirmation_generator.py`, `tests/test_reading_generator.py`
**Tests:** 384 unit tests total, all passing. `ruff check app/` — all checks passed.

### Fix 15 (confirmations) — generate_full_timeline_confirmations()
`_active_meds_at(med_history, cutoff_date)` — parses `med_history` JSONB list to determine which medications were active at the start of each inter-visit interval. `generate_full_timeline_confirmations()` — Beta(α=6.5, β=0.65) per-interval adherence (mean≈91%, SD≈10%), weekend discount (×0.821), ON CONFLICT DO NOTHING on `(patient_id, medication_name, scheduled_time)`.

### Fix 15 (readings) — device outage + white-coat dip
`_build_outage_days()` — 1-2 outage episodes of 2-4 consecutive absent days per interval. `_white_coat_dip_amount()` — linear ramp 10-15 mmHg in 3-5 days before visit.

---

## Pattern Engine — Hardcode Audit + Threshold Consistency — 2026-04-26

**Author:** Kush Patel
**Files changed:** `threshold_utils.py`, `adherence_analyzer.py`, `tests/test_pattern_engine.py`
**Tests:** 58 → 64 unit tests, all passing.

### Fix 5 — Comorbidity adjustment now covers severe-single comorbidity
`apply_comorbidity_adjustment()` condition simplified from `if cardio and metabolic` to `if cardio`. Patient 1091 (CHF only) now correctly receives threshold of 133 mmHg.

### Fix 3/26 — TITRATION_WINDOWS drug-class-aware Pattern B gate
`_TITRATION_WINDOWS` dict, `_infer_drug_class()`, and `get_titration_window()` added. Diuretics/beta-blockers → 14d, ACE/ARBs → 28d, amlodipine → 56d, default → 42d.

### Adherence analyzer uses patient-adaptive threshold
Removed hardcoded `_HIGH_BP_SYSTOLIC_THRESHOLD = 140`. ClinicalContext query moved to top of `run_adherence_analyzer()`.

---

## Pattern Engine — v5.0 Spec Implementation — 2026-04-26

**Author:** Prakriti Sharma
**Files changed:** `threshold_utils.py` (new), `inertia_detector.py`, `adherence_analyzer.py`, `deterioration_detector.py`, `tests/test_pattern_engine.py`
**Tests:** 39 → 58 unit tests, all passing. `ruff check app/services/pattern_engine/` — all checks passed.

### threshold_utils.py (new file)
- `compute_slope()` — pure-Python least-squares
- `compute_patient_threshold(historic_bp_systolic)` — `max(130, mean + 1.5×SD)` capped at 145; fallback 140
- `classify_comorbidity_concern(problem_codes)` — ICD-10 prefix matching for cardio (I50/I63/G45) and metabolic (E11/N18)
- `apply_comorbidity_adjustment(threshold, concern_state)` — −7 mmHg adjustment (floor 130)
- `get_last_med_change_date(med_history, last_med_change_fallback)` — traverses med_history JSONB

### inertia_detector.py, adherence_analyzer.py, deterioration_detector.py
All updated for patient-adaptive threshold, comorbidity adjustment, med_history traversal, slope direction check, step-change sub-detector.

---

## Phase 7 — New Clinical Features — 2026-04-27

**Author:** Sahil Khalsa
**Files changed (new):** `backend/app/api/ble_webhook.py`, `backend/app/api/calibration.py`, `backend/app/api/gap_explanations.py`, `backend/app/models/alert_feedback.py`, `backend/app/models/calibration_rule.py`, `backend/app/models/gap_explanation.py`, `backend/app/models/outcome_verification.py`, `backend/app/services/feedback/__init__.py`, `backend/app/services/feedback/calibration_engine.py`, `backend/app/services/feedback/outcome_tracker.py`, `backend/tests/test_phase5_phase7.py`
**Tests:** 392 → 426 unit tests (34 new), all passing. `ruff check app/` — all checks passed.

### Fix 44 — BLE webhook
`POST /api/ble-webhook` accepts vendor-normalised BLE BP readings. Inserts with `source='ble_auto'`, ON CONFLICT DO NOTHING. Writes `reading_ingested` audit event only on insert.

### Fix 45 — Off-hours tagging + escalation sweep
`_is_off_hours(dt)` tags alerts generated between 6 PM–8 AM UTC or on weekends. `_run_escalation_sweep()` sets `escalated=True` on gap_urgent/deterioration alerts unacknowledged for 24h.

### Fix 42 L2 — Calibration recommendations
`calibration_engine.py`: surfaces patient/detector pairs with 4+ dismissals and no active rule. Admin API: GET/POST `/api/admin/calibration-recommendations` and `/api/admin/calibration-rules`.

### Fix 42 L3 — Outcome verification (30-day retrospective)
`outcome_tracker.py`: `schedule_outcome_check()` creates OutcomeVerification at dismiss time with check_after=+30d. Admin API: GET/POST `/api/admin/outcome-verifications`.

### Fix 41 — Gap explanations
`GET/POST/DELETE /api/gap-explanations`. New `gap_explanations` table.

---

## Phase 5 — API and Alert Improvements — 2026-04-26

**Author:** Sahil Khalsa
**Files changed:** `backend/app/api/alerts.py`, `backend/app/models/alert_feedback.py` (new), `backend/app/models/__init__.py`, `scripts/setup_db.py`, `scripts/run_shadow_mode.py`, `scripts/run_pipeline_tests.py`, `backend/tests/test_api.py`
**Tests:** 384 → 392 unit tests, all passing. `ruff check app/` — all checks passed.

### Fix 24 — Alert API patient_id filter
`GET /api/alerts` now accepts optional `?patient_id=` query parameter.

### Fix 42 L1 — Alert disposition feedback
`POST /api/alerts/{alert_id}/acknowledge` accepts optional `AcknowledgeRequest` body (`disposition`, `reason_text`, `clinician_id`). `AlertFeedback` row inserted when disposition provided. Response includes `feedback_recorded: bool`.

### Fix 13 — Shadow mode CLI arguments
Replaced hardcoded `PATIENT_ID`, `IEMR_PATH`, `OUTPUT_PATH` constants with `argparse` (`--patient`, `--iemr`, `--output`).

### Fix 33 — Shadow mode statistics — Wilson CI + window overlap + per-detector breakdown
Added `agreement_ci_95_lower_pct`, `agreement_ci_95_upper_pct`, `fully_independent_eval_points`, `overlapping_eval_points`, and `detector_breakdown` to JSON output.

### Fix 14 — Multi-patient pipeline runner
`scripts/run_pipeline_tests.py` extended to accept `--patients` comma-separated list.

---

## AUDIT.md Fixes Implemented — 2026-04-26

### Fix 25 — Comorbidity risk score saturated at 5 problems
Replaced `_comorbidity_count()` with `_comorbidity_severity_score()`. Weights: CHF/Stroke/TIA=25pts, Diabetes/CKD/CAD=15pts, other=5pts. Total clamped to 100.

### Fix 58 — sig_gap saturated at 14 days; sig_inertia saturated at 90 days
Added `_compute_window_days(patient, context)` using adaptive window formula. `sig_gap` normalises against `window_days`. `sig_inertia` divisor changed from 90.0 → 180.0.

### Fix 61 — No staleness indicator on patients.risk_score
Added `risk_score_computed_at TIMESTAMPTZ` column to Patient ORM model and DB. `compute_risk_score()` sets both `risk_score` and `risk_score_computed_at = now`. Frontend shows amber "Score stale" badge when > 26 hours old.

---

## Bugs Found and Fixed — 2026-04-25

### Bug 16 — check_problem_assessments() allowed condition hallucinations when active_problems was non-empty
`check_problem_assessments()` exited immediately with `passed=True` whenever `active_problems` was truthy. Fixed by removing the early-exit and adding `_CONDITION_SYNONYMS` dict (15 entries). Now scans all condition names regardless of list size.

---

## Bugs Found and Fixed — 2026-04-22

### Bug 7 — Worker reads wrong TypedDict keys from detector results
`_handle_pattern_recompute` read `gap["flagged"]`, `gap["urgent"]`, `inertia["detected"]` — none exist in actual TypedDict definitions. Fixed key references: `gap["status"]`, `inertia["inertia_detected"]`, etc.

### Bug 14 — Frontend types declared risk_score as non-nullable number
`Patient.risk_score` and `BriefingPayload.risk_score` typed as `number` in `types.ts`. Changed to `number | null`.

### Bug 15 — PatientList used getMockReadings() and non-null-safe risk_score sort
Removed `getMockReadings` import and mock-data columns. Fixed sort to `(b.risk_score ?? 0) - (a.risk_score ?? 0)`.

### Bug 13 — Pattern B interpretation missing clinical hedging language
Pattern B used `"treatment review warranted"` — assertive language violating ARIA clinical boundary. Changed to `"possible treatment-review case — elevated BP with high adherence signal"`.

### Bug 12 — Synthetic disclosure absent from data_limitations
`data_limitations` never disclosed that home BP readings are synthetic. Added mandatory synthetic disclosure to all monitoring-active return paths.

### Bug 11 — CHF not prioritised first in active_problems
Added `_PROBLEM_PRIORITY` ICD-10 prefix map and `_sort_problems()` helper in composer.py.

### Bug 10 — Raw day counts in medication_status and visit_agenda
Added `_human_duration(days)` helper that converts to natural language ("about 12 years ago").

### Bug 9 — 8 ruff violations across backend/app/
Fixed B904, I001, UP035, UP017 violations.

### Bug 8 — Alert rows never written during pattern_recompute
Added `_upsert_alert()` helper and alert insertion block in processor.py.

---

## Bugs Found and Fixed — 2026-04-21

Clinical data quality audit on live briefing for patient 1091.

### Bug 1 — "PREVENTIVE CARE" in active_problems
Added `if icd10 and icd10.startswith("Z00"): continue` in `_build_conditions()`.

### Bug 2 — Non-clinical items in overdue_labs
Added filter in `_build_service_requests()` for physician names, redacted entities, patient education. Follow-up items reduced from 10 → 6.

### Bug 3 — Discontinued medications appearing as current regimen
Changed to sentinel/tombstone pattern: `seen[key] = None` on Discontinue. Medications reduced from 38 → 14.

### Bug 4 — Discontinued medications surviving under different MED_CODEs (BYETTA case)
Added `discontinued_names` set populated on Discontinue; applied in secondary name-based deduplication.

### Bug 5 — Supplies, diagnostic tests, and device scripts in current_medications
Added `_NON_DRUG_MARKERS` and `_NON_DRUG_EXACT` constants. All 14 remaining medications are actual pharmaceuticals.

### Bug 6 — "Order overdue lab:" prefix used for non-lab follow-up items
Changed prefix to "Pending follow-up:" in `_build_visit_agenda()` in composer.py.

---

## Supabase Connection String
Host: db.xxxxxxxxxxxx.supabase.co
Database: postgres
Port: 5432
Team members get the connection string from Kush directly.
Never commit the password.
