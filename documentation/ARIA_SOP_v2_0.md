# ARIA — Adaptive Real-time Intelligence Architecture
## Standard Operating Procedure
**Version 2.0 | April 2026 | Read this before touching any code**

> **Document status:** This SOP supersedes v1.1 and incorporates all findings from the April 2026 system audit. Sections marked **(CURRENT)** describe the live system. Sections marked **(ROADMAP — Phase N)** describe planned changes from the audit roadmap.

---

## 1. What Is ARIA

ARIA is a between-visit clinical intelligence platform for hypertension management. A GP with 1,800 patients has 8 minutes per consultation and no structured view of what happened to their hypertensive patients since the last appointment. Readings taken at home, medications adjusted, missed doses, worsening trends — none of this reaches the clinician before they walk into the room.

ARIA fixes this by generating a structured pre-visit briefing delivered to the clinician dashboard at 7:30 AM before the clinic day begins. The briefing is built from 28 days of home blood pressure readings, the patient's complete medication history, and longitudinal EHR data.

**The core clinical problem ARIA solves:** A patient whose BP is elevated may be failing on medication (treatment needs to change) or may have an adherence problem (treatment is correct but underused). These require completely different clinical responses. Without between-visit data, the clinician cannot confidently distinguish them. ARIA can.

### 1.1 What ARIA Is NOT

- Not a remote patient monitoring programme requiring extra staff
- Not an AI scribe or documentation tool
- Not a patient-facing application
- Not a system that makes clinical decisions

### 1.2 Clinical Boundary — Non-Negotiable

```
ARIA does not recommend specific medication adjustments.
ARIA does not send alerts directly to patients.
Every output is decision support for the clinician only.

Language rule: "possible adherence concern"      NOT "non-adherent"
Language rule: "treatment review warranted"      NOT "medication failure"
Language rule: "sustained elevated readings"     NOT "hypertensive crisis"
```

---

## 2. How ARIA Works — Three-Layer AI Architecture

ARIA processes each patient through three layers in strict sequence. **Never reverse this order.**

| Layer | Name | What It Does | Technology |
|---|---|---|---|
| 1 | Deterministic Rule Engine | Gap detection, therapeutic inertia, adherence-BP correlation, deterioration detection. Pure SQL. No AI. | PostgreSQL queries + Python |
| 2 | Weighted Risk Scoring | Priority score 0.0–100.0 per patient. Runs after all Layer 1 detectors complete. Sorts patients on dashboard. | Python weighted formula |
| 3 | LLM Explanation | Converts deterministic briefing JSON to 3-sentence readable summary. Optional. Always runs last. | `claude-sonnet-4-20250514` |

### 2.1 Layer 1 — Four Detectors and Their Known Issues

All four detectors use the patient's 28-day reading window. **The following known defects exist in v4.3 and are scheduled for Phase 0/Phase 2 fixes:**

#### Gap Detector *(correct — no defects)*
Computes days since last reading. Applies risk-tier thresholds:
- High tier: flag ≥ 1 day, urgent ≥ 3 days
- Medium tier: flag ≥ 3 days, urgent ≥ 5 days
- Low tier: flag ≥ 7 days, urgent ≥ 14 days

#### Inertia Detector *(two known defects — Phase 2 fix)*
**AUDIT ITEM 1:** Uses hard-coded 140 mmHg threshold. Should use patient-adaptive threshold.
**AUDIT ITEM 4:** Reads only `clinical_context.last_med_change` (ingestion-time snapshot). Ignores `clinical_context.med_history` JSONB which contains phone refill calls and in-person medication changes.

All four conditions must be simultaneously true:
1. Average systolic ≥ 140 mmHg over 28 days *(should be patient-adaptive)*
2. ≥ 5 elevated readings
3. Elevated condition spans > 7 days
4. No medication change on/after first elevated reading *(should use med_history JSONB)*

#### Deterioration Detector *(one known defect — Phase 0 immediate fix)*
**AUDIT ITEM 2:** Fires on any positive slope regardless of absolute BP. A patient rising from 115 to 119 triggers a deterioration alert. The 14-day window is also shorter than the 28-day window used by other detectors.

Fires when both signals are positive:
- Signal 1: positive least-squares slope across 14-day window
- Signal 2: recent 3-day average exceeds days 4–10 baseline average
- **Missing: Signal 3 — recent_avg ≥ patient threshold** ← Phase 0 add

#### Adherence-BP Correlation Detector *(two known defects — Phase 0 immediate fix)*
**AUDIT ITEM 3:** Pattern B fires even when BP is actively declining (treatment working). Missing treatment-working suppression.
**AUDIT ITEM 11:** Pattern A (high BP + low adherence) never writes an alert row. Adherence concern is invisible in urgent_flags.

