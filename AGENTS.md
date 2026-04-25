# ARIA v4.3 — Global Agent Context
## Leap of Faith Technologies | IIT CS 595 | Spring 2026
## Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma
## Demo: Thursday 2026-04-24

---

## FIRST ACTION — READ STATUS.md

Before starting ANY task, read `STATUS.md` in the project root.
It contains what is actually built, schema changes from the original spec,
plan changes, and known issues discovered during implementation.
Do NOT assume CLAUDE.md is current — STATUS.md overrides it where they differ.

---

## GIT POLICY — ABSOLUTE — NEVER VIOLATE

```
NEVER run:  git push
NEVER run:  git commit
NEVER run:  git add
NEVER edit: backend/.env  (contains live Supabase password)
```

When implementation is complete, tell the user:
- Which files changed
- What commit message to use

The user runs all git commands themselves.

---

## What ARIA Does

A between-visit clinical intelligence platform for hypertension management.
A GP with 1,800 patients has 8 minutes per consultation and no structured
view of what happened to hypertensive patients since the last appointment.

ARIA fixes this by:
1. Ingesting patient EHR data via FHIR R4 Bundle (from iEMR source JSON)
2. Generating clinically realistic synthetic home BP readings and medication confirmations spanning the patient's full care timeline
3. Running three-layer AI analysis (rules → scoring → explanation)
4. Delivering a structured pre-visit briefing at 7:30 AM on appointment days

---

## Full Pipeline — Current Status (as of 2026-04-21)

```
iEMR JSON
  ↓ [DONE] scripts/run_adapter.py  → data/fhir/bundles/1091_bundle.json
FHIR Bundle
  ↓ [DONE] scripts/run_ingestion.py → patients, clinical_context, readings (clinic), audit_events
PostgreSQL tables
  ↓ [DONE] scripts/run_generator.py → readings (generated), medication_confirmations
Synthetic data
  ↓ [DONE] worker/processor.py (pattern_recompute job)
     → gap_detector, inertia_detector, adherence_analyzer, deterioration_detector (Layer 1)
     → compute_risk_score (Layer 2)
     → alerts table populated
Layer 1 + 2 outputs
  ↓ [DONE] worker/processor.py (briefing_generation job)
     → compose_briefing → briefings table (Layer 1 deterministic JSON)
     → generate_llm_summary → briefings.llm_response.readable_summary (Layer 3, optional)
Briefing
  ↓ [DONE] GET /api/briefings/{patient_id} → frontend BriefingCard
Dashboard
  ↓ [DONE] Next.js frontend (port 3000), wired to real backend
```

Shadow mode: `scripts/run_shadow_mode.py` — DONE. 94.3% agreement (33/35, 0 false negatives).
  Run: `python scripts/run_shadow_mode.py --patient 1091 --iemr data/raw/iemr/1091_data.json`
  Results in data/shadow_mode_results.json. Frontend shadow-mode page at /shadow-mode.
`scripts/run_scheduler.py` (standalone CLI) — standalone trigger not yet wired.

---

## Three-Layer AI Architecture

```
Layer 1 — Deterministic Rule Engine (ALWAYS runs first)
  gap_detector.py        — days since last home reading
  inertia_detector.py    — elevated BP with no med change
  adherence_analyzer.py  — confirmation rate vs BP pattern
  deterioration_detector.py — sustained worsening trend
  Pure SQL, no AI. Must be correct before Layers 2 or 3 run.

Layer 2 — Weighted Risk Score (runs after Layer 1)
  compute_risk_score()   — weighted 0.0-100.0 priority score
  Stored in patients.risk_score. Used to sort dashboard within tier.

Layer 3 — LLM Readable Summary (optional, on top of Layer 1)
  generate_llm_summary() — 3-sentence summary via claude-sonnet-4-20250514
  Skipped if ANTHROPIC_API_KEY not set. Briefing is complete without it.

Layer 3 Output Validation (always runs after generate_llm_summary)
  validate_llm_output() — guardrails + faithfulness check on LLM text
  Guardrails: blocks forbidden clinical language, PHI leak, prompt injection
  Faithfulness: validates output against the Layer 1 payload it was given
  Retry: one retry on failure before setting readable_summary=None
  Audit: writes audit_events row with outcome=success|failure every call
  File: backend/app/services/briefing/llm_validator.py
```

---

## Clinical Boundaries — NON-NEGOTIABLE

```
NEVER recommend specific medications.
NEVER send alerts directly to patients.
NEVER display raw readings to patients.
NEVER make clinical decisions.

Language rules (enforced at code level, not as a guideline):
  USE:   "possible adherence concern"
  NEVER: "non-adherent"

  USE:   "treatment review warranted"
  NEVER: "medication failure"

  USE:   "sustained elevated readings"
  NEVER: "hypertensive crisis"

Every output is decision support for the clinician only.
```

---

## Tech Stack

```
Backend:    Python 3.11, FastAPI, SQLAlchemy 2.0 async, Pydantic v2
Database:   PostgreSQL via Supabase — asyncpg driver, 8 tables, 11 indexes
Frontend:   Next.js 14, TypeScript strict, Tailwind CSS, recharts
AI:         Anthropic claude-sonnet-4-20250514 (Layer 3 only)
Background: processing_jobs table + Python polling worker (30s interval)
```

---

## Database — 8 Tables

