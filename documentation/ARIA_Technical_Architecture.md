# ARIA — Complete Technical Architecture & System Documentation
## Adaptive Real-time Intelligence Architecture | IIT CS 595 | Spring 2026
## Leap of Faith Technologies

---

## Table of Contents

1. [What ARIA Is — The Clinical Problem](#1-what-aria-is--the-clinical-problem)
2. [System Overview — The Full Pipeline](#2-system-overview--the-full-pipeline)
3. [Database Layer — 12 Tables Explained](#3-database-layer--12-tables-explained)
4. [Step 1 — FHIR Data Ingestion](#4-step-1--fhir-data-ingestion)
5. [Step 2 — Synthetic Data Generation](#5-step-2--synthetic-data-generation)
6. [Step 3 — Layer 1: The Rule Engine](#6-step-3--layer-1-the-rule-engine)
   - [Gap Detector](#61-gap-detector)
   - [Therapeutic Inertia Detector](#62-therapeutic-inertia-detector)
   - [Deterioration Detector](#63-deterioration-detector)
   - [Adherence Analyzer](#64-adherence-analyzer)
7. [Step 4 — Layer 2: Risk Scoring](#7-step-4--layer-2-risk-scoring)
8. [Step 5 — Layer 3: LLM Explanation & Validation](#8-step-5--layer-3-llm-explanation--validation)
9. [Step 6 — Briefing Composition](#9-step-6--briefing-composition)
10. [Step 7 — Drug Interaction Detection](#10-step-7--drug-interaction-detection)
11. [Step 8 — Background Worker & Scheduler](#11-step-8--background-worker--scheduler)
12. [Step 9 — Risk Tier Reclassification](#12-step-9--risk-tier-reclassification)
13. [Step 10 — GP Dashboard & Alert Inbox](#13-step-10--gp-dashboard--alert-inbox)
14. [Step 11 — Patient PWA](#14-step-11--patient-pwa)
15. [Step 12 — Chatbot (On-Demand Layer 3)](#15-step-12--chatbot-on-demand-layer-3)
16. [Step 13 — Shadow Mode Validation](#16-step-13--shadow-mode-validation)
17. [API Layer Summary](#17-api-layer-summary)
18. [Security & Audit](#18-security--audit)
19. [Tech Stack Reference](#19-tech-stack-reference)

---

## 1. What ARIA Is — The Clinical Problem

### The Problem

A GP (General Practitioner / family doctor) in the UK manages approximately 1,800 patients. Each consultation is 8 minutes. A hypertension (high blood pressure) patient attends every 3–6 months. Between those visits:

- The patient measures their BP at home — but the GP never sees these readings
- The patient may or may not be taking their medications consistently
- BP may be silently worsening with no intervention
- The GP arrives at the appointment with no structured view of what changed since the last visit

**The GP is flying blind.**

### What ARIA Does

ARIA is a between-visit clinical intelligence platform that:

1. Ingests structured patient data from the hospital EHR (Electronic Health Record) system
2. Generates and collects home BP readings and medication adherence data
3. Runs automated clinical pattern detection every night
4. Delivers a structured pre-visit briefing to the GP at 7:30 AM on appointment day
5. Provides an AI chatbot for on-demand clinical questions during the consultation

### Clinical Boundaries (Non-Negotiable)

ARIA is **decision support only**. It does not:
- Recommend specific medications or dosage adjustments
- Send alerts directly to patients
- Make diagnostic conclusions
- Display clinical intelligence to patients

Every output uses specific controlled language:
- "possible adherence concern" — never "non-adherent"
- "treatment review warranted" — never "medication failure"
- "sustained elevated readings" — never "hypertensive crisis"

These boundaries are enforced at code level through LLM output validation, not just as style guidelines.

---

## 2. System Overview — The Full Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                                 │
│  iEMR Hospital EHR  ──►  FHIR R4 Adapter  ──►  FHIR Ingestion     │
│  Patient PWA (home)  ──►  BP Readings + Medication Confirmations   │
│  Synthetic Generator ──►  Clinically realistic demo data           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PostgreSQL Database                             │
│  12 tables: patients, readings, clinical_context, alerts,          │
│  briefings, medication_confirmations, processing_jobs,             │
│  audit_events, alert_feedback, gap_explanations,                   │
│  calibration_rules, outcome_verifications                          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼ (midnight nightly + 7:30 AM)
┌─────────────────────────────────────────────────────────────────────┐
│               THREE-LAYER AI ARCHITECTURE                           │
│                                                                     │
│  Layer 1 — Deterministic Rule Engine (always runs first)           │
│    ├── Gap Detector (SQL: days since last reading)                 │
│    ├── Therapeutic Inertia Detector (elevated BP + no med change)  │
│    ├── Deterioration Detector (BP worsening trend)                 │
│    └── Adherence Analyzer (dose confirmation rate)                 │
│                           │                                         │
│                           ▼ (after Layer 1 completes)              │
│  Layer 2 — Risk Scorer                                             │
│    └── Weighted score 0.0–100.0 → stored on patients table        │
│                           │                                         │
│                           ▼ (after Layer 2 completes, optional)    │
│  Layer 3 — LLM Explanation                                         │
│    └── claude-sonnet-4-20250514 → 3-sentence readable summary     │
│        Validated by guardrail checker before storing               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    GP DASHBOARD (Next.js 14)                        │
│  Patient list sorted by tier + score                               │
│  Alert inbox with disposition options                              │
│  Pre-visit briefing card                                           │
│  AI chatbot for on-demand questions                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Database Layer — 12 Tables Explained

The entire system is backed by a single PostgreSQL database hosted on Supabase, accessed via the asyncpg driver with SQLAlchemy 2.0 async ORM.

### 3.1 `patients` — The Central Registry

Every patient who has been ingested into ARIA has exactly one row here.

| Column | Type | Purpose |
|---|---|---|
| `patient_id` | TEXT PK | iEMR medical record number (e.g. "1091") |
| `name` | TEXT | Patient full name (nullable) |
| `gender` | CHAR(1) | M, F, or U |
| `age` | SMALLINT | Current age |
| `risk_tier` | TEXT | high, medium, or low |
| `tier_override` | TEXT | Human-readable reason for override |
| `tier_override_source` | TEXT | system, system_score, clinician, or NULL |
| `tier_override_suppressed_until` | TIMESTAMPTZ | Clinician override lockout expiry |
| `risk_score` | NUMERIC(5,2) | Layer 2 score, 0.0–100.0 |
| `risk_score_computed_at` | TIMESTAMPTZ | When score was last computed |
| `monitoring_active` | BOOLEAN | Whether home monitoring is enabled |
| `next_appointment` | TIMESTAMPTZ | Upcoming consultation date |
| `enrolled_at` | TIMESTAMPTZ | When patient joined ARIA |
| `enrolled_by` | TEXT | Clinician who enrolled them |

**Why `tier_override_source` matters:** The system distinguishes between overrides set by EHR diagnosis (immovable — CHF, stroke, TIA), by the scoring algorithm, and by the clinician. A `system` override cannot be changed by anyone except re-ingesting the EHR with a different diagnosis. A `clinician` override is suppressed for 28 days (NICE NG136 §1.6.3).

### 3.2 `clinical_context` — Pre-computed Clinical Summary

One row per patient. Populated at ingestion from the FHIR Bundle. Contains everything ARIA knows about the patient's clinical history.

| Column | Type | Purpose |
|---|---|---|
| `active_problems` | TEXT[] | Problem names (e.g. ["Hypertension", "CHF"]) |
| `problem_codes` | TEXT[] | Parallel ICD-10/SNOMED codes |
| `current_medications` | TEXT[] | Current regimen names |
| `med_rxnorm_codes` | TEXT[] | Parallel RxNorm codes |
| `med_history` | JSONB | Full medication timeline sorted chronologically |
| `last_med_change` | DATE | Most recent medication change date (stale snapshot) |
| `allergies` | TEXT[] | Allergy names |
| `allergy_reactions` | TEXT[] | Parallel reaction descriptions |
| `last_visit_date` | DATE | Most recent clinic visit |
| `last_clinic_systolic` | SMALLINT | Last measured clinic BP |
| `historic_bp_systolic` | SMALLINT[] | All historical clinic systolic readings |
| `historic_bp_dates` | DATE[] | Parallel dates for historic readings |
| `overdue_labs` | TEXT[] | Labs that are past due date |
| `social_context` | TEXT | Living situation, carer notes, social factors |
| `recent_labs` | JSONB | Lab results keyed by LOINC code |
| `problem_assessments` | JSONB | Physician assessment text per problem per visit |

**Important:** `last_med_change` is a stale single snapshot. All detectors use `med_history` JSONB for accuracy — it contains the full timeline of every medication change.

### 3.3 `readings` — Home BP Measurements

Every home BP reading submitted by the patient (or generator) creates one row.

| Column | Type | Purpose |
|---|---|---|
| `reading_id` | UUID PK | Auto-generated |
| `systolic_1`, `diastolic_1` | SMALLINT | First reading of the session |
| `systolic_2`, `diastolic_2` | SMALLINT | Second reading (NULL if single) |
| `systolic_avg`, `diastolic_avg` | NUMERIC(5,1) | **Primary analysis values** |
| `effective_datetime` | TIMESTAMPTZ | When the reading was taken |
| `session` | TEXT | morning, evening, or ad_hoc |
| `source` | TEXT | generated, manual, ble_auto, or clinic |
| `submitted_by` | TEXT | patient, carer, generator, or clinic |
| `medication_taken` | TEXT | yes, no, partial, or NULL |
| `symptoms` | TEXT[] | Patient-reported: headache, dizziness, etc. |

**Critical design decision:** Device outages are represented as **absent rows**, never NULL values. This is clinically accurate — a device that was off produces no data, not zero data.

**Idempotency:** UNIQUE constraint on `(patient_id, effective_datetime, source)` prevents duplicate ingestion.

### 3.4 `medication_confirmations` — Adherence Tracking

Records whether a patient confirmed taking each scheduled dose.

| Column | Type | Purpose |
|---|---|---|
| `confirmation_id` | UUID PK | Auto-generated |
| `medication_name` | TEXT | Drug name |
| `scheduled_time` | TIMESTAMPTZ | When dose was due |
| `confirmed_at` | TIMESTAMPTZ | When patient confirmed (NULL = missed) |
| `confirmation_type` | TEXT | synthetic_demo, tap, photo, qr_scan |
| `minutes_from_schedule` | SMALLINT | How late/early the confirmation was |

**NULL vs absent:** `confirmed_at = NULL` means a missed dose (the row exists, but no confirmation). An absent row means the scheduled time hasn't arrived yet.

### 3.5 `alerts` — Clinical Flags

Every pattern detection event that crosses a threshold creates an alert row.

| Column | Type | Purpose |
|---|---|---|
| `alert_id` | UUID PK | Auto-generated |
| `alert_type` | TEXT | gap_urgent, gap_briefing, inertia, deterioration, adherence |
| `gap_days` | SMALLINT | For gap alerts: days without a reading |
| `systolic_avg` | NUMERIC(5,1) | For inertia/deterioration: the elevated average |
| `triggered_at` | TIMESTAMPTZ | When the pattern was detected |
| `acknowledged_at` | TIMESTAMPTZ | When the GP reviewed it (NULL = unreviewed) |
| `off_hours` | BOOLEAN | True if detected outside 8AM–6PM or on weekends |
| `escalated` | BOOLEAN | True if unacknowledged for >24h (gap_urgent/deterioration only) |

### 3.6 `briefings` — Pre-visit Intelligence Packages

Each briefing is a complete JSON document generated before a patient appointment.

| Column | Type | Purpose |
|---|---|---|
| `briefing_id` | UUID PK | Auto-generated |
| `appointment_date` | DATE | The appointment this briefing is for (NULL = mini-briefing) |
| `llm_response` | JSONB | The full briefing payload (see Section 9) |
| `model_version` | TEXT | Claude model used for Layer 3 |
| `prompt_hash` | TEXT | SHA-256 of the Layer 3 prompt (for reproducibility) |
| `generated_at` | TIMESTAMPTZ | When briefing was created |
| `read_at` | TIMESTAMPTZ | When GP first opened it |

**Mini-briefings:** When a gap_urgent or deterioration alert fires outside appointment days, ARIA generates a mini-briefing (appointment_date = NULL) to give the GP current context.

**Active briefing rule:** The API returns only the most recent active briefing. "Active" means appointment_date is NULL (mini-briefing) OR appointment_date >= today. Past appointment briefings are excluded — the GP sees current state, not stale pre-visit content.

### 3.7 `processing_jobs` — Job Queue

Background work is coordinated through this table. The worker polls it every 30 seconds.

| Column | Type | Purpose |
|---|---|---|
| `job_type` | TEXT | pattern_recompute, briefing_generation, bundle_import |
| `idempotency_key` | TEXT UNIQUE | Prevents duplicate jobs |
| `status` | TEXT | queued → running → succeeded or failed |
| `error_message` | TEXT | Failure details if status=failed |

**Idempotency key format:**
- Pattern recompute: `"pattern_recompute:{patient_id}:{YYYY-MM-DD}"`
- Briefing generation: `"briefing_generation:{patient_id}:{YYYY-MM-DD}"`
- Bundle import: `"bundle_import:{patient_id}:{bundle_hash}"`

### 3.8 `audit_events` — Immutable Audit Trail

Every significant system action creates an audit row. These are append-only and never updated.

| Column | Type | Purpose |
|---|---|---|
| `actor_type` | TEXT | system, clinician, or admin |
| `actor_id` | TEXT | Who performed the action |
| `action` | TEXT | bundle_import, reading_ingested, briefing_viewed, alert_acknowledged, llm_validation |
| `outcome` | TEXT | success or failure — never omitted |
| `details` | TEXT | Failure reason for llm_validation; resource details otherwise |

### 3.9 `alert_feedback` — Clinician Disposition

When a GP acknowledges an alert, their clinical judgement is recorded here.

| Column | Type | Purpose |
|---|---|---|
| `alert_id` | UUID | Which alert was reviewed |
| `disposition` | TEXT | agree_acting, agree_monitoring, or disagree |
| `detector_type` | TEXT | gap, inertia, deterioration, or adherence |
| `reason_text` | TEXT | Optional free-text note |

### 3.10 `gap_explanations` — Clinical Context for Gaps

GPs can explain why a patient had a reading gap.

| Column | Type | Purpose |
|---|---|---|
| `reason` | TEXT | device_issue, travel, illness, unknown, non_compliance |
| `gap_start`, `gap_end` | DATE | The dates covered |
| `notes` | TEXT | Free-text clinical context |

### 3.11 `calibration_rules` — Per-patient Detector Tuning

If a detector fires consistently but the GP disagrees, a calibration rule suppresses future alerts for that patient/detector combination.

### 3.12 `outcome_verifications` — 30-day Follow-up Audit

When a GP disagrees with an alert, ARIA checks back 30 days later to see if the dismissed signal was actually a warning of future deterioration.

---

## 4. Step 1 — FHIR Data Ingestion

### 4.1 The Clinical Data Problem

The hospital uses a system called **iEMR** (integrated Electronic Medical Record). iEMR stores data in its own proprietary JSON format with fields like `PROBLEM`, `MEDICATIONS`, `VITALS`, `ALLERGY`. ARIA needs this data but cannot natively read iEMR format.

**Solution:** ARIA converts iEMR data to **FHIR R4** (Fast Healthcare Interoperability Resources, Version 4) — the international healthcare data exchange standard — and then ingests the FHIR Bundle into its own database.

### 4.2 The FHIR Adapter (`fhir/adapter.py`)

The adapter takes raw iEMR JSON and produces a standards-compliant FHIR R4 Bundle.

**What gets mapped:**

| iEMR Field | FHIR Resource | ARIA Table |
|---|---|---|
| `PROBLEM.value` + `PROBLEM_CODE` | `Condition` (clinicalStatus=active) | `clinical_context.active_problems` |
| `MEDICATIONS` (current visit) | `MedicationRequest` (most-recent-wins) | `clinical_context.current_medications` |
| `MEDICATIONS` (all visits) | `_aria_med_history` (non-FHIR key) | `clinical_context.med_history` JSONB |
| `VITALS SYSTOLIC + DIASTOLIC` | `Observation` LOINC 55284-4 | `readings` table |
| `VITALS PULSE` | `Observation` LOINC 8867-4 | `clinical_context.last_clinic_pulse` |
| `VITALS WEIGHT` | `Observation` LOINC 29463-7 | `clinical_context.last_clinic_weight_kg` |
| `VITALS SpO2` | `Observation` LOINC 59408-5 | `clinical_context.last_clinic_spo2` |
| `ALLERGY` (Active only) | `AllergyIntolerance` | `clinical_context.allergies` |
| `PLAN` (PLAN_NEEDS_FOLLOWUP=YES) | `ServiceRequest` | `clinical_context.overdue_labs` |
| Problem assessments | `_aria_problem_assessments` (non-FHIR key) | `clinical_context.problem_assessments` JSONB |

**Key design decisions:**

1. **Multi-visit deduplication:** When a patient has 65 clinic visits, the adapter processes all of them. For Conditions and MedicationRequests, later visits overwrite earlier ones (most recent state wins). For Observations (BP readings), every visit creates a new row — the full history is preserved.

2. **effectiveDateTime from VITALS_DATETIME:** Vital sign observations use the actual measurement timestamp, not the visit admission date. This is clinically critical — using ADMIT_DATE would misplace readings by hours or days.

3. **Non-FHIR metadata injection:** FHIR R4 has no standard resource for medication timelines or per-visit assessment notes. ARIA injects these as custom keys (`_aria_med_history`, `_aria_problem_assessments`, `_aria_visit_dates`) in the Bundle. The ingestion layer reads these special keys and stores them in the appropriate JSONB columns.

4. **ICD-10 normalisation:** Problem codes are normalised (dots removed, uppercased) so `I50.9`, `i50.9`, and `I509` all match as CHF.

### 4.3 The Ingestion Pipeline (`fhir/ingestion.py`)

After the adapter produces a FHIR Bundle, the ingestion pipeline writes it to the database in strict dependency order.

```
FHIR Bundle
    │
    ├─► 1. Patient resource → patients table
    │       (INSERT ... ON CONFLICT DO NOTHING — never overwrite existing)
    │
    ├─► 2. All resources → clinical_context table
    │       (INSERT ... ON CONFLICT DO UPDATE — always refresh clinical data)
    │
    ├─► 3. Observation resources → readings table (source="clinic")
    │       (INSERT ... ON CONFLICT DO NOTHING per unique constraint)
    │
    └─► 4. Audit event → audit_events table
            (Always written, even if all above steps had no new data)
```

**Risk tier auto-overrides at ingestion:**

| Diagnosis | ICD-10 Code | Override Applied |
|---|---|---|
| Congestive Heart Failure | I50.x | risk_tier="high", source="system" |
| Haemorrhagic stroke | I61.x | risk_tier="high", source="system" |
| Ischaemic stroke | I63.x / I64.x | risk_tier="high", source="system" |
| TIA | G45.x | risk_tier="high", source="system" |

These overrides have `tier_override_source = "system"` and are **immovable** — no clinician, no scoring algorithm can demote these patients below High. Only re-ingesting the EHR with a different diagnosis clears them.

**Cold-start suppression:** If the patient was enrolled less than 21 days ago, the inertia, deterioration, and adherence detectors are suppressed. The gap detector still runs. This ensures at least 7 home readings exist before any pattern analysis begins.

---

## 5. Step 2 — Synthetic Data Generation

### 5.1 Why Synthetic Data?

ARIA is a research prototype (IIT CS 595). Real patients do not yet have home monitoring devices connected to ARIA. The synthetic generator fills this gap by producing clinically realistic data that passes scrutiny from a medical reviewer.

The generator is also used to set up the 4 demo patients for presentations.

### 5.2 Reading Generator (`generator/reading_generator.py`)

**Baseline calculation:**

The generator does not use a hardcoded baseline. It computes the patient's personal baseline from their historical clinic readings:

```
baseline = median(historic_bp_systolic)
```

If fewer than 2 clinic readings exist, falls back to 163.0 mmHg.

For Patient 1091, 65 clinic readings (2008–2013) give a mean of 133.8 mmHg and SD of 16.2. The demo window (~158 mmHg) reflects the 2011–2013 period of the clinic history.

**Clinical realism rules (non-negotiable):**

| Rule | Technical Implementation |
|---|---|
| Day-to-day SD: 8–12 mmHg | Gaussian noise: `systolic += random.gauss(0, 10)` |
| Morning 5–10 mmHg higher than evening | Session offset added to morning readings |
| Never round numbers | Anti-rounding: `reading += random.uniform(-2, 2)` |
| Two-reading session | Reading 2 = Reading 1 − random(2, 6) |
| Diastolic ratio | `diastolic = systolic × random.uniform(0.60, 0.66)` |
| Heart rate | 64–82 bpm, slight negative correlation with systolic when beta-blocker present |
| Device outage | 1–2 episodes of 2–4 absent days per inter-visit interval (rows simply not created) |
| White-coat dip | Systolic drops 10–15 mmHg in 3–5 days before appointment |
| Post-appointment return | Returns to elevated baseline after the dip |

**Full timeline generation:**

For demo patients, the generator does not just create 28 days of readings. It generates synthetic readings for the **entire care timeline** — for each consecutive pair of clinic readings, it linearly interpolates between the two anchors and adds Gaussian noise. This produces a continuous home monitoring history that mirrors what the patient would have had if monitoring existed throughout their care.

**Two-reading protocol:**

Following the European Society of Hypertension (ESH) guideline, each session produces two readings taken approximately 2 minutes apart. The average of both is used for all analysis. The second reading is always slightly lower than the first (biological reflex relaxation effect).

**White-coat exclusion window:**

Readings generated within 3–5 days of the next appointment show the white-coat dip. The detectors then apply a 5-day exclusion window before the appointment to prevent this physiological artefact from falsely suppressing inertia or deterioration signals.

### 5.3 Confirmation Generator (`generator/confirmation_generator.py`)

Generates medication adherence data over the full care timeline.

**Adherence model:**

```
Per-interval adherence rate ~ Beta(α, β)
  anchored near 91% mean with ±10–15 percentage point variation
```

A Beta distribution is used because real patient adherence is bounded (0–100%) and right-skewed — most patients confirm most doses, but with natural variation.

**Dosing frequency detection:**

The generator parses medication names to determine how many doses per day to schedule:

- "BID" / "twice" / "two times" → 2 doses/day
- "TID" / "three times" → 3 doses/day
- "QID" / "four times" → 4 doses/day
- Default → 1 dose/day (QD)

**Supply/test filtering:**

Non-pharmaceutical items are excluded from confirmation scheduling: syringes, test containers, glucose meters, lancets, nebulisers. These are identified by keyword matching against the medication name.

**Idempotency:**

The unique constraint on `(patient_id, medication_name, scheduled_time)` prevents duplicate confirmations on re-runs.

---

## 6. Step 3 — Layer 1: The Rule Engine

Layer 1 runs every night at midnight via the background worker for every monitoring-active patient. It is pure deterministic SQL and Python — no AI, no inference, no probability. It either detects a pattern or it does not.

**Why deterministic first?** If an LLM flagged a patient directly, you could never explain why or audit the decision. Layer 1 produces an auditable, explainable finding that Layer 3 can then summarise in readable language.

**Adaptive detection window (applies to all four detectors):**

```python
if next_appointment and last_visit_date and interval > 0:
    window_days = min(90, max(14, (next_appointment - last_visit_date).days))
else:
    window_days = 28  # fallback
```

This means the analysis window adapts to the actual gap between appointments. A patient seen every 3 months gets a 90-day window. A patient seen every 2 weeks gets a 14-day window. This is clinically correct — the relevant monitoring period is the inter-visit interval.

**Patient-specific threshold (used by inertia and deterioration):**

```python
stable_readings = [r for r in historic_bp_systolic if r is "stable visit"]
patient_threshold = max(130, mean(stable_readings) + 1.5 × SD(stable_readings))
patient_threshold = min(patient_threshold, 145)  # cap
```

**Comorbidity adjustment:** −7 mmHg from the threshold (floor 130) when CHF, Stroke, or TIA is active, OR when both cardiovascular and metabolic comorbidities are elevated. These patients need tighter control, so ARIA flags earlier.

### 6.1 Gap Detector

**File:** `backend/app/services/pattern_engine/gap_detector.py`

**The question:** Has this patient stopped sending home BP readings?

**Logic:**

```python
last_reading = SELECT MAX(effective_datetime) FROM readings
               WHERE patient_id = ? AND source != 'clinic'

gap_days = (now - last_reading).days
```

**Tier-aware thresholds:**

| Risk Tier | Flag Threshold | Urgent Threshold |
|---|---|---|
| High | 1 day | 3 days |
| Medium | 3 days | 5 days |
| Low | 7 days | 14 days |

High-risk patients (CHF, stroke) are flagged after just 1 day without a reading because a gap in monitoring for these patients has serious clinical consequences.

**Output — `GapResult`:**
```python
{
    "status": "none" | "flag" | "urgent",
    "gap_days": 9,
    "triggered_at": "2026-05-02T00:05:00Z",
    "thresholds": {"flag": 3, "urgent": 5}
}
```

**Alert written:** If status is "flag" → `gap_briefing` alert type. If "urgent" → `gap_urgent` alert type.

**`off_hours` flag:** Set to True if the nightly job runs during 6PM–8AM UTC or on a weekend. This is stamped from the job execution time, not the reading datetime.

### 6.2 Therapeutic Inertia Detector

**File:** `backend/app/services/pattern_engine/inertia_detector.py`

**The question:** Has BP been consistently elevated for an extended period with no medication change? This is called "therapeutic inertia" — a clinically recognised failure mode where a doctor repeatedly sees elevated BP but delays adjusting treatment.

**Five conditions — ALL must be true:**

**Condition 1:** At least 5 readings at or above the patient threshold
```python
elevated_readings = [r for r in window_readings if r.systolic_avg >= patient_threshold]
len(elevated_readings) >= 5
```

**Condition 2:** The elevation spans more than 7 days
```python
duration = (last_elevated_reading - first_elevated_reading).days
duration > 7
```

**Condition 3:** No medication change since before the elevated window started
```python
# Uses med_history JSONB — NOT last_med_change column (stale snapshot)
last_change_date = max(
    med["date"] for med in med_history
    if med["date"] <= first_elevated_reading.date()
)
last_change_date < window_start  # or no change at all
```

**Condition 4:** The 7-day recent average is still at or above threshold (not declining)
```python
recent_7day_avg = mean([r.systolic_avg for r in readings[-7:]])
recent_7day_avg >= patient_threshold
```

**Condition 5:** The overall average across the window is elevated
```python
window_avg = mean([r.systolic_avg for r in window_readings])
window_avg >= patient_threshold
```

**White-coat exclusion:**
Readings within 5 days of `next_appointment` are excluded from all inertia calculations. This prevents the pre-appointment white-coat dip from falsely masking a real inertia signal.

**Titration suppression:** If a medication was recently changed, inertia is not flagged until the drug's therapeutic window has passed. Window is drug-class-aware:

| Drug Class | Titration Window |
|---|---|
| Diuretics, Beta-blockers | 14 days |
| ACE inhibitors, ARBs | 28 days |
| Amlodipine (CCB) | 56 days |
| Default | 42 days |

**Output — `InertiaResult`:**
```python
{
    "inertia_detected": True,
    "avg_systolic": 157.3,
    "elevated_count": 19,
    "duration_days": 28,
    "window_days": 28,
    "patient_threshold": 138.5,
    "triggered_at": "2026-05-02T00:05:00Z"
}
```

### 6.3 Deterioration Detector

**File:** `backend/app/services/pattern_engine/deterioration_detector.py`

**The question:** Is this patient's BP measurably and consistently worsening? Not just high — trending upward.

**Primary signal — Three gates (all must pass):**

**Gate 1:** Positive linear slope across the window
```python
# Ordinary least squares regression
slope = polyfit(x=day_numbers, y=systolic_values, deg=1)[0]
slope > 0  # upward trend
```

**Gate 2:** Recent 3-day average exceeds days 4–10 baseline
```python
recent_avg = mean(readings[-3:].systolic_avg)
baseline_avg = mean(readings[-10:-3].systolic_avg)
recent_avg > baseline_avg
```

**Gate 3:** Recent average is at or above patient threshold (absolute gate)
```python
recent_avg >= patient_threshold
# Prevents firing on e.g. 115→119 — a small rise in normal range
```

**Step-change sub-detector (OR gate — fires independently):**

If any of the three gates fail but a dramatic step-change occurred:
```python
recent_7d_mean = mean(readings[-7:].systolic_avg)
prior_7d_mean = mean(readings[-21:-14].systolic_avg)  # 3 weeks ago
step = recent_7d_mean - prior_7d_mean

if step >= 15 and recent_7d_mean >= patient_threshold:
    # Deterioration flagged regardless of slope
```

This catches patients where BP jumped suddenly (e.g. medication stopped) rather than gradually drifted up.

**Minimum data requirement:** At least 7 readings in the window. Fewer than 7 → no detection (insufficient for trend analysis).

**White-coat exclusion:** Same 5-day pre-appointment exclusion as inertia.

**Output — `DeteriorationResult`:**
```python
{
    "deterioration_detected": True,
    "slope": 0.8,           # mmHg per day
    "recent_avg": 162.4,    # 3-day recent average
    "baseline_avg": 151.1,  # days 4-10 baseline
    "step_change": None,    # or step value if sub-detector fired
    "triggered_at": "2026-05-02T00:05:00Z"
}
```

### 6.4 Adherence Analyzer

**File:** `backend/app/services/pattern_engine/adherence_analyzer.py`

**The question:** Is the patient taking their medications? And if not (or even if yes), what does that mean given their current BP?

**Adherence calculation:**
```python
for each medication:
    scheduled = COUNT(confirmations WHERE scheduled_time in window)
    confirmed = COUNT(confirmations WHERE confirmed_at IS NOT NULL in window)
    adherence_pct = (confirmed / scheduled) × 100

overall_adherence = mean(all medication adherence rates)
```

**Clinical threshold:** <80% adherence flags a concern. This is the NICE-recommended threshold.

**Pattern matrix:**

| BP Level | Adherence | Pattern | Clinical Interpretation |
|---|---|---|---|
| Elevated (≥ threshold) | Low (<80%) | A | "Possible adherence concern" — missed doses may explain elevated BP |
| Elevated (≥ threshold) | High (≥80%) | B | "Treatment review warranted" — BP elevated despite good adherence |
| Normal (< threshold) | Low (<80%) | C | "Contextual review" — BP controlled despite missed doses |
| Normal (< threshold) | High (≥80%) | None | No concern |

**Pattern B suppression (treatment working):**

Pattern B fires when BP is high despite good adherence. But if a medication was recently changed and appears to be working (BP declining), Pattern B should be suppressed — the treatment is taking effect.

All three conditions must be true to suppress:
```python
slope < -0.3          # BP declining (at least 0.3 mmHg/day)
recent_7d_avg < patient_threshold  # actually below threshold recently
days_since_med_change <= titration_window  # within the drug's expected effect window
```

**Critical guard:** Suppression MUST NOT apply when there is no recent medication change. A patient with stable high BP and high adherence who had their last med change 2 years ago — Pattern B must fire.

**Language enforcement (code-level):**

The analyzer never writes "non-adherent" or "medication failure" to any output field. The validator will reject any LLM summary containing these words.

**Output — `AdherenceResult`:**
```python
{
    "pattern": "A",  # or "B", "C", "none"
    "overall_adherence_pct": 58.3,
    "per_medication": [
        {"name": "Amlodipine", "adherence_pct": 61.0, "confirmed": 17, "scheduled": 28},
        {"name": "Ramipril",   "adherence_pct": 55.5, "confirmed": 15, "scheduled": 27}
    ],
    "bp_elevated": True,
    "triggered_at": "2026-05-02T00:05:00Z"
}
```

---

## 7. Step 4 — Layer 2: Risk Scoring

**File:** `backend/app/services/pattern_engine/risk_scorer.py`

After Layer 1 detects patterns, Layer 2 answers a different question: **"Given all the clinical signals, how urgent is this patient relative to all other patients?"**

The risk score (0.0–100.0) drives dashboard sort order and determines which patients the GP sees first.

### 7.1 The Formula

```
score = (systolic_signal  × 0.30)
      + (inertia_signal   × 0.25)
      + (adherence_signal × 0.20)
      + (gap_signal       × 0.15)
      + (comorbidity_score × 0.10)
```

### 7.2 Component Calculations

**Systolic signal (30%):**
```python
# How far above the patient's personal baseline is their average BP?
sig_systolic = clamp((avg_systolic - baseline) / 30.0 × 100.0, 0, 100)
# +30 mmHg above baseline = 100 points; at baseline = 0 points
```

**Inertia signal (25%):**
```python
# How long since the last medication change? Saturates at 6 months.
sig_inertia = clamp(days_since_med_change / 180.0 × 100.0, 0, 100)
# 180 days = 100 points; recent change = 0 points
```

**Adherence signal (20%):**
```python
# Inverted adherence — missed doses score higher
sig_adherence = clamp(100 - overall_adherence_pct, 0, 100)
# 0% adherence = 100 points; 100% adherence = 0 points
```

**Gap signal (15%):**
```python
# Normalised to the adaptive window — not hardcoded to 14 days
sig_gap = clamp(gap_days / window_days × 100.0, 0, 100)
# Full window gap = 100 points; no gap = 0 points
```

**Comorbidity score (10%):**
```python
# Severity-weighted — NOT a raw count divided by 5
score = 0
for code in problem_codes:
    if code in CHF_STROKE_TIA:   score += 25  # I50, I61, I63, I64, G45
    elif code in METABOLIC:       score += 15  # E11, N18, I25
    else:                         score += 5
comorbidity_score = clamp(score, 0, 100)
```

**Why severity-weighted?** A patient with CHF + Diabetes is clinically very different from a patient with two mild conditions. Raw count scoring fails to capture this. A single CHF diagnosis scores 25 of a possible 100 on this component.

### 7.3 Storage and Staleness

After computation:
```python
patient.risk_score = round(final_score, 2)
patient.risk_score_computed_at = datetime.now(UTC)
```

A staleness badge appears in the UI if `risk_score_computed_at` is more than 26 hours ago — this catches patients who missed a nightly computation cycle.

### 7.4 Dashboard Sort Order

```sql
SELECT * FROM patients
ORDER BY
    CASE risk_tier WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 END,
    risk_score DESC
```

Tier is always primary — a Medium patient with a score of 95 never appears above a High patient with a score of 10. Within each tier, higher scores appear first.

---

## 8. Step 5 — Layer 3: LLM Explanation & Validation

**Files:** `backend/app/services/briefing/summarizer.py`, `backend/app/services/briefing/llm_validator.py`

### 8.1 What Layer 3 Does

Layer 3 converts the deterministic Layer 1 briefing JSON into a 3-sentence human-readable summary that a GP can read in seconds before entering the consultation room.

**Model:** `claude-sonnet-4-20250514`

**Input:** The full Layer 1 briefing payload (trend data, medication history, adherence rates, alerts, risk score)

**Output:**
```json
{
  "readable_summary": "Home BP has averaged 157 mmHg over the past 28 days..."
}
```

**Critical constraint:** Layer 3 can only explain what Layer 1 already found. It cannot introduce new clinical conclusions, recommend medications, or infer diagnoses.

### 8.2 The Prompt

The system prompt (`prompts/briefing_summary_prompt.md`) instructs the model to:
- Produce exactly 3 sentences
- Lead with the most clinically urgent finding
- Use only data present in the payload
- Use controlled language ("possible adherence concern", "treatment review warranted")
- Never address the patient directly
- Never name specific medications to adjust

### 8.3 The Validator (`llm_validator.py`)

Before any LLM output is stored, it passes through a multi-group validator. If validation fails, the output is retried once. If it fails again, `readable_summary = None` and the structured briefing is shown directly.

**Group A — Safety checks (automatic reject):**

| Check | Rule |
|---|---|
| PHI leak | patient_id verbatim in output |
| Prompt injection | "[INST]", "system:", "ignore previous" |

**Group B — Clinical language guardrails:**

| Forbidden phrase | Reason |
|---|---|
| "non-adherent", "non-compliant" | Stigmatising language |
| "hypertensive crisis" | Alarmist; not ARIA's role |
| "medication failure" | Clinical conclusion |
| "increase.*mg", "decrease.*mg", "prescribe" | Prescriptive action |
| "tell the patient" | Patient-facing language |
| "diagnos" | Diagnostic conclusion |
| "emergency" | Alarmist |

**Group C — Faithfulness checks:**

| Check | Rule |
|---|---|
| Sentence count | Must be exactly 3 sentences |
| Risk score | Referenced score must be within ±10 of Layer 2 score |
| Adherence language | Pattern A → concern language; Pattern B → treatment review language |
| Titration notice | "titration" can only appear if titration notice is in medication_status |
| Urgent flag | "urgent" can only appear if urgent_flags is non-empty |
| Overdue labs | Lab references must exist in overdue_labs |
| Condition grounding | Conditions mentioned must be in active_problems (synonym-aware) |
| Drug names | Drug names mentioned must be in medication_status |
| BP plausibility | All BP values must be 60–250 mmHg and within ±20 of trend data |
| Drug interactions | Interaction claims must match drug_interactions payload |

**Audit:** Every LLM validation attempt creates an `audit_events` row with action="llm_validation", outcome="success" or "failure", and the specific failed check in the details field.

### 8.4 Prompt Integrity

The SHA-256 hash of the Layer 3 prompt is stored in `briefings.prompt_hash`. This allows auditors to verify exactly which prompt version produced any given briefing — important for clinical accountability.

---

## 9. Step 6 — Briefing Composition

**File:** `backend/app/services/briefing/composer.py`

The briefing composer orchestrates all Layer 1 detector outputs into a single structured JSON document — the `llm_response` JSONB field stored in the briefings table.

### 9.1 Full Briefing Payload Structure

```json
{
  "trend_summary": "Narrative description of BP trend over adaptive window",
  "trend_avg_systolic": 157.3,
  "medication_status": "Current regimen + last change date + titration notice if applicable",
  "adherence_summary": "Per-medication rates + pattern interpretation",
  "active_problems": ["Congestive Heart Failure", "Hypertension", "Type 2 Diabetes"],
  "problem_assessments": {
    "Hypertension": "Most recent physician assessment text from clinic visit"
  },
  "overdue_labs": ["HbA1c (due 2026-01-14)", "BNP (due 2026-03-01)"],
  "drug_interactions": [
    {
      "rule": "triple_whammy",
      "severity": "critical",
      "drugs_involved": ["Ibuprofen", "Lisinopril", "Furosemide"],
      "description": "NSAID + ACE inhibitor + diuretic combination...",
      "comorbidity_amplified": true
    }
  ],
  "visit_agenda": [
    "Review critical drug interaction: triple whammy combination",
    "Review sustained elevated home BP (avg 157 mmHg) — treatment review warranted",
    "Address 3 overdue laboratory investigations"
  ],
  "urgent_flags": ["Therapeutic inertia: no medication change in 186 days"],
  "risk_score": 74.5,
  "data_limitations": "Home monitoring active. 4 absent days (device outage).",
  "patient_context": "Lives alone. No carer. Works night shifts."
}
```

### 9.2 Visit Agenda Priority Order

The visit agenda is a prioritised action list for the GP, with a maximum of 6 items. Priority order:

```
0. Critical drug interactions (patient safety — always first)
1. Urgent unacknowledged alerts + concern-level drug interactions
2. Therapeutic inertia flag
3. Adherence concern (Pattern A) + warning-level interactions
4. Elevated BP variability flag
5. Overdue labs
6. Active problems review + next appointment scheduling
```

### 9.3 Trend Average — Single Source of Truth

`trend_avg_systolic` is the home-readings-only average (excludes source="clinic") for the adaptive window. This exact number is surfaced via `GET /api/patients` so the dashboard BP Trend column shows the identical number the briefing shows. No independent recomputation occurs on the frontend.

When no active briefing exists (between visits), the frontend falls back to a live 28-day computation with white-coat exclusion.

### 9.4 Mini-Briefings

When a gap_urgent or deterioration alert fires outside of appointment days, the composer generates a mini-briefing with `appointment_date = NULL`. This gives the GP current context even when no appointment is scheduled. Mini-briefings are always active and are never filtered out by the "past appointment" exclusion.

---

## 10. Step 7 — Drug Interaction Detection

**File:** `backend/app/services/briefing/medication_safety.py`

Drug interactions are detected deterministically — no LLM. The detector takes the patient's current medication list and active problem codes and checks against four hard-coded clinical rules.

### 10.1 Four Rules

**Rule 1: NSAID + Antihypertensive**

NSAIDs (ibuprofen, naproxen, diclofenac, etc.) reduce the efficacy of antihypertensive medications by causing sodium retention and vasoconstriction.

- Base severity: warning
- Escalates to: concern (if CHF or CKD active — these patients have less renal reserve)

**Rule 2: Triple Whammy**

The most serious combination: NSAID + ACE inhibitor/ARB + diuretic. All three together dramatically increase risk of acute kidney injury.

- Base severity: concern
- Escalates to: critical (if both CHF and CKD active)
- Triple whammy supersedes Rule 1 (deduplication — only one entry is shown)

**Rule 3: K-sparing Diuretic + ACE/ARB**

Potassium-sparing diuretics (spironolactone, amiloride) combined with ACE inhibitors or ARBs can cause hyperkalaemia (dangerous potassium elevation).

- Base severity: warning
- Escalates to: concern (if CKD active — impaired potassium excretion)

**Rule 4: Beta-blocker + Non-DHP Calcium Channel Blocker**

Beta-blockers (metoprolol, atenolol) combined with non-dihydropyridine CCBs (verapamil, diltiazem) can cause dangerous bradycardia and heart block.

- Base severity: always concern (no escalation needed — inherently serious)

### 10.2 Detection Logic

```python
meds_lower = [m.lower() for m in current_medications]
codes = set(problem_codes)

# NSAID detection
has_nsaid = any(n in " ".join(meds_lower) for n in
    ["ibuprofen", "naproxen", "diclofenac", "indomethacin", "celecoxib", "meloxicam"])

# ACE/ARB detection
has_ace_arb = any(a in " ".join(meds_lower) for a in
    ["lisinopril", "ramipril", "enalapril", "losartan", "valsartan", "candesartan"])

# Comorbidity check
has_chf = any(c.startswith("I50") for c in codes)
has_ckd = any(c.startswith("N18") for c in codes)
```

### 10.3 Visit Agenda Integration

Drug interactions are woven into the visit agenda by severity:
- Critical → position 0 (before everything else)
- Concern → position 1 (alongside urgent alerts)
- Warning → position 3a (before variability flag)

---

## 11. Step 8 — Background Worker & Scheduler

**Files:** `backend/app/services/worker/processor.py`, `scheduler.py`

### 11.1 The Processing Jobs Queue

ARIA uses a database-backed job queue (the `processing_jobs` table) rather than an external message broker. This keeps the system simple and self-contained.

**Job lifecycle:**
```
queued → running → succeeded
                └→ failed (with error_message)
```

**Idempotency:** Before creating any job, a check is made on the `idempotency_key`. If a job with that key already exists, no new job is created. `ON CONFLICT DO NOTHING` at the database level provides a final safety net.

### 11.2 The Worker (`processor.py`)

The worker is a long-running async process that polls for queued jobs every 30 seconds.

**Claiming a job (concurrent-safe):**
```sql
UPDATE processing_jobs
SET status = 'running', started_at = NOW()
WHERE job_id = (
    SELECT job_id FROM processing_jobs
    WHERE status = 'queued'
    ORDER BY queued_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING *
```

`FOR UPDATE SKIP LOCKED` ensures that if two worker instances run simultaneously, they claim different jobs without race conditions.

**Job type: `pattern_recompute`**

Runs the full Layer 1 + Layer 2 pipeline for one patient:

```
1. Cold-start check (enrolled < 21 days → skip inertia/deterioration/adherence)
2. Run gap_detector → write alert if threshold crossed
3. Run inertia_detector → write alert if detected
4. Run deterioration_detector → write alert if detected
5. Run adherence_analyzer → write alert if Pattern A detected
6. Compute risk_score (Layer 2) → update patients.risk_score
7. Apply tier reclassification (hysteresis)
8. Check for escalation (gap_urgent/deterioration unacknowledged >24h → escalated=True)
9. Check calibration rules → suppress alerts for patients with active rules
```

**Alert deduplication:** Before writing an alert, the worker checks if an unacknowledged alert of the same type for the same patient already exists. If so, no duplicate is written.

**Job type: `briefing_generation`**

```
1. Run full Layer 1 (all 4 detectors)
2. Run Layer 2 (risk score)
3. Compose deterministic briefing JSON (composer.py)
4. Optional: Run Layer 3 LLM summary (summarizer.py)
5. Validate LLM output (llm_validator.py)
6. Store briefing row
7. Write audit_event: briefing_viewed (read_at updated when GP opens it)
```

**Retry logic:** Failed jobs are retried with exponential backoff:
- Retry 1: 30 seconds
- Retry 2: 120 seconds (2 minutes)
- Retry 3: 480 seconds (8 minutes)
- After 3 failures: status = 'failed', error_message stored

### 11.3 The Scheduler (`scheduler.py`)

Two scheduled tasks run via APScheduler:

**7:30 AM — Briefing generation:**
```python
# Find all patients with appointment today
patients = SELECT * FROM patients
           WHERE monitoring_active = TRUE
           AND next_appointment::DATE = TODAY
           AND no existing briefing for today

# Enqueue one briefing_generation job per patient
for patient in patients:
    INSERT INTO processing_jobs (job_type, patient_id, idempotency_key)
    VALUES ('briefing_generation', patient.id, f'briefing_generation:{patient.id}:{today}')
    ON CONFLICT DO NOTHING
```

**Midnight UTC — Pattern recompute sweep:**
```python
# Enqueue pattern_recompute for ALL monitoring-active patients
patients = SELECT * FROM patients WHERE monitoring_active = TRUE

# Spread jobs over 2-hour window to avoid thundering herd
for patient in patients:
    delay = hash(patient.id) % 7200  # 0–7200 seconds
    INSERT INTO processing_jobs (...)
    ON CONFLICT DO NOTHING
```

The hash-based delay is deterministic — the same patient always gets the same delay offset, spreading load evenly without randomness that could cluster jobs.

---

## 12. Step 9 — Risk Tier Reclassification

After every `pattern_recompute` job, the worker calls `_apply_tier_reclassification()`. This implements clinical hysteresis — preventing patients from ping-ponging between tiers on minor score fluctuations.

### 12.1 Guard Order (First Match Wins)

```
1. tier_override_source == "system"
   → Return immediately. Immovable. CHF/stroke/TIA patients cannot be demoted.

2. tier_override_source == "clinician" AND now < suppressed_until AND score < 85
   → Skip reclassification. Clinician's judgement is respected.

3. tier_override_source == "clinician" AND now < suppressed_until AND score >= 85
   → Break-glass. Promote only (safety override when score is dangerously high).

4. Apply hysteresis table
```

### 12.2 Hysteresis Transition Table

| Transition | Score Condition | Additional Gates |
|---|---|---|
| medium → high | score ≥ 75 | None |
| high → medium | score < 40 | tier_override_source must be "system_score" (not a clinician override) |
| medium → low | score < 25 | enrolled ≥ 90 days + no severe/moderate comorbidity + no active urgent alerts |
| low → medium | score ≥ 40 | None |

**Comorbidity block for medium→low:**
Patients with CHF (I50), Stroke (I61, I63, I64), TIA (G45) — SEVERE — or Diabetes (E11), CKD (N18), CAD (I25) — MODERATE — can never be reclassified to Low, regardless of score.

### 12.3 Clinician Override Endpoint

`PATCH /api/patients/{patient_id}/tier`

```json
{
  "risk_tier": "medium",
  "reason": "Patient has been stable for 3 months on current regimen"
}
```

- Demotion → sets `tier_override_suppressed_until = now + 28 days` (NICE NG136 §1.6.3)
- Promotion → clears suppressed_until
- Returns 409 if `tier_override_source == "system"` — cannot override CHF/stroke/TIA

The 28-day suppression window reflects the clinical reality that a medication change takes 4–6 weeks to show BP effect. If the GP demotes a patient (judging them lower risk), the system respects that for one full titration cycle before re-evaluating.

---

## 13. Step 10 — GP Dashboard & Alert Inbox

**Frontend:** Next.js 14, TypeScript strict mode, Tailwind CSS
**All API calls:** `frontend/src/lib/api.ts`
**All types:** `frontend/src/lib/types.ts`

### 13.1 Patient List (`PatientList.tsx`)

The patient list renders from `GET /api/patients` which returns all patients sorted by tier then risk_score DESC.

**Columns displayed:**
- Patient name + ID
- Risk tier badge (with tooltip explaining chronic vs acute risk)
- Risk score bar (visual 0–100 bar with hover tooltip explaining the weighting)
- BP Trend (home readings average from active briefing; falls back to live 28-day computation)
- Next appointment date

**Search:** URL-based (`?q=` param). Filters patient ID, name, and active conditions. Requires a `Suspense` boundary because `useSearchParams` in Next.js App Router only works inside Suspense.

**Tier filter tabs:** All / High / Medium / Low. Filter state is local (not URL-based).

### 13.2 Alert Inbox (`AlertInbox.tsx`)

Displays unacknowledged alerts ordered by triggered_at DESC.

**Alert types with visual coding:**

| Type | Color | Icon |
|---|---|---|
| gap_urgent | Red border | AlertTriangle |
| deterioration | Red border | AlertTriangle |
| gap_briefing | Amber border | AlertTriangle |
| inertia | Amber border | AlertTriangle |
| adherence | Amber border | AlertTriangle |

**Escalated badge:** Shown when `alert.escalated = true` (unacknowledged >24h).

**Off-hours badge:** Shown when `alert.off_hours = true`.

**Acknowledge dropdown — 3 dispositions:**

| Disposition | Clinical Meaning |
|---|---|
| Agree: acting on this | GP will change treatment or refer |
| Agree: monitoring | GP will watch closely but not act yet |
| Disagree | Not clinically relevant |

The disposition is sent to `POST /api/alerts/{id}/acknowledge` as a JSON body. It is stored in `alert_feedback` table and used for calibration and outcome verification.

**Undo window:** 24 hours. An "Undo" button appears on acknowledged alerts within the window.

### 13.3 Briefing Card (`BriefingCard.tsx`)

The full pre-visit briefing rendered as a card with sections:

1. BP Trend Sparkline (`SparklineChart.tsx`) — morning and evening lines
2. Medication Status — current regimen, last change date, titration notice
3. Drug Interactions — severity badges, drugs involved, comorbidity amplification flag
4. Adherence Summary — per-medication confirmed/total with colour-coded % badge
5. Active Problems + Assessment texts
6. Overdue Labs
7. Visit Agenda — prioritised action list
8. Urgent Flags

**Tier Override Modal:** A pencil icon next to the risk tier badge opens a modal where the GP can manually change the tier with a reason (1–500 chars). System-locked patients (CHF/stroke/TIA) see a red lock message instead.

---

## 14. Step 11 — Patient PWA

**Location:** `patient-app/` (port 3001)
**Framework:** Next.js 14 PWA with `@ducanh2912/next-pwa`

The patient-facing app is entirely separate from the GP dashboard. It uses a separate JWT secret (`patient_jwt_secret`) with 8-hour expiry and role="patient" — blast-radius isolation from clinician sessions.

**Three screens:**

1. **Login** (`/`) — Patient ID + date of birth authentication
2. **BP Submit** (`/submit`) — Capture two readings per session (morning/evening), medication taken status, and optional symptoms
3. **Medication Confirm** (`/confirm`) — List of today's scheduled doses; patient taps each one to confirm

**ICS Calendar files:** Patients can download a `.ics` calendar file with their medication schedule, compatible with iOS Calendar, Google Calendar, and Outlook.

**What patients never see:**
- Risk score or tier
- Alert text
- Briefing content
- Any ARIA clinical intelligence output

---

## 15. Step 12 — Chatbot (On-Demand Layer 3)

**Files:** `backend/app/services/chat/agent.py`, `tools.py`
**System prompt:** `prompts/chat_system_prompt.md`

### 15.1 What the Chatbot Does

The chatbot is embedded in the briefing page. The GP can ask natural language questions about the current patient and receive data-grounded answers in 1–3 sentences.

**Example questions it handles:**

- "Why was this patient flagged?" → Calls `get_briefing`, summarises urgent_flags
- "How's their BP over the last 14 days?" → Calls `get_patient_readings` with 14-day window
- "When was amlodipine started?" → Calls `get_medication_history` + `get_clinical_context`, correlates drug start date with active_problems for indication inference
- "Any drug interactions?" → Calls `get_briefing`, reads `drug_interactions` field, reports severity first
- "Give me a quick overview" → Calls `get_briefing`, summarises 3 most clinically urgent findings

### 15.2 Available Tools

```python
get_briefing(patient_id)
    → Returns full briefing payload including trend_summary, medication_status,
      adherence_summary, drug_interactions, visit_agenda, urgent_flags, risk_score

get_patient_readings(patient_id, days=28)
    → Returns home BP readings for the specified window

get_medication_history(patient_id)
    → Returns full med_history JSONB timeline

get_clinical_context(patient_id)
    → Returns active_problems, overdue_labs, social_context, problem_assessments
```

### 15.3 Guardrails (Same as Layer 3)

The chatbot uses the same clinical language boundaries as the briefing summariser. It will not:
- Recommend specific medications or dosages
- Make diagnostic conclusions
- Address the patient directly
- Answer questions about other patients

The system prompt explicitly blocks these with examples of what to say instead.

### 15.4 Follow-up Chips

After each response, the chatbot surfaces contextually relevant follow-up questions as chips (tap to send). These are generated by the backend based on which tools were called in the response.

---

## 16. Step 13 — Shadow Mode Validation

**Script:** `scripts/run_shadow_mode.py`
**Page:** `frontend/src/app/(main)/shadow-mode/page.tsx`

### 16.1 What Shadow Mode Is

Shadow Mode is a clinical validation framework. It replays all 65 historical clinic visits for Patient 1091 and simulates what ARIA would have flagged at each visit — using only data that was available before that visit occurred.

This answers the question: **"Would ARIA's rules have caught clinically significant events that the physician recorded?"**

### 16.2 Ground Truth

The iEMR data for Patient 1091 includes a field `PROBLEM_STATUS2_FLAG` with values:
- 3 = stable (physician considered problem under control)
- 2 = concerned (physician noted a concern)
- 1 = urgent (physician flagged urgency)

ARIA's pattern outputs are compared against these physician judgements.

### 16.3 Results

```
Total evaluation points:  35
Correctly flagged:         33  (94.3%)
False negatives:            0  (ARIA never missed a real clinical event)
False positives:            2  (ARIA flagged events the physician didn't record)
```

The 2 false positives are documented:
- **Fix 1:** Inertia fired on a reading immediately after a medication change (titration window not yet implemented) — resolved
- **Fix 3:** Deterioration fired on a small rise entirely within normal range — absolute threshold gate added (gate 3 of the deterioration detector)

Both are resolved in production. Shadow mode is run periodically to verify regressions do not occur.

---

## 17. API Layer Summary

**Framework:** FastAPI with async SQLAlchemy sessions

**Authentication:**
- Clinician routes: JWT with role="clinician" or "admin"
- Patient routes: Separate JWT with role="patient" and `patient_jwt_secret`

**Key endpoints:**

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/patients` | GET | Patient list with risk tier, score, trend |
| `/api/patients/{id}` | GET | Single patient with full clinical context |
| `/api/patients/{id}/tier` | PATCH | Clinician tier override |
| `/api/briefings/{patient_id}` | GET | Active briefing for patient |
| `/api/alerts` | GET | All unacknowledged alerts |
| `/api/alerts/{id}/acknowledge` | POST | Acknowledge with disposition |
| `/api/readings/{patient_id}` | GET | Home BP readings with window filter |
| `/api/ingest` | POST | FHIR Bundle ingestion |
| `/api/admin/trigger-scheduler` | POST | Manual briefing generation trigger |
| `/api/chat` | POST | Streaming chat endpoint (SSE) |
| `/api/auth/patient/login` | POST | Patient JWT |
| `/api/confirmations/pending` | GET | Today's unconfirmed doses (patient) |
| `/api/confirmations/confirm` | POST | Mark dose as confirmed (patient) |

---

## 18. Security & Audit

### 18.1 JWT Isolation

Clinician and patient sessions use completely separate JWT secrets. A patient token cannot access any clinician endpoint. A clinician token cannot access the patient submission endpoints.

Token expiry:
- Clinician: 12 hours
- Patient: 8 hours

### 18.2 Audit Trail

Every clinically significant action writes an `audit_events` row. This includes:

- `bundle_import` — every FHIR Bundle ingestion
- `reading_ingested` — every home BP reading stored
- `briefing_viewed` — every time a GP opens a briefing (`read_at` also updated on the briefing row)
- `alert_acknowledged` — every alert disposition
- `llm_validation` — every Layer 3 validation attempt (success or failure, with failed check details)
- `tier_reclassified` — every automated tier change

### 18.3 Clinical Language Enforcement

The LLM validator rejects any output containing forbidden clinical phrases. This runs on every Layer 3 invocation — both nightly briefings and chatbot responses. The rejection is logged as an audit event.

---

## 19. Tech Stack Reference

| Component | Technology |
|---|---|
| Backend framework | FastAPI (Python 3.11) |
| Database ORM | SQLAlchemy 2.0 async |
| Database driver | asyncpg |
| Database host | PostgreSQL via Supabase |
| Data validation | Pydantic v2 |
| Background jobs | APScheduler + processing_jobs table |
| AI model | claude-sonnet-4-20250514 (Layer 3 only) |
| Frontend framework | Next.js 14 (App Router) |
| Frontend language | TypeScript (strict mode) |
| Styling | Tailwind CSS |
| Charts | recharts |
| Icons | lucide-react |
| Patient PWA | Next.js 14 + @ducanh2912/next-pwa |
| Linting | ruff (backend), TypeScript compiler (frontend) |
| Testing | pytest (unit + integration) |

---

*Document version: 1.0 | Last updated: May 2026 | Leap of Faith Technologies, IIT CS 595*
