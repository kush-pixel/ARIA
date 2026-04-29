# ARIA v4.3 — Adaptive Real-time Intelligence Architecture
## Leap of Faith Technologies | IIT CS 595 | Spring 2026
## Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma

---

## GIT POLICY — NON-NEGOTIABLE

NEVER run git push under any circumstances.
NEVER run git commit on my behalf.
NEVER run git add on my behalf.
When implementation is complete, tell me which files changed and what commit message to use.
I will run all git commands myself.

---

## WHAT ARIA IS

A between-visit clinical intelligence platform for hypertension management.
A GP with 1800 patients has 8 minutes per consultation and no structured view of what happened
to their hypertensive patients since the last appointment. ARIA fixes this by:
1. Ingesting patient EHR data via FHIR R4 Bundle
2. Generating clinically realistic synthetic home BP readings and medication confirmations
3. Running three-layer AI analysis (rules → scoring → explanation)
4. Delivering a structured pre-visit briefing at 7:30 AM on appointment days

---

## CLINICAL BOUNDARY — NEVER VIOLATE

ARIA does not recommend specific medication adjustments.
ARIA does not send alerts directly to patients.
ARIA does not display raw readings to patients.
ARIA does not make clinical decisions.
Every output is decision support for the clinician only.
Language: "possible adherence concern" NOT "non-adherent"
Language: "treatment review warranted" NOT "medication failure"
Language: "sustained elevated readings" NOT "hypertensive crisis"
This boundary is enforced at code level, not as a guideline.

---

## AI ARCHITECTURE — THREE LAYERS (v4.3)

### Layer 1 — Deterministic Rule Engine (always runs first)
Gap detection, therapeutic inertia, adherence-BP correlation, deterioration detection.
Pure SQL queries, no AI, no LLM. Must be correct before Layers 2 and 3 run.

### Layer 2 — AI Risk Scoring
Computes numeric priority score after Layer 1 completes. Weights:
  28-day avg systolic vs personal baseline: 30% | Days since last med change: 25%
  Adherence rate: 20% | Gap duration: 15% | Active comorbidity count: 10%
Output: risk_score float 0.0-100.0. Higher = higher clinical priority within tier.
File: backend/app/services/pattern_engine/risk_scorer.py

### Layer 3 — LLM Explanation (optional, runs after Layer 1 verified)
Converts deterministic briefing JSON to 3-sentence readable summary.
Model: claude-sonnet-4-20250514 | Prompt: prompts/briefing_summary_prompt.md
Log: model_version, prompt_hash, generated_at in briefing row.
If validation fails: retry once, then store readable_summary=None.
File: backend/app/services/briefing/llm_validator.py

### Layer 3 Guardrails (absolute — payload irrelevant):
  "non-adherent", "non-compliant" | "hypertensive crisis" | "medication failure"
  "increase.*mg" / "decrease.*mg" / "prescribe" | "tell the patient" | "diagnos" | "emergency"
  Patient ID verbatim (PHI) | "[INST]", "system:", "ignore previous" (injection)

### Layer 3 Faithfulness (vs Layer 1 payload):
  Exactly 3 sentences | risk_score ±10 | adherence language grounded in adherence_summary
  "titration" requires titration notice in medication_status | "urgent" requires urgent_flags
  overdue lab claims require overdue_labs | conditions grounded in active_problems
  drug names in medication_status | BP values 60-250 mmHg within ±20 of trend data
  Audit: every result → audit_events action="llm_validation"

### Out of Scope — Do Not Build
Personalisation AI | Vision AI for medication photos | Advanced ML pipelines

---

## ENVIRONMENT

Conda: aria (Python 3.11) — activate: conda activate aria
Backend: uvicorn app.main:app --reload --port 8000 (from backend/)
Tests: python -m pytest tests/ -v -m "not integration" (from backend/)
Lint: ruff check app/ (on PATH — never python -m ruff) | Format: ruff format app/
Frontend: npm run dev (port 3000)
Database: PostgreSQL via Supabase | DATABASE_URL in backend/.env | asyncpg driver

