# ARIA v4.3 — Adaptive Real-time Intelligence Architecture
## Leap of Faith Technologies | IIT CS 595 | Spring 2026
## Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma

---

## GIT POLICY — NON-NEGOTIABLE

NEVER run git push under any circumstances.
NEVER run git commit on my behalf.
NEVER run git add on my behalf.
When implementation is complete, tell me:
  - Which files changed
  - What commit message to use
I will run all git commands myself.

---

## WHAT ARIA IS

A between-visit clinical intelligence platform for hypertension management.
A GP with 1800 patients has 8 minutes per consultation and no structured
view of what happened to their hypertensive patients since the last appointment.

ARIA fixes this by:
1. Ingesting patient EHR data via FHIR R4 Bundle
2. Generating clinically realistic synthetic home BP readings and medication confirmations spanning the patient's full care timeline
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
Gap detection, therapeutic inertia, adherence-BP correlation,
deterioration detection. Pure SQL queries, no AI, no LLM.
This is the foundation. Must be correct before Layers 2 and 3 run.

### Layer 2 — AI Risk Scoring (weighted scoring for prioritisation)
Computes a numeric priority score for each patient after Layer 1 completes.
Used to sort patients within each risk tier on the dashboard.
Inputs and weights:
  - 28-day avg systolic vs personal baseline:  30% weight
  - Days since last medication change (inertia): 25% weight
  - Adherence rate from confirmations:          20% weight
  - Gap duration (days without reading):        15% weight
  - Active comorbidity count (CHF, T2DM etc.):  10% weight
Output: risk_score float 0.0-100.0, stored on patient record.
Higher score = higher clinical priority within the same tier.
File: backend/app/services/pattern_engine/risk_scorer.py

### Layer 3 — AI Explanation (optional LLM readable summary)
Converts deterministic briefing JSON to 3-sentence readable summary.
Runs AFTER deterministic briefing is complete and correct.
Never generate LLM summary before Layer 1 briefing is verified.
Model: claude-sonnet-4-20250514
Prompt: prompts/briefing_summary_prompt.md
Log: model_version, prompt_hash, generated_at in briefing row.

### Layer 3 Output Validation (runs immediately after Layer 3)
Every LLM response must pass two checks before readable_summary is stored.
If either check fails: log warning, retry once, then store readable_summary=None.
Layer 1 briefing is always the authoritative output — Layer 3 is additive.
File: backend/app/services/briefing/llm_validator.py

Guardrails (absolute — payload irrelevant, phrase always blocked):
  "non-adherent", "non-compliant"         — use "possible adherence concern"
  "hypertensive crisis"                   — use "sustained elevated readings"
  "medication failure"                    — use "treatment review warranted"
  "increase.*mg" / "decrease.*mg" / "prescribe" — dosage recommendation forbidden
  "tell the patient"                      — patient-facing language forbidden
  "diagnos"                               — diagnostic claim forbidden
  "emergency"                             — escalation language forbidden
  Patient ID appearing verbatim           — PHI leak
  Prompt injection patterns               — "[INST]", "system:", "ignore previous"

Faithfulness validation (contextual — compared against Layer 1 payload):
  Sentence count must be exactly 3 (spec requirement)
  Risk score mentioned must match payload["risk_score"] ±10
  "adherence concern" requires Pattern A in payload["adherence_summary"]
  "treatment review" requires Pattern B in payload["adherence_summary"]
  "titration" requires "titration window" in payload["medication_status"]
  "urgent" requires non-empty payload["urgent_flags"]
  "lab" / "overdue" requires non-empty payload["overdue_labs"]
  Problem names mentioned must exist in payload["active_problems"] or payload["problem_assessments"]
  "insufficient data" requires non-empty payload["data_limitations"]
  Drug names mentioned must exist in payload["medication_status"]
  BP values mentioned must be 60-250 mmHg and within ±20 of payload trend data
  Urgent language with empty urgent_flags = contradiction → blocked

Audit: every validation result writes to audit_events
  action="llm_validation", resource_type="Briefing", outcome="success"|"failure"
  details=failed_check + reason on failure

### MVP AI Features (build both)
1. Risk scoring — Layer 2 weighted score
2. LLM explanation — Layer 3 readable summary

### Optional MVP Enhancements (build if time allows)
1. Adherence inference from reading patterns
2. Data quality detection (flag suspicious reading patterns)

### Out of Scope — Do Not Build
- Personalisation AI
- Vision AI for medication photos
- Advanced ML pipelines

---

## ENVIRONMENT

Conda environment: aria (Python 3.11)
Activate: conda activate aria

