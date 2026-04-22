# ARIA Backend Context
## Python 3.11 | FastAPI | SQLAlchemy 2.0 async | Pydantic v2

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Setup and Running

```bash
conda activate aria
cd backend
uvicorn app.main:app --reload --port 8000
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

---

## Testing and Linting

```bash
# Unit tests only (CI-safe, no live DB required)
cd backend
python -m pytest tests/ -v -m "not integration"

# Integration tests (requires live Supabase connection)
python -m pytest tests/ -v

# Lint — MUST PASS before reporting a task complete
ruff check app/

# Auto-fix and format
ruff check app/ --fix
ruff format app/
```

Current test counts (all passing):
- test_fhir_adapter.py:         42 (41 unit + 1 integration)
- test_ingestion.py:            37 (36 unit + 1 integration)
- test_reading_generator.py:    14 unit + 1 integration
- test_pattern_engine.py:       39 unit
- test_risk_scorer.py:          14 unit
- test_briefing_composer.py:    61 unit
- test_api.py:                  24 unit
- test_worker.py:               23 unit + 1 integration

---

## Database — PostgreSQL via Supabase

```
Driver:     asyncpg
ORM:        SQLAlchemy 2.0 async
Connection: DATABASE_URL in backend/.env
            (auto-converted postgresql:// → postgresql+asyncpg:// in db/base.py)
Schema:     8 tables, 11 indexes — managed by scripts/setup_db.py (safe to re-run)
Migration:  ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS med_history JSONB
            — included in setup_db.py
```

---

## SQLAlchemy 2.0 Async — Critical Syntax

```python
# CORRECT — always use this pattern
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

async def get_patient(session: AsyncSession, patient_id: str) -> Patient | None:
    result = await session.execute(
        select(Patient).where(Patient.patient_id == patient_id)
    )
    return result.scalar_one_or_none()

# WRONG — never use, breaks async
session.query(Patient).filter(...).first()
```

FastAPI dependency injection (use everywhere in API layer):
```python
from app.db.session import get_session
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

async def my_route(session: AsyncSession = Depends(get_session)):
    ...
```

---

## Pydantic v2 — Critical Syntax

```python
# CORRECT
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    anthropic_api_key: str = ""

# WRONG — never use v1 syntax
class Config:
    env_file = ".env"
```

---

## ORM Models — 8 Tables (backend/app/models/)

### patients
```
patient_id          TEXT PRIMARY KEY   (iEMR MED_REC_NO)
gender              CHAR(1)            M | F | U
age                 SMALLINT
risk_tier           TEXT               high | medium | low
tier_override       TEXT               e.g. "CHF in problem list"
risk_score          NUMERIC(5,2)       Layer 2 score 0.0-100.0 — sort key within tier
monitoring_active   BOOLEAN            FALSE = EHR-only, no home readings
next_appointment    TIMESTAMPTZ
enrolled_at         TIMESTAMPTZ
enrolled_by         TEXT
```

### clinical_context (one row per patient, pre-computed at ingestion)
```
patient_id          TEXT PRIMARY KEY REFERENCES patients
active_problems     TEXT[]             parallel to problem_codes (index n matches)
problem_codes       TEXT[]             ICD-10 or SNOMED
current_medications TEXT[]             parallel to med_rxnorm_codes
med_rxnorm_codes    TEXT[]
med_history         JSONB              full timeline: [{name, rxnorm, date, activity}]
last_med_change     DATE
allergies           TEXT[]
last_visit_date     DATE
last_clinic_systolic   SMALLINT
last_clinic_diastolic  SMALLINT
historic_bp_systolic   SMALLINT[]     all clinic readings chronologically (generator anchor)
historic_bp_dates      DATE[]         parallel to historic_bp_systolic
overdue_labs        TEXT[]             follow-up items (labs, referrals, protocols)
social_context      TEXT
last_updated        TIMESTAMPTZ
```

### readings
```
reading_id          UUID PK
patient_id          TEXT REFERENCES patients
systolic_1          SMALLINT NOT NULL
diastolic_1         SMALLINT NOT NULL
systolic_2          SMALLINT           NULL for single-reading sessions
diastolic_2         SMALLINT
systolic_avg        NUMERIC(5,1)       PRIMARY analysis value — always computed
diastolic_avg       NUMERIC(5,1)
heart_rate_avg      NUMERIC(5,1)
effective_datetime  TIMESTAMPTZ NOT NULL
session             TEXT               morning | evening | ad_hoc
source              TEXT               generated | manual | ble_auto | clinic
submitted_by        TEXT               patient | carer | generator | clinic
```

### medication_confirmations
```
confirmation_id     UUID PK
patient_id          TEXT REFERENCES patients
medication_name     TEXT NOT NULL
rxnorm_code         TEXT
scheduled_time      TIMESTAMPTZ NOT NULL
confirmed_at        TIMESTAMPTZ        NULL = missed dose
confirmation_type   TEXT               synthetic_demo | tap | photo | qr_scan
confidence          TEXT               simulated (generated) | self_report (real)
minutes_from_schedule SMALLINT
```

### alerts
```
alert_id            UUID PK
patient_id          TEXT REFERENCES patients
alert_type          TEXT               gap_urgent | gap_briefing | inertia | deterioration
gap_days            SMALLINT
systolic_avg        NUMERIC(5,1)
triggered_at        TIMESTAMPTZ
acknowledged_at     TIMESTAMPTZ        NULL = unacknowledged
```

### briefings
```
briefing_id         UUID PK
patient_id          TEXT REFERENCES patients
appointment_date    DATE NOT NULL
llm_response        JSONB NOT NULL     9-field structured payload (see briefing/AGENTS.md)
generated_at        TIMESTAMPTZ
delivered_at        TIMESTAMPTZ
read_at             TIMESTAMPTZ        set when GET /api/briefings/{id} is called
model_version       TEXT               populated by Layer 3 (e.g. claude-sonnet-4-20250514)
prompt_hash         TEXT               SHA-256 of the Layer 3 prompt template
```

### processing_jobs
```
job_id              UUID PK
job_type            TEXT               pattern_recompute | briefing_generation | bundle_import
patient_id          TEXT REFERENCES patients
idempotency_key     TEXT NOT NULL UNIQUE
status              TEXT               queued | running | succeeded | failed
error_message       TEXT
queued_at           TIMESTAMPTZ
started_at          TIMESTAMPTZ
finished_at         TIMESTAMPTZ
```

### audit_events
```
audit_id            UUID PK
actor_type          TEXT               system | clinician | admin
patient_id          TEXT REFERENCES patients
action              TEXT               bundle_import | reading_ingested | briefing_viewed | alert_acknowledged
resource_type       TEXT
resource_id         TEXT
outcome             TEXT               success | failure — NEVER omit
event_timestamp     TIMESTAMPTZ
details             TEXT
```

---

## All 11 API Routes

```
GET  /health                          → {"status": "ok", "env": "..."}
GET  /api/patients                    → Patient[] sorted: risk_tier DESC, risk_score DESC
GET  /api/patients/{patient_id}       → Patient | 404
GET  /api/briefings/{patient_id}      → Briefing | 404 (marks read_at, writes audit)
GET  /api/readings?patient_id=        → Reading[] (28-day window)
POST /api/readings                    → 201 Reading (manual entry + audit)
GET  /api/alerts                      → Alert[] (unacknowledged only)
POST /api/alerts/{alert_id}/acknowledge → 200 (writes audit)
GET  /api/adherence/{patient_id}      → AdherenceData[] (28-day, per medication)
POST /api/ingest                      → 201 summary (validates FHIR Bundle then ingests)
POST /api/admin/trigger-scheduler     → {"enqueued": N} (DEMO_MODE=true guard)
```

---

## Audit Rule — No Exceptions

These four actions MUST create an `audit_events` row every time they occur:
```
bundle_import       → action="bundle_import",      resource_type="Bundle"
reading_ingested    → action="reading_ingested",   resource_type="Reading"
briefing_viewed     → action="briefing_viewed",    resource_type="Briefing" + set briefings.read_at
alert_acknowledged  → action="alert_acknowledged", resource_type="Alert"
outcome is always "success" or "failure" — never omit this field
```

---

## Layer Execution Order — Strict

```
Layer 1 (gap + inertia + adherence + deterioration) MUST complete first
  ↓
Layer 2 (compute_risk_score) runs after all Layer 1 detectors
  ↓
Layer 3 (generate_llm_summary) only after Layer 1 briefing is verified
```

Never call `generate_llm_summary()` before `compose_briefing()` has persisted a row.

---

## DO NOT

- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()` only
- Do NOT use Pydantic v1 `class Config:` — use `SettingsConfigDict`
- Do NOT use bare `except:` — catch specific exception types
- Do NOT hardcode values — use `app/config.py` settings or module-level constants
- Do NOT return raw SQLAlchemy ORM objects from API routes — call `.model_dump()` or use response models
- Do NOT add sync database calls anywhere in the API or service layer
- Do NOT skip the audit event — even on failure, write `outcome="failure"`
- Do NOT run `ruff` via `python -m ruff` — the binary is on PATH as `ruff`
