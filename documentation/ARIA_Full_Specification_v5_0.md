# ARIA — Adaptive Real-time Intelligence Architecture
## Full Technical Specification
**Leap of Faith Technologies | IIT CS 595 — See One. Do One. Build One.**
**Version 5.0 | April 2026**

> **Document status:** This is the updated specification reflecting all findings from the April 2026 system audit (AUDIT.md). It supersedes v4.3 and documents both the current production state and the phased improvement roadmap. Sections marked **(CURRENT)** describe the system as built; sections marked **(ROADMAP — Phase N)** describe planned improvements.

---

## 1. Executive Summary

ARIA — Adaptive Real-time Intelligence Architecture — is a between-visit clinical intelligence platform built for hypertension management at GP practice level. It solves one specific problem: a GP with 1,800 patients and 8 minutes per consultation has no structured view of what happened to their hypertensive patients since the last appointment. Readings taken at home, medications adjusted, missed doses, worsening trends — none of this reaches the clinician before they walk into the room.

ARIA fixes this by generating a production-shaped home blood pressure and medication-confirmation stream for the MVP, analysing it against each patient's longitudinal clinical history, detecting clinically significant review patterns, and delivering a structured pre-visit briefing to the clinician dashboard at 7:30 AM before the clinic day begins.

The runtime architecture is a secure modular monolith: FHIR Bundle import, FastAPI APIs, PostgreSQL persistence, an asynchronous processing worker for recalculation and briefing generation, a React clinician dashboard, and an optional readable summary layer that sits on top of a deterministic briefing payload.

ARIA is not a remote patient monitoring programme requiring a parallel clinical workforce. It is not an AI scribe. It is not a patient-facing coaching tool. It is the intelligence layer that currently does not exist before the visit.

### Delivery Boundary — v4.3 MVP

ARIA v4.3 is a production-shaped pilot MVP, not a hospital-launch claim. The build supports synthetic home BP and synthetic medication-confirmation events, FHIR Bundle import instead of live SMART on FHIR, basic role-based access control, immutable audit logging, TLS in transit, secrets-managed deployment, and strict environment separation between demo and any future PHI environment. Live PHI deployment, live EHR API connectivity, and full compliance hardening remain post-MVP workstreams.

### Clinical Boundary — Non-Negotiable

ARIA does not recommend specific medication adjustments. It does not send alerts directly to patients. It does not display raw readings to patients. It does not make clinical decisions. Every output is decision support for the clinician. ARIA presents review flags with the basis for each flag and uses terms such as **"possible adherence concern"** rather than definitive labels. This boundary must be enforced at the product level, not as a guideline.

### 1.1 What ARIA Is Being Built On

ARIA is built within the LOF See One. Do One. Build One. programme at Illinois Institute of Technology (CS 595). It uses the LOF iEMR dataset as a test fixture — real longitudinal patient records with 124 visits spanning 11 years, 65 in-clinic BP readings across 53 unique clinic dates, and a full medication and problem history. The MVP integration path is standards-based FHIR R4 Bundle import rather than a live vendor API connection.

AI coding agents may be used to accelerate scaffolding, tests, migrations, and UI work, but all clinical logic, SQL, security controls, and prompt content remain under named human review before merge.

### 1.2 Competitive Position

| Platform | Model | Gap vs ARIA |
|---|---|---|
| Cadence | 24/7 RPM with parallel nurse team. Health system scale, Epic integration. | Requires care navigator staff. No pre-visit GP briefing. Not viable for independent GP. |
| HealthSnap | RPM + Chronic Care Management. Care navigators manage alerts. | Staffed programme, not a clinician intelligence tool. |
| CureApp HT | FDA-cleared digital therapeutic. Patient-facing lifestyle coaching. | No longitudinal EHR context. No pre-visit briefing. |
| AI Scribes (Suki, DAX) | Document the visit in real time. | Handle during/after the visit only. Nothing before it. |
| **ARIA** | Pre-visit briefing. FHIR-native ingestion. CuffLink monitoring. No extra staff. | **Gap no competitor addresses.** |

---

## 2. AI Architecture — Three-Layer Model (v5.0)

ARIA processes each patient through three layers in strict sequence. Layer 1 always runs first and must be correct before Layers 2 and 3 execute. Never reverse this order.

### 2.1 Layer 1 — Deterministic Rule Engine **(CURRENT + active improvements)**

Gap detection, therapeutic inertia, adherence-BP correlation, and deterioration detection. Pure SQL queries. No AI. No LLM. This is the foundation of ARIA. All clinical signals are computed deterministically from the readings and clinical_context tables. Layer 1 runs asynchronously via the processing_jobs worker — never in the HTTP request path.

#### Patient-Adaptive Threshold **(ROADMAP — Phase 2)**

All four Layer 1 detectors currently use a hard-coded population threshold of 140 mmHg systolic. This is the primary source of false positives in shadow mode validation. The v5.0 target is a patient-adaptive threshold computed from the patient's own longitudinal clinic BP history:

```
patient_threshold = max(130, stable_baseline_mean + 1.5 × SD)
                    capped at 145 mmHg
```

Where `stable_baseline_mean` and `SD` are derived from `clinical_context.historic_bp_systolic` filtered to readings associated with physician-labeled stable visits (`PROBLEM_STATUS2_FLAG = 3`).

If fewer than three stable readings exist, fall back to 140 mmHg.

The threshold utility is extracted to `backend/app/services/pattern_engine/threshold_utils.py` and imported by all four detectors.

#### Comorbidity-Adjusted Threshold **(ROADMAP — Phase 2)**

Shadow mode validation at 94.3% agreement (33/35, 0 false negatives, 2 false positives) confirmed that when cardiovascular and metabolic comorbidities are simultaneously in elevated concern state, lowering the patient threshold by 7 mmHg (floor 130 mmHg) improves clinical agreement. This adjustment is applied after computing the patient-adaptive threshold:

```
if cardiovascular_concern and metabolic_concern:
    effective_threshold = max(130, patient_threshold - 7)
```

Comorbidity concern state is derived from `clinical_context.active_problems` using `classify_comorbidity_concern()` in `threshold_utils.py`.

#### Therapeutic Inertia Detection

**Current state:** Fires when all four conditions are met:
1. Average systolic ≥ 140 mmHg over last 28 days
2. ≥ 5 elevated readings
3. Elevated condition persists > 7 days
4. No medication change on or after the first elevated reading

**Known defects (Audit items 1 and 4):**
- Uses hard-coded 140 mmHg threshold instead of patient-adaptive threshold
- Reads only `clinical_context.last_med_change` (a single ingestion-time snapshot), ignoring `clinical_context.med_history` JSONB which contains all phone refill calls and in-person medication changes

**Phase 2 fix:** Replace `last_med_change` query with a traversal of `clinical_context.med_history` JSONB to find the most recent medication date prior to the first elevated reading. Add slope direction check: if the 7-day recent average is already below the patient threshold, do not fire even if the 28-day mean exceeds it.

#### Deterioration Detection

**Current state:** Fires when:
1. Positive least-squares slope across the 14-day window
2. Recent 3-day average exceeds the days 4–10 baseline average

**Known defect (Audit item 2):** Fires on any positive slope regardless of absolute BP level. A patient rising from 115 to 119 triggers a deterioration alert.

**Phase 0 fix (immediate):** Add `and recent_avg >= patient_threshold` as a required third gate. Until the adaptive threshold is implemented, use 140 mmHg as the fallback.

**Phase 2 extension:** Add a step-change sub-detector: compare the 7-day rolling mean of the most recent week against the 7-day rolling mean of three weeks ago. If the step exceeds 15 mmHg, flag deterioration regardless of overall linear slope.