Backend root: backend/
Run: uvicorn app.main:app --reload --port 8000 (from backend/)
Tests: python -m pytest tests/ -v -m "not integration" (from backend/)
Lint: ruff check app/ (on PATH — never python -m ruff)
Format: ruff format app/

Frontend root: frontend/
Run: npm run dev (port 3000)

Database: PostgreSQL via Supabase
Connection: DATABASE_URL in backend/.env
Async driver: asyncpg
ORM: SQLAlchemy 2.0 async

---

## TECH STACK

Backend:    Python 3.11, FastAPI, SQLAlchemy 2.0 async, Pydantic v2
Database:   PostgreSQL (Supabase) — 8 tables, asyncpg driver
Frontend:   Next.js 14, TypeScript strict, Tailwind CSS, recharts
AI:         Anthropic claude-sonnet-4-20250514 (Layer 3 only)
Background: processing_jobs table + Python polling worker
Auth:       JWT, clinician/admin roles

---

## PYDANTIC V2 SYNTAX (critical — v1 breaks)

CORRECT:
  from pydantic_settings import BaseSettings, SettingsConfigDict
  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", extra="ignore")

WRONG (v1 — never use):
  class Config:
      env_file = ".env"

---

## SQLALCHEMY 2.0 ASYNC SYNTAX (critical — sync breaks)

CORRECT:
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select
  async def get_patient(session: AsyncSession, patient_id: str):
      result = await session.execute(
          select(Patient).where(Patient.patient_id == patient_id)
      )
      return result.scalar_one_or_none()

WRONG (never use):
  session.query(Patient).filter(...).first()

---

## PROJECT STRUCTURE

aria-platform/
  data/
    raw/iemr/           <- iEMR patient JSON (git excluded)
    fhir/bundles/       <- FHIR Bundle files (git excluded)
    synthetic/          <- generated readings (git excluded)

  backend/
    app/
      __init__.py
      config.py         <- Pydantic v2 settings, reads backend/.env
      main.py           <- FastAPI app, CORS, routers, lifespan
      db/
        __init__.py
        base.py         <- SQLAlchemy Base, async engine, session factory
        session.py      <- get_session FastAPI dependency
      models/           <- SQLAlchemy ORM models (8 tables)
        __init__.py
        patient.py
        clinical_context.py
        reading.py
        medication_confirmation.py
        alert.py
        briefing.py
        processing_job.py
        audit_event.py
      api/
        __init__.py
        patients.py     <- enrolment, patient list, tier assignment
        readings.py     <- POST /api/readings ingestion
        briefings.py    <- GET /api/briefings/{patient_id}
        alerts.py       <- GET /api/alerts[?patient_id=] (alert inbox; optional filter — Fix 24)
                          POST /api/alerts/{id}/acknowledge accepts optional disposition payload (Fix 42 L1)
        ingest.py       <- POST /api/ingest (FHIR Bundle)
        admin.py        <- POST /api/admin/trigger-scheduler (demo mode)
        shadow_mode.py  <- GET /api/shadow-mode (ARIA vs physician agreement results)
      services/
        __init__.py
        fhir/
          __init__.py
          adapter.py          <- iEMR JSON -> FHIR Bundle
          ingestion.py        <- FHIR Bundle -> 8 PostgreSQL tables
          validator.py        <- validate FHIR Bundle structure
        generator/
          __init__.py
          reading_generator.py      <- synthetic full-timeline home BP (inter-visit interpolation, parametric baseline)
          confirmation_generator.py <- synthetic full-timeline medication confirmations (Beta-distributed adherence per interval)
        pattern_engine/
          __init__.py
          gap_detector.py           <- Layer 1: gap detection SQL
          inertia_detector.py       <- Layer 1: therapeutic inertia SQL
          adherence_analyzer.py     <- Layer 1: adherence-BP correlation SQL
          deterioration_detector.py <- Layer 1: sustained worsening trend
          variability_detector.py   <- Layer 1: BP coefficient of variation detector (Phase 2)
          risk_scorer.py            <- Layer 2: weighted priority score
          threshold_utils.py        <- shared patient-adaptive threshold + comorbidity adjustment (all 4 detectors)
        briefing/
          __init__.py
          composer.py     <- deterministic briefing JSON (Layer 1 output)
          summarizer.py   <- optional LLM summary (Layer 3)
          llm_validator.py <- Layer 3 output guardrails + faithfulness validation
        worker/
          __init__.py
          processor.py    <- processing_jobs polling loop (30s interval)
          scheduler.py    <- 7:30 AM briefing enqueue + manual trigger
      utils/
        __init__.py
        datetime_utils.py  <- timezone-aware datetime helpers
        fhir_utils.py      <- FHIR resource helpers
        clinical_utils.py  <- BP classification, risk scoring helpers
        logging_utils.py   <- get_logger(name) -> Logger
    tests/
      __init__.py
      test_fhir_adapter.py
      test_ingestion.py
      test_reading_generator.py
      test_pattern_engine.py
      test_risk_scorer.py
      test_briefing_composer.py
      test_llm_validator.py  <- Layer 3 output validation + guardrails (51 tests)
      test_api.py
      test_integration.py    <- @pytest.mark.integration

  frontend/
    src/
      app/
        page.tsx layout.tsx
        patients/ page.tsx [id]/page.tsx
      components/
        dashboard/
          PatientList.tsx      <- patients sorted by risk_score within tier
          RiskTierBadge.tsx    <- High/Medium/Low badge
          RiskScoreBar.tsx     <- visual priority bar (Layer 2 output)
          AlertInbox.tsx       <- urgent unacknowledged alerts
        briefing/
          BriefingCard.tsx     <- full pre-visit briefing
          SparklineChart.tsx   <- 28-day BP trend (recharts)
          AdherenceSummary.tsx <- adherence rate per medication
          VisitAgenda.tsx      <- prioritised visit items
        shared/
          PatientHeader.tsx
          LoadingSpinner.tsx
      lib/
        api.ts types.ts auth.ts

  scripts/
    run_adapter.py     <- convert iEMR JSON to FHIR Bundle
    run_ingestion.py   <- ingest FHIR Bundle into PostgreSQL
    run_generator.py   <- generate synthetic readings + confirmations for full patient timeline
    run_worker.py      <- start background processing worker
    run_scheduler.py   <- manually trigger 7:30 AM logic
    run_shadow_mode.py <- validate ARIA vs physician notes (target 80%)
    setup_db.py        <- create all tables and indexes

  prompts/
    briefing_summary_prompt.md  <- Layer 3 LLM system prompt

  reports/
    sprint-1/
    sprint-2/