Pattern classifications:
- Pattern A: high BP + low adherence → "possible adherence concern" *(should write alert row)*
- Pattern B: high BP + high adherence → "possible treatment-review case" *(suppression missing)*
- Pattern C: normal BP + low adherence → contextual review

**Phase 0 fix for Pattern B suppression:** When `slope < -0.3 AND recent_7day_avg < threshold AND days_since_med_change ≤ 14`, suppress Pattern B to `"none"` with note "treatment appears effective — monitoring." The 14-day gate is critical — suppression must not apply when no recent medication change occurred.

### 2.2 Layer 2 Risk Scoring Formula

The risk score is a weighted sum of five signals, normalised to 0.0–100.0 and stored in `patients.risk_score`. Higher score = higher clinical priority within the same tier.

| Signal | Weight | Source |
|---|---|---|
| 28-day avg systolic vs personal baseline | 30% | readings table |
| Days since last medication change | 25% | clinical_context.last_med_change |
| Adherence rate (inverted) | 20% | medication_confirmations table |
| Gap duration (days without reading) | 15% | readings table |
| Active comorbidity severity | 10% | clinical_context.problem_codes |

**AUDIT ITEM 25 — Comorbidity score defect (Phase 0 fix):** Current code: `count / 5.0 * 100.0`, saturates at 5 problems. Patient 1091 has 17 coded problems, making this signal useless.

**Phase 0 severity-weighted replacement:**

| Condition | ICD-10 | Points |
|---|---|---|
| CHF | I50.* | 25 |
| Stroke | I63.*, I64 | 25 |
| TIA | G45.* | 25 |
| Diabetes T2DM | E11.* | 15 |
| CKD | N18.* | 15 |
| CAD | I25.* | 15 |
| Any other | — | 5 each |

Total clamped to 100.

### 2.3 Patient-Adaptive Threshold *(ROADMAP — Phase 2)*

All Layer 1 detectors currently use a hard-coded 140 mmHg threshold. Phase 2 replaces this with:

```python
patient_threshold = max(130, stable_baseline_mean + 1.5 * stable_baseline_sd)
# capped at 145 mmHg
# stable_baseline_mean derived from historic_bp_systolic at physician-labeled stable visits
# falls back to 140 if fewer than 3 stable readings exist
```

For CHF/cardiovascular + metabolic comorbidity patients:
```python
effective_threshold = max(130, patient_threshold - 7)
```

This logic is extracted to `backend/app/services/pattern_engine/threshold_utils.py`.

### 2.4 Shadow Mode Validation

Shadow mode runs the alert engine against iEMR synthetic data and compares against physician `PROBLEM_STATUS2_FLAG` ground truth.

**v4.3 result (April 2026): 94.3% agreement (33/35, 0 false negatives, 2 false positives)**

This exceeds the 80% minimum gate. Zero false negatives is the most important metric — ARIA must not be silent when a physician was concerned.

**v5.0 target:** Add ~10–15 evaluation points from no-vitals visits with explicit physician HTN assessments. Parameterise by CLI argument.

---

## 3. Environment Setup

Do every step in order. Do not skip any.

### 3.1 Prerequisites

- Anaconda or Miniconda installed
- Git configured with your name and email
- VS Code with Python extension
- Node.js 18 or higher (for frontend work)

### 3.2 Clone the Repository

```bash
git clone https://github.com/kush-pixel/ARIA.git
cd ARIA
```

### 3.3 Create Conda Environment

```bash
conda create -n aria python=3.11 -y
conda activate aria
```

### 3.4 Install Backend Dependencies

```bash
cd backend
pip install -r requirements-dev.txt
```

### 3.5 Set Up Environment Variables

Get the Supabase connection string and Anthropic API key from Krishna. Then:

```powershell
# From project root
Copy-Item ".env.example" "backend\.env"
code backend\.env
# Fill in DATABASE_URL and ANTHROPIC_API_KEY
```

### 3.6 Verify Your Setup

```bash
cd backend
python -c "from pydantic_settings import BaseSettings; print('pydantic v2 OK')"
python -c "import asyncpg; print('asyncpg OK')"
python -c "import sqlalchemy; print('sqlalchemy', sqlalchemy.__version__)"
ruff --version
python -m pytest --version
```

Expected output:
```
pydantic v2 OK
asyncpg OK
sqlalchemy 2.0.x
ruff 0.8.x
```

---

## 4. Project Structure