#### Adherence-BP Correlation

**Current state:** Three patterns based on 28-day adherence rate and mean systolic:
- Pattern A: high BP + low adherence → "possible adherence concern"
- Pattern B: high BP + high adherence → "possible treatment-review case"
- Pattern C: normal BP + low adherence → contextual review

**Known defect (Audit item 3):** Pattern B fires when a patient's BP is actively declining (e.g. falling from 170 to 135) because the 28-day window mean is still above 140. This fires on exactly the patients whose treatment is succeeding.

**Phase 0 fix (immediate):** Add Pattern B suppression block. If `slope < -0.3 AND recent_7day_avg < patient_threshold AND days_since_med_change <= 14`: suppress Pattern B to `"none"` with interpretation "treatment appears effective — monitoring." The 14-day medication change gate is critical — suppression must not apply when no recent medication change occurred.

**Phase 0 fix (immediate):** Write adherence Pattern A alerts to the `alerts` table (`alert_type = "adherence"`). Currently, adherence is the only Layer 1 detector that does not produce an alert row, making it invisible in the urgent_flags briefing section.

#### White-Coat Window Exclusion **(ROADMAP — Phase 2)**

Readings within 3 days of `patients.next_appointment` are suppressed by the synthetic generator (white-coat dip). Including these in window computations pulls the mean downward and can suppress legitimate flags. Both the inertia and deterioration detectors will exclude readings where `effective_datetime >= (next_appointment - timedelta(days=3))`. Excluded readings remain in the DB and visible in the briefing trend chart.

#### Adaptive Analysis Window **(ROADMAP — Phase 3)**

All four detectors currently use a fixed 28-day window regardless of visit interval. For patients seen quarterly, ARIA analyses only the last third of the inter-visit period. The v5.0 target:

```
window_days = min(90, max(14, (next_appointment - last_visit_date).days))
```

Cap at 90 to bound computation requirements; floor at 14 so short intervals do not degenerate. All four detectors receive the same window value for consistency. **Prerequisite:** full care timeline readings (Phase 3).

### 2.2 Layer 2 — Weighted Risk Scoring **(CURRENT + fix required)**

After all Layer 1 detectors complete, the risk scorer computes a numeric priority score (0.0–100.0) for each patient. The score is stored in `patients.risk_score` and used to sort patients within each risk tier on the clinician dashboard.

#### Signal Weights (unchanged)

| Signal | Weight | Source |
|---|---|---|
| 28-day avg systolic vs personal baseline | 30% | readings table |
| Days since last medication change | 25% | clinical_context.last_med_change |
| Adherence rate (inverted) | 20% | medication_confirmations table |
| Gap duration (days without reading) | 15% | readings table |
| Active comorbidity count | 10% | clinical_context.problem_codes |

#### Comorbidity Scoring — Severity-Weighted Model **(ROADMAP — Phase 0)**

**Current defect (Audit item 25):** The comorbidity signal uses `count / 5.0 * 100.0`, saturating at 5 problems. Patient 1091 has 17 coded problems, making every complex patient score 100 on this signal and rendering it useless for differentiation within the high-risk cohort.

**Fix:** Replace with a severity-weighted model:

| Condition | ICD-10 | Points |
|---|---|---|
| CHF | I50.* | 25 |
| Stroke | I63.*, I64 | 25 |
| TIA | G45.* | 25 |
| Diabetes T2DM | E11.* | 15 |
| CKD | N18.* | 15 |
| CAD | I25.* | 15 |
| Any other coded problem | — | 5 each |

Total clamped to 100. CHF + Diabetes = 40/100; CHF + Stroke = 50/100; provides meaningful clinical differentiation.

### 2.3 Layer 3 — LLM Explanation (Optional) **(CURRENT)**

The optional readable summary layer converts the deterministic briefing JSON from Layer 1 into a 3-sentence readable summary per section. Model: `claude-sonnet-4-20250514` via Anthropic API. The prompt template is stored in `prompts/briefing_summary_prompt.md`. Every Layer 3 call must log `model_version`, `prompt_hash`, and `generated_at` in the briefings table. Layer 3 never runs before the Layer 1 deterministic briefing is complete and verified. The deterministic briefing JSON is always the primary output — Layer 3 is additive.

### 2.4 MVP Build Scope

Both Layer 2 (risk scoring) and Layer 3 (LLM explanation) are in the current build. Personalisation AI, vision AI for medication photo confirmation, and advanced ML pipelines are explicitly out of scope.

### 2.5 Shadow Mode Validation **(CURRENT — 94.3% achieved)**

Shadow mode generates synthetic home readings from the iEMR baseline, fires the alert engine, and compares output against physician assessment text (`PROBLEM_STATUS2_FLAG`) in visit notes.

**v4.3 result (April 2026):** 94.3% agreement (33/35 labelled evaluation points, 0 false negatives, 2 false positives). This exceeds the 80% minimum gate. Shadow mode evaluated 53 unique BP clinic dates, of which 35 had ground truth labels from physician HTN assessments.

**v5.0 target:** Shadow mode will be extended to include 71 no-vitals visits where the physician explicitly assessed HTN in `PROBLEM_STATUS2_FLAG` + `PROBLEM_ASSESSMENT_TEXT`, adding approximately 10–15 additional evaluation points. Shadow mode will also become parameterised by CLI argument (`--patient`, `--iemr`) rather than hardcoded to patient 1091.

**False negatives:** Zero false negatives in the current build. This is the highest-priority shadow mode metric — ARIA must not be silent when a physician was concerned.

---

## 3. CuffLink — Home Monitoring Layer

CuffLink is ARIA's home monitoring layer — the name given to ARIA's own BP cuff integration and reading submission pipeline. It is not a third-party product. Naming and owning this layer decouples ARIA's intelligence engine from any single cuff vendor and gives the platform a patient-facing identity.

### 3.1 Supported Pathways

| Pathway | Compatible Devices | Status |
|---|---|---|
| BLE Bluetooth cuff | Omron, iHealth, Withings, Beurer — any device broadcasting BLE BP Profile (GATT 0x1810) | **Roadmap** — MVP uses generated readings through the same pipeline |
| Manual entry (any cuff) | Any validated oscillometric upper-arm cuff | **Roadmap** — MVP uses generated readings |
| SMS submission | Feature phone or no smartphone | **Deferred post-MVP** |
| Carer web form | Patient cannot self-monitor | **Deferred post-MVP** |
| No monitoring | Patient declines all monitoring | ARIA delivers pre-visit briefing from EHR data alone |

### 3.2 Clinical Measurement Protocol

CuffLink enforces the ESH/AHA-recommended home BP measurement protocol.

- **Timing:** Twice daily. Morning within one hour of waking, before medication. Evening before bed.
- **Preparation:** Five minutes rest. No coffee, exercise, or smoking within 30 minutes. Sitting, back supported, feet flat, arm at heart level.
- **Two readings per session:** One minute apart. Average stored as primary analysis value. If readings differ by more than 5 mmHg a third is taken automatically.
- **First week protocol:** Morning and evening readings every day for seven days to establish personal baseline.

### 3.3 CuffLink Data Flow

```
Synthetic generator (MVP/demo) OR patient takes reading (future)
    ↓
CuffLink / generator validates format and attaches idempotency key
    ↓
POST /api/readings
    ↓
ARIA writes readings row + processing_jobs entry + audit event
    ↓
Background worker recalculates: Layer 1 detectors → alerts
    ↓
7:30 AM scheduler enqueues appointment-day briefing jobs
    ↓
Structured briefing composer → optional readable summary → clinician dashboard
    ↓
Optional low-PHI notification email: "ARIA briefing ready" (no identifiers or values)
```