---

## DATABASE — 8 TABLES (PostgreSQL via Supabase)

### patients
patient_id          TEXT PRIMARY KEY        FHIR Patient.id / iEMR MED_REC_NO
gender              CHAR(1)                 M | F | U
age                 SMALLINT
risk_tier           TEXT NOT NULL           high | medium | low
tier_override       TEXT                    e.g. "CHF in problem list"
risk_score          NUMERIC(5,2)            Layer 2 score 0.0-100.0
risk_score_computed_at TIMESTAMPTZ         set on every risk_score update; frontend shows staleness badge if > 26 hours
monitoring_active   BOOLEAN DEFAULT TRUE    FALSE = EHR-only pathway
next_appointment    TIMESTAMPTZ
enrolled_at         TIMESTAMPTZ DEFAULT NOW()
enrolled_by         TEXT

### clinical_context (one row per patient, pre-computed at ingestion)
patient_id          TEXT PRIMARY KEY REFERENCES patients
active_problems     TEXT[]                  parallel to problem_codes
problem_codes       TEXT[]                  ICD-10 or SNOMED
current_medications TEXT[]                  parallel to med_rxnorm_codes
med_rxnorm_codes    TEXT[]
med_history         JSONB               full medication timeline,
                                        list of {name, rxnorm, date, activity}
                                        sorted chronologically, deduped by
                                        (name, date, activity)
last_med_change     DATE
allergies           TEXT[]
last_visit_date     DATE
last_clinic_systolic   SMALLINT
last_clinic_diastolic  SMALLINT
historic_bp_systolic   SMALLINT[]           all clinic readings chronologically
historic_bp_dates      DATE[]               parallel to historic_bp_systolic
overdue_labs        TEXT[]
social_context      TEXT
problem_assessments JSONB               per-problem visit assessment history,
                                        list of {problem_code, visit_date, htn_flag,
                                        status_text, assessment_text} sorted by date DESC
recent_labs         JSONB               latest lab values: creatinine, K+, HbA1c, eGFR
                                        {loinc_code: {value, unit, date}}
last_clinic_pulse      SMALLINT         bpm from last BP visit (LOINC 8867-4)
last_clinic_weight_kg  NUMERIC(5,1)     kg from last BP visit (LOINC 29463-7)
last_clinic_spo2       NUMERIC(4,1)     % from last visit (LOINC 59408-5)
historic_spo2          NUMERIC[]        all clinic SpO2 readings chronologically
allergy_reactions   TEXT[]              parallel to allergies — reaction type per allergy
last_updated        TIMESTAMPTZ DEFAULT NOW()