```
ARIA/
  AUDIT.md                     ← system audit — 47 items + phased roadmap
  STATUS.md                    ← read every day before coding
  .env.example                 ← environment variable template
  backend/
    app/
      config.py                ← Pydantic v2 settings
      main.py                  ← FastAPI app entry point
      db/                      ← SQLAlchemy engine and session
      models/                  ← 8 ORM models (one per table)
      api/                     ← FastAPI route handlers
      services/
        fhir/                  ← iEMR adapter + FHIR ingestion
        generator/             ← synthetic home BP + confirmations
        pattern_engine/        ← Layer 1 detectors + Layer 2 scorer
          threshold_utils.py   ← (Phase 2) adaptive + comorbidity thresholds
        briefing/              ← Layer 1 composer + Layer 3 LLM
        worker/                ← background job processor + scheduler
      utils/                   ← shared helpers
    tests/                     ← pytest test suite
  frontend/
    src/
      app/                     ← Next.js pages
      components/              ← React components
      lib/                     ← API client and TypeScript types
  data/
    raw/iemr/                  ← iEMR patient JSON (git excluded)
    fhir/bundles/              ← FHIR Bundle files (git excluded)
    synthetic/                 ← generated readings (git excluded)
  scripts/                     ← pipeline runner scripts
  prompts/                     ← LLM prompt files
  documentation/               ← technical specification and SOP
```

---

## 5. Database — 8 Tables

PostgreSQL hosted on Supabase. All tables use `TIMESTAMPTZ` not `TIMESTAMP`. All UUID primary keys use `gen_random_uuid()`. All indexes must be created before any data is inserted.

| Table | Key Columns | Purpose |
|---|---|---|
| patients | patient_id PK, risk_tier, risk_score | Patient enrolment, risk tier, Layer 2 score |
| clinical_context | patient_id PK (1:1 with patients) | Pre-computed EHR context. See critical notes below. |
| readings | reading_id UUID, patient_id, systolic_avg, effective_datetime | Home BP readings (synthetic for MVP) |
| medication_confirmations | confirmation_id UUID, scheduled_time, confirmed_at | Medication adherence tracking |
| alerts | alert_id UUID, alert_type, patient_id | Gap, inertia, deterioration, **adherence** *(v5.0 Phase 0)* |
| briefings | briefing_id UUID, appointment_date, llm_response JSONB | Pre-visit briefing payload |
| processing_jobs | job_id UUID, idempotency_key UNIQUE, status | Background job queue |
| audit_events | audit_id UUID, action, outcome | Immutable audit trail |

### 5.1 Critical Notes on clinical_context

```
Parallel arrays — must stay in sync:
  active_problems[n]       ↔  problem_codes[n]
  current_medications[n]   ↔  med_rxnorm_codes[n]
  historic_bp_systolic[n]  ↔  historic_bp_dates[n]  (stored as ISO strings e.g. "2024-01-15")

If you add to one array you MUST add to the parallel array at the same index.
historic_bp_dates MUST be TEXT[] not DATE[] — asyncpg does not support DATE[] directly.
```

**Known defects in v4.3 (Phase 1 fixes):**
- `social_context` column exists but is **never populated** — always NULL
- `last_visit_date` only counts 53 BP clinic dates, misses 71 non-vitals visits
- Allergy reactions and active status not captured
- Physician problem assessments (`PROBLEM_STATUS2_FLAG`, `PROBLEM_ASSESSMENT_TEXT`) not captured
- PULSE, WEIGHT, SpO2, TEMPERATURE not stored despite being present in iEMR

**v5.0 columns added via migration (safe to re-run):**
```sql
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS med_history JSONB;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS allergy_reactions TEXT[];
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_pulse SMALLINT;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_weight_kg NUMERIC(5,1);
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_spo2 SMALLINT;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS problem_assessments JSONB;
```

### 5.2 Alerts Table — alert_type Enum

Current v4.3: `'gap_urgent'` | `'gap_briefing'` | `'inertia'` | `'deterioration'`

**Phase 0 addition:** `'adherence'` — Pattern A now writes an alert row.

**AUDIT ITEM 30 (Phase 0):** `delivered_at` is currently never set in `_upsert_alert()`. Fix: set `delivered_at = datetime.now(UTC)` at insert time.

### 5.3 Readings Table — Idempotency

**Current v4.3 — batch-level (broken for new clinic visits):**
```python
if clinic_count == 0:
    insert_all_clinic_readings()
# Any existing clinic reading blocks ALL new inserts for this patient
```