---

## TECH STACK

Backend: Python 3.11, FastAPI, SQLAlchemy 2.0 async, Pydantic v2
Database: PostgreSQL (Supabase) — 12 tables, asyncpg
Frontend: Next.js 14, TypeScript strict, Tailwind CSS, recharts
AI: Anthropic claude-sonnet-4-20250514 (Layer 3 only)
Background: processing_jobs table + Python polling worker | Auth: JWT, clinician/admin roles

---

## PYDANTIC V2 SYNTAX (critical — v1 breaks)

CORRECT:
  from pydantic_settings import BaseSettings, SettingsConfigDict
  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", extra="ignore")
WRONG (v1): class Config: env_file = ".env"

---

## SQLALCHEMY 2.0 ASYNC SYNTAX (critical — sync breaks)

CORRECT: await session.execute(select(Model).where(...)) → result.scalar_one_or_none()
WRONG: session.query(Model).filter(...).first()  ← never use

---

## PROJECT STRUCTURE

backend/app/
  config.py, main.py (FastAPI + routers + lifespan)
  db/: base.py (engine + session factory), session.py (get_session dependency)
  models/: patient, clinical_context, reading, medication_confirmation, alert,
           alert_feedback, briefing, processing_job, audit_event,
           gap_explanation, calibration_rule, outcome_verification
  api/: patients, readings, briefings, alerts, ingest, admin, adherence,
        shadow_mode, ble_webhook, calibration, gap_explanations
  services/
    fhir/: adapter.py (iEMR→FHIR), ingestion.py (FHIR→DB), validator.py
    generator/: reading_generator.py, confirmation_generator.py
    pattern_engine/: gap_detector, inertia_detector, adherence_analyzer,
                     deterioration_detector, variability_detector,
                     risk_scorer (Layer 2), threshold_utils
    briefing/: composer.py (Layer 1 output), summarizer.py (Layer 3), llm_validator.py
    feedback/: calibration_engine.py, outcome_tracker.py
    worker/: processor.py (30s poll loop + escalation), scheduler.py (7:30 AM)
  utils/: datetime_utils, fhir_utils, clinical_utils, logging_utils

frontend/src/
  app/: page.tsx, layout.tsx, patients/page.tsx, patients/[id]/page.tsx
  components/dashboard/: PatientList, RiskTierBadge, RiskScoreBar, AlertInbox
  components/briefing/: BriefingCard, SparklineChart, AdherenceSummary, VisitAgenda
  components/shared/: PatientHeader, LoadingSpinner
  lib/: api.ts, types.ts, auth.ts

scripts/: run_adapter, run_ingestion, run_generator, run_worker, run_scheduler,
          run_shadow_mode, setup_db
prompts/: briefing_summary_prompt.md

---

## DATABASE — 12 TABLES

### patients
patient_id TEXT PK | gender CHAR(1) M|F|U | age SMALLINT
risk_tier TEXT NOT NULL (high|medium|low) | tier_override TEXT
risk_score NUMERIC(5,2) [Layer 2, 0.0-100.0]
risk_score_computed_at TIMESTAMPTZ [staleness badge if >26h]
monitoring_active BOOLEAN DEFAULT TRUE [FALSE = EHR-only]
next_appointment TIMESTAMPTZ | enrolled_at TIMESTAMPTZ | enrolled_by TEXT