### readings (home BP readings + generated)
reading_id          UUID PK DEFAULT gen_random_uuid()
patient_id          TEXT REFERENCES patients
systolic_1          SMALLINT NOT NULL
diastolic_1         SMALLINT NOT NULL
heart_rate_1        SMALLINT
systolic_2          SMALLINT               NULL if single reading
diastolic_2         SMALLINT
heart_rate_2        SMALLINT
systolic_avg        NUMERIC(5,1) NOT NULL  primary analysis value
diastolic_avg       NUMERIC(5,1) NOT NULL
heart_rate_avg      NUMERIC(5,1)
effective_datetime  TIMESTAMPTZ NOT NULL
session             TEXT NOT NULL           morning | evening | ad_hoc
                                            clinic ingestion uses ad_hoc
source              TEXT NOT NULL           generated | manual | ble_auto | clinic
                                            clinic = inserted by FHIR ingestion
                                            used by idempotency COUNT check
submitted_by        TEXT NOT NULL           patient | carer | generator | clinic
bp_position         TEXT                    seated | standing
bp_site             TEXT                    left_arm | right_arm
consent_version     TEXT NOT NULL DEFAULT '1.0'
medication_taken    TEXT                    yes | no | partial | NULL
created_at          TIMESTAMPTZ DEFAULT NOW()

### medication_confirmations
confirmation_id     UUID PK DEFAULT gen_random_uuid()
patient_id          TEXT REFERENCES patients
medication_name     TEXT NOT NULL
rxnorm_code         TEXT
scheduled_time      TIMESTAMPTZ NOT NULL
confirmed_at        TIMESTAMPTZ            NULL = missed dose
confirmation_type   TEXT                   synthetic_demo | tap | photo | qr_scan
confidence          TEXT NOT NULL DEFAULT 'self_report'
minutes_from_schedule SMALLINT
created_at          TIMESTAMPTZ DEFAULT NOW()

### alerts
alert_id            UUID PK DEFAULT gen_random_uuid()
patient_id          TEXT REFERENCES patients
alert_type          TEXT NOT NULL           gap_urgent | gap_briefing | inertia | deterioration | adherence
gap_days            SMALLINT
systolic_avg        NUMERIC(5,1)
triggered_at        TIMESTAMPTZ DEFAULT NOW()
delivered_at        TIMESTAMPTZ
acknowledged_at     TIMESTAMPTZ

### briefings
briefing_id         UUID PK DEFAULT gen_random_uuid()
patient_id          TEXT REFERENCES patients
appointment_date    DATE NOT NULL
llm_response        JSONB NOT NULL          structured briefing payload
model_version       TEXT                    Layer 3 model used (e.g. claude-sonnet-4-20250514)
prompt_hash         TEXT                    SHA-256 of Layer 3 system prompt (audit traceability)
generated_at        TIMESTAMPTZ DEFAULT NOW()
delivered_at        TIMESTAMPTZ
read_at             TIMESTAMPTZ

### processing_jobs
job_id              UUID PK DEFAULT gen_random_uuid()
job_type            TEXT NOT NULL           pattern_recompute | briefing_generation | bundle_import
patient_id          TEXT REFERENCES patients
idempotency_key     TEXT NOT NULL UNIQUE
status              TEXT NOT NULL           queued | running | succeeded | failed
payload_ref         TEXT
error_message       TEXT
queued_at           TIMESTAMPTZ DEFAULT NOW()
started_at          TIMESTAMPTZ
finished_at         TIMESTAMPTZ
created_by          TEXT NOT NULL DEFAULT 'system'

### audit_events
audit_id            UUID PK DEFAULT gen_random_uuid()
actor_type          TEXT NOT NULL           system | clinician | admin
actor_id            TEXT
patient_id          TEXT REFERENCES patients
action              TEXT NOT NULL           bundle_import | reading_ingested | briefing_viewed | alert_acknowledged | llm_validation
resource_type       TEXT NOT NULL
resource_id         TEXT
outcome             TEXT NOT NULL           success | failure
request_id          TEXT
event_timestamp     TIMESTAMPTZ DEFAULT NOW()
details             TEXT

---

## CRITICAL INDEXES (create before any data is inserted)

CREATE UNIQUE INDEX idx_processing_jobs_idempotency
  ON processing_jobs (idempotency_key);

CREATE INDEX idx_readings_patient_datetime
  ON readings (patient_id, effective_datetime DESC);

CREATE INDEX idx_readings_patient_session
  ON readings (patient_id, session, effective_datetime DESC);

CREATE INDEX idx_alerts_undelivered
  ON alerts (patient_id, delivered_at)
  WHERE delivered_at IS NULL;

CREATE INDEX idx_patients_appointment
  ON patients (next_appointment)
  WHERE monitoring_active = TRUE;

CREATE INDEX idx_cc_problem_codes
  ON clinical_context USING GIN (problem_codes);