**Phase 3 fix — per-observation idempotency:**
```sql
CREATE UNIQUE INDEX idx_readings_patient_datetime_source
  ON readings (patient_id, effective_datetime, source);

-- Then in ingestion:
INSERT INTO readings (...) VALUES (...)
ON CONFLICT (patient_id, effective_datetime, source) DO NOTHING;
```

---

## 6. Synthetic Data Rules

ARIA generates synthetic home BP readings anchored on real iEMR clinic data. A clinical reviewer will immediately spot unrealistic data. These rules are non-negotiable.

| Rule | Requirement | Failure Mode to Avoid |
|---|---|---|
| Day-to-day systolic SD | 8–12 mmHg | **NEVER less than 5** — flat variance is the most common error |
| Morning vs evening | Morning 5–10 mmHg HIGHER | Must be visible every single week |
| Round numbers | NEVER exactly round | Real: 153, 161, 148, 167 — not 155, 160, 150 |
| Two-reading session | Readings 1 and 2 differ by 2–6 mmHg | Reading 2 slightly lower (rest effect) |
| Diastolic | Systolic × 0.60–0.66 | Does not move independently in treated hypertension |
| Device outage | ABSENT ROWS — 1–2 episodes of 2–4 days | **Never store null/zero** for outage days |
| White-coat dip | 10–15 mmHg drop 3–5 days before appointment | Decline is gradual, not sudden |

### 6.1 Patient A Demo Scenario (28 Days)

Patient A is patient 1091 from the iEMR dataset. Readings anchored on clinic BPs of 185/72 and 180/73.

| Days | Pattern | Detail |
|---|---|---|
| 1–7 | Baseline | Morning ~163 mmHg SD=8. Evening 6–9 lower. HR 64–72 (metoprolol effect). |
| 8–14 | Inertia develops | Systolic drifts to ~165. One missed evening reading Saturday. |
| 15–18 | Continued elevation | 164–167 mmHg. Device outage days 16–17 — **absent rows**. |
| 19–21 | Pre-appointment dip | Gradual drop to 148–153. White-coat adherence pattern. |
| 22–28 | Post-appointment return | 160–166 mmHg. Weekend misses days 25–26. |

### 6.2 Parametric Baseline — v5.0 *(ROADMAP — Phase 3)*

The v4.3 generator hard-codes ~163 mmHg baseline for Patient A. Phase 3 replaces this:

```python
baseline_mean = statistics.median(clinical_context.historic_bp_systolic)
baseline_sd = statistics.stdev(clinical_context.historic_bp_systolic)
# Same generator works for any patient without code change
```

### 6.3 Full Care Timeline Generation — v5.0 *(ROADMAP — Phase 3)*

Currently the generator produces 28 days before the most recent appointment. Phase 3 adds `generate_full_timeline_readings(clinic_readings)` that generates synthetic readings **between all consecutive clinic BP pairs** spanning the patient's entire 11-year care history.

This is a prerequisite for:
- Adaptive analysis window (28 → 90 days based on visit interval)
- Long-term trend layer in briefing
- Patient-adaptive threshold (needs stable reading history)

**Prerequisite:** Per-observation idempotency must be in place first (Phase 3 gate).

---

## 7. Git Workflow

Never commit directly to `main`. Always create a feature branch.

**Never commit:**
- `backend/.env`
- Patient JSON files (`data/raw/iemr/`)
- `CLAUDE.md`
- `.claude/` folder

### 7.1 Daily Workflow

```bash
# 1. Pull latest changes
git pull origin main

# 2. Read STATUS.md — see what was built since your last session

# 3. Create a feature branch
git checkout -b feat/your-feature-name

# 4. Implement your feature

# 5. Run tests
cd backend && python -m pytest tests/ -v -m "not integration"

# 6. Run linter — must pass with zero errors
ruff check app/

# 7. Stage specific files (not git add -A)
git add backend/app/services/pattern_engine/adherence_analyzer.py

# 8. Commit
git commit -m "fix(pattern): add Pattern B treatment-working suppression with 14-day gate"

# 9. Push branch
git push origin feat/your-feature-name

# 10. Update STATUS.md: move task from IN PROGRESS to COMPLETE
```

### 7.2 Commit Message Format

```
feat(fhir): implement physician problem assessment capture
fix(detector): add threshold gate to deterioration detector
fix(inertia): replace last_med_change with med_history JSONB traversal
fix(scorer): replace linear comorbidity count with severity-weighted model
test(pattern): add Pattern B suppression tests
chore(deps): add apscheduler to requirements.txt
```

### 7.3 Branch Naming

