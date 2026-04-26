# ARIA v4.3 — Project Status
Last updated: 2026-04-26 by Yash (AUDIT.md Fixes 25, 58, 61 implemented — severity-weighted comorbidity score, adaptive gap/inertia normalization, risk_score_computed_at staleness indicator)

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
- backend/app/models/ — all 8 ORM models (patients, clinical_context, readings, medication_confirmations, alerts, briefings, processing_jobs, audit_events); patients model now includes risk_score_computed_at TIMESTAMPTZ column (Fix 61)
- scripts/setup_db.py — creates 8 tables + 13 indexes/migrations; safe to re-run (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS); includes risk_score_computed_at migration (Fix 61)
- backend/app/utils/logging_utils.py — get_logger(name) returns a named stdlib Logger with ISO timestamp format; used by all backend modules
- backend/app/services/fhir/adapter.py — iEMR JSON → FHIR R4 Bundle; 6 resource types (Patient, Condition, MedicationRequest, Observation, AllergyIntolerance, ServiceRequest); most-recent-wins deduplication keyed by iEMR code for all types except Observation; VITALS_DATETIME used for effectiveDateTime (never ADMIT_DATE); non-standard `_age` extension passes age to ingestion layer; _build_med_history() collects full medication timeline (104 events for patient 1091) deduplicated by (name, date, activity), passed as _aria_med_history metadata on bundle dict. Data quality fixes applied 2026-04-21: (1) Z00.xx encounter-type codes filtered from Conditions, (2) discontinued medications excluded via sentinel tombstone + cross-MED_CODE name propagation, (3) supplies/tests/device scripts filtered from MedicationRequests via _NON_DRUG_MARKERS/_NON_DRUG_EXACT, (4) secondary name-level deduplication collapses same drug appearing under multiple MED_CODEs, (5) non-clinical PLAN items (physician names, redacted vendors, patient education) filtered from ServiceRequests. Patient 1091: medications reduced from 38 → 14 (all actual drugs); conditions reduced from 18 → 17 (PREVENTIVE CARE removed); follow-up items reduced from 10 → 6 (admin/education entries removed).
- backend/tests/test_fhir_adapter.py — 42 tests (41 unit + 1 integration); all passing; includes TestBuildMedHistory (7 cases), test_bundle_contains_aria_med_history_key, and 6 new tests covering Z00.xx filtering, discontinued medication exclusion, cross-MED_CODE discontinue propagation, and non-clinical service request filtering
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

- backend/app/services/briefing/composer.py — compose_briefing() async function; queries DB for 28-day readings, unacknowledged alerts, medication confirmations, clinical context; assembles all 9 deterministic briefing fields (trend_summary, medication_status, adherence_summary, active_problems, overdue_labs, visit_agenda, urgent_flags, risk_score, data_limitations); persists Briefing row + audit_event row; clinical language enforced at code level ("possible adherence concern", "treatment review warranted"); Layer 1 only — no LLM. Data quality fix 2026-04-21: visit agenda item prefix changed from "Order overdue lab:" to "Pending follow-up:" because the overdue_labs field contains a mix of actual lab orders and clinical referrals/protocols.
- backend/app/services/briefing/summarizer.py — generate_llm_summary() async function; loads prompt from prompts/briefing_summary_prompt.md; computes SHA-256 prompt_hash; calls claude-sonnet-4-20250514 for 3-sentence readable summary; retry loop (max 2 attempts): calls validate_llm_output() after each attempt — on pass stores readable_summary, on fail retries once then stores None; populates model_version, prompt_hash, generated_at on briefing row for audit; must only run after Layer 1 is verified
- backend/app/services/briefing/llm_validator.py — Layer 3 output validation and guardrails; ValidationResult dataclass; three check groups: Group A safety (check_phi_leak, check_prompt_injection), Group B guardrails (check_guardrails — 10 forbidden phrases), Group C faithfulness (check_sentence_count, check_risk_score_consistency, check_adherence_language, check_titration_window, check_urgent_flags, check_overdue_labs, check_problem_assessments, check_data_limitations, check_medication_hallucination, check_bp_plausibility, check_contradiction); validate_llm_output() runs all checks in order, returns on first failure; always writes audit_events row with action="llm_validation", outcome="success"|"failure"; ruff clean. check_problem_assessments() updated 2026-04-25: previously exited immediately when active_problems was non-empty, allowing hallucinated conditions to pass unchecked; now scans all recognised condition names against payload terms regardless of list size; _CONDITION_SYNONYMS map (15 entries) prevents false positives when LLM writes "heart failure" for payload "CHF" or "diabetes" for "T2DM"
- backend/app/services/briefing/__init__.py — exports compose_briefing, generate_llm_summary
- prompts/briefing_summary_prompt.md — Layer 3 system prompt; enforces 3-sentence output, clinical language rules, no medication recommendations
- backend/tests/test_briefing_composer.py — 61 unit tests (all passing); covers all helper functions, all 9 briefing fields, clinical language enforcement, async compose_briefing with mocked session, error handling, summarizer helpers
- backend/tests/test_llm_validator.py — 57 unit tests (all passing); fixture-based, no real DB or API calls; covers all 14 check functions individually + 4 full validate_llm_output integration tests; asyncio mode=auto; tests: 3 PHI leak, 3 prompt injection, 11 guardrail (9 parametrized + 2), 3 sentence count, 4 risk score, 4 adherence language, 3 titration window, 3 urgent flags, 3 overdue labs, 6 problem assessments (empty list, hallucinated condition with non-empty problems, synonym acceptance, known condition passes, no condition mention, grounding via problem_assessments keys), 3 medication hallucination, 4 BP plausibility, 3 contradiction, 4 full pipeline (audit event on pass/fail, first-failed-check ordering, compliant summary)