### 3.4 Future Integration Pathways

| Integration | Pathway |
|---|---|
| TheraCare (LOF) | Post-MVP once API access is granted. CuffLink reading submission becomes a screen within TheraCare. |
| Apple HealthKit | Heart rate, activity, sleep data as contextual enrichment. Apple Watch does not provide systolic/diastolic — not usable as primary BP source. |
| OMRON Connect SDK | Direct device integration bypassing generic BLE. Post-MVP upgrade. |
| Tenovi cellular gateway | Cellular-enabled cuff for elderly patients without smartphones. Phase 2. |
| BLE vendor cloud webhook | Register with cuff vendor (Omron Connect, Withings Health), configure webhook to POST on each measurement. Fastest integration path — no BLE SDK required. |

---

## 4. EHR Integration — FHIR R4 Ingestion Interface

ARIA is designed to ingest standards-based clinical data from EHR systems, not just the LOF iEMR dataset. For the MVP, the supported interoperability path is FHIR R4 Bundle import. Live vendor connectivity is explicitly deferred.

### 4.1 Architecture — Two Layers

**Layer 1 — EHR Adapter:** Converts source data into FHIR R4 resources. For the iEMR dataset, a Python script reads the JSON and produces a FHIR Bundle. The adapter is the only EHR-specific code in the system.

**Layer 2 — ARIA FHIR Ingestion:** Receives a FHIR Bundle, validates required resources, records an ingest job, and populates the core PostgreSQL tables. This layer never sees iEMR field names.

### 4.2 FHIR Resources

| FHIR Resource | What ARIA Extracts |
|---|---|
| Patient | Demographics — id, gender, birthDate |
| Condition | Problem list — ICD-10/SNOMED code, clinicalStatus, onsetDateTime |
| MedicationRequest | Medication list — code, status, dosageInstruction, authoredOn |
| AllergyIntolerance | Allergy list — substance code, **active status filter**, **reaction manifestation** *(v5.0)* |
| Observation (vital signs) | In-clinic BP: LOINC 55284-4. **Also PULSE (8867-4), WEIGHT (29463-7), SpO2 (59408-5), TEMPERATURE (8310-5)** *(v5.0 Phase 1)* |
| Observation (labs) | **Creatinine (2160-0), Potassium (2823-3), HbA1c (4548-4), eGFR (62238-1)** *(v5.0 Phase 8)* |
| ServiceRequest | Pending labs and orders — status=active, intent=order |
| Appointment | Upcoming appointment date/time |
| Encounter | Visit metadata — last_visit_date, longitudinal context |
| Provenance | Source metadata — ingest-job traceability (optional in MVP) |

### 4.3 Non-Standard Bundle Keys (iEMR-Specific)

| Key | Content | Status |
|---|---|---|
| `_aria_med_history` | Full medication timeline across all visits. `{name, rxnorm, date, activity}` dicts. Used by inertia detector and briefing titration messaging. | **CURRENT** |
| `_aria_problem_assessments` | Per-visit physician HTN assessment: `{problem_code, visit_date, htn_flag, status_text, assessment_text}`. Captures `PROBLEM_STATUS2_FLAG`, `PROBLEM_STATUS2`, `PROBLEM_ASSESSMENT_TEXT`. | **ROADMAP — Phase 1** |
| `_aria_visit_dates` | All 124 visit ADMIT_DATE values regardless of type. Used to set accurate `last_visit_date`. | **ROADMAP — Phase 1** |

### 4.4 iEMR Adapter — Mapping from 1091_data.json

| iEMR Source Field | FHIR Resource Produced | Notes |
|---|---|---|
| PROBLEM.value + PROBLEM_CODE | Condition | clinicalStatus=active, ICD-10 code |
| MEDICATIONS.* | MedicationRequest | RxNORM from code_mappings, authoredOn from MED_DATE_ADDED |
| MEDICATIONS.* (all visits) | `_aria_med_history` (non-FHIR) | Full timeline via bundle["_aria_med_history"] |
| VITALS.SYSTOLIC_BP + DIASTOLIC_BP | Observation (LOINC 55284-4) | Components 8480-6 and 8462-4 |
| **VITALS.PULSE** | **Observation (LOINC 8867-4)** | **v5.0 Phase 1** |
| **VITALS.WEIGHT** | **Observation (LOINC 29463-7)** | **v5.0 Phase 1** |
| **VITALS.PULSEOXYGEN** | **Observation (LOINC 59408-5)** | **v5.0 Phase 1** |
| **VITALS.TEMPERATURE** | **Observation (LOINC 8310-5)** | **v5.0 Phase 1** |
| ALLERGY.ALLERGY_DESCRIPTION | AllergyIntolerance | **v5.0: Filter on ALLERGY_STATUS=Active; add ALLERGY_REACTION as reaction.manifestation** |
| PLAN.value where PLAN_NEEDS_FOLLOWUP=YES | ServiceRequest | status=active, intent=order |
| SOCIAL_HX | `social_context` field | **v5.0 Phase 1: _build_social_context() joins SOCIAL_HX across all visits** |
| **PROBLEM.PROBLEM_STATUS2_FLAG + PROBLEM_ASSESSMENT_TEXT** | **`_aria_problem_assessments`** | **v5.0 Phase 1: per-visit HTN concern state** |
| **ADMIT_DATE (all 124 visits)** | **`_aria_visit_dates`** | **v5.0 Phase 1: for accurate last_visit_date** |

### 4.5 Conversion Fidelity — Known Gaps

The following iEMR fields are silently discarded in the current v4.3 adapter. Phase 1 addresses the highest-priority items:

**Phase 1 (high clinical value):**
- `PROBLEM_STATUS2_FLAG`, `PROBLEM_STATUS2`, `PROBLEM_ASSESSMENT_TEXT` — physician concern state per problem
- `VITALS.PULSE`, `VITALS.WEIGHT`, `VITALS.PULSEOXYGEN`, `VITALS.TEMPERATURE`
- `ALLERGY_REACTION`, `ALLERGY_STATUS` — reaction severity and active status
- `SOCIAL_HX` — social history text
- Visit dates for all 124 visits (currently only 53 BP clinic dates used for `last_visit_date`)

**Phase 8 (requires additional infrastructure):**
- Lab values (creatinine, potassium, HbA1c, eGFR) — requires pharmacy/lab system integration
- `EXAM_TEXT`, `ROS_TEXT`, `VISIT_TEXT` — physical examination narratives

**Permanently deferred (clinical boundary):**
- `PLAN_FINDINGS_TEXT`, `PLAN_STATUS`, `PLAN_TYPE` beyond ServiceRequest
- Raw visit notes for LLM processing without explicit consent

### 4.6 Realistic Interoperability Boundary for the MVP

ARIA does not claim a live, launch-ready Epic/Cerner/Athenahealth integration. The supported path is read-only FHIR Bundle import, deterministic downstream processing, and clinician review in the ARIA dashboard. SMART on FHIR launch, vendor-specific OAuth scopes, write-back, and CDS Hooks insertion are future workstreams.

---

## 5. Synthetic Data Specification

ARIA generates synthetic home readings anchored on real iEMR in-clinic BP readings. For the MVP, ARIA also generates synthetic medication-confirmation events. This is not invented data — it is a clinically realistic extrapolation from real patient baseline readings using peer-reviewed variance parameters.

### 5.1 Clinical Grounding