| Type | Format | Example |
|---|---|---|
| New feature | `feat/<description>` | `feat/threshold-utils` |
| Bug fix | `fix/<description>` | `fix/deterioration-threshold-gate` |
| Phase 0 fixes | `fix/phase-0-correctness` | Multiple audit items in one branch |
| Tests | `test/<description>` | `test/pattern-b-suppression` |
| Documentation | `docs/<description>` | `docs/spec-v5` |

### 7.4 STATUS.md — How to Update It

STATUS.md is the shared coordination file. Update it every time you complete a task, discover a schema change, or find something that affects other teammates.

| When | What to update |
|---|---|
| Finish a task | Move file from IN PROGRESS to COMPLETE |
| Start a task | Add to IN PROGRESS with your name |
| Schema change | Add to Schema Changes — critical, affects every teammate's models |
| Discover a limitation | Add to Known Issues — saves teammates from hitting the same issue |
| Complete a Phase 0 fix | Note which audit item number is resolved |

---

## 8. Code Standards

### 8.1 Python Rules

- Type hints on **ALL** public functions
- Docstrings on **ALL** public classes and functions
- Async everywhere in API and database layer — **no sync SQLAlchemy**
- `ruff check app/` must pass with zero errors before any commit
- No bare `except` — always catch a specific exception
- No hardcoded values — use config or constants

### 8.2 Critical Syntax — SQLAlchemy 2.0 Async

**This is the most common mistake. Always use async SQLAlchemy. Never use `session.query()`.**

```python
# CORRECT
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

async def get_patient(session: AsyncSession, patient_id: str):
    result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    return result.scalar_one_or_none()

# WRONG — never use this
session.query(Patient).filter(...).first()
```

### 8.3 Critical Syntax — Pydantic v2

```python
# CORRECT
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    anthropic_api_key: str

# WRONG — never use v1 syntax
class Config:
    env_file = ".env"
```

### 8.4 TypeScript Rules

- Strict mode always — no `any`, no implicit `any`
- All component props typed with explicit interfaces
- All API calls through `src/lib/api.ts` only — never `fetch()` in components
- All shared types in `src/lib/types.ts`
- Tailwind utility classes only — no inline styles

**AUDIT ITEM 31 (Phase 6):** Remove `sortPatients()` from `PatientList.tsx`. Backend sort order (`risk_tier ASC`, `risk_score DESC`) is authoritative. Frontend silently overriding it causes silent divergence.

**AUDIT ITEM 23 (Phase 6):** The briefing icon `apptToday` condition only activates for today's appointments. A clinician reviewing tomorrow's schedule sees no briefing indicators even when briefings exist. Phase 6 changes this to activate whenever a briefing row exists for the patient.

### 8.5 Audit Requirements

Every sensitive action **must** create an `audit_events` row. This is non-negotiable.

| Action | Required audit fields |
|---|---|
| FHIR Bundle import | `action='bundle_import'`, `resource_type='Bundle'`, `outcome='success'\|'failure'` |
| Reading ingested | `action='reading_ingested'`, `resource_type='Reading'` |
| Briefing viewed | `action='briefing_viewed'` + update `briefings.read_at` |
| Alert acknowledged | `action='alert_acknowledged'`, `resource_type='Alert'` |

**AUDIT ITEM 38 (Phase 8):** Add a PostgreSQL trigger on the readings table so direct DB writes also produce audit records.

### 8.6 Pattern Engine — What Must NOT Be Duplicated

**AUDIT ITEM 18 (Phase 2):** `composer.py` currently re-implements inertia detection inline with `_ELEVATED_SYSTOLIC = 140.0`. This duplicates Layer 1 with the same hard-coded threshold defect. In Phase 2, `_build_visit_agenda()` must receive and consume the `InertiaResult` dict from Layer 1, not re-compute inertia independently.

---

## 9. Testing

### 9.1 Running Tests

```bash
# Always run from backend/ folder
cd backend

# Unit tests (no real patient data needed)
python -m pytest tests/ -v -m "not integration"

# Integration tests (requires real patient JSON)
python -m pytest tests/test_integration.py -v

# Specific test file
python -m pytest tests/test_pattern_engine.py -v

# With coverage
python -m pytest --cov=app tests/ -m "not integration"
```

### 9.2 Test Files