CREATE INDEX idx_confirmations_patient_scheduled
  ON medication_confirmations (patient_id, scheduled_time DESC);

CREATE INDEX idx_confirmations_missed
  ON medication_confirmations (patient_id, scheduled_time)
  WHERE confirmed_at IS NULL;

CREATE INDEX idx_processing_jobs_status_type
  ON processing_jobs (status, job_type, queued_at);

CREATE INDEX idx_audit_events_patient_time
  ON audit_events (patient_id, event_timestamp DESC);

CREATE INDEX idx_patients_risk_score
  ON patients (risk_tier, risk_score DESC);

CREATE UNIQUE INDEX idx_readings_patient_datetime_source
  ON readings (patient_id, effective_datetime, source);

CREATE UNIQUE INDEX idx_confirmations_patient_med_scheduled
  ON medication_confirmations (patient_id, medication_name, scheduled_time);

-- Migrations (added after initial schema) — run via setup_db.py ADD COLUMN IF NOT EXISTS, safe to re-run:
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS med_history JSONB;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS problem_assessments JSONB;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS recent_labs JSONB;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_pulse SMALLINT;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_weight_kg NUMERIC(5,1);
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_spo2 NUMERIC(4,1);
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS historic_spo2 NUMERIC[];
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS allergy_reactions TEXT[];
ALTER TABLE patients ADD COLUMN IF NOT EXISTS risk_score_computed_at TIMESTAMPTZ;

---

## SYNTHETIC DATA RULES (never violate — clinical reviewer will catch errors)

Day-to-day systolic SD:    8-12 mmHg (NEVER less than 5 — flat variance is wrong)
Morning/evening diff:      morning 5-10 mmHg HIGHER than evening every week
Round numbers:             NEVER exactly round — real readings: 153, 161, 148, 167
Two-reading session:       readings 1 and 2 differ by 2-6 mmHg (2 slightly lower)
Diastolic:                 systolic × 0.60-0.66
Heart rate:                64-82 bpm, slight negative correlation with systolic
                           when beta-blocker in regimen (metoprolol effect)
Device outage:             1-2 episodes of 2-4 days per inter-visit interval
                           ABSENT ROWS — never null values in readings table
White-coat dip:            systolic drops 10-15 mmHg in 3-5 days before appointment
Post-appointment return:   readings return to elevated baseline after dip

Generation scope — full care timeline (not just 28 days):
  For each consecutive pair of clinic readings, generate daily synthetic readings
  by linearly interpolating between the two BP anchors with Gaussian noise.
  Baseline derived from median(clinical_context.historic_bp_systolic) — NOT hardcoded.
  Falls back to PATIENT_A_MORNING_MEAN = 163.0 only when fewer than 2 clinic readings exist.
  Patient 1091 has 65 clinic readings (2008-01-21 to 2013-09-26, mean 133.8 mmHg, SD 16.2).
  The elevated demo window (~158 mmHg avg) reflects the 2011-2013 period used in the briefing.

Confirmation scope — full care timeline (not just 28 days):
  For each inter-visit interval, generate confirmations for every medication active
  during that interval, derived from clinical_context.med_history (not hardcoded med list).
  Per-interval adherence drawn from Beta distribution anchored near patient's known overall
  adherence (≈91% for patient 1091) with ±10-15 percentage point interval-to-interval variation.
  Idempotency: unique constraint on (patient_id, medication_name, scheduled_time).

Prerequisite before full-timeline generation:
  readings table must have UNIQUE INDEX on (patient_id, effective_datetime, source)
  so re-running generator uses ON CONFLICT DO NOTHING (not batch-level skip).

---

## FHIR R4 MAPPING (iEMR -> FHIR)

PROBLEM.value + PROBLEM_CODE  -> Condition
  clinicalStatus=active, code.text=problem name, code.coding[0].code=ICD-10

MEDICATIONS (all visits)      -> _aria_med_history (non-FHIR bundle key)
  Not a FHIR resource. Passed as bundle["_aria_med_history"].
  Full medication timeline across all visits, distinct from
  MedicationRequest resources (current med list, most-recent-wins).
  Consumed by ingestion.py, stored in clinical_context.med_history.

MEDICATIONS.*                 -> MedicationRequest (current med list only)
  medicationCodeableConcept from MED_NAME+MED_DOSE
  authoredOn from MED_DATE_ADDED
  RxNorm from code_mappings
  Deduplication: most-recent-wins by MED_CODE across all visits.

VITALS SYSTOLIC+DIASTOLIC     -> Observation
  LOINC 55284-4 (BP panel)
  component 8480-6 (systolic), component 8462-4 (diastolic)
  effectiveDateTime from VITALS_DATETIME (NOT ADMIT_DATE)