- **Day-to-day systolic SD:** 8–12 mmHg. Using ±2 mmHg is a spreadsheet, not a patient.
- **Morning/evening differential:** Morning 5–10 mmHg higher due to circadian morning surge. Present in ~80% of hypertensive patients.
- **Round numbers:** Never exactly round. Real readings cluster with irregular scatter: 157, 164, 161, 153, 168.
- **White-coat adherence pattern:** Readings drop 10–15 mmHg systolic in 3–5 days before appointment.
- **Device outage:** 1–2 episodes of 2–4 days per 28-day period, stored as **absent rows**, never null values.

### 5.2 Patient A Scenario — Therapeutic Inertia (28 Days)

Anchored on patient 1091 clinic BPs of 185/72 and 180/73.

| Days | Pattern | Detail |
|---|---|---|
| 1–7 | Baseline | Morning systolic Gaussian ~163 mmHg, SD=8. Evening 6–9 lower. HR 64–72, negative correlation with systolic (metoprolol effect). |
| 8–14 | Inertia develops | Systolic drifts to ~165. One missed evening reading Saturday. |
| 15–18 | Continued elevation | 164–167 mmHg. Device outage days 16–17 — absent rows. |
| 19–21 | Pre-appointment dip | Gradual drop to 148–153 over 3 days. White-coat adherence pattern. |
| 22–28 | Post-appointment return | 160–166 mmHg. Weekend misses days 25–26. |

### 5.3 Parametric Baseline — Multi-Patient Support **(ROADMAP — Phase 3)**

The v4.3 generator uses a hard-coded baseline of ~163 mmHg for Patient A. The v5.0 generator derives the baseline parametrically:

```python
baseline_mean = median(clinical_context.historic_bp_systolic)
baseline_sd = std(clinical_context.historic_bp_systolic)
```

This allows the same generator to produce realistic data for any patient without code changes.

### 5.4 Full Care Timeline Generation **(ROADMAP — Phase 3)**

The v4.3 generator produces only 28 days of readings before the most recent appointment. For patient 1091 with 11 years of clinic history, the detectors see only a narrow recent window.

**v5.0 target:** `generate_full_timeline_readings(clinic_readings)` generates synthetic readings between **all** consecutive pairs of clinic readings spanning the patient's entire care history:

- **Interpolation:** Linear interpolation between consecutive clinic BP anchors
- **Noise:** Gaussian SD = 8–12 mmHg per the clinical rules in Section 5.1
- **Morning/evening variation:** Morning 5–10 mmHg higher every day
- **Device outage episodes:** 1–2 per inter-visit gap, 2–4 consecutive days each — absent rows
- **White-coat dip:** 3 days before each clinic visit, gradual 10–15 mmHg decline
- **Idempotency:** Skip intervals where generated readings already exist
- **Storage:** `source='generated'`, `submitted_by='generator'`

**Prerequisite:** Per-observation idempotency (Phase 3 gate — see Section 6.9).

### 5.5 Synthetic Data Rules — Summary

| Parameter | Rule | Failure Mode to Avoid |
|---|---|---|
| Day-to-day systolic SD | 8–12 mmHg | Never less than 5 — flat variance is the most common synthetic data error |
| Morning/evening differential | Morning 5–10 mmHg higher | Must be visible every week |
| Round numbers | Never exactly round | Apply ±1–2 mmHg noise to any intended base |
| Two-reading session | Readings 1 and 2 differ by 2–6 mmHg | Reading 2 typically slightly lower |
| Diastolic | Systolic × 0.60–0.66 | Diastolic does not move independently in treated hypertension |
| Heart rate | 64–82 bpm | Slight negative correlation with systolic when beta-blocker in regimen |
| Device outage | 1–2 episodes of 2–4 days | Must be absent rows, never null or zero values |

---

## 6. Data Architecture

ARIA uses eight PostgreSQL tables in the MVP runtime, with additional tables in the v5.0 roadmap for feedback, gap explanations, and calibration. No JSONB is used in the query path except for the rendered briefing payload and `med_history`/`problem_assessments` context fields.

### 6.1 Design Decisions

**`clinical_context` — pre-computed at ingestion:** Instead of normalising the EHR into eight separate tables and running multi-join queries for every briefing, the ingestion script does that join work once. The pattern engine queries one main context row per patient.

**Gap detection via window function:** `LAG(effective_datetime) OVER (PARTITION BY patient_id ORDER BY effective_datetime)` — not a stored column. A stored gap column breaks when readings arrive out of chronological order.

**`systolic_avg` — stored column:** Derived only from values within the same row. Not subject to ordering race conditions.

**`briefings.llm_response` — JSONB:** The one place JSONB is appropriate because the briefing is a structured document rendered, not queried field-by-field.

**Per-observation idempotency **(v5.0 Phase 3):** The current batch-level idempotency (skip all inserts if any clinic readings exist) prevents adding new clinic visits. The v5.0 fix uses a unique index on `(patient_id, effective_datetime, source)` with `ON CONFLICT DO NOTHING`.

### 6.2 Table 1 — patients

| Column | Type | Notes |
|---|---|---|
| patient_id | TEXT PRIMARY KEY | FHIR Patient.id / iEMR MED_REC_NO |
| gender | CHAR(1) | 'M' \| 'F' \| 'U' |
| age | SMALLINT | Age at enrolment |
| risk_tier | TEXT NOT NULL | 'high' \| 'medium' \| 'low' |
| tier_override | TEXT | 'CHF in problem list' \| 'Stroke history' \| 'TIA history' |
| risk_score | NUMERIC(5,2) | Layer 2 weighted priority score 0.0–100.0 |
| monitoring_active | BOOLEAN DEFAULT TRUE | FALSE = EHR-only pathway |
| next_appointment | TIMESTAMPTZ | Used by 7:30 AM briefing scheduler |
| enrolled_at | TIMESTAMPTZ DEFAULT NOW() | |
| enrolled_by | TEXT | Clinician ID |

### 6.3 Table 2 — clinical_context

One row per patient. Pre-computed from EHR/FHIR data at ingestion. **v5.0 adds several columns via migration.**

| Column | Type | Notes |
|---|---|---|
| patient_id | TEXT PRIMARY KEY | |
| active_problems | TEXT[] | Parallel to problem_codes |
| problem_codes | TEXT[] | ICD-10 or SNOMED |
| current_medications | TEXT[] | Parallel to med_rxnorm_codes |
| med_rxnorm_codes | TEXT[] | |
| med_history | JSONB | Full medication timeline `{name, rxnorm, date, activity}`. Used by inertia detector for time-sliced last_med_change. |
| last_med_change | DATE | Most recent MedicationRequest.authoredOn. Superseded by med_history traversal in Phase 2. |
| allergies | TEXT[] | |
| **allergy_reactions** | **TEXT[]** | **v5.0 Phase 1: reaction manifestation parallel to allergies** |
| last_visit_date | DATE | **v5.0 Phase 1: updated to max(all 124 visit dates), not just BP clinic dates** |
| last_clinic_systolic | SMALLINT | |
| last_clinic_diastolic | SMALLINT | |
| **last_clinic_pulse** | **SMALLINT** | **v5.0 Phase 1: from VITALS.PULSE** |
| **last_clinic_weight_kg** | **NUMERIC(5,1)** | **v5.0 Phase 1: from VITALS.WEIGHT** |
| **last_clinic_spo2** | **SMALLINT** | **v5.0 Phase 1: from VITALS.PULSEOXYGEN** |
| historic_bp_systolic | SMALLINT[] | All clinic systolics chronologically |
| historic_bp_dates | TEXT[] | ISO date strings (TEXT[] not DATE[] — asyncpg workaround) |
| overdue_labs | TEXT[] | ServiceRequest names |
| **recent_labs** | **JSONB** | **v5.0 Phase 8: `{loinc, name, value, unit, date}` for creatinine, K+, HbA1c, eGFR** |
| social_context | TEXT | **v5.0 Phase 1: populated from SOCIAL_HX across all visits** |
| **problem_assessments** | **JSONB** | **v5.0 Phase 1: per-visit `{problem_code, visit_date, htn_flag, status_text, assessment_text}`** |
| last_updated | TIMESTAMPTZ DEFAULT NOW() | |