| File | What It Tests |
|---|---|
| `test_fhir_adapter.py` | iEMR JSON → FHIR Bundle. All resource types including `_aria_med_history`. 28 tests passing. |
| `test_ingestion.py` | FHIR Bundle ingestion, idempotency, risk tier overrides (I50/I63/G45), med_history population, audit events |
| `test_reading_generator.py` | Synthetic data SD (8–12 mmHg), morning/evening differential, no round numbers, device outage gaps, white-coat dip |
| `test_pattern_engine.py` | Gap thresholds, inertia all 4 conditions, **Pattern B suppression with 14-day gate**, adherence alert write *(tests updated for Phase 0 fixes)* |
| `test_risk_scorer.py` | Layer 2 weighted formula, 0.0–100.0 range, **severity-weighted comorbidity model** *(updated Phase 0)* |
| `test_briefing_composer.py` | All briefing fields present, visit agenda priority order, **no duplicate inertia logic** *(updated Phase 2)* |
| `test_api.py` | API endpoint status codes, response schema, audit events created, **alert patient_id filter** *(Phase 5)* |
| `test_integration.py` | Full end-to-end with real patient 1091 data (`@pytest.mark.integration`) |

### 9.3 Shadow Mode Validation

Before demo sign-off, run shadow mode validation:

```bash
conda activate aria
cd C:\Users\patel\Projects\ARIA
python scripts/run_shadow_mode.py
```

**Current result (v4.3):** 94.3% agreement (33/35, 0 false negatives, 2 false positives).

**What the summary reports:**
- Total evaluation points and breakdown (BP clinic dates vs no-vitals assessments in v5.0)
- Agreement on labelled points: N/total (X%)
- Concerned: N, Stable: N, No ground truth: N (excluded from agreement rate)
- False negative count — must be 0 before demo sign-off

**v5.0 CLI (Phase 5):**
```bash
python scripts/run_shadow_mode.py --patient 2045 --iemr data/raw/iemr/2045_data.json
```

### 9.4 Phase 0 Acceptance Tests

Before reporting Phase 0 complete, verify:

```bash
# 1. Trigger pattern_recompute and check:
#    - deterioration alert NOT firing for patients below 140 systolic
#    - Pattern B suppressed when med change < 14 days AND slope < -0.3
#    - Pattern A writes an alert row (alert_type='adherence')
#    - alerts.delivered_at is set (not NULL)
#    - briefings.appointment_date matches patient.next_appointment (not idempotency_key)
#    - comorbidity score differentiates CHF+Stroke (50) vs CHF+T2DM (40)

curl -X POST http://localhost:8000/api/admin/trigger-scheduler
# then check alerts table and briefings table
```

---

## 10. Phase 0 Implementation Guide

Phase 0 fixes produce correct output on existing data with no re-ingestion required. These are the first changes to make.

### 10.1 Fix: Add Threshold Gate to Deterioration Detector

**File:** `backend/app/services/pattern_engine/deterioration_detector.py` line 150

**Change:**
```python
# BEFORE (fires on any positive slope)
deterioration = slope > 0.0 and recent_avg > baseline_avg

# AFTER (requires elevated absolute value)
INTERIM_THRESHOLD = 140.0  # replaced with patient_threshold in Phase 2
deterioration = slope > 0.0 and recent_avg > baseline_avg and recent_avg >= INTERIM_THRESHOLD
```

### 10.2 Fix: Pattern B Suppression + 14-Day Gate

**File:** `backend/app/services/pattern_engine/adherence_analyzer.py`

After pattern classification, before returning:

```python
if pattern == "B":
    slope = _compute_slope(readings_28d)
    recent_7d_avg = _compute_recent_avg(readings_28d, days=7)
    days_since_med_change = _days_since_med_change(clinical_context)
    
    if (slope < -0.3
            and recent_7d_avg < THRESHOLD  # patient_threshold in Phase 2
            and days_since_med_change is not None
            and days_since_med_change <= 14):
        pattern = "none"
        interpretation = "treatment appears effective — monitoring"
```

### 10.3 Fix: Write Adherence Alert Row

**File:** `backend/app/services/worker/processor.py`

After adherence analysis, add:

```python
if adherence["pattern"] == "A":
    await _upsert_alert(session, pid, "adherence")
```

Update `Alert` model to accept `"adherence"` in alert_type. Update `_build_urgent_flags()` in `composer.py` to handle the new type.

### 10.4 Fix: Set `delivered_at` at Alert Insert

**File:** `backend/app/services/worker/processor.py` — inside `_upsert_alert()`

```python
# Add to the alert object at creation:
alert.delivered_at = datetime.now(UTC)
```

### 10.5 Fix: Read Appointment Date from Patient Record

**File:** `backend/app/services/worker/processor.py` line 247

```python
# BEFORE
date_str = job.idempotency_key[-10:]

# AFTER
if patient.next_appointment:
    appointment_date = patient.next_appointment.date()
else:
    appointment_date = date.today()  # fallback for demo mode
```

### 10.6 Fix: Severity-Weighted Comorbidity Score