### clinical_context (one row per patient, pre-computed at ingestion)
patient_id TEXT PK REF patients
active_problems TEXT[] ‖ problem_codes TEXT[] [parallel, ICD-10/SNOMED]
current_medications TEXT[] ‖ med_rxnorm_codes TEXT[] [parallel]
med_history JSONB [{name, rxnorm, date, activity} sorted chrono, deduped by (name,date,activity)]
last_med_change DATE [stale snapshot — use med_history JSONB for detector logic]
allergies TEXT[] ‖ allergy_reactions TEXT[] [parallel]
last_visit_date DATE | last_clinic_systolic SMALLINT | last_clinic_diastolic SMALLINT
historic_bp_systolic SMALLINT[] ‖ historic_bp_dates DATE[] [parallel, all clinic readings]
overdue_labs TEXT[] | social_context TEXT
problem_assessments JSONB [{problem_code, visit_date, htn_flag, status_text, assessment_text} DESC]
recent_labs JSONB [{loinc_code: {value, unit, date}}]
last_clinic_pulse SMALLINT | last_clinic_weight_kg NUMERIC(5,1) | last_clinic_spo2 NUMERIC(4,1)
historic_spo2 NUMERIC[] | last_updated TIMESTAMPTZ

### readings
reading_id UUID PK | patient_id TEXT REF patients
systolic_1 SMALLINT NOT NULL | diastolic_1 SMALLINT NOT NULL | heart_rate_1 SMALLINT
systolic_2 SMALLINT | diastolic_2 SMALLINT | heart_rate_2 SMALLINT [NULL if single reading]
systolic_avg NUMERIC(5,1) NOT NULL | diastolic_avg NUMERIC(5,1) NOT NULL | heart_rate_avg NUMERIC(5,1)
effective_datetime TIMESTAMPTZ NOT NULL
session TEXT NOT NULL (morning|evening|ad_hoc) [clinic ingestion uses ad_hoc]
source TEXT NOT NULL (generated|manual|ble_auto|clinic) [used by idempotency check]
submitted_by TEXT NOT NULL (patient|carer|generator|clinic)
bp_position TEXT (seated|standing) | bp_site TEXT (left_arm|right_arm)
consent_version TEXT DEFAULT '1.0' | medication_taken TEXT (yes|no|partial|NULL)

### medication_confirmations
confirmation_id UUID PK | patient_id TEXT REF patients
medication_name TEXT NOT NULL | rxnorm_code TEXT
scheduled_time TIMESTAMPTZ NOT NULL | confirmed_at TIMESTAMPTZ [NULL = missed dose]
confirmation_type TEXT (synthetic_demo|tap|photo|qr_scan)
confidence TEXT DEFAULT 'self_report' | minutes_from_schedule SMALLINT

### alerts
alert_id UUID PK | patient_id TEXT REF patients
alert_type TEXT NOT NULL (gap_urgent|gap_briefing|inertia|deterioration|adherence)
gap_days SMALLINT | systolic_avg NUMERIC(5,1)
triggered_at TIMESTAMPTZ | delivered_at TIMESTAMPTZ | acknowledged_at TIMESTAMPTZ
off_hours BOOLEAN DEFAULT FALSE [TRUE if 6PM–8AM UTC or weekend]
escalated BOOLEAN DEFAULT FALSE [TRUE if unacknowledged >24h for gap_urgent/deterioration]

### briefings
briefing_id UUID PK | patient_id TEXT REF patients | appointment_date DATE NOT NULL
llm_response JSONB NOT NULL [structured briefing payload]
model_version TEXT | prompt_hash TEXT [SHA-256 of Layer 3 prompt]
generated_at TIMESTAMPTZ | delivered_at TIMESTAMPTZ | read_at TIMESTAMPTZ

### processing_jobs
job_id UUID PK | job_type TEXT (pattern_recompute|briefing_generation|bundle_import)
patient_id TEXT REF patients | idempotency_key TEXT NOT NULL UNIQUE
status TEXT (queued|running|succeeded|failed) | payload_ref TEXT | error_message TEXT
queued_at TIMESTAMPTZ | started_at TIMESTAMPTZ | finished_at TIMESTAMPTZ
created_by TEXT DEFAULT 'system'

### audit_events
audit_id UUID PK | actor_type TEXT (system|clinician|admin) | actor_id TEXT
patient_id TEXT REF patients
action TEXT (bundle_import|reading_ingested|briefing_viewed|alert_acknowledged|llm_validation)
resource_type TEXT | resource_id TEXT | outcome TEXT (success|failure)
request_id TEXT | event_timestamp TIMESTAMPTZ | details TEXT