### 6.4 Table 3 — readings

| Column | Type | Notes |
|---|---|---|
| reading_id | UUID PK | |
| patient_id | TEXT REFERENCES patients | |
| systolic_1 | SMALLINT NOT NULL | |
| diastolic_1 | SMALLINT NOT NULL | |
| heart_rate_1 | SMALLINT | |
| systolic_2 | SMALLINT | NULL if single reading |
| diastolic_2 | SMALLINT | |
| heart_rate_2 | SMALLINT | |
| systolic_avg | NUMERIC(5,1) NOT NULL | Primary analysis value |
| diastolic_avg | NUMERIC(5,1) NOT NULL | |
| heart_rate_avg | NUMERIC(5,1) | |
| effective_datetime | TIMESTAMPTZ NOT NULL | |
| session | TEXT NOT NULL | 'morning' \| 'evening' \| 'ad_hoc' |
| source | TEXT NOT NULL | 'generated' \| 'manual' \| 'ble_auto' \| 'clinic' |
| submitted_by | TEXT NOT NULL | 'patient' \| 'carer' \| 'generator' \| 'clinic' |
| bp_position | TEXT | 'seated' \| 'standing' |
| bp_site | TEXT | 'left_arm' \| 'right_arm' |
| consent_version | TEXT NOT NULL DEFAULT '1.0' | |
| medication_taken | TEXT | 'yes' \| 'no' \| 'partial' \| NULL |
| created_at | TIMESTAMPTZ DEFAULT NOW() | |

### 6.5 Table 5 — alerts

| Column | Type | Notes |
|---|---|---|
| alert_id | UUID PK | |
| patient_id | TEXT REFERENCES patients | |
| alert_type | TEXT NOT NULL | 'gap_urgent' \| 'gap_briefing' \| 'inertia' \| 'deterioration' \| **'adherence'** *(v5.0 Phase 0)* |
| gap_days | SMALLINT | |
| systolic_avg | NUMERIC(5,1) | |
| triggered_at | TIMESTAMPTZ DEFAULT NOW() | |
| delivered_at | TIMESTAMPTZ | **v5.0 Phase 0: set to NOW() at insert** |
| acknowledged_at | TIMESTAMPTZ | |

### 6.6 Table 6 — briefings

| Column | Type | Notes |
|---|---|---|
| briefing_id | UUID PK | |
| patient_id | TEXT REFERENCES patients | |
| appointment_date | DATE NOT NULL | **v5.0 Phase 0: derived from patient.next_appointment, not idempotency_key** |
| llm_response | JSONB NOT NULL | Structured briefing payload |
| generated_at | TIMESTAMPTZ DEFAULT NOW() | |
| model_version | TEXT | Layer 3 LLM model ID |
| prompt_hash | TEXT | SHA-256 of prompt template |
| delivered_at | TIMESTAMPTZ | |
| read_at | TIMESTAMPTZ | |

### 6.7 New Tables — v5.0 Roadmap

#### gap_explanations *(Phase 5)*
Patient-submitted explanations for reading gaps.

| Column | Type |
|---|---|
| explanation_id | UUID PK |
| patient_id | TEXT REFERENCES patients |
| start_date | DATE NOT NULL |
| end_date | DATE NOT NULL |
| reason_code | TEXT — 'device_malfunction' \| 'device_lost' \| 'travelling' \| 'illness' \| 'intentional_pause' \| 'forgot' |
| free_text | TEXT |
| reported_at | TIMESTAMPTZ DEFAULT NOW() |

#### alert_feedback *(Phase 5)*
Clinician disposition on acknowledged alerts.

| Column | Type |
|---|---|
| feedback_id | UUID PK |
| alert_id | UUID REFERENCES alerts |
| disposition | TEXT — 'agree_acting' \| 'agree_monitoring' \| 'disagree' |
| reason_text | TEXT |
| clinician_id | TEXT |
| patient_id | TEXT |
| detector_type | TEXT |
| created_at | TIMESTAMPTZ DEFAULT NOW() |

#### calibration_rules *(Phase 7)*
Clinician-approved threshold adjustments.

| Column | Type |
|---|---|
| rule_id | UUID PK |
| patient_id | TEXT |
| detector_type | TEXT |
| parameter | TEXT |
| adjusted_value | NUMERIC |
| approved_by | TEXT |
| approved_at | TIMESTAMPTZ |
| dismissal_count | SMALLINT |

### 6.8 Indexes

```sql
-- Core query path — most critical index
CREATE INDEX idx_readings_patient_datetime
  ON readings (patient_id, effective_datetime DESC);

CREATE INDEX idx_readings_patient_session
  ON readings (patient_id, session, effective_datetime DESC);

-- Alert delivery
CREATE INDEX idx_alerts_undelivered
  ON alerts (patient_id, delivered_at) WHERE delivered_at IS NULL;

-- 7:30 AM scheduler
CREATE INDEX idx_patients_appointment
  ON patients (next_appointment) WHERE monitoring_active = TRUE;

-- CHF/stroke override check
CREATE INDEX idx_cc_problem_codes
  ON clinical_context USING GIN (problem_codes);

-- Adherence queries
CREATE INDEX idx_confirmations_patient_scheduled
  ON medication_confirmations (patient_id, scheduled_time DESC);

CREATE INDEX idx_confirmations_missed
  ON medication_confirmations (patient_id, scheduled_time)
  WHERE confirmed_at IS NULL;

-- Job queue
CREATE UNIQUE INDEX idx_processing_jobs_idempotency
  ON processing_jobs (idempotency_key);

CREATE INDEX idx_processing_jobs_status_type
  ON processing_jobs (status, job_type, queued_at);

-- Audit
CREATE INDEX idx_audit_events_patient_time
  ON audit_events (patient_id, event_timestamp DESC);

-- Dashboard sort (Layer 2)
CREATE INDEX idx_patients_risk_score
  ON patients (risk_tier, risk_score DESC);

-- v5.0 Phase 3: per-observation idempotency
CREATE UNIQUE INDEX idx_readings_patient_datetime_source
  ON readings (patient_id, effective_datetime, source);

-- Schema migrations (safe to re-run via setup_db.py)
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS med_history JSONB;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS allergy_reactions TEXT[];
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_pulse SMALLINT;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_weight_kg NUMERIC(5,1);
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS last_clinic_spo2 SMALLINT;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS recent_labs JSONB;
ALTER TABLE clinical_context ADD COLUMN IF NOT EXISTS problem_assessments JSONB;
```

### 6.9 Idempotency — Current and Target

**Current (v4.3) — batch-level idempotency:**
```python
if clinic_count == 0:
    insert_all_clinic_readings()
# Any existing clinic reading blocks all new inserts
```

**Target (v5.0 Phase 3) — per-observation idempotency:**
```python
INSERT INTO readings (...) VALUES (...)
ON CONFLICT (patient_id, effective_datetime, source) DO NOTHING;
# Each reading inserts independently — new visits add cleanly
```

---

## 7. Pattern Engine — Core Queries

These queries define the core SQL logic. They are not executed in the HTTP request path. Layer 1 results flow to alerts and briefings via the background worker.

### 7.1 Gap Detection