**File:** `backend/app/services/pattern_engine/risk_scorer.py`

Replace `_comorbidity_count()` implementation:

```python
_COMORBIDITY_WEIGHTS = {
    "I50": 25,  # CHF
    "I63": 25, "I64": 25,  # Stroke
    "G45": 25,  # TIA
    "E11": 15,  # T2DM
    "N18": 15,  # CKD
    "I25": 15,  # CAD
}
_DEFAULT_PROBLEM_WEIGHT = 5

def _comorbidity_score(context: ClinicalContext) -> float:
    total = 0
    for code in (context.problem_codes or []):
        prefix = code[:3]
        total += _COMORBIDITY_WEIGHTS.get(prefix, _DEFAULT_PROBLEM_WEIGHT)
    return min(100.0, float(total))
```

---

## 11. Running the System

### 11.1 Set Up the Database

```bash
conda activate aria
cd C:\Users\patel\Projects\ARIA
python scripts/setup_db.py
# Creates all 8 tables, 12+ indexes, and runs column migrations on Supabase
```

### 11.2 Run the iEMR Adapter

```bash
python scripts/run_adapter.py --patient data/raw/iemr/1091_data.json
# Converts iEMR JSON to FHIR Bundle
# Output: data/fhir/bundles/1091_bundle.json
```

### 11.3 Ingest the FHIR Bundle

```bash
python scripts/run_ingestion.py --bundle data/fhir/bundles/1091_bundle.json
# Populates all 8 PostgreSQL tables
# Safe to re-run (idempotent)
# v4.3 NOTE: batch-level idempotency — delete existing clinic readings first if re-ingesting
# v5.0 Phase 3: per-observation idempotency makes re-ingestion safe automatically
```

### 11.4 Generate Synthetic Readings

```bash
python scripts/run_generator.py --patient 1091
# v4.3: Generates 28-day home BP readings for Patient A scenario
# v5.0 Phase 3: Generates full care timeline readings spanning all clinic dates
```

### 11.5 Start the Background Worker

```bash
# In a separate terminal window
python scripts/run_worker.py
# Polls processing_jobs every 30 seconds
# Must be running for briefing generation to work
```

### 11.6 Start the Backend API

```bash
cd backend
uvicorn app.main:app --reload --port 8000
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 11.7 Trigger Pattern Recompute (After Phase 0 Fixes)

```bash
curl -X POST http://localhost:8000/api/admin/trigger-scheduler
# Enqueues briefing_generation for appointment-day patients
# Worker picks up within 30 seconds
# After Phase 0 fixes, also manually trigger pattern_recompute:
curl -X POST "http://localhost:8000/api/admin/trigger-recompute?patient_id=1091"
```

### 11.8 Start the Frontend

```bash
cd frontend
npm install
npm run dev
# Dashboard at http://localhost:3000
```

### 11.9 Run Shadow Mode Validation

```bash
conda activate aria
cd C:\Users\patel\Projects\ARIA
python scripts/run_shadow_mode.py
# Current result: 94.3% agreement (33/35)
# After Phase 2 detector fixes, re-run and verify agreement maintained or improved
```

---

## 12. Common Errors and Fixes

| Error | Fix |
|---|---|
| `No module named yaml` | `pip install pyyaml` |
| `No module named asyncpg` | `pip install asyncpg` |
| `SQLAlchemy MissingGreenlet error` | You used sync SQLAlchemy in async context. Switch to `await session.execute(select(...))` |
| `Pydantic ValidationError on startup` | You used v1 syntax. Change to `model_config = SettingsConfigDict(...)` |
| `Tests show 0 items collected` | Always run pytest from `backend/` folder, not project root |
| `DATABASE_URL not found` | `backend/.env` is missing or empty. Copy from `.env.example`. |
| `Supabase connection refused` | Check DATABASE_URL uses asyncpg driver: `postgresql+asyncpg://...` |
| `risk_score column not found` | Run `setup_db.py` again — schema may have been created before risk_score was added |
| `med_history column not found on clinical_context` | Run `setup_db.py` again — med_history JSONB is added via ALTER TABLE migration |
| `asyncpg error with DATE[] arrays` | `historic_bp_dates` must be `TEXT[]` not `DATE[]`. Store as ISO strings e.g. `2024-01-15`. |
| `Inertia firing on declining BP` | Phase 2 fix not yet applied — slope direction check missing in v4.3 |
| `Pattern B firing on improving patients` | Phase 0 fix not yet applied — Pattern B suppression missing in v4.3 |
| `Adherence concern not visible in urgent_flags` | Phase 0 fix not yet applied — Pattern A does not write alert row in v4.3 |
| `Comorbidity score identical for all complex patients` | Phase 0 fix not yet applied — linear count saturates at 5 in v4.3 |
| `Deterioration alert firing at 115 mmHg systolic` | Phase 0 fix not yet applied — threshold gate missing in v4.3 |
| `Adding new clinic visit fails silently` | Phase 3 fix not yet applied — batch idempotency blocks all inserts in v4.3 |
| `social_context always NULL in briefing` | Phase 1 fix not yet applied — adapter does not read SOCIAL_HX in v4.3 |
| `last_visit_date shows old clinic date, not recent phone call` | Phase 1 fix not yet applied — only BP visit dates counted in v4.3 |