VITALS PULSE/WEIGHT/SpO2/TEMP -> Observation (additional, same visit)
  LOINC 8867-4 (pulse), 29463-7 (weight kg), 59408-5 (SpO2 %), 8310-5 (temperature)
  stored in clinical_context last_clinic_pulse/weight_kg/spo2, historic_spo2[]
  SpO2 < 92% in a CHF patient triggers a visit agenda alert

PROBLEM assessments (all visits) -> _aria_problem_assessments (non-FHIR bundle key)
  Not a FHIR resource. Appended to bundle root as bundle["_aria_problem_assessments"].
  Collected per-visit: {problem_code, visit_date, htn_flag, status_text, assessment_text}
  Stored in clinical_context.problem_assessments JSONB.
  Used by briefing composer to surface most recent assessment per active problem.

VISIT dates (all visits)      -> _aria_visit_dates (non-FHIR bundle key)
  Not a FHIR resource. List of ADMIT_DATE values from all 124 visits regardless of type.
  Used to set clinical_context.last_visit_date = max(all_visit_dates).
  Currently last_visit_date only reflects BP clinic dates — this corrects that.

ALLERGY                       -> AllergyIntolerance
  Filter: ALLERGY_STATUS == "Active" only — inactive allergies must not appear in briefing
  Include: ALLERGY_REACTION stored in reaction[0].manifestation[0].text
  Stored in clinical_context.allergy_reactions[] parallel to allergies[]

PLAN where PLAN_NEEDS_FOLLOWUP=YES -> ServiceRequest

---

## PATTERN ENGINE QUERIES (Layer 1)

### Gap detection
SELECT EXTRACT(EPOCH FROM (NOW() - MAX(effective_datetime))) / 86400 AS gap_days
FROM readings WHERE patient_id = $1;
Thresholds: High tier flag>=1 urgent>=3 | Medium flag>=3 urgent>=5 | Low flag>=7 urgent>=14

### Therapeutic inertia (ALL conditions required)
AVG(systolic_avg) >= patient_threshold over the adaptive window (see below)
  patient_threshold = max(130, stable_baseline_mean + 1.5×SD) capped at 145 mmHg
  derived from historic_bp_systolic filtered to physician-labeled stable visits
  falls back to 140 if fewer than 3 stable-labeled readings exist
  comorbidity adjustment: threshold lowered by 7 mmHg (floor 130) when EITHER
    (a) cardiovascular AND metabolic comorbidities both in elevated concern state, OR
    (b) any severe-weight comorbidity (CHF/Stroke/TIA) in elevated concern state
  full mode uses clinical_context.problem_assessments status flags (post Fix 7)
  degraded mode (pre Fix 7) uses clinical_context.active_problems presence
  log threshold_adjustment_mode per computation
Slope direction check: if 7-day recent avg < patient_threshold, do NOT fire (BP declining)
COUNT(*) >= 5 readings with systolic_avg >= patient_threshold
NOW() - MIN(effective_datetime) > 7 days
Most recent med change date from clinical_context.med_history JSONB < MIN(effective_datetime) OR NULL
  (NOT clinical_context.last_med_change — that is a single stale ingestion-time snapshot)
  reads max({date}) across all med_history entries where date <= first_elevated_reading_date
  uses the activity field (add|modify|remove) — dose direction parsing (increase/decrease)
  deferred to a dedicated dose_parser.py module

### Deterioration (three gates required)
Positive linear slope across the adaptive window
Recent 3-day avg > baseline days 4-10 avg
recent_avg >= patient_threshold (absolute gate — prevents firing on 115→119 rise)
Step-change sub-detector: if (7-day recent mean) - (7-day mean three weeks ago) >= 15 mmHg,
  flag deterioration regardless of overall linear slope
patient_threshold derived identically to the inertia detector (including comorbidity adjustment)

### Adherence-BP correlation
Join readings and medication_confirmations on same date.
adherence_pct = COUNT(confirmed_at) / COUNT(*) * 100
Clinical threshold: < 80% flags non-adherence
Pattern A: high systolic + low adherence -> "possible adherence concern" -> write alert row
Pattern B: high systolic + high adherence -> "possible treatment-review case"
  Suppress Pattern B to "none" when ALL of: slope < -0.3 AND recent_7day_avg < patient_threshold
    AND days_since_med_change <= titration_window (drug-class-aware — see TITRATION_WINDOWS)
  TITRATION_WINDOWS lookup (derive from most recently changed drug in med_history):
    diuretics → 14 days, beta-blockers → 14 days, ACE inhibitors/ARBs → 28 days,
    long-acting CCBs (amlodipine) → 56 days, default → 42 days
  Without the med-change gate, a noise-driven negative slope on a persistently-elevated
  patient incorrectly suppresses a real treatment-review signal.
  Suppression must not apply when no recent med change exists — that is NOT a succeeding treatment.