```sql
SELECT patient_id,
       EXTRACT(EPOCH FROM (NOW() - MAX(effective_datetime))) / 86400 AS gap_days
FROM readings
WHERE patient_id = $1
GROUP BY patient_id;

-- Thresholds by risk tier:
-- High:   flag >= 1 day,  urgent >= 3 days
-- Medium: flag >= 3 days, urgent >= 5 days
-- Low:    flag >= 7 days, urgent >= 14 days
```

### 7.2 Therapeutic Inertia Detection — v4.3 (Current)

```sql
SELECT AVG(r.systolic_avg) AS rolling_avg_systolic,
       MIN(r.effective_datetime) AS elevated_since,
       cc.last_med_change
FROM readings r
JOIN clinical_context cc USING (patient_id)
WHERE r.patient_id = $1
  AND r.effective_datetime >= NOW() - INTERVAL '28 days'
  AND r.systolic_avg >= 140  -- ← AUDIT: hard-coded, replace with patient threshold
GROUP BY cc.last_med_change
HAVING COUNT(*) >= 5
  AND NOW() - MIN(r.effective_datetime) > INTERVAL '7 days'
  AND (cc.last_med_change IS NULL
       OR cc.last_med_change < MIN(r.effective_datetime)::DATE);
-- ↑ AUDIT: ignores med_history JSONB; phone refills invisible
```

### 7.3 Therapeutic Inertia Detection — v5.0 Target (Phase 2)

```python
# Python layer — replaces static SQL
async def run_inertia_detector(session, patient_id, patient_threshold):
    # 1. Query readings with adaptive window
    readings = await get_readings_in_window(session, patient_id, window_days)
    # 2. Exclude white-coat pre-visit window
    readings = [r for r in readings if r.effective_datetime < (next_appt - 3 days)]
    # 3. Check average vs patient_threshold (not hard-coded 140)
    elevated = [r for r in readings if r.systolic_avg >= patient_threshold]
    # 4. Check last_med_change from med_history JSONB (not last_med_change column)
    last_med_change = get_last_med_change_from_history(
        med_history, cutoff=first_elevated_reading_date
    )
```

### 7.4 Adherence Rate

```sql
SELECT medication_name,
       COUNT(*) AS scheduled_doses,
       COUNT(confirmed_at) AS confirmed_doses,
       ROUND(COUNT(confirmed_at)::NUMERIC / NULLIF(COUNT(*), 0) * 100, 1) AS adherence_pct
FROM medication_confirmations
WHERE patient_id = $1
  AND scheduled_time >= NOW() - INTERVAL '28 days'
GROUP BY medication_name
ORDER BY adherence_pct ASC;

-- adherence_pct < 80 is the standard clinical threshold
```

### 7.5 Adherence-BP Correlation

```sql
SELECT r.effective_datetime::DATE AS reading_date,
       r.systolic_avg,
       COUNT(mc.confirmation_id) AS doses_scheduled,
       COUNT(mc.confirmed_at) AS doses_confirmed,
       ROUND(COUNT(mc.confirmed_at)::NUMERIC / NULLIF(COUNT(mc.confirmation_id), 0) * 100, 1) AS daily_adherence_pct
FROM readings r
LEFT JOIN medication_confirmations mc
  ON mc.patient_id = r.patient_id
 AND mc.scheduled_time::DATE = r.effective_datetime::DATE
WHERE r.patient_id = $1
  AND r.effective_datetime >= NOW() - INTERVAL '28 days'
GROUP BY r.reading_id, r.effective_datetime, r.systolic_avg
ORDER BY r.effective_datetime ASC;

-- Pattern A: high systolic + low adherence_pct → possible adherence concern
-- Pattern B: high systolic + high adherence_pct → possible treatment-review case
--            (suppressed if slope < -0.3 AND recent_7day < threshold AND days_since_med_change <= 14)
-- Pattern C: normal systolic + low adherence_pct → contextual review
```

### 7.6 Daily Pattern_Recompute Sweep — v5.0 (Phase 4)

```sql
-- Scheduler: enqueue for ALL monitoring_active patients daily
INSERT INTO processing_jobs (job_type, patient_id, idempotency_key, status, created_by)
SELECT 'pattern_recompute',
       patient_id,
       'pattern_recompute:' || patient_id || ':' || CURRENT_DATE,
       'queued',
       'system'
FROM patients
WHERE monitoring_active = TRUE
ON CONFLICT (idempotency_key) DO NOTHING;

-- Current v4.3: only appointment-day patients get briefing jobs;
-- non-appointment-day patients' risk scores, inertia, and gap counters are stale.
```

---

## 8. Briefing — Structured Output

### 8.1 Briefing JSON Structure

```json
{
  "trend_summary": "28-day BP pattern narrative",
  "medication_status": "current regimen, last change date, titration window flag",
  "adherence_summary": "rate per medication + pattern interpretation",
  "active_problems": ["Hypertension", "CHF", "T2DM"],
  "problem_assessments": {"CHF": "last assessed: Under Evaluation (2026-01-14)"},
  "overdue_labs": ["HbA1c — last checked 18 months ago"],
  "visit_agenda": ["prioritised 3–6 items"],
  "urgent_flags": ["active unacknowledged alerts"],
  "risk_score": 87.4,
  "data_limitations": "whether home monitoring available and for how long",
  "social_context": "lives alone, disclosed financial stress (v5.0)",
  "long_term_trend": "3-month trajectory: stable elevation since November (v5.0 Phase 4)"
}
```

### 8.2 Visit Agenda Priority Order

1. Urgent alerts (gap_urgent, deterioration, adherence Pattern A)
2. Inertia flag (from Layer 1 InertiaResult, not re-computed in composer)
3. Adherence concern (from Layer 1 AdherenceResult)
4. SpO2 < 92% flag for CHF patients *(v5.0 Phase 1)*
5. Potassium abnormality for diuretic patients *(v5.0 Phase 8)*
6. Overdue labs
7. Physician problem assessment — "CHF: Under Evaluation at last visit" *(v5.0 Phase 1)*
8. Active problems review
9. Next appointment recommendation

### 8.3 Medication Status — Titration Window Messaging *(v5.0 Phase 2)*

When `days_since_med_change <= 42`:
> "— within expected titration window, full response may not yet be established."

### 8.4 Duplicate Inertia Logic Removal *(v5.0 Phase 2)*

The briefing composer currently re-implements inertia detection inline with the same hard-coded 140 mmHg threshold (`_ELEVATED_SYSTOLIC = 140.0` in `composer.py`). In v5.0, `_build_visit_agenda()` receives the `InertiaResult` dict from Layer 1 and consumes `inertia_result["inertia_detected"]` directly. The inline check is removed.

---

## 9. System Architecture

### 9.1 Component Overview

| Component | Technology | Role |
|---|---|---|
| EHR Adapter | Python script per source | iEMR JSON → FHIR Bundle. Only source-specific code. |
| FHIR Ingestion | Python / FastAPI | Validates Bundle, populates 8+ tables, emits audit events. Idempotent. |
| Processing Worker | Python background | Consumes processing_jobs for pattern recompute, briefing, bundle import. |
| Synthetic Reading Generator | Python | Generates 28-day (v4.3) or full-timeline (v5.0) reading series from clinic history. |
| Pattern Engine | Python / SQLAlchemy async | Layer 1 detectors + Layer 2 scorer. Runs in worker. |
| Briefing Composer | Python | Deterministic structured JSON. Layer 3 optional summary layer on top. |
| Auth + RBAC | Supabase Auth | JWT ≤ 1 hour, clinician/admin roles. |
| Clinician Dashboard | React / Next.js 14 | Briefing viewer, sparkline, alert inbox, patient list sorted by tier then risk_score. |
| 7:30 AM Scheduler | APScheduler | Enqueues briefing jobs for appointment-day patients. v5.0 adds daily pattern_recompute sweep. |
| CuffLink Web/App | React (+ React Native roadmap) | Patient reading submission. MVP uses generated readings through same API. |