- backend/app/services/pattern_engine/risk_scorer.py — Layer 2 compute_risk_score(patient_id, session); fetches full Patient object (for next_appointment); queries 28-day readings, clinical_context, medication_confirmations; computes weighted 0.0–100.0 priority score from: systolic-vs-baseline (30%), medication inertia (25%, saturates at 180 days), inverted adherence (20%), reading gap (15%, normalised against adaptive window), severity-weighted comorbidity (10%); comorbidity uses ICD-10 prefix weights — CHF/Stroke/TIA=25pts each, Diabetes/CKD/CAD=15pts each, other=5pts, clamped to 100 (Fix 25); gap normalization uses _compute_window_days() — same adaptive window formula as Layer 1 detectors, min 14/max 90/fallback 28 (Fix 58); inertia saturates at 180 days not 90 (Fix 58); persists patients.risk_score AND patients.risk_score_computed_at=now() on every update (Fix 61); no audit_event required for this computation
- backend/tests/test_risk_scorer.py — 14 unit tests passing with mocked AsyncSession; updated for Fix 25/58/61: _session_for() now returns Patient mock object (not string); test_signal_weights parametrize updated (inertia max at 180d, gap max at 28d with window fallback, comorbidity max uses ICD-10 codes); test_score_clamped_0_100 uses CHF+Stroke+TIA+DM+CKD+CAD codes to hit 100; test_persists_to_patients_table expected score updated 38.67→27.17 and UPDATE assertion extended to include risk_score_computed_at

- backend/app/services/pattern_engine/gap_detector.py — Layer 1 run_gap_detector(session, patient_id) → GapResult; tier-aware thresholds (high: flag≥1d/urgent≥3d, medium: flag≥3d/urgent≥5d, low: flag≥7d/urgent≥14d); returns gap_days, flagged, urgent booleans; no readings → gap_days=None, flagged=False
- backend/app/services/pattern_engine/inertia_detector.py — Layer 1 run_inertia_detector(session, patient_id) → InertiaResult; all 4 conditions required simultaneously: avg systolic≥140, ≥5 elevated readings, elevated span >7 days, no med change on/after first elevated reading; fail-safe: any unmet condition → detected=False
- backend/app/services/pattern_engine/adherence_analyzer.py — Layer 1 run_adherence_analyzer(session, patient_id) → AdherenceResult; 28-day adherence rate from confirmations vs avg systolic; Pattern A (elevated+low adherence)="possible adherence concern", Pattern B (elevated+high adherence)="treatment review warranted", Pattern C (normal+low adherence)="contextual review", none=no concern; clinical language enforced at code level
- backend/app/services/pattern_engine/deterioration_detector.py — Layer 1 run_deterioration_detector(session, patient_id) → DeteriorationResult; dual-signal: least-squares positive slope over 14 days AND recent 3-day avg > days 4–10 baseline avg; both signals required to reduce false positives; <7 readings → detected=False; pure Python slope (no numpy)
- backend/tests/test_pattern_engine.py — 39 unit tests (all passing); covers all 4 Layer 1 detectors and their edge cases (no readings, sparse data, borderline thresholds, all pattern types)
- frontend/src/ — dashboard shell with mock data (Krishna)
  - All 24 components and pages created (PatientList, BriefingCard, SparklineChart, AdherenceSummary, VisitAgenda, AlertInbox, RiskTierBadge, RiskScoreBar, Sidebar, ThemeToggle, Admin page)
  - npm run build passing with 0 TypeScript errors, 0 lint errors
  - Dark mode, Inter font, teal/sage colour system, clinical language enforced
- frontend/src/lib/api.ts — fully wired to real backend; shared apiFetch<T>() helper; mock stubs removed; getPatient/getBriefing return null on 404 gracefully
- frontend/src/lib/mockData.ts — TODAY computed dynamically (new Date().toISOString().slice(0,10)); no longer hardcoded
- frontend/src/components/dashboard/PatientList.tsx — lastReading() uses getMockReadings() lookup instead of hardcoded patient ID strings
- frontend/src/components/dashboard/AlertInbox.tsx — acknowledge button calls acknowledgeAlert() in api.ts; no longer UI-only