### alert_feedback
feedback_id UUID PK | alert_id UUID REF alerts | patient_id TEXT NOT NULL
detector_type TEXT (gap|inertia|deterioration|adherence)
disposition TEXT (agree_acting|agree_monitoring|disagree)
reason_text TEXT | clinician_id TEXT

### gap_explanations
explanation_id UUID PK | patient_id TEXT NOT NULL
gap_start DATE | gap_end DATE
reason TEXT (device_issue|travel|illness|unknown|non_compliance)
notes TEXT | reported_by TEXT DEFAULT 'clinician' | reporter_id TEXT

### calibration_rules
rule_id UUID PK | patient_id TEXT NOT NULL
detector_type TEXT (gap|inertia|deterioration|adherence)
dismissal_count INTEGER | approved_by TEXT | approved_at TIMESTAMPTZ
notes TEXT | active BOOLEAN DEFAULT TRUE

### outcome_verifications
verification_id UUID PK | feedback_id UUID REF alert_feedback | alert_id UUID REF alerts
patient_id TEXT NOT NULL | dismissed_at TIMESTAMPTZ NOT NULL
check_after TIMESTAMPTZ NOT NULL [dismissed_at + 30 days]
outcome_type TEXT DEFAULT 'pending' (pending|deterioration_cluster|none)
prompted_at TIMESTAMPTZ | clinician_response TEXT (relevant|not_relevant|unsure)
responded_at TIMESTAMPTZ | response_notes TEXT

---

## CRITICAL INDEXES

UNIQUE idx_processing_jobs_idempotency: processing_jobs(idempotency_key)
UNIQUE idx_readings_patient_datetime_source: readings(patient_id, effective_datetime, source)
UNIQUE idx_confirmations_patient_med_scheduled: medication_confirmations(patient_id, medication_name, scheduled_time)
idx_readings_patient_datetime: readings(patient_id, effective_datetime DESC)
idx_readings_patient_session: readings(patient_id, session, effective_datetime DESC)
idx_alerts_undelivered: alerts(patient_id, delivered_at) WHERE delivered_at IS NULL
idx_patients_appointment: patients(next_appointment) WHERE monitoring_active = TRUE
idx_cc_problem_codes: clinical_context USING GIN(problem_codes)
idx_confirmations_patient_scheduled: medication_confirmations(patient_id, scheduled_time DESC)
idx_confirmations_missed: medication_confirmations(patient_id, scheduled_time) WHERE confirmed_at IS NULL
idx_processing_jobs_status_type: processing_jobs(status, job_type, queued_at)
idx_audit_events_patient_time: audit_events(patient_id, event_timestamp DESC)
idx_patients_risk_score: patients(risk_tier, risk_score DESC)
idx_alert_feedback_patient_detector: alert_feedback(patient_id, detector_type, created_at DESC)
idx_alert_feedback_alert: alert_feedback(alert_id)
idx_calibration_rules_patient_detector: calibration_rules(patient_id, detector_type, active)
idx_outcome_verifications_pending: outcome_verifications(outcome_type, check_after) WHERE outcome_type='pending'
idx_outcome_verifications_patient: outcome_verifications(patient_id, prompted_at DESC)
idx_gap_explanations_patient: gap_explanations(patient_id, gap_start DESC)

Migrations (ADD COLUMN IF NOT EXISTS — safe to re-run via setup_db.py):
  clinical_context: med_history JSONB, problem_assessments JSONB, recent_labs JSONB,
                    last_clinic_pulse SMALLINT, last_clinic_weight_kg NUMERIC(5,1),
                    last_clinic_spo2 NUMERIC(4,1), historic_spo2 NUMERIC[], allergy_reactions TEXT[]
  patients: risk_score_computed_at TIMESTAMPTZ
  alerts: off_hours BOOLEAN DEFAULT FALSE, escalated BOOLEAN DEFAULT FALSE