### 9.2 Technology Stack

| Component | Detail |
|---|---|
| Backend | Python 3.11, FastAPI |
| Database | PostgreSQL (Supabase), asyncpg driver |
| ORM | SQLAlchemy 2.0 async — **never** `session.query()` |
| Pydantic | v2 — `SettingsConfigDict`, **never** `class Config` |
| Background Processing | Python worker + processing_jobs table |
| Authentication | Supabase Auth OIDC-compatible. **v5.0: JWT ≤ 1 hour, MFA enabled** |
| LLM | Anthropic `claude-sonnet-4-20250514` (Layer 3 only) |
| Frontend | Next.js 14, TypeScript strict, Tailwind CSS, recharts |
| Email | SendGrid — low-PHI notification only |
| Rate Limiting | **v5.0 Phase 8: slowapi middleware** |
| Secrets | Environment variables — no secrets in source control |

### 9.3 Continuous Pattern Monitoring — v5.0 (Phase 4)

**Current v4.3 gap:** Only appointment-day patients receive briefing jobs from the scheduler. Non-appointment-day patients' gap counters, inertia flags, and risk scores are stale between appointments.

**v5.0 fix:** `enqueue_pattern_recompute_sweep()` runs daily at midnight UTC. It enqueues a `pattern_recompute` job for every `monitoring_active=TRUE` patient using idempotency key `pattern_recompute:{patient_id}:{YYYY-MM-DD}`. Re-runs are safe via `ON CONFLICT DO NOTHING`.

### 9.4 Cold Start Detection *(v5.0 Phase 3)*

When a patient is first enrolled, there are zero home readings. Inertia, deterioration, and adherence detectors produce null or misleading output.

**Fix:** At the start of `_handle_pattern_recompute`, check enrollment age:
```python
if (now - patient.enrolled_at).days < 14:
    # Skip inertia, deterioration, adherence
    data_limitations = f"Patient enrolled {days} days ago — minimum 14-day monitoring period required. Briefing based on EHR data only."
# Gap detector still runs — zero readings in week 1 is itself a gap signal
```

---

## 10. Phased Implementation Roadmap

Phases are ordered so that no fix depends on an incomplete prerequisite. Fixes within a phase can run in parallel.

### Phase 0 — Standalone Correctness (No Dependencies, Start Immediately)

| # | Fix | File | Size |
|---|---|---|---|
| 2 | Add threshold gate to deterioration detector | deterioration_detector.py | 1 line |
| 3 + 26 | Pattern B suppression + 14-day med change gate | adherence_analyzer.py | ~17 lines |
| 11 | Write adherence alert row in processor | processor.py | 3 lines |
| 30 | Set `delivered_at` on alert insert | processor.py | 1 line |
| 20 | Read appointment date from patient record | processor.py | 5 lines |
| 25 | Severity-weighted comorbidity risk score | risk_scorer.py | ~20 lines |

After Phase 0: trigger `pattern_recompute` via admin endpoint to refresh all production scores.

### Phase 1 — Ingestion Data Fixes (Single Re-Ingestion Pass)

Apply all changes to `adapter.py` and `ingestion.py` together. Re-ingest once after all Phase 1 fixes are complete.

| # | Fix | Files |
|---|---|---|
| 12 | Capture all 124 visit dates for `last_visit_date` | adapter.py, ingestion.py |
| 8 | Populate `social_context` | adapter.py, ingestion.py |
| 9 | Allergy reactions + active-status filter | adapter.py, ingestion.py |
| 7 | Capture physician problem assessments | adapter.py, ingestion.py, clinical_context model |
| 6 | Capture PULSE/WEIGHT/SpO2 + DB migration for new columns | adapter.py, ingestion.py, migration |

### Phase 2 — Detector and Briefing Fixes (After Phase 1 Data in DB)

| # | Fix | Files |
|---|---|---|
| 1 + 4 | Patient-adaptive threshold + med_history in inertia detector | inertia_detector.py |
| 18 | Remove duplicate inertia from briefing composer | composer.py |
| 5 | Comorbidity-adjusted threshold in all four detectors | threshold_utils.py (new), all 4 detectors |
| 27 | Exclude white-coat pre-visit window | inertia_detector.py, deterioration_detector.py |
| 29 + 34 | Surface social context + titration timing in briefing | composer.py |

After Phase 2: run shadow mode. Agreement rate should be ≥ 94.3%.

### Phase 3 — Generator Expansion

| # | Fix | Files |
|---|---|---|
| 22 | Per-observation idempotency **(gate for all generator work)** | ingestion.py |
| 19 | Parametric baseline from patient clinic BPs | reading_generator.py |
| 15 | Full care timeline synthetic readings | reading_generator.py, run_generator.py |
| 17 | Cold start detection | processor.py |
| 28 | Adaptive window based on inter-visit interval | all 4 detectors |

### Phase 4 — Scheduler and Worker

| # | Fix | Files |
|---|---|---|
| 10 | Daily pattern_recompute sweep for all active patients | scheduler.py |
| 21 | `next_appointment` update endpoint | patients.py API |
| 47 | Long-term trend layer in briefing | composer.py |
| 46 | Mini-briefing for between-visit urgent alerts | processor.py, composer.py |
| 40 | Dead-letter queue (max 3 retries, exponential backoff) | processor.py |

### Phase 5 — API and Alert Improvements

| # | Fix | Files |
|---|---|---|
| 24 | Alert `patient_id` filter | alerts.py |
| 42 (L1) | Alert disposition on acknowledge | alerts.py, new `alert_feedback` table |
| 41 | Gap explanations table and API | new table, new API route |
| 13 | Shadow mode CLI argument | run_shadow_mode.py |
| 33 | Shadow mode window overlap reporting | run_shadow_mode.py |

### Phase 6 — Frontend

| # | Fix | Files |
|---|---|---|
| 23 | Briefing icon for any briefing (not just today) | PatientList.tsx |
| 31 | Remove duplicate frontend sort | PatientList.tsx |
| 14 | Multi-patient pagination and tier filter | dashboard components |

### Phase 7 — New Clinical Features

| # | Feature |
|---|---|
| 43 | Patient-facing reading submission + symptom flags |
| 45 | Escalation pathway + off-hours alert tagging |
| 42 (L2) | Feedback loop Layer 2: calibration recommendations |
| 42 (L3) | Feedback loop Layer 3: 30-day outcome verification |
| 44 | BLE webhook connector (vendor cloud → ARIA readings API) |

### Phase 8 — Infrastructure and Security

| # | Fix |
|---|---|
| 35 | Verify patient research ID pseudonymization (replace MED_REC_NO if needed) |
| 36 | Confirm JWT expiry ≤ 1 hour |
| 37 | Add API rate limiting (slowapi: POST /api/readings 60/min, POST /api/ingest 5/min) |
| 38 | DB-level audit trigger on readings table |
| 39 | Enable TOTP MFA in Supabase Auth |
| 16 | Lab values ingestion (FHIR Observation, LOINC codes) |

---

## 11. Demo Specification

### 11.1 Three Patient Scenarios

**Patient A — Therapeutic Inertia**
Source: patient 1091 iEMR (real data). Age 80, hypertension, T2DM, CHF, CAD. Risk tier: High (CHF override). 28 days of FHIR-generated home readings anchored on clinic BPs 185/72 and 180/73. Inertia flagged day 8. White-coat dip days 19–21. Device outage days 16–17. Synthetic medication confirmations: 91% adherence across all three medications.