- backend/app/main.py — FastAPI app entry point; CORS (localhost:3000); all 7 routers registered; WorkerProcessor starts/stops on lifespan
- backend/app/api/patients.py — GET /api/patients (sorted tier→score DESC), GET /api/patients/{id}; _serialise() now includes risk_score_computed_at (Fix 61)
- backend/app/api/briefings.py — GET /api/briefings/{patient_id}; marks read_at; writes audit event
- backend/app/api/alerts.py — GET /api/alerts (unacknowledged only), POST /api/alerts/{id}/acknowledge + audit event
- backend/app/api/readings.py — GET /api/readings?patient_id= (28-day window), POST /api/readings (manual entry + audit)
- backend/app/api/ingest.py — POST /api/ingest; validates FHIR Bundle then calls ingest_fhir_bundle()
- backend/app/api/admin.py — POST /api/admin/trigger-scheduler; guarded by DEMO_MODE=true
- backend/app/api/adherence.py — GET /api/adherence/{patient_id}; per-medication adherence breakdown from medication_confirmations (28-day window); matches frontend AdherenceData type exactly
- backend/tests/test_api.py — 24 unit tests (all passing); covers all 11 API routes; uses httpx.AsyncClient + mocked sessions; no live DB required
- backend/app/api/shadow_mode.py — GET /api/shadow-mode/results; serves pre-computed shadow mode results from data/shadow_mode_results.json; no DB dependency; returns 404 if results not yet generated
- scripts/run_shadow_mode.py — shadow mode validation script; **COMPLETE, PASSING at 94.3%** (see Shadow Mode section below)
- frontend/src/app/shadow-mode/page.tsx — shadow mode results page; shows visit-by-visit ARIA vs physician comparison, between-visit alert timeline, detector breakdowns, agreement metrics
- frontend/src/lib/types.ts — Patient interface now includes risk_score_computed_at: string | null (Fix 61)
- frontend/src/components/dashboard/PatientList.tsx — staleness badge: amber AlertTriangle icon + "Score stale" label shown beneath RiskScoreBar when risk_score_computed_at > 26 hours ago; isScoreStale() helper added (Fix 61)

### IN PROGRESS
- None

### NOT STARTED
- scripts/run_scheduler.py — standalone manual scheduler trigger script

---

## Schema Changes Since v4.3 Spec
- briefings table: added model_version TEXT (nullable) and prompt_hash TEXT (nullable) columns for Layer 3 LLM audit trail (per CLAUDE.md: "Log: model_version, prompt_hash, generated_at in briefing row")
- readings rows inserted by ingestion.py for clinic BP readings from FHIR Observations use session="ad_hoc" and source="clinic" to distinguish them from home monitoring readings (source="generated" | "manual" | "ble_auto")
- clinical_context table: added med_history JSONB column (ALTER TABLE … ADD COLUMN IF NOT EXISTS); stores full medication timeline as list[{name, rxnorm, date, activity}] sorted chronologically; 104 entries for patient 1091
- patients table: added risk_score_computed_at TIMESTAMPTZ column (ALTER TABLE … ADD COLUMN IF NOT EXISTS); set on every risk_score update; frontend shows staleness badge when > 26 hours old (Fix 61)

---

## Plan Changes
- Layer 2 risk scorer was implemented before Layer 1 detectors. Now that all 4 Layer 1 detectors are complete, risk_scorer.py should be updated to consume Layer 1 outputs (GapResult, InertiaResult, AdherenceResult, DeteriorationResult) instead of running its own direct DB queries. processor.py pattern_recompute handler should call detectors in order before compute_risk_score().
- Frontend built with mock data first (Krishna), then wired to real backend (Sahil). api.ts now hits real endpoints — mock data no longer used at runtime.

---

## API Endpoints Status

| Endpoint | Status | Notes |
|---|---|---|
| GET /api/patients | DONE | |
| GET /api/patients/{id} | DONE | |
| GET /api/briefings/{patient_id} | DONE | marks read_at, writes audit |
| GET /api/readings?patient_id= | DONE | 28-day window |
| POST /api/readings | DONE | manual entry |
| GET /api/alerts | DONE | unacknowledged only |
| POST /api/alerts/{id}/acknowledge | DONE | writes audit |
| GET /api/adherence/{patient_id} | DONE | per-medication breakdown |
| POST /api/ingest | DONE | validates then ingests |
| POST /api/admin/trigger-scheduler | DONE | DEMO_MODE guard |
| GET /api/shadow-mode/results | DONE | serves pre-computed JSON results file |
| GET /health | DONE | |

---