Pattern C: normal systolic + low adherence -> "contextual review"

### Adaptive detection window (all four detectors)
if next_appointment IS NULL or last_visit_date IS NULL or interval <= 0:
  window_days = 28 (fallback default)
else:
  window_days = min(90, max(14, (next_appointment - last_visit_date).days))
Replaces hardcoded _WINDOW_DAYS = 28 in gap, inertia, adherence, and deterioration queries.
Floor 14: prevents degenerate short windows. Cap 90: bounds computation.
Log window_days_source ("adaptive" vs "fallback_default").
When available readings < window_days, log window_truncated_to_available and use available range
  (conservative behaviour; benefits silently once Fix 15 full-timeline readings land).

### White-coat exclusion (inertia + deterioration only)
After querying readings, filter out rows where effective_datetime >= (next_appointment - 5 days)
before any threshold comparison. 5-day window aligns with the synthetic generator's dip rule
(10-15 mmHg drop 3-5 days before appointment) — a 3-day window leaks dip-influenced days 4-5
into threshold computation. When next_appointment IS NULL, no exclusion is applied.
Excluded readings remain in the DB and are visible in the briefing trend — they are only
excluded from detector threshold computations.

---

## RISK SCORING (Layer 2)

risk_scorer.py runs after all Layer 1 detectors complete.
Inputs: Layer 1 outputs + clinical_context data
Score = weighted sum normalised to 0.0-100.0:
  (systolic_vs_baseline * 0.30) +
  (days_since_med_change * 0.25) +
  (100 - adherence_pct) * 0.20 +
  (gap_days_normalised * 0.15) +
  (comorbidity_severity_score * 0.10)

comorbidity_severity_score — severity-weighted, clamped 0-100 (NEVER raw count / 5):
  CHF (I50), Stroke (I63/I64), TIA (G45):  25 points each
  Diabetes (E11), CKD (N18), CAD (I25):    15 points each
  Any other coded problem:                    5 points each

Normalisation (Fix 58 — must match adaptive window):
  sig_gap     = clamp(gap_days / window_days * 100.0)         — window_days from adaptive window, NOT hardcoded / 14
  sig_inertia = clamp(days_since_med_change / 180.0 * 100.0)  — saturates at 6 months (NOT 90 days)

Store in patients.risk_score AND patients.risk_score_computed_at = now().
Frontend shows staleness badge when risk_score_computed_at > 26 hours ago (single missed sweep tolerable, second is not).
Dashboard PatientList sorts by: risk_tier first (High > Medium > Low),
then risk_score DESC within each tier.

---

## BRIEFING JSON STRUCTURE (deterministic Layer 1 output)

briefings.llm_response JSONB:
{
  "trend_summary": str,          <- adaptive-window BP pattern (14-90 days based on inter-visit interval)
                                    + 90-day trajectory from historic_bp_systolic where available
  "medication_status": str,      <- current regimen, last change date;
                                    when days_since_med_change <= titration_window append
                                    "— within expected titration window, full response may not yet be established"
                                    titration_window is drug-class-aware (TITRATION_WINDOWS lookup):
                                    diuretics/beta-blockers → 14d, ACE/ARBs → 28d, amlodipine → 56d, default → 42d
  "adherence_summary": str,      <- rate per medication + pattern interpretation
  "active_problems": list,       <- from clinical_context.active_problems
  "problem_assessments": dict,   <- {problem_name: most_recent_assessment_text} from clinical_context.problem_assessments
  "overdue_labs": list,          <- from clinical_context.overdue_labs + recent_labs abnormal flags
  "visit_agenda": list,          <- prioritised 3-6 items
  "urgent_flags": list,          <- active unacknowledged alerts (including adherence type)
  "risk_score": float,           <- Layer 2 score
  "data_limitations": str        <- whether home monitoring available; cold-start suppression notice if < 21 days enrolled
}

visit_agenda priority order:
  1. Urgent alerts
  2. Inertia flag
  3. Adherence concern
  4. Overdue labs
  5. Active problems review
  6. Next appointment recommendation

---

## DEMO PATIENTS

Patient A — Therapeutic Inertia (patient 1091 iEMR, monitoring_active=TRUE)
  Risk tier: High (CHF auto-override, ICD-10 I50.9)
  Full care timeline synthetic readings spanning 2008-01-21 to 2013-09-26 (65 clinic BP anchors)
  Parametric baseline: median(historic_bp_systolic) ≈ 134 mmHg; demo window elevated ~158 mmHg
  14 active medications including Metoprolol, Lisinopril, Lasix (derived from med_history)
  Adherence: ~91% per interval, Beta-distributed with ±10-15% interval-to-interval variation
  Expected briefing: sustained elevated 28-day avg ~158 mmHg, no med change since 2013,
                     high adherence signal → treatment review warranted (not adherence concern)