---

## SYNTHETIC DATA RULES (never violate — clinical reviewer will catch errors)

Day-to-day systolic SD: 8-12 mmHg (NEVER less than 5 — flat variance is wrong)
Morning/evening diff: morning 5-10 mmHg HIGHER than evening every week
Round numbers: NEVER exactly round — real readings: 153, 161, 148, 167
Two-reading session: readings 1 and 2 differ by 2-6 mmHg (2 slightly lower)
Diastolic: systolic × 0.60-0.66
Heart rate: 64-82 bpm, slight negative correlation with systolic when beta-blocker in regimen
Device outage: 1-2 episodes of 2-4 days per inter-visit interval — ABSENT ROWS, never null values
White-coat dip: systolic drops 10-15 mmHg in 3-5 days before appointment
Post-appointment return: readings return to elevated baseline after dip

Generation scope — full care timeline:
  For each consecutive clinic reading pair, generate daily synthetic readings by linearly
  interpolating between anchors with Gaussian noise.
  Baseline: median(historic_bp_systolic) — NOT hardcoded. Falls back to 163.0 only if <2 clinic readings.
  Patient 1091: 65 clinic readings (2008-01-21 to 2013-09-26, mean 133.8, SD 16.2).
  Demo window (~158 mmHg avg) reflects 2011-2013 period.

Confirmation scope — full care timeline:
  Per-interval adherence from Beta distribution anchored near ~91% ±10-15 pp variation.
  Derived from clinical_context.med_history (not hardcoded med list).
  Idempotency: unique constraint on (patient_id, medication_name, scheduled_time).

---

## FHIR R4 MAPPING (iEMR → FHIR)

PROBLEM.value + PROBLEM_CODE → Condition (clinicalStatus=active, ICD-10 code)
MEDICATIONS (all visits) → _aria_med_history (non-FHIR bundle key)
  Full timeline across all visits → clinical_context.med_history JSONB
MEDICATIONS (current) → MedicationRequest (most-recent-wins by MED_CODE)
VITALS SYSTOLIC+DIASTOLIC → Observation LOINC 55284-4
  effectiveDateTime from VITALS_DATETIME (NOT ADMIT_DATE)
  components: 8480-6 (systolic), 8462-4 (diastolic)
VITALS PULSE/WEIGHT/SpO2/TEMP → Observation LOINC 8867-4/29463-7/59408-5/8310-5
  stored in clinical_context; SpO2 < 92% in CHF triggers visit agenda alert
PROBLEM assessments → _aria_problem_assessments (non-FHIR bundle key)
  {problem_code, visit_date, htn_flag, status_text, assessment_text} → problem_assessments JSONB
VISIT dates → _aria_visit_dates: max(all ADMIT_DATE) → clinical_context.last_visit_date
ALLERGY → AllergyIntolerance (ALLERGY_STATUS=="Active" only; include ALLERGY_REACTION)
PLAN where PLAN_NEEDS_FOLLOWUP=YES → ServiceRequest

---

## PATTERN ENGINE QUERIES (Layer 1)

### Gap detection
Days since MAX(effective_datetime) per patient.
Thresholds: High tier flag>=1 urgent>=3 | Medium flag>=3 urgent>=5 | Low flag>=7 urgent>=14

### Therapeutic inertia (ALL conditions required)
  patient_threshold = max(130, stable_baseline_mean + 1.5×SD) capped at 145 mmHg
    derived from historic_bp_systolic filtered to stable visits; falls back to 140 if <3 stable readings
    comorbidity adjustment: −7 mmHg (floor 130) when CHF/Stroke/TIA active OR both cardiovascular+metabolic elevated
    log threshold_adjustment_mode ("full" post Fix 7 / "degraded" pre Fix 7)
  Slope check: if 7-day recent avg < patient_threshold → do NOT fire (BP declining)
  COUNT(*) >= 5 readings >= patient_threshold over adaptive window
  Window duration > 7 days
  Most recent med change (from med_history JSONB max date <= first elevated reading) < window start OR NULL
    (NOT last_med_change — that is a stale single snapshot)