---

## 13. Audit Remediation Tracker

Track Phase 0 fixes as they are completed. Update STATUS.md when each item is resolved.

| Audit Item | Description | Phase | Status |
|---|---|---|---|
| 2 | Deterioration: add threshold gate | 0 | `[ ]` |
| 3 + 26 | Pattern B suppression + 14-day gate | 0 | `[ ]` |
| 11 | Write adherence alert row | 0 | `[ ]` |
| 30 | Set delivered_at on alert insert | 0 | `[ ]` |
| 20 | Appointment date from patient record | 0 | `[ ]` |
| 25 | Severity-weighted comorbidity score | 0 | `[ ]` |
| 12 | Capture all 124 visit dates | 1 | `[ ]` |
| 8 | Populate social_context | 1 | `[ ]` |
| 9 | Allergy reactions + active-status | 1 | `[ ]` |
| 7 | Capture physician problem assessments | 1 | `[ ]` |
| 6 | Capture PULSE/WEIGHT/SpO2 | 1 | `[ ]` |
| 1 + 4 | Adaptive threshold + med_history inertia | 2 | `[ ]` |
| 18 | Remove duplicate inertia in composer | 2 | `[ ]` |
| 5 | Comorbidity-adjusted threshold (threshold_utils) | 2 | `[ ]` |
| 27 | Exclude white-coat window | 2 | `[ ]` |
| 22 | Per-observation idempotency **(Phase 3 gate)** | 3 | `[ ]` |
| 19 | Parametric baseline generator | 3 | `[ ]` |
| 15 | Full care timeline readings | 3 | `[ ]` |
| 17 | Cold start detection | 3 | `[ ]` |
| 28 | Adaptive window (14–90 days) | 3 | `[ ]` |
| 10 | Daily pattern_recompute sweep | 4 | `[ ]` |

---

## 14. Team Assignments

### Week 1 — Foundation

| Who | What | Files |
|---|---|---|
| Kush | PostgreSQL schema, FHIR adapter, FHIR ingestion, setup_db.py | models/, services/fhir/, scripts/ |
| Krishna | Synthetic reading generator, medication confirmation generator | services/generator/ |
| Prakriti | Pattern engine Layer 1 (all 4 detectors) | services/pattern_engine/ |

### Week 2 — Intelligence

| Who | What | Files |
|---|---|---|
| Yash | Layer 2 risk scorer | services/pattern_engine/risk_scorer.py |
| Sahil | Briefing composer (deterministic JSON), LLM summarizer | services/briefing/ |
| Nesh | Background worker processor and scheduler | services/worker/ |
| Sahil | All FastAPI API routes | app/api/ |

### Week 3 — Dashboard

| Who | What | Files |
|---|---|---|
| Krishna + Kush | Full frontend dashboard | frontend/src/ |
| All | Shadow mode validation, demo prep, end-to-end testing | scripts/run_shadow_mode.py |

### Phase 0 Remediation (Immediate)

| Item | Who | Estimated Size |
|---|---|---|
| Deterioration threshold gate | Prakriti | 1 line |
| Pattern B suppression + 14-day gate | Prakriti | ~17 lines |
| Write adherence alert row | Nesh | 3 lines |
| Set delivered_at on alert insert | Nesh | 1 line |
| Appointment date from patient record | Nesh | 5 lines |
| Severity-weighted comorbidity score | Yash | ~20 lines |

### Demo Patients

| Patient | Source | Scenario | Key Finding |
|---|---|---|---|
| A | Patient 1091 iEMR, monitoring_active=TRUE | Therapeutic inertia | 21 days elevated BP avg 163/101, 91% adherence, no medication change — likely treatment failure |
| B | Patient 1091 iEMR, monitoring_active=FALSE | EHR-only pathway | Overdue lab flag, NSAID + antihypertensive drug interaction flag |

---

*ARIA v5.0 | Leap of Faith Technologies | IIT CS 595 Spring 2026 | SOP v2.0*