```
patients              — risk_tier, risk_score (Layer 2), monitoring_active, next_appointment
clinical_context      — active_problems[], current_medications[], med_history JSONB,
                        problem_assessments JSONB, recent_labs JSONB,
                        last_clinic_pulse/weight_kg/spo2, allergy_reactions[]
readings              — systolic_avg, diastolic_avg, source ('generated'|'clinic'|'manual')
                        UNIQUE INDEX on (patient_id, effective_datetime, source)
medication_confirmations — confirmed_at (NULL = missed dose), confidence='simulated' for demo
                           UNIQUE INDEX on (patient_id, medication_name, scheduled_time)
alerts                — alert_type (gap_urgent|gap_briefing|inertia|deterioration|adherence), gap_days, acknowledged_at
briefings             — llm_response JSONB (9 briefing fields + problem_assessments), model_version, prompt_hash
processing_jobs       — status (queued→running→succeeded|failed), idempotency_key UNIQUE
audit_events          — action, actor_type, outcome ('success'|'failure')
```

---

## Key Patient Data — Patient 1091 (Demo Patient A)

```
patient_id:           "1091"
risk_tier:            "high"  (CHF auto-override, code I50.9)
tier_override:        "CHF in problem list"
monitoring_active:    true
risk_score:           69.48   (Layer 2, computed 2026-04-21)
last_clinic_systolic: 128 mmHg
last_clinic_diastolic: 63 mmHg
last_med_change:      2013-09-26
active_medications:   14  (ASPIRIN 81, METOPROLOL, LASIX 20, LISINOPRIL 10,
                           NAMENDA, ISOSORBIDE MONONITRATE 30, WARFARIN 3,
                           LANTUS 100, AMLODIPINE, SULAR 20, OMEPRAZOLE 40,
                           NOVOLOG 100, GLUCAGON 1, PRAVASTATIN 10)
active_problems:      17  (includes CHF, CAD, HYPERTENSION, T2DM, MEMORY LOSS, etc.)
allergies:            SULFA, PENICILLIN, DICLOXACILLIN, BYETTA, SIMVASTATIN, CRESTOR
synthetic_readings:   47  (14 morning + 13 evening + 4 pre-appointment + 6 dip + 10 return)
confirmations:        420 total, 377 confirmed (89.8%)
28-day avg BP:        158/100 mmHg (Stage 2 hypertension range)
gap_days:             ~0 (readings current as of pipeline run 2026-04-21)
inertia_detected:     true (avg systolic 158 with no med change since 2013)
adherence_pattern:    B (high confirmation rate + elevated readings = treatment review)
```

---

## How to Run the System

```bash
# 1. Start backend (from backend/)
conda activate aria
uvicorn app.main:app --reload --port 8000

# 2. Start frontend (from frontend/)
npm run dev
# → http://localhost:3000

# 3. Run full pipeline for patient 1091 (from project root)
conda activate aria
python scripts/run_adapter.py --patient data/raw/iemr/1091_data.json --patient-id 1091
python scripts/run_ingestion.py
python scripts/run_generator.py --patient 1091

# 4. Trigger briefing generation (demo mode — backend must be running)
curl -X POST http://localhost:8000/api/admin/trigger-scheduler
# or use the /admin page in the frontend

# 5. Run unit tests
cd backend && python -m pytest tests/ -v -m "not integration"

# 6. Lint
cd backend && ruff check app/
```

---

## Service Architecture

```
backend/app/services/
  fhir/           adapter.py, ingestion.py, validator.py
  generator/      reading_generator.py, confirmation_generator.py
  pattern_engine/ gap_detector.py, inertia_detector.py,
                  adherence_analyzer.py, deterioration_detector.py,
                  risk_scorer.py
  briefing/       composer.py (Layer 1), summarizer.py (Layer 3),
                  llm_validator.py (Layer 3 output validation + guardrails)
  worker/         processor.py (job runner), scheduler.py (7:30 AM trigger)
```

---

## Audit Requirements (enforced everywhere)

Every one of these actions MUST create a row in `audit_events`:
```
bundle_import       action="bundle_import"      resource_type="Bundle"
reading_ingested    action="reading_ingested"    resource_type="Reading"
briefing_viewed     action="briefing_viewed"     resource_type="Briefing"  + update briefings.read_at
alert_acknowledged  action="alert_acknowledged"  resource_type="Alert"
llm_validation      action="llm_validation"      resource_type="Briefing"
                    outcome="success"|"failure", details=failed_check+reason on failure
```
`outcome` is always `"success"` or `"failure"` — never omit.

---

## DO NOT

- Do NOT commit `.env` files — they contain live database credentials
- Do NOT run Layer 3 before Layer 1 is complete and verified
- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()`
- Do NOT use Pydantic v1 syntax (`class Config:`) — use `SettingsConfigDict`
- Do NOT recommend specific medications in any generated output
- Do NOT use `ADMIT_DATE` for observation timestamps — always `VITALS_DATETIME`
- Do NOT insert `NULL` values for device outage days — use absent rows
- Do NOT use hardcoded 140 mmHg inertia threshold — use patient-adaptive threshold from historic_bp_systolic
- Do NOT use `clinical_context.last_med_change` for inertia — use med_history JSONB (stale single-date snapshot)
- Do NOT generate synthetic data for only 28 days — full care timeline required for all patients
- Do NOT skip the midnight pattern_recompute sweep — risk scores go stale without it