### Deterioration (three gates)
  Positive linear slope across adaptive window
  Recent 3-day avg > baseline days 4-10 avg
  recent_avg >= patient_threshold (prevents firing on 115→119 rise)
  Step-change sub-detector: (7-day recent mean) − (7-day mean 3 weeks ago) >= 15 mmHg fires regardless of slope
  patient_threshold derived identically to inertia detector

### Adherence-BP correlation
  adherence_pct = confirmed_at count / scheduled count × 100 (join readings + confirmations by date)
  Clinical threshold: <80% flags concern
  Pattern A: high systolic + low adherence → "possible adherence concern" → alert row
  Pattern B: high systolic + high adherence → "possible treatment-review case"
    Suppress to "none" when ALL: slope < -0.3 AND recent_7day_avg < threshold AND
    days_since_med_change <= titration_window. Suppression must NOT apply when no recent med change.
  TITRATION_WINDOWS (from most recently changed drug in med_history):
    diuretics/beta-blockers → 14d | ACE/ARBs → 28d | amlodipine → 56d | default → 42d
  Pattern C: normal systolic + low adherence → "contextual review"

### Adaptive detection window (all four detectors)
  window_days = 28 (fallback) if next_appointment or last_visit_date is NULL or interval <= 0
  else: min(90, max(14, (next_appointment - last_visit_date).days))
  Floor 14: prevents degenerate windows. Cap 90: bounds computation.
  Log window_days_source ("adaptive" vs "fallback_default").
  When available readings < window_days, log window_truncated_to_available and use available range.

### White-coat exclusion (inertia + deterioration only)
  Filter out readings where effective_datetime >= (next_appointment − 5 days) before threshold comparison.
  5-day window covers the synthetic generator's 3-5 day dip window.
  When next_appointment IS NULL, no exclusion applied.

---

## RISK SCORING (Layer 2)

Score = weighted sum normalised to 0.0-100.0:
  (systolic_vs_baseline × 0.30) + (days_since_med_change_norm × 0.25) +
  ((100 - adherence_pct) × 0.20) + (gap_days_norm × 0.15) + (comorbidity_severity × 0.10)

comorbidity_severity (severity-weighted, clamped 0-100 — NEVER raw count / 5):
  CHF (I50), Stroke (I63/I64), TIA (G45): 25 pts each
  Diabetes (E11), CKD (N18), CAD (I25): 15 pts each
  Any other coded problem: 5 pts each

Normalisation (must match adaptive window):
  sig_gap     = clamp(gap_days / window_days × 100.0)        [NOT hardcoded /14]
  sig_inertia = clamp(days_since_med_change / 180.0 × 100.0) [saturates at 6 months, NOT 90 days]

Store risk_score AND risk_score_computed_at = now() on patients table.
Staleness badge when risk_score_computed_at > 26 hours ago.
Dashboard sorts: risk_tier first (High > Medium > Low), then risk_score DESC within tier.

---

## BRIEFING JSON STRUCTURE

briefings.llm_response JSONB fields:
  trend_summary: adaptive-window BP pattern (14-90 days) + 90-day trajectory from historic_bp_systolic
  medication_status: current regimen + last change date; append titration notice when within window
    titration_window drug-class-aware: diuretics/beta-blockers 14d, ACE/ARBs 28d, amlodipine 56d, default 42d
  adherence_summary: per-med rate + pattern (A/B/C) interpretation
  active_problems: list from clinical_context.active_problems
  problem_assessments: {problem_name: most_recent_assessment_text}
  overdue_labs: from clinical_context.overdue_labs + abnormal recent_labs flags
  visit_agenda: 3-6 items in priority order:
    1. Urgent alerts  2. Inertia  3. Adherence concern  4. Overdue labs
    5. Active problems review  6. Next appointment recommendation
  urgent_flags: active unacknowledged alerts (all types including adherence)
  risk_score: float (Layer 2)
  data_limitations: monitoring availability; cold-start notice if <21 days enrolled