ARIA pre-visit briefing: sustained elevated BP avg 163/101 over 21 days without medication adjustment, high adherence signal. Clinical interpretation: likely treatment failure rather than adherence concern. Medication review needed.

**Patient B — iEMR Only**
Source: patient 1091 iEMR, `monitoring_active=FALSE`. No home readings. ARIA uses EHR data alone. Briefing surfaces: overdue lab flag, drug interaction flag (Voltaren NSAID alongside antihypertensives), unresolved complaint from last visit note. Demonstrates ARIA is useful without CuffLink.

**Patient C — New Patient Cold Start**
No prior iEMR history. Age 58, smoker, family history of hypertension. 10 days of morning-only readings averaging 154/95, rising trend. Briefing: QRISK3 score, 10-day trend chart, NSAID flag, smoking flag, six-item visit agenda. Cold start detection ensures no misleading inertia or deterioration flags before the 14-day minimum.

### 11.2 Demo Timing

| Element | Time |
|---|---|
| Patient A walkthrough (inertia, white-coat, briefing review) | 4 minutes |
| Patient B walkthrough (EHR-only, drug interaction, overdue lab) | 3 minutes |
| Patient C walkthrough (cold start, QRISK3, 10-day trend) | 3 minutes |
| Q&A buffer | 2 minutes |
| **Total** | **12 minutes** |

---

## 12. Security, Compliance, and AI-Agent Delivery Guardrails

### 12.1 MVP Security Baseline (Current)

Named user accounts, basic clinician/admin role checks, TLS in transit, environment-variable-based secret handling, separated deployment environments. The dashboard is the only PHI-rich surface. Email must remain low-PHI with no patient identifiers or clinical values.

### 12.2 v5.0 Security Additions (Phase 8)

| Control | Action |
|---|---|
| JWT expiry | Confirm ≤ 1 hour. A leaked 7-day token gives 168× larger exposure window. |
| API rate limiting | `slowapi` middleware: POST /api/readings 60/min, POST /api/ingest 5/min, GET /api/alerts 30/min |
| DB-level audit trigger | PostgreSQL trigger on readings table — ensures audit record even on direct DB write or app bug |
| MFA | TOTP via Supabase Auth — no backend code change, configuration only |
| Research ID pseudonymization | Verify `patient_id` = 1091 is not the actual hospital MED_REC_NO cross-referenceable with hospital systems. Replace with `ARIA-001` at adapter level if needed. |

### 12.3 Audit and Access Logging

Every action below **must** create an `audit_events` row:

| Action | Required Fields |
|---|---|
| FHIR Bundle import | action='bundle_import', resource_type='Bundle', outcome='success'\|'failure' |
| Reading ingested | action='reading_ingested', resource_type='Reading' |
| Briefing viewed | action='briefing_viewed' + update briefings.read_at |
| Alert acknowledged | action='alert_acknowledged', resource_type='Alert' |

v5.0 adds a PostgreSQL trigger on the readings table so direct DB writes also produce audit records.

### 12.4 AI Coding Agent Operating Model

AI coding agents may generate boilerplate, tests, migrations, API clients, UI scaffolding, and refactoring suggestions. They may not be treated as the final authority on clinical thresholds, security language, SQL semantics, audit logic, or prompt content. Every pull request must have a named human owner, human review of clinical and security-sensitive changes, and runnable tests attached.

### 12.5 De-identified Research Data

The current dataset is de-identified with research permission. De-identified data is not PHI — no BAA is required. Key controls for research data:
- Verify MED_REC_NO cannot be re-identified via hospital cross-reference
- JWT access tokens ≤ 1 hour
- No PHI at INFO log level
- Research permission scope review before adding additional patients

---

## 13. Clinician Feedback Loop **(ROADMAP — Phases 5–7)**

ARIA's current alert disposition is binary: acknowledged or unacknowledged. The v5.0 feedback loop adds structured clinician response and outcome tracking without black-box AI self-modification.

### Layer 1 — Alert Disposition (Phase 5)

Extend acknowledge endpoint to accept `disposition`:
- `agree_acting` — clinician is taking action
- `agree_monitoring` — clinician agrees but is monitoring
- `disagree` — clinician does not think this alert is warranted

Stored in `alert_feedback` table with `reason_text`.

### Layer 2 — Calibration Recommendations (Phase 7)

When a detector accumulates ≥ 4 dismissals of the same type for the same patient, surface a calibration recommendation in the admin dashboard. Clinician approves or rejects. Approved rules stored in `calibration_rules` table with full provenance (who approved, when, based on how many dismissals). **No automatic self-modification** — every threshold change requires explicit clinician approval.

### Layer 3 — Outcome Verification (Phase 7)

When a clinician dismisses an alert, ARIA tracks the patient for 30 days. If a concerning event follows (deterioration cluster, urgent visit), prompt the clinician: "Alert dismissed on [date] — patient had a deterioration event 12 days later. Was the alert relevant in retrospect?" Retrospective labels feed Layer 2 calibration evidence.

---

## 14. Gap Explanation System **(ROADMAP — Phase 5)**

ARIA currently cannot distinguish a device malfunction from intentional non-monitoring from a patient taking a trip. All gaps produce the same alert regardless of cause.

**v5.0 `gap_explanations` table** allows patients to retroactively explain gaps via the patient-facing interface:
- `device_malfunction`, `device_lost` → alert retained, labeled "equipment issue, replace or verify"
- `travelling` → alert downgraded to low-priority briefing item
- `illness` → alert retained with clinical context flag
- `intentional_pause` → escalation suppressed but briefing item retained
- `forgot` → tracked as non-compliance signal without suppressing alert

A consistency check flags if readings resume from the same BLE source shortly after a "device broken" report.

---

## 15. Glossary

| Term | Definition |
|---|---|
| ARIA | Adaptive Real-time Intelligence Architecture |
| CuffLink | ARIA's home monitoring layer — BP cuff integration, reading submission, medication confirmation pipeline |
| iEMR | Intelligent Electronic Medical Record — LOF's patient record system, used as test fixture |
| FHIR R4 | Fast Healthcare Interoperability Resources v4 — ARIA's ingestion interface |
| SMART on FHIR | OAuth 2.0-based authentication for FHIR API access — required for live EHR connections |
| GPConnect | NHS standard for structured GP record sharing |
| Therapeutic inertia | Failure to intensify treatment when readings are consistently above target |
| Patient-adaptive threshold | `max(130, stable_baseline_mean + 1.5 × SD)` capped at 145 mmHg — derived from patient's own BP history |
| Comorbidity-adjusted threshold | Patient threshold reduced by 7 mmHg (floor 130) when cardiovascular + metabolic comorbidities simultaneously in elevated concern state |
| Pattern B suppression | Suppressing treatment-review classification when slope < -0.3 AND recent 7-day avg < threshold AND medication changed within 14 days |
| Shadow mode | Validation: ARIA alert engine vs iEMR physician PROBLEM_STATUS2_FLAG ground truth. v4.3 result: 94.3% (33/35, 0 FN, 2 FP) |
| White-coat adherence | BP readings improve before appointments and return to elevated levels afterwards |
| Full care timeline | Synthetic readings generated between all consecutive clinic BP pairs spanning the patient's entire care history |
| Medication Possession Ratio (MPR) | Proportion of days medication was available based on dispensing history — proxy for adherence |
| Parallel arrays | `active_problems[n]` corresponds to `problem_codes[n]`, `historic_bp_systolic[n]` to `historic_bp_dates[n]` |
| cold start | Patient enrolled with no home readings yet — minimum 14-day window required before pattern analysis |
| Dead-letter queue | Job status after 3 retry failures — surfaced in admin dashboard for investigation |