Patient B — iEMR Only (patient 1091, monitoring_active=FALSE)
  No home readings generated
  Briefing from EHR data alone
  Expected briefing: overdue lab flag, NSAID+antihypertensive interaction flag

---

## RISK TIER AUTO-OVERRIDES (apply at ingestion)

CHF in problem_codes -> tier_override="CHF in problem list", force risk_tier="high"
Stroke in problem_codes -> tier_override="Stroke history", force risk_tier="high"
TIA in problem_codes -> tier_override="TIA history", force risk_tier="high"

---

## SHADOW MODE VALIDATION

Target: >= 80% agreement — ACHIEVED: 94.3% (33/35 labelled evaluation points, 0 false negatives, 2 false positives)
Ground truth: PROBLEM_STATUS2_FLAG (3=Green/stable, 2=Yellow/concerned, 1=Red/urgent)
              PROBLEM_STATUS2 text and PROBLEM_ASSESSMENT_TEXT for narrative context
Evaluation points: BP clinic dates + no-vitals iEMR visits with explicit HTN flag and non-empty assessment
Script: python scripts/run_shadow_mode.py --patient 1091 --iemr data/raw/iemr/1091_data.json
  --patient defaults to 1091, --iemr defaults to matching path
  Results written to data/shadow_mode_results.json
False negatives must be reviewed before demo sign-off.
The 2 false positives are documented in AUDIT.md Fix 1 and Fix 3 (now resolved in production detectors).

---

## BACKGROUND WORKER

Poll processing_jobs WHERE status='queued' every 30 seconds.
Status flow: queued -> running -> succeeded | failed
Job types: pattern_recompute | briefing_generation | bundle_import
Idempotency: check idempotency_key before processing.
Demo mode: POST /api/admin/trigger-scheduler fires scheduler on demand.

7:30 AM scheduler: enqueues briefing_generation for appointment-day patients.
  Query: monitoring_active=TRUE AND next_appointment::DATE = today
  Idempotency key: "briefing_generation:{patient_id}:{YYYY-MM-DD}"
  Appointment date sourced from patients.next_appointment (NOT parsed from idempotency key)

Midnight UTC scheduler (APScheduler): enqueues pattern_recompute for ALL active patients.
  Query: ALL monitoring_active=TRUE patients
  Idempotency key: "pattern_recompute:{patient_id}:{YYYY-MM-DD}"
  Re-runs are safe — ON CONFLICT DO NOTHING on idempotency_key
  Ensures gap counters, risk scores, and inertia flags stay current for non-appointment-day patients

Cold-start suppression (in pattern_recompute handler):
  If (now - enrolled_at).days < 21: skip inertia, deterioration, adherence detectors
  Set data_limitations = "Patient enrolled N days ago — minimum 21-day monitoring period required"
  Gap detector still runs — zero readings in first week is itself a gap signal
  21-day (not 14) avoids a cliff-edge with the adaptive window floor of 14 days — guarantees
  at least 7 days of home readings exist before any detector first runs, even on weekly-visit
  patients where the adaptive window floors to 14

---

## AUDIT REQUIREMENTS

Every action below MUST create an audit_events row:
  bundle_import -> action="bundle_import", resource_type="Bundle"
  reading_ingested -> action="reading_ingested", resource_type="Reading"
  briefing_viewed -> action="briefing_viewed" + update briefings.read_at
  alert_acknowledged -> action="alert_acknowledged"
  llm_validation -> action="llm_validation", resource_type="Briefing"
                    outcome="success"|"failure", details=failed_check on failure
outcome must be "success" or "failure" — never omit.

---

## CODE STANDARDS

Python:
  - Type hints on ALL public functions
  - Docstrings on ALL public classes and functions
  - Async everywhere in API and DB layer (no sync SQLAlchemy)
  - Use get_logger from app.utils.logging_utils
  - ruff check app/ must pass before reporting done
  - No bare except — catch specific exceptions
  - No hardcoded values — use config or constants

TypeScript:
  - Strict mode, no any, all props typed with interfaces
  - All API calls through src/lib/api.ts only
  - All shared types in src/lib/types.ts
  - Tailwind utility classes only — no inline styles

Testing:
  - Unit tests: fixture-based, no real patient data
  - Integration tests: @pytest.mark.integration marker
  - Run unit: python -m pytest tests/ -v -m "not integration"
