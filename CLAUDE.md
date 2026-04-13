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
2. Generating clinically realistic synthetic home BP readings (28 days)
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
        alerts.py       <- GET /api/alerts (alert inbox)
        ingest.py       <- POST /api/ingest (FHIR Bundle)
        admin.py        <- POST /api/admin/trigger-scheduler (demo mode)
      services/
        __init__.py
        fhir/
          __init__.py
          adapter.py          <- iEMR JSON -> FHIR Bundle
          ingestion.py        <- FHIR Bundle -> 8 PostgreSQL tables
          validator.py        <- validate FHIR Bundle structure
        generator/
          __init__.py
          reading_generator.py      <- synthetic 28-day home BP
          confirmation_generator.py <- synthetic medication confirmations
        pattern_engine/
          __init__.py
          gap_detector.py           <- Layer 1: gap detection SQL
          inertia_detector.py       <- Layer 1: therapeutic inertia SQL
          adherence_analyzer.py     <- Layer 1: adherence-BP correlation SQL
          deterioration_detector.py <- Layer 1: sustained worsening trend
          risk_scorer.py            <- Layer 2: weighted priority score
        briefing/
          __init__.py
          composer.py     <- deterministic briefing JSON (Layer 1 output)
          summarizer.py   <- optional LLM summary (Layer 3)
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
    run_generator.py   <- generate synthetic readings for a patient
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
alert_type          TEXT NOT NULL           gap_urgent | gap_briefing | inertia | deterioration
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
action              TEXT NOT NULL           bundle_import | reading_ingested | briefing_viewed | alert_acknowledged
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

-- Migration (added after initial schema) — run via setup_db.py, not a CREATE INDEX:
ALTER TABLE clinical_context
  ADD COLUMN IF NOT EXISTS med_history JSONB;

---

## SYNTHETIC DATA RULES (never violate — clinical reviewer will catch errors)

Day-to-day systolic SD:    8-12 mmHg (NEVER less than 5 — flat variance is wrong)
Morning/evening diff:      morning 5-10 mmHg HIGHER than evening every week
Round numbers:             NEVER exactly round — real readings: 153, 161, 148, 167
Two-reading session:       readings 1 and 2 differ by 2-6 mmHg (2 slightly lower)
Diastolic:                 systolic × 0.60-0.66
Heart rate:                64-82 bpm, slight negative correlation with systolic
                           when beta-blocker in regimen (metoprolol effect)
Device outage:             1-2 episodes of 2-4 days per 28 days
                           ABSENT ROWS — never null values in readings table
White-coat dip:            systolic drops 10-15 mmHg in 3-5 days before appointment
Post-appointment return:   readings return to elevated baseline after dip

Patient A scenario (anchored on patient 1091 real clinic BPs 185/72 and 180/73):
  Days 1-7:   Baseline. Morning Gaussian ~163 mmHg SD=8. Evening 6-9 lower.
  Days 8-14:  Inertia develops. Systolic drifts to ~165. One missed evening (Saturday).
  Days 15-18: Continued elevation 164-167. Device outage days 16-17.
  Days 19-21: Pre-appointment dip to 148-153. Gradual decline, not sudden.
  Days 22-28: Return to 160-166. Weekend misses days 25-26.
Adherence signal: 91% synthetic confirmations across all three medications.

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

ALLERGY                       -> AllergyIntolerance
PLAN where PLAN_NEEDS_FOLLOWUP=YES -> ServiceRequest

---

## PATTERN ENGINE QUERIES (Layer 1)

### Gap detection
SELECT EXTRACT(EPOCH FROM (NOW() - MAX(effective_datetime))) / 86400 AS gap_days
FROM readings WHERE patient_id = $1;
Thresholds: High tier flag>=1 urgent>=3 | Medium flag>=3 urgent>=5 | Low flag>=7 urgent>=14

### Therapeutic inertia (ALL conditions required)
AVG(systolic_avg) >= 140 over last 28 days
COUNT(*) >= 5 elevated readings
NOW() - MIN(effective_datetime) > 7 days
last_med_change < MIN(effective_datetime) OR last_med_change IS NULL

### Adherence-BP correlation
Join readings and medication_confirmations on same date.
adherence_pct = COUNT(confirmed_at) / COUNT(*) * 100
Clinical threshold: < 80% flags non-adherence
Pattern A: high systolic + low adherence -> "possible adherence concern"
Pattern B: high systolic + high adherence -> "possible treatment-review case"
Pattern C: normal systolic + low adherence -> "contextual review"

---

## RISK SCORING (Layer 2)

risk_scorer.py runs after all Layer 1 detectors complete.
Inputs: Layer 1 outputs + clinical_context data
Score = weighted sum normalised to 0.0-100.0:
  (systolic_vs_baseline * 0.30) +
  (days_since_med_change * 0.25) +
  (100 - adherence_pct) * 0.20 +
  (gap_days_normalised * 0.15) +
  (comorbidity_count * 0.10)

Store in patients.risk_score.
Dashboard PatientList sorts by: risk_tier first (High > Medium > Low),
then risk_score DESC within each tier.

---

## BRIEFING JSON STRUCTURE (deterministic Layer 1 output)

briefings.llm_response JSONB:
{
  "trend_summary": str,       <- 28-day BP pattern
  "medication_status": str,   <- current regimen, last change date
  "adherence_summary": str,   <- rate per medication + pattern interpretation
  "active_problems": list,    <- from clinical_context.active_problems
  "overdue_labs": list,       <- from clinical_context.overdue_labs
  "visit_agenda": list,       <- prioritised 3-6 items
  "urgent_flags": list,       <- active unacknowledged alerts
  "risk_score": float,        <- Layer 2 score
  "data_limitations": str     <- whether home monitoring available
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
  Risk tier: High (CHF auto-override)
  28-day synthetic readings anchored on clinic BPs 185/72 and 180/73
  Medications: Metoprolol, Lisinopril, Lasix
  Adherence signal: 91%
  Expected briefing: sustained elevated BP avg 163/101 over 21 days,
                     no med change, high adherence signal,
                     likely treatment failure not adherence concern

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

Target: >= 80% agreement between ARIA alert output and physician assessment
Ground truth: PROBLEM_STATUS2_FLAG (3=Green, 2=Yellow, 1=Red)
              PROBLEM_ASSESSMENT_TEXT for narrative context
Script: scripts/run_shadow_mode.py
False negatives must be reviewed before demo sign-off.

---

## BACKGROUND WORKER

Poll processing_jobs WHERE status='queued' every 30 seconds.
Status flow: queued -> running -> succeeded | failed
Job types: pattern_recompute | briefing_generation | bundle_import
Idempotency: check idempotency_key before processing.
Demo mode: POST /api/admin/trigger-scheduler fires scheduler on demand.
7:30 AM scheduler: enqueues briefing_generation for appointment-day patients.

---

## AUDIT REQUIREMENTS

Every action below MUST create an audit_events row:
  bundle_import -> action="bundle_import", resource_type="Bundle"
  reading_ingested -> action="reading_ingested", resource_type="Reading"
  briefing_viewed -> action="briefing_viewed" + update briefings.read_at
  alert_acknowledged -> action="alert_acknowledged"
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