---

## DEMO PATIENTS

Patient A — Therapeutic Inertia (1091, monitoring_active=TRUE)
  Risk tier: High (CHF auto-override I50.9)
  65 clinic BP anchors (2008-01-21 to 2013-09-26), parametric baseline ~134 mmHg
  Demo window elevated ~158 mmHg avg | 14 active medications | Adherence ~91% Beta-distributed
  Expected: sustained elevated 28-day avg, no med change since 2013 → treatment review warranted

Patient B — iEMR Only (1091, monitoring_active=FALSE)
  No home readings | Briefing from EHR only
  Expected: overdue lab flag, NSAID+antihypertensive interaction flag

---

## RISK TIER AUTO-OVERRIDES (at ingestion)

CHF → tier_override="CHF in problem list", risk_tier="high"
Stroke → tier_override="Stroke history", risk_tier="high"
TIA → tier_override="TIA history", risk_tier="high"

---

## SHADOW MODE VALIDATION

Target >=80% — ACHIEVED: 94.3% (33/35 points, 0 false negatives, 2 false positives)
Ground truth: PROBLEM_STATUS2_FLAG (3=stable, 2=concerned, 1=urgent)
Script: python scripts/run_shadow_mode.py --patient 1091 --iemr data/raw/iemr/1091_data.json
Results: data/shadow_mode_results.json
False positives documented in AUDIT.md Fix 1 and Fix 3 (resolved in production detectors).

---

## BACKGROUND WORKER

Poll processing_jobs WHERE status='queued' every 30 seconds.
Status: queued → running → succeeded | failed
Job types: pattern_recompute | briefing_generation | bundle_import
Idempotency: check idempotency_key before processing. Demo: POST /api/admin/trigger-scheduler

7:30 AM scheduler: briefing_generation for monitoring_active=TRUE AND next_appointment::DATE = today
  Idempotency key: "briefing_generation:{patient_id}:{YYYY-MM-DD}"
  Appointment date from patients.next_appointment (NOT parsed from idempotency key)

Midnight UTC (APScheduler): pattern_recompute for ALL monitoring_active=TRUE patients
  Idempotency key: "pattern_recompute:{patient_id}:{YYYY-MM-DD}"
  ON CONFLICT DO NOTHING — re-runs safe

Cold-start suppression (in pattern_recompute handler):
  If (now - enrolled_at).days < 21: skip inertia, deterioration, adherence detectors
  Set data_limitations = "Patient enrolled N days ago — minimum 21-day monitoring period required"
  Gap detector still runs. 21 days (not 14) ensures ≥7 home readings before first detector run.

---

## AUDIT REQUIREMENTS

Every action MUST create an audit_events row:
  bundle_import → resource_type="Bundle"
  reading_ingested → resource_type="Reading"
  briefing_viewed → resource_type="Briefing" + update briefings.read_at
  alert_acknowledged → resource_type="Alert"
  llm_validation → resource_type="Briefing", outcome="success"|"failure", details=failed_check
outcome must be "success" or "failure" — never omit.

---

## CODE STANDARDS

Python:
  Type hints on ALL public functions | Docstrings on ALL public classes and functions
  Async everywhere in API and DB layer | Use get_logger from app.utils.logging_utils
  ruff check app/ must pass before reporting done
  No bare except — catch specific exceptions | No hardcoded values — use config or constants

TypeScript:
  Strict mode, no any, all props typed with interfaces
  All API calls through src/lib/api.ts only | All shared types in src/lib/types.ts
  Tailwind utility classes only — no inline styles

Testing:
  Unit tests: fixture-based, no real patient data
  Integration tests: @pytest.mark.integration marker
  Run: python -m pytest tests/ -v -m "not integration"

# SPECIAL INSTRUCTION
Do not make any changes until you have 95% confidence in what you need to build. Ask me follow up questions until you reach that confidence.