## Known Issues
- Supabase project may be paused (free tier). Un-pause at supabase.com before running `python scripts/setup_db.py`. Last connection attempt: 2026-04-12, error: DNS resolution failure.
- adapter.py `_age` extension key (`_age`) is non-standard FHIR. ingestion.py must read this same constant from adapter.py (`_PATIENT_AGE_EXT`) rather than hardcoding the string, to keep both files in sync. Current state: both files define `_PATIENT_AGE_EXT = "_age"` independently with matching values — silent divergence risk if adapter.py changes the key. Fix before Task 5: import the constant in ingestion.py rather than redefining it.
- ANTHROPIC_API_KEY in backend/.env is placeholder — Layer 3 LLM summary will be skipped until real key is set. Briefing still generates via Layer 1 without it.
- Some iEMR medication entries have null MED_ACTIVITY (e.g. ASPIRIN 81, METOPROLOL in patient 1091's earliest visits). The activity field in med_history will be null for these entries. Acceptable for now — briefing composer should handle null activity gracefully.
- risk_score for patient 1091 (69.48) was computed before the medication list was corrected. It should be recomputed via a pattern_recompute job after the next demo run. The score will shift slightly because the adherence denominator changed (420 confirmations vs the old 1092).

---

## Shadow Mode Development — 2026-04-22

### Overview
Shadow mode replays all 124 iEMR clinic visits in chronological order and asks: if ARIA had been running during this patient's care, would it have fired (or not fired) at the same moments the physician was concerned? Ground truth comes from iEMR `PROBLEM_STATUS2_FLAG` (flag 1 or 2 = physician concerned, flag 3 = stable). Visits without an HTN flag are excluded from agreement scoring.

**Final result: 94.3% agreement (33/35 labelled), PASSED ✓** (target was ≥80%)  
`random.seed(1)` is set in the script for reproducible results.

### What the script does (`scripts/run_shadow_mode.py`)

**Step 1 — Clinic BP extraction (53 visits)**  
Loads all clinic BP readings from the Supabase DB (`source='clinic'`). Deduplicates by date keeping the chronologically last reading per day (settled post-assessment BP, not white-coat spike). Applies physician label from iEMR `PROBLEM_STATUS2_FLAG` via a label map.

**Step 2 — Synthetic home reading generation (28-day windows)**  
For each BP visit, generates a 28-day window of synthetic home readings using the same `ReadingGenerator` rules as production (SD 8–12 mmHg, morning/evening split, device outage episodes, white-coat dip before visit, post-appointment return). Minimum 4 readings in window required — visits with fewer are skipped.

**Step 3 — Patient-adaptive threshold**  
Computes a personal BP threshold from prior clinic visits only: `max(130, stable_baseline_mean + 1.5×SD)` capped at 145 mmHg. Falls back to population threshold of 140 mmHg if fewer than 3 stable readings exist. This is more accurate than the production hard-coded 140 mmHg for all patients.

**Step 4 — Continuous monitoring (between-visit alerts)**  
Scans every 7 days through each inter-visit gap using a rolling 28-day synthetic window. Fires all four detectors at each check point. Keeps only the earliest alert per gap (avoids double-counting). Alert format includes `alert_type` (pipe-separated codes), `days_before_visit`, plain-English `message`, and a `reasons[]` list with one entry per fired detector.

**Step 5 — Four Layer 1 detectors (shadow versions)**  
All four detectors are re-implemented locally (no DB calls) operating on the in-memory synthetic window:
- **GAP** — ≥3 consecutive days absent while window mean ≥ threshold
- **INE (Therapeutic Inertia)** — window mean ≥ threshold + no medication change since `last_med_change`
- **DET (Deterioration)** — positive least-squares slope across the 28-day window
- **ADH (Adherence)** — synthetic adherence rate computed per-visit; Pattern A (low adherence + high BP) and Pattern B (high adherence + high BP = possible treatment-review case)

**Step 6 — Scoring and agreement**  
`aria_fired = gap_urgent OR inertia OR deterioration OR adherence`.  
`result` = agree | false_negative | false_positive | no_ground_truth.  
Only visits with ground truth (flag 1/2 or 3) contribute to agreement %.

**Step 7 — Best demo window identification**  
Finds the contiguous run of visits with the highest density of `agree` results — used by the frontend to highlight the recommended demo window.

**Step 8 — Output**  
Results written to `data/shadow_mode_results.json`. Per-visit summary printed to console with label, window mean, threshold, fired detectors, and result. Between-visit alerts printed per gap.

### Other active problems display
Each visit in the results includes `other_active_problems` — a list of non-HTN physician assessments from that visit (name, PROBLEM_STATUS2_FLAG, PROBLEM_STATUS2 text, assessment text). These are displayed on the shadow mode frontend to give clinical context for false positive/negative cases.

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

### Frontend — shadow mode page (`frontend/src/app/shadow-mode/`)
New route `/shadow-mode` added to the app. Features:
- **Summary banner** — agreement %, pass/fail badge, evaluation point counts
- **Best demo window** — highlighted date range with summary
- **Visit timeline** — expandable cards for each of 52 evaluation points
  - Shows: visit date, source (clinic BP vs no-vitals), physician label badge, ARIA fired/silent badge, result (AGREE / FALSE NEG / FALSE POS / NO GT)
  - Expanded view: synthetic reading sparkline, detector breakdown (GAP / INE / DET / ADH), `other_active_problems`, and "First alert ARIA would have sent" section
- **Between-visit alerts** — per-gap alert strip with days-before-visit, color-coded `AlertTypeBadge` components (Reading Gap / Therapeutic Inertia / BP Deterioration / Treatment Review), and bullet-point plain-English reasons per detector
- **No ground truth** visits shown with neutral badge (excluded from scoring)
- Null BP handled gracefully (displays "—" instead of crashing)

### Alert clarity improvements (2026-04-22)
Between-visit alerts previously showed raw acronyms ("GAP|INE") and a single concatenated message string. Updated to:
- **`AlertTypeBadge` component** — maps GAP→"Reading Gap" (red), INE→"Therapeutic Inertia" (orange), DET→"BP Deterioration" (red), ADH→"Treatment Review" (amber) with full-name labels and color-coded pills
- **`reasons[]` list on each alert** — one plain-English sentence per fired detector, stored in JSON and displayed as bullet points in the UI
- Example reasons:
  - "No readings received for 5 consecutive days while BP was elevated (avg 148 mmHg)"
  - "High BP (avg 148 mmHg) despite good medication adherence (85%) — possible treatment review warranted"
  - "Sustained elevated BP (avg 163 mmHg, threshold 140 mmHg) with no medication change in the past 42 days — possible therapeutic inertia"

### Types updated (`frontend/src/lib/types.ts`)
Added `reasons?: string[]` to `BetweenVisitAlert` interface to carry per-detector reasons from script output to frontend.

### New API endpoint
`GET /api/shadow-mode/results` — serves `data/shadow_mode_results.json` directly. No DB dependency. 404 if file not present (run `python scripts/run_shadow_mode.py` first). Route registered in `backend/app/main.py`.

### System audit (AUDIT.md)
A full **47-item** system audit was conducted comparing production ARIA against shadow mode validated behaviour. Key findings documented in `AUDIT.md` at the project root. Three investigation sections cover:
1. **Non-HTN visit handling** — physician assessments for non-HTN conditions (PROBLEM_STATUS2_FLAG, PROBLEM_ASSESSMENT_TEXT) are never captured by production detectors; detectors are BP-only
2. **Conversion fidelity** — iEMR fields silently discarded during adapter.py conversion: PULSE (all 53 BP visits), WEIGHT (12-lb loss over 14 months), TEMPERATURE, PULSEOXYGEN (includes 84% SpO2 for CHF patient Nov 2011), EXAM_TEXT, ROS_TEXT, PROBLEM_STATUS2_FLAG, PROBLEM_ASSESSMENT_TEXT, ALLERGY_REACTION; `social_context` DB column exists but is never populated
3. **Hardcoded patient references** — `PATIENT_ID = "1091"` in run_shadow_mode.py, default bundle in run_ingestion.py, `_DEMO_PATIENT_ID` in reset_demo.py — none of these are acceptable for multi-patient operation

The 57 audit items are categorised as: Critical (7), High (14), Medium (13), Low (7), Infrastructure (6), New clinical features (7), Layer 3 LLM safety (3 Critical, 4 High, 3 Medium, 1 Low). Phased roadmap (Phase 0–8) in AUDIT.md.

**2026-04-23:** AUDIT.md revised — item count corrected from 25 to 47; "51 BP clinic visits" corrected to 53 unique dates; shadow mode result corrected from 91.4% (32/35, 1 FN) to 94.3% (33/35, 0 FN); new critical item added (Fix 5: comorbidity-adjusted threshold not in production).

**2026-04-24:** CLAUDE.md and all .claude agents/skills updated to reflect every audit finding. Specific changes:
- CLAUDE.md: RISK SCORING updated with severity-weighted comorbidity model (Fix 25); PATTERN ENGINE QUERIES: adaptive window formula + white-coat exclusion added (Fix 27/28); BRIEFING JSON STRUCTURE: titration window notice added to medication_status (Fix 34)
- Contradictions resolved: `clinical-validator.md` corrected from "ALL 4" to "ALL 5" inertia conditions; `briefing.md` and `briefing-engineer.md` corrected from 9 to 10 briefing fields
- All 47 AUDIT.md fixes now documented in their owning .claude agent or skill: Fix 18/23/25/27/28/29/30/31/34/47 were previously missing from the instruction set

**2026-04-24 (later):** Layer 3 LLM output validation and guardrails implemented (AUDIT.md Fixes 48–57).
- New file: `backend/app/services/briefing/llm_validator.py` — 14 checks across 3 groups (PHI leak, prompt injection, 10 guardrails, 11 faithfulness checks); audit_events written on every call; ruff clean
- Updated: `backend/app/services/briefing/summarizer.py` — retry loop (max 2 attempts) wraps LLM call + validation; readable_summary=None after two failures; Layer 1 briefing always primary
- New file: `backend/tests/test_llm_validator.py` — 51 tests, all passing
- AUDIT.md expanded to 57 items (Fixes 48–57 added); CLAUDE.md updated (briefings table, test list, audit_events action column); AGENTS.md, all .claude agents/skills verified complete
- Groq/Gemini revert (earlier same day): summarizer.py and config.py reverted to Anthropic-only after branch merge introduced non-spec LLM keys

---

## AUDIT.md Fixes Implemented — 2026-04-26

### Fix 25 — Comorbidity risk score saturated at 5 problems
**File:** `backend/app/services/pattern_engine/risk_scorer.py` line 161
**Issue:** `_clamp(_comorbidity_count(context) / 5.0 * 100.0)` treated every patient with 5+ coded problems identically. Patient 1091 has 17 problems — the signal was maxed out and useless for differentiation within the high-risk cohort.
**Fix:** Replaced `_comorbidity_count()` with `_comorbidity_severity_score()` using ICD-10 prefix matching. Weights: CHF (I50) / Stroke (I63, I64) / TIA (G45) = 25 pts each; Diabetes (E11) / CKD (N18) / CAD (I25) = 15 pts each; any other coded problem = 5 pts. Total clamped to 100. CHF + Diabetes now scores 40/100 vs CHF + Stroke at 50/100 — clinically meaningful differentiation.

### Fix 58 — sig_gap saturated at 14 days; sig_inertia saturated at 90 days
**File:** `backend/app/services/pattern_engine/risk_scorer.py` lines 150/159
**Issue:** `sig_gap = _clamp(gap_days / 14.0 * 100.0)` — a 45-day gap and a 14-day gap both scored 100/100, making the signal useless for quarterly-schedule patients. `sig_inertia = _clamp(days / 90.0 * 100.0)` — saturated at 3 months, any patient without a med change for 91 days to 5 years was treated identically.
**Fix:** Added `_compute_window_days(patient, context)` using the same adaptive window formula as Layer 1 detectors: `min(90, max(14, (next_appointment - last_visit_date).days))`, fallback 28. `sig_gap` now normalises against `window_days` instead of 14. `sig_inertia` divisor changed from 90.0 → 180.0 (saturates at 6 months). Patient query upgraded from `select(Patient.patient_id)` to `select(Patient)` to obtain `next_appointment`.

### Fix 61 — No staleness indicator on patients.risk_score
**Files:** `backend/app/models/patient.py`, `backend/app/services/pattern_engine/risk_scorer.py`, `scripts/setup_db.py`, `backend/app/api/patients.py`, `frontend/src/lib/types.ts`, `frontend/src/components/dashboard/PatientList.tsx`
**Issue:** `patients.risk_score` had no timestamp. A stale score from a failed midnight sweep displayed with no visual warning — clinically misleading during active patient episodes.
**Fix:** Added `risk_score_computed_at TIMESTAMPTZ` column to Patient ORM model and DB (migration in setup_db.py). `compute_risk_score()` now sets both `risk_score` and `risk_score_computed_at = now` in the same UPDATE. API `_serialise()` exposes the field. Frontend `PatientList.tsx` shows an amber AlertTriangle badge + "Score stale" label under RiskScoreBar when `risk_score_computed_at` is older than 26 hours (single missed sweep tolerable, second is not).
**Tests:** All 14 risk scorer tests updated and passing — mock structure, expected scores, and UPDATE assertion corrected for all three fixes.

---

## Bugs Found and Fixed — 2026-04-25

### Bug 16 — check_problem_assessments() allowed condition hallucinations when active_problems was non-empty
**Date:** 2026-04-25
**Issue:** `check_problem_assessments()` in `llm_validator.py` exited immediately with `passed=True` whenever `payload["active_problems"]` was truthy. For a patient like 1091 with `active_problems = ["HYPERTENSION", "CHF", "T2DM"]`, an LLM output mentioning "atrial fibrillation" or "CKD" — neither of which the patient has — passed all validation checks and was stored in `readable_summary` unchallenged. The function was written to guard against the empty-list case only, leaving the non-empty case with zero cross-checking.
**Scenario:** LLM outputs "The patient's atrial fibrillation and CKD add significant cardiovascular risk" for a patient with no such diagnoses. All 14 validation checks pass. A clinician reads fabricated conditions in the pre-visit briefing and may act on them.
**File:** `backend/app/services/briefing/llm_validator.py`
**Fix:** Removed the early-exit on non-empty `active_problems`. Function now builds a lowercase string of all known payload terms from both `active_problems` and `problem_assessments` keys, then scans the LLM output for every recognised condition name. Added `_CONDITION_SYNONYMS` dict (15 entries) mapping LLM-facing terms to canonical synonym sets — prevents false positives when LLM writes "heart failure" for payload entry "CHF" or "diabetes" for "T2DM". Returns `failed_check="problem_hallucination"` on first condition that cannot be grounded in payload terms.
**Tests:** 6 new tests added to `test_llm_validator.py` (51→57 total): empty list blocks condition, hallucinated condition with non-empty problems blocked, synonym accepted, known condition passes, no condition mention passes, grounding via `problem_assessments` keys.
**Verified by:** `conda run -n aria python -m pytest tests/test_llm_validator.py -v` — 57 passed; `ruff check app/services/briefing/llm_validator.py` — all checks passed

---

## Bugs Found and Fixed — 2026-04-22

### Bug 7 — Worker reads wrong TypedDict keys from detector results
**Date:** 2026-04-22
**Issue:** `_handle_pattern_recompute` in `processor.py` read `gap["flagged"]`, `gap["urgent"]`, `inertia["detected"]`, `inertia.get("avg_systolic_28d")`, and `deterioration["detected"]` — none of which exist in the actual TypedDict definitions. Would cause `KeyError` at runtime on every pattern_recompute job.
**File:** `backend/app/services/worker/processor.py` lines 130-151
**Fix:** Updated 3 `logger.info` calls to use correct keys: `gap["status"]`, `inertia["inertia_detected"]`, `inertia.get("avg_systolic")`, `deterioration["deterioration"]`. No TypedDict changes.
**Verified by:** `python -m pytest tests/test_worker.py -v` — 23 passed

### Bug 14 — Frontend types declared risk_score as non-nullable number
**Date:** 2026-04-22
**Issue:** `Patient.risk_score` and `BriefingPayload.risk_score` typed as `number` in `types.ts`, but backend returns `null` when DB value is NULL. `RiskScoreBar` arithmetic on null would produce NaN and a broken bar.
**Files:** `frontend/src/lib/types.ts`, `frontend/src/components/dashboard/RiskScoreBar.tsx`
**Fix:** Changed both to `number | null`. In `RiskScoreBar`, introduced `safeScore = score ?? 0` used in all arithmetic and display.
**Verified by:** `npm run build` — 0 TypeScript errors

### Bug 15 — PatientList used getMockReadings() and non-null-safe risk_score sort
**Date:** 2026-04-22
**Issue:** `PatientList.tsx` imported `getMockReadings` from `mockData.ts` and called it in `lastReading()`. The "Last BP" and "Last Reading" columns showed fabricated data. Sort comparator `b.risk_score - a.risk_score` was not null-safe.
**File:** `frontend/src/components/dashboard/PatientList.tsx`
**Fix:** Removed `getMockReadings` import, `lastReading()`, `daysSince()` functions, and the two mock-data columns. Fixed sort to `(b.risk_score ?? 0) - (a.risk_score ?? 0)`.
**Verified by:** `npm run build` — 0 TypeScript errors

### Bug 13 — Pattern B interpretation missing clinical hedging language
**Date:** 2026-04-22
**Issue:** Pattern B in `adherence_analyzer.py` used `"treatment review warranted"` — assertive language that violates the ARIA clinical boundary rule requiring hedged language ("possible ...").
**File:** `backend/app/services/pattern_engine/adherence_analyzer.py` line ~40
**Fix:** Changed to `"possible treatment-review case — elevated BP with high adherence signal"`.
**Verified by:** `python -m pytest tests/test_pattern_engine.py -v` — 39 passed

### Bug 12 — Synthetic disclosure absent from data_limitations
**Date:** 2026-04-22
**Issue:** `data_limitations` field never disclosed that home BP readings are synthetic demo data derived from real iEMR baselines. A reviewer reading the briefing would have no indication the readings weren't real patient data.
**File:** `backend/app/services/briefing/composer.py` `_build_data_limitations()`
**Fix:** Appended mandatory synthetic disclosure sentence to all monitoring-active return paths (limited readings and sufficient readings). EHR-only path unchanged.
**Verified by:** `python -m pytest tests/test_briefing_composer.py -v` — 61 passed

### Bug 11 — CHF not prioritised first in active_problems
**Date:** 2026-04-22
**Issue:** `active_problems` in the briefing payload passed through unsorted. CHF (highest clinical priority) was not guaranteed to appear first — it appeared wherever it happened to be in the iEMR source.
**File:** `backend/app/services/briefing/composer.py` line ~531
**Fix:** Added `_PROBLEM_PRIORITY` ICD-10 prefix map and `_sort_problems()` helper. Applied to `active_problems` at payload assembly. CHF (I50) → 0, HTN (I10) → 1, T2DM (E11) → 2, CAD (I25) → 3, everything else alphabetical.
**Verified by:** `python -m pytest tests/test_briefing_composer.py -v` — 61 passed

### Bug 10 — Raw day counts in medication_status and visit_agenda
**Date:** 2026-04-22
**Issue:** `composer.py` output raw integers like "4590 days ago" in `medication_status` and the inertia visit_agenda item. Clinically unreadable for a GP.
**File:** `backend/app/services/briefing/composer.py` lines 141, 311
**Fix:** Added `_human_duration(days)` helper that converts to natural language ("about 12 years ago"). Replaced both raw day count format strings.
**Verified by:** `python -m pytest tests/test_briefing_composer.py -v` — 61 passed

### Bug 9 — 8 ruff violations across backend/app/
**Date:** 2026-04-22
**Issue:** 8 ruff violations: B904 (raise without `from exc`) in `ingest.py`; I001 (unsorted imports) and UP035 (deprecated `typing.AsyncGenerator`) in `main.py`; UP017 (`timezone.utc` → `UTC`) in all 4 pattern engine detectors.
**Files:** `app/api/ingest.py`, `app/main.py`, `app/services/pattern_engine/{gap,adherence,deterioration,inertia}_detector.py`
**Fix:** Fixed each manually — `raise ... from exc`, sorted imports, `from collections.abc import AsyncGenerator`, `from datetime import UTC` + replaced `timezone.utc`.
**Verified by:** `ruff check app/` → "All checks passed!"

### Bug 8 — Alert rows never written during pattern_recompute
**Date:** 2026-04-22
**Issue:** `_handle_pattern_recompute` ran all 4 detectors and logged results but never wrote any rows to the `alerts` table. Alerts page was always empty regardless of clinical findings.
**File:** `backend/app/services/worker/processor.py`
**Fix:** Added `_upsert_alert()` module-level helper and alert insertion block after all 4 detectors. Inserts `gap_urgent`, `gap_briefing`, `inertia`, and `deterioration` alerts as appropriate. Deduplicates by `(patient_id, alert_type, date(triggered_at))` to prevent duplicate rows on re-run.
**Verified by:** `python -m pytest tests/test_worker.py -v` — 23 passed

---

## Bugs Found and Fixed — 2026-04-21

Clinical data quality audit performed by tracing the live briefing for patient 1091 back through the database, FHIR bundle, and iEMR source data to root cause. Five distinct bugs found and fixed; all 274 unit tests passing after fixes.

### Bug 1 — "PREVENTIVE CARE" in active_problems
**Symptom:** The briefing's `active_problems` list included "PREVENTIVE CARE" (ICD-10 Z00.00) alongside CHF, HYPERTENSION, CAD.
**Root cause:** `_build_conditions()` in `adapter.py` included all FHIR Condition resources built from iEMR PROBLEM entries without filtering administrative encounter codes. Z00.00 means "General adult medical examination" — it is a visit type, not a clinical problem.
**Fix:** Added `if icd10 and icd10.startswith("Z00"): continue` in `_build_conditions()`. Z00.x covers all encounter-for-examination codes.
**File:** `backend/app/services/fhir/adapter.py`

### Bug 2 — Non-clinical items in overdue_labs (physician name, redacted vendor, patient education)
**Symptom:** `overdue_labs` contained "Dr. Gary Rogers", "XXXXXXXXX's Medical Surgical Supply", "Hypoglycemia - General Advice on Treatment", "Instructions for Sliding Scale Fast Acting Insulin". The visit agenda said things like "Order overdue lab: Dr. Gary Rogers."
**Root cause:** `_build_service_requests()` in `adapter.py` included every iEMR PLAN entry with `PLAN_NEEDS_FOLLOWUP=YES` regardless of type. The iEMR PLAN array stores a heterogeneous mix: actual lab orders, clinical referrals, patient education materials, and administrative contacts, all with the same flag.
**Fix:** Added a filter in `_build_service_requests()` to skip entries whose text starts with "Dr." (physician names), contains "XXXXXXXXX" (redacted entities), starts with "Instructions for" (patient education), or contains "General Advice" (patient education). Follow-up items reduced from 10 → 6 for patient 1091.
**File:** `backend/app/services/fhir/adapter.py`

### Bug 3 — Discontinued medications appearing as current regimen
**Symptom:** `current_medications` and the briefing's `medication_status` listed SIMVASTATIN 20, CRESTOR 2.5, HUMALOG INSULIN LISPRO, NOVOLIN 70/30, VOLTAREN, CLINDAMYCIN, BACTROBAN, and MECLIZINE as active. SIMVASTATIN and CRESTOR are both on the patient's allergy list — showing them as active prescriptions is a patient safety flag.
**Root cause:** `_build_medication_requests()` did not read the `MED_ACTIVITY` field. The field value "Discontinue" was present on the most recent iEMR entry for each of these drugs but was silently ignored.
**Additional sub-bug:** The original approach of `continue`-ing on Discontinue was wrong because the earlier active entry for the same `MED_CODE` key survived in the `seen` dict. Skipping the Discontinue entry left the previous active entry intact.
**Fix:** Changed to a sentinel/tombstone pattern: when `MED_ACTIVITY='Discontinue'`, store `seen[key] = None` rather than `continue`. This overwrites any earlier active entry for the same key. At the secondary dedup step, `None` values are filtered out.
**File:** `backend/app/services/fhir/adapter.py`

### Bug 4 — Discontinued medications surviving under different MED_CODEs (BYETTA case)
**Symptom:** BYETTA appeared in `current_medications` even after the sentinel fix. BYETTA is also on the allergy list. It was prescribed under one MED_CODE (26592850, no activity) and discontinued under a completely different MED_CODE (26604040, activity='Discontinue'). The sentinel tombstoned key 26604040 but left key 26592850 active.
**Root cause:** iEMR assigns a new MED_CODE to each prescription instance. A drug discontinued as "code B" does not automatically tombstone its earlier prescription "code A". The existing MED_CODE-keyed deduplication has no way to link the two.
**Fix:** Added a `discontinued_names` set that is populated whenever a Discontinue entry is processed (storing the normalised drug name alongside the sentinel). In the secondary name-based deduplication step, any active entry whose normalised name appears in `discontinued_names` is excluded — even if it has a different MED_CODE.
**File:** `backend/app/services/fhir/adapter.py`

### Bug 5 — Supplies, diagnostic tests, and device scripts in current_medications
**Symptom:** `current_medications` listed SHARPS CONTAINER (×3 → ×1 after dedup), B-D 50 UNIT SYRINGES 29 ULTRAFINE NEEDLE (×2), PEN NEEDLES, HEARING TEST, VNG (×2), Rx for Compression Stockings, Rx for Vestibular Rehabilitation. The adherence summary reported things like "SHARPS CONTAINER: 80% adherence" and "HEARING TEST: 86% adherence".
**Root cause:** iEMR stores all clinical orders — including injection supplies, diagnostic tests, and device prescriptions — in the same `MEDICATIONS` array as actual pharmaceuticals. `_build_medication_requests()` had no filter for these non-drug entries.
**Fix:** Added `_NON_DRUG_MARKERS` (tuple of upper-cased substrings: "RX FOR ", "SYRINGE", "SHARPS", " CONTAINER", "PEN NEEDLE", " TEST") and `_NON_DRUG_EXACT` (frozenset: {"VNG"}) constants. Applied before building each MedicationRequest resource; matching entries are skipped with a debug log. Medications reduced from 38 → 14 for patient 1091 (all 14 are actual pharmaceuticals).
**File:** `backend/app/services/fhir/adapter.py`

### Bug 6 — "Order overdue lab:" prefix used for non-lab follow-up items
**Symptom:** The visit agenda said "Order overdue lab: Humalog Sliding Scale." and "Order overdue lab: VESTIBULAR REHABILITATION." — an insulin dosing protocol and a physiotherapy referral were both described as lab orders.
**Root cause:** `_build_visit_agenda()` in `composer.py` hard-coded the string "Order overdue lab:" for all `overdue_labs` items, but the field contains a mix of actual lab orders ("C-Peptide, Serum"), clinical protocols ("Humalog Sliding Scale"), and referrals ("Cardiology Consult").
**Fix:** Changed prefix to "Pending follow-up:" — accurate for all item types in the field.
**File:** `backend/app/services/briefing/composer.py`

### Pipeline regenerated after fixes
After all adapter fixes, the FHIR bundle was regenerated from the raw iEMR data, `clinical_context` was updated via re-ingestion, stale medication confirmations were cleared and regenerated against the corrected 14-medication list, and the briefing was recomposed. Confirmed clean output in the live database.

---

## Supabase Connection String
Host: db.xxxxxxxxxxxx.supabase.co
Database: postgres
Port: 5432
Team members get the connection string from Kush directly.
Never commit the password.
