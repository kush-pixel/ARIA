# ARIA — Adaptive Real-time Intelligence Architecture
## Master Product Document
**Leap of Faith Technologies | IIT CS 595 | Spring 2026**
**Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma**

---

## Table of Contents

1. [The Problem We Solve](#1-the-problem-we-solve)
2. [What ARIA Is](#2-what-aria-is)
3. [Competitive Landscape](#3-competitive-landscape)
4. [How ARIA Works — End to End](#4-how-aria-works--end-to-end)
5. [Three-Layer AI Architecture](#5-three-layer-ai-architecture)
6. [Drug Interaction Detector](#6-drug-interaction-detector)
7. [Risk Tier System](#7-risk-tier-system)
8. [The Clinician Dashboard](#8-the-clinician-dashboard)
9. [The Patient App — CuffLink](#9-the-patient-app--cufflink)
10. [Ask ARIA — Clinical Chatbot](#10-ask-aria--clinical-chatbot)
11. [EHR Integration](#11-ehr-integration)
12. [Synthetic Data Engine](#12-synthetic-data-engine)
13. [Validation — Shadow Mode](#13-validation--shadow-mode)
14. [Technology Stack](#14-technology-stack)
15. [Clinical Boundaries — Non-Negotiable](#15-clinical-boundaries--non-negotiable)
16. [Limitations — Honest Assessment](#16-limitations--honest-assessment)
17. [Future Work — Product Roadmap](#17-future-work--product-roadmap)
18. [Demo Guide](#18-demo-guide)

---

## 1. The Problem We Solve

A GP managing 1,800 hypertensive patients gets approximately **8 minutes per consultation** and has **no structured view of what happened between appointments**.

When a patient walks in, the GP typically sees:
- A single clinic BP reading from 3 months ago
- A static medication list from the last visit
- No record of home readings, missed doses, or worsening trends
- No flag for drug interactions introduced since the last review

This creates compounding problems:

**Therapeutic inertia** — Studies estimate it contributes to 40–80% of uncontrolled hypertension cases. The GP sees persistently elevated BP but makes no medication adjustment because they lack structured evidence that the current regimen is failing.

**Invisible between-visit events** — A patient whose BP was controlled at the last appointment may have experienced a 3-week upward trend at home. Without a monitoring system, that trend is invisible until the patient returns, often too late.

**Drug interaction blind spots** — A patient who added an NSAID (e.g. Voltaren for knee pain) since their last hypertension review may now be in a triple whammy interaction with their ACE inhibitor and diuretic — an acute kidney injury risk. No structured alert exists between visits.

ARIA fixes all three by giving the GP a **structured pre-visit intelligence briefing** at 7:30 AM on appointment day — before the patient walks in.

---

## 2. What ARIA Is

ARIA is a **between-visit clinical intelligence platform** for hypertension management at GP practice scale.

It does four things:

1. **Ingests** structured patient EHR data via FHIR R4 Bundle from the clinic's iEMR system
2. **Monitors** home blood pressure and medication adherence via the CuffLink patient app
3. **Analyses** this data through a three-layer AI pipeline — deterministic rules → weighted risk scoring → LLM narrative — plus a deterministic drug interaction detector
4. **Delivers** a prioritised pre-visit clinical briefing, sorted by patient urgency, so the GP knows exactly what to discuss in those 8 minutes

**What ARIA is not:**
- Not a remote monitoring programme requiring extra clinical staff
- Not an AI scribe (it operates before the visit, not during)
- Not a patient-facing coaching tool
- Not a diagnostic system or prescribing tool

Every ARIA output is **decision support for the clinician only**. ARIA organises what the GP already knows and surfaces what they haven't seen yet — in 30 seconds, before the patient walks in.

---

## 3. Competitive Landscape

| Platform | Model | Gap vs ARIA |
|---|---|---|
| **Cadence** | 24/7 RPM with parallel nurse team. Epic integration. | Requires a dedicated care navigator team. No pre-visit GP briefing. Not viable for an independent practice. |
| **HealthSnap** | RPM + Chronic Care Management. Care navigators manage alerts. | Staffed programme, not a clinician intelligence tool. |
| **CureApp HT** | FDA-cleared digital therapeutic. Patient-facing lifestyle coaching. | No longitudinal EHR context. No pre-visit briefing. No drug interaction detection. |
| **AI Scribes (Suki, DAX)** | Real-time visit documentation. | Handle what happens during the visit. Nothing before it. |
| **ARIA** | Pre-visit briefing. FHIR-native ingestion. CuffLink monitoring. Drug interaction detection. Adaptive risk tier management. No extra staff. | **The gap no competitor addresses.** |

The market offers tools for in-visit and post-visit workflows. **No competitor owns the pre-visit intelligence layer.** ARIA fills that gap.

---

## 4. How ARIA Works — End to End

```
Patient takes home BP reading (or CuffLink submits automatically)
    ↓
POST /api/readings  →  readings table  +  audit event
    ↓
Background worker wakes (LISTEN/NOTIFY or 30s poll)
    ↓
Layer 1: 5 deterministic detectors run (gap, inertia, adherence, deterioration, variability)
    ↓
Layer 2: Weighted risk score computed (0–100), stored on patients table
    ↓
_apply_tier_reclassification(): nightly hysteresis check — may promote or demote tier
    ↓
Alerts table updated (one per detector that fires, deduplicated by day)
    ↓
7:30 AM APScheduler: briefing jobs queued for today's appointment patients
    ↓
Drug interaction check: 4 rules run against current_medications + problem_codes (no LLM)
    ↓
Layer 3: LLM generates 3-sentence readable summary (validated before storage)
    ↓
Clinician opens dashboard → patients sorted by tier, then risk score
    ↓
Briefing page: full pre-visit intelligence, drug interactions, visit agenda, AI summary, audit logged
```

The entire pipeline is **asynchronous**. Nothing runs in the HTTP request path. The GP's morning experience is: open dashboard → see who matters most today → click for full briefing.

---

## 5. Three-Layer AI Architecture

ARIA's intelligence pipeline has three layers that run in strict sequence. Layer 1 must be complete before Layer 2 runs. Layer 3 never runs before Layer 1 is verified.

### Layer 1 — Deterministic Rule Engine

**No AI. No probability estimates. Pure clinical logic.**

Five detectors run for every monitoring-active patient on the nightly sweep:

#### Gap Detection
Days since the patient's last home reading, compared against tier-appropriate thresholds:

| Risk Tier | Flag Threshold | Urgent Threshold |
|---|---|---|
| High | 1 day | 3 days |
| Medium | 3 days | 5 days |
| Low | 7 days | 14 days |

A High Risk patient with CHF who stops measuring at home is flagged after a single missed day.

#### Therapeutic Inertia Detection
Fires when all four conditions are simultaneously true:
1. Average systolic over the adaptive window exceeds the **patient's own adaptive threshold** (not a population number)
2. At least 5 elevated readings confirm this is not a one-off spike
3. The elevated pattern has persisted for more than 7 days
4. No medication change appears in the medication history that could explain the elevation (accounting for drug-class titration windows)

**Patient-adaptive threshold:** `max(130, stable_baseline_mean + 1.5 × SD)` capped at 145 mmHg. For patient 1091 with mean ~134 mmHg and SD ~16 mmHg, this gives ~158 mmHg. After CHF comorbidity adjustment (-7 mmHg), the effective threshold is ~151 mmHg.

**Comorbidity adjustment:** CHF, Stroke, TIA, or Haemorrhagic stroke → threshold lowered 7 mmHg (floor 130).

**White-coat exclusion:** Readings within 5 days of the next appointment are excluded from threshold comparison. Prevents the pre-appointment BP dip from masking legitimate flags.

#### Adherence-BP Correlation
Three distinct clinical patterns:

| Pattern | Condition | Interpretation |
|---|---|---|
| A | High BP + low adherence (<80%) | "Possible adherence concern" — conversation about barriers |
| B | High BP + high adherence | "Treatment review warranted" — medications may need adjustment |
| C | Normal BP + low adherence | "Contextual review" — BP controlled despite partial adherence |

Pattern B is suppressed during active titration windows (drug-class-aware: diuretics/beta-blockers 14d, ACE/ARBs 28d, amlodipine 56d) when BP is actively declining. Suppression is never applied when no recent medication change exists.

#### Deterioration Detection
Three gates that must all pass:
1. Positive linear slope across the adaptive window (≥0.3 mmHg/day)
2. Recent 3-day average exceeds the days 4–10 baseline average
3. Recent average is at or above the patient's adaptive threshold (prevents alerting on 115→119 rises)

A **step-change sub-detector** catches sudden BP jumps: 7-day rolling mean this week minus 7-day mean from 3 weeks ago ≥ 15 mmHg fires regardless of linear slope.

#### Variability Detection
Coefficient of variation (CV = SD/mean × 100) of systolic readings:
- CV ≥ 15%: "Consider ABPM referral" agenda item
- CV 12–14%: "Monitor trend"

High BP variability is an independent cardiovascular risk factor — a patient averaging 135 mmHg with swings of 115–165 mmHg carries higher risk than a patient at 142 mmHg with stable readings.

---

### Layer 2 — Weighted Risk Scoring

After Layer 1 completes, a numeric priority score (0.0–100.0) is computed for every patient and stored on the patients table. This controls the dashboard sort order within each tier.

**Five signals, weights sum to 1.00:**

| Signal | Weight | What it measures |
|---|---|---|
| Systolic vs personal baseline | 30% | How far current BP deviates from the patient's own historical mean |
| Medication inertia | 25% | Days since last medication change (saturates at 6 months / 180 days) |
| Inverted adherence | 20% | `100 - adherence_pct` |
| Reading gap | 15% | Gap days normalised to the adaptive window (not a fixed divisor) |
| Comorbidity severity | 10% | CHF/Stroke/TIA = 25pts each; DM/CKD/CAD = 15pts; others 5pts — clamped 0–100 |

This is a deterministic formula, not a machine learning model. Without labeled outcome data, an ML model would fit noise. The weighted rule-based approach is interpretable, auditable, and correctable. Production deployment would recalibrate weights via clinician feedback using the `calibration_rules` table.

A staleness badge appears when the score is more than 26 hours old.

---

### Layer 3 — LLM Narrative (Optional)

After the deterministic briefing is complete and persisted, an LLM converts the structured JSON into a **3-sentence readable summary**.

**Model:** claude-sonnet-4-20250514 (currently gpt-4o-mini in demo — one-line reversion).

**11 validation checks before output reaches the UI:**
- No forbidden phrases: "non-adherent", "non-compliant", "hypertensive crisis", "medication failure", dose-change instructions, "prescribe", "diagnose", "emergency", "tell the patient"
- No PHI leak (patient ID verbatim)
- No prompt injection ("[INST]", "system:", "ignore previous")
- Exactly 3 sentences
- Risk score within ±10 of computed value
- BP values clinically plausible (60–250 mmHg) and within ±20 mmHg of trend data
- Drug names grounded in medication_status
- Urgency claims grounded in urgent_flags
- Condition names grounded in active_problems (synonym map for known variants)

If validation fails: retry once, then store `readable_summary = null`. The full deterministic briefing is always shown. Every validation attempt writes an `audit_events` row.

---

## 6. Drug Interaction Detector

**File:** `backend/app/services/briefing/medication_safety.py`

A completely deterministic drug interaction checker. **No LLM. No additional database queries.** Called from `composer.py` at briefing generation time using the already-fetched `ClinicalContext` object.

### Four Rules

| Rule | Combination | Base Severity | Escalation |
|---|---|---|---|
| `nsaid_antihypertensive` | NSAID + any antihypertensive | warning | → concern when CHF or CKD present |
| `triple_whammy` | NSAID + ACE/ARB + any diuretic | concern | → critical when both CHF AND CKD present |
| `k_sparing_ace_arb` | K-sparing diuretic + ACE inhibitor or ARB | warning | → concern when CKD present |
| `bb_non_dhp_ccb` | Beta-blocker + non-DHP CCB (verapamil/diltiazem) | concern | No escalation — always concern |

**Deduplication:** Triple whammy is evaluated before the NSAID + antihypertensive rule. When triple whammy fires, the simpler NSAID rule is suppressed — the clinician sees one finding, not two overlapping ones.

**Severity levels:**
- `warning` — clinically relevant, review recommended
- `concern` — requires active review before or during the visit
- `critical` — requires immediate discussion; suggests urgent review

### Comorbidity Amplification
The `comorbidity_amplified: true` flag in the interaction output tells the clinician that severity was escalated specifically because of an active comorbidity. For example: "NSAID + ramipril + furosemide — triple whammy, concern level. Comorbidities (CHF + CKD) escalate severity to critical."

### Clinical Significance for Demo Patient 1091
Patient 1091 has Voltaren (diclofenac — NSAID) in the medication list alongside ramipril (ACE inhibitor) and furosemide (loop diuretic). This produces a **triple whammy** interaction. With CHF (I50.9) active, severity escalates toward critical. This flag appears in the Visit Agenda at the highest priority.

### Visit Agenda Priority with Interactions

| Priority | Agenda Item |
|---|---|
| 0 | Critical drug interactions (before all other items) |
| 1 | Urgent alerts (gap_urgent, deterioration) |
| 1a | Concern-level drug interactions (alongside urgent alerts) |
| 2 | Therapeutic inertia |
| 3 | Adherence concern |
| 3a | Warning-level drug interactions |
| 4 | Variability flag |
| 5 | Overdue labs |
| 6 | Active problems review |

### Briefing Payload Field
```json
"drug_interactions": [
  {
    "rule": "triple_whammy",
    "severity": "critical",
    "drugs_involved": ["Voltaren", "ramipril", "furosemide"],
    "description": "Triple whammy combination: NSAID with ACE inhibitor/ARB and diuretic — significantly elevated acute kidney injury risk. Comorbidities (CHF + CKD) escalate severity to critical.",
    "comorbidity_amplified": true
  }
]
```

---

## 7. Risk Tier System

Risk tier is the categorical label (`high` | `medium` | `low`) displayed on the clinician dashboard. It controls the primary sort order. Risk score (0–100) is the secondary sort within each tier.

ARIA's tier system has three independent layers: **ingestion-time overrides**, **nightly algorithmic reclassification**, and **clinician manual overrides**.

### 7.1 Ingestion-Time Auto-Overrides (Immovable Floors)

When certain ICD-10 codes appear in the problem list at FHIR ingestion, the patient is immediately promoted to High Risk. These overrides cannot be demoted via the clinician endpoint or the nightly job — they require updating the EHR problem list and re-ingesting.

| Condition | ICD-10 | Override Reason | Source |
|---|---|---|---|
| Congestive Heart Failure | I50.* | "CHF in problem list" | system |
| Haemorrhagic Stroke | I61.* | "Haemorrhagic stroke history" | system |
| Ischaemic Stroke | I63/I64 | "Stroke history" | system |
| TIA | G45.* | "TIA history" | system |

**Why these four?** Missing CHF or stroke on a patient's risk tier has direct patient safety implications. The hard floor ensures no algorithm or clinician decision can inadvertently leave a CHF patient in Medium Risk.

### 7.2 New Database Columns

Two columns were added to the `patients` table to support the full reclassification system:

| Column | Values | Purpose |
|---|---|---|
| `tier_override_source` | `system` \| `system_score` \| `clinician` \| `NULL` | Tracks the authority that set the current tier. Controls all reclassification guards. |
| `tier_override_suppressed_until` | `TIMESTAMPTZ` \| `NULL` | When a clinician demotes a patient, the nightly job will not reverse the decision until this timestamp expires. |

### 7.3 Nightly Tier Reclassification

`_apply_tier_reclassification()` runs in `processor.py` after every `pattern_recompute` job, once Layer 2 scoring completes.

**Guard order (first match wins):**
1. `tier_override_source == "system"` → return immediately (immovable floor — CHF/Stroke/TIA)
2. `tier_override_source == "clinician"` AND `now < suppressed_until` AND `score < 85` → skip (clinician's decision is active)
3. `tier_override_source == "clinician"` AND `now < suppressed_until` AND `score >= 85` → break-glass: clear suppression, apply promotion only
4. Apply hysteresis transition table:

| Transition | Score Condition | Additional Gates |
|---|---|---|
| medium → high | score ≥ 75 | None |
| high → medium | score < 40 | `source == "system_score"` only — never demotes a system override |
| medium → low | score < 25 | Enrolled ≥ 90 days + no severe/moderate comorbidity + no active urgent alerts |
| low → medium | score ≥ 40 | None |

**Hysteresis band:** The gap between 40 and 75 is intentional. A patient holding steady in this band does not oscillate between tiers. This prevents thrash without requiring a previous-score history column.

**Comorbidity block for medium → low:** A patient with any of the following codes cannot be demoted to Low regardless of score: CHF (I50), Haemorrhagic Stroke (I61), Ischaemic Stroke (I63/I64), TIA (G45), Diabetes (E11), CKD (N18), CAD (I25).

Every tier change writes an `audit_events` row: `actor_type="system"`, `action="tier_reclassified"`, `details="tier=medium→high score=78.4 source=system_score"`.

### 7.4 Clinician Manual Override

**Endpoint:** `PATCH /api/patients/{patient_id}/tier` (requires clinician JWT)

**Request:**
```json
{ "risk_tier": "medium", "reason": "BP controlled over 6 months, patient stable" }
```

**Behaviour:**
- **Demotion (e.g., high → medium):** Sets `tier_override_suppressed_until = now + 28 days` (NICE NG136 §1.6.3 — 4-week review standard). The nightly job will not reverse this for 28 days unless score ≥ 85 (break-glass).
- **Promotion (e.g., medium → high):** Clears `tier_override_suppressed_until` immediately.
- **409 Conflict:** If `tier_override_source == "system"` (CHF/Stroke/TIA patient) — instructs clinician to update the EHR and re-ingest. Cannot bypass safety floor via API.
- **422:** Invalid tier string or empty/missing reason.

**Why 28 days?** NICE NG136 §1.6.3 specifies a 4-week review standard for hypertension management changes. Aligning the suppression window to this standard means the clinician's decision is protected for exactly one clinical review cycle.

### 7.5 Re-ingestion Tier Safety

The old `ON CONFLICT DO NOTHING` pattern at ingestion meant a CHF diagnosis added to the EHR mid-care would never promote the patient to High Risk in ARIA. This is fixed with a two-step upsert:

- **Step A:** Demographics always update (gender, age — safe to overwrite)
- **Step B:** Tier update applies only when the new ingestion carries a system condition OR no active override exists to protect

A clinician-overridden tier is preserved across re-ingestions unless the EHR now carries a system condition (CHF/Stroke/TIA) — safety always wins.

---

## 8. The Clinician Dashboard

### Patient List

The GP opens the dashboard and sees their patient panel sorted by clinical urgency:
- **Tier first:** High > Medium > Low
- **Risk score second:** Within each tier, patients sorted by Layer 2 score descending

**Columns:**

| Column | What it shows |
|---|---|
| Patient | Patient name (if available) + patient ID, gender, age |
| Chronic Risk | High / Medium / Low tier badge. Radix tooltip explains basis (system override vs score-driven vs clinician-set) |
| Chief Concern | Derived from active problems: CHF, Stroke, TIA, Diabetes, CKD, CAD. Always appends Hypertension. Never null — every ARIA patient has hypertension. |
| Priority Score | 0–100 composite score bar; "Score outdated (>26h)" badge if stale |
| BP Trend | Low / Stable / High relative to 140 mmHg (NICE/ESC clinical target). Reads directly from `trend_avg_systolic` in the active briefing when available; falls back to live 28-day computation between visits. |
| Appointment | Time shown only for today's appointments; "—" otherwise |

**Single source of truth for BP Trend:** The briefing composer stores `trend_avg_systolic` (home-readings-only mean, `source != "clinic"`) directly in the briefing payload. The dashboard reads this value from the active briefing rather than recomputing independently — eliminating up to 14 mmHg divergence between the dashboard number and the briefing number.

**Between visits:** When no active briefing exists, `trend_avg_systolic` is null and the frontend falls back to a live 28-day computation from the readings table.

**Tier filter tabs:** All / High / Medium / Low with counts. 10 patients per page.

### Patient Briefing Page

The briefing is the core deliverable of ARIA. Every section is deterministic except the optional 3-sentence AI summary at the top.

**Active briefing filter:** `GET /api/briefings/{patient_id}` returns the most-recent **active** briefing only — `appointment_date >= today OR appointment_date IS NULL`. Past appointment briefings are excluded so clinicians never see stale pre-visit content after a visit has already occurred. Mini-briefings (generated for between-visit urgent alerts, `appointment_date = NULL`) always pass through.

**Header:** Patient name and ID, demographics, tier badge, risk score bar, appointment time, briefing generated-at and read-at timestamps. Viewing the briefing writes an audit_events row and sets `briefings.read_at`.

**AI Summary (Layer 3):** 3-sentence readable narrative in a blue card. Hidden if LLM validation failed.

**Data Limitations Banner:** Amber if home monitoring inactive. Gray notice for cold-start suppression (enrolled < 21 days).

**BP Trend Section:**
- Text: adaptive-window home average, trend direction, Stage classification
- 3-month clinic trajectory from EHR
- recharts LineChart with a reference line at `trend_avg_systolic` (same number as the dashboard column)

**Drug Interactions Section:** Appears when `drug_interactions` is non-empty. Severity-coded cards (critical = red, concern = amber, warning = gray). Each card shows: the rule name, drugs involved, plain-English description, and comorbidity escalation notice if applicable.

**Medication Status:** Current antihypertensive regimen and last change date. 60+ drug names across 11 classes filtered in `hypertension-meds.ts`. Titration notice appended within drug's response window.

**Adherence Signal:** Per-medication confirmation rates for antihypertensives. Pattern A/B/C classification. Language always "possible adherence concern" — enforced at string level and LLM validator.

**Active Problems:** Priority-sorted condition badges (CHF → HTN → DM → CAD → others alphabetically).

**Overdue Investigations:** Flagged from ServiceRequest resources and abnormal recent lab flags.

**Visit Agenda:** Prioritised 3–6 item consultation plan. Critical drug interactions lead the list. Urgent alerts follow. Clinical findings then labs then problems.

**Clinical Flags:** Active unacknowledged alerts as amber boxes. Acknowledged alerts disappear from future briefings.

### Alert Inbox

| Type | Colour | Meaning |
|---|---|---|
| `gap_urgent` | Red | No home BP beyond urgent threshold |
| `gap_briefing` | Amber | Gap — review at next appointment |
| `inertia` | Amber | Possible therapeutic inertia |
| `deterioration` | Red | Possible sustained BP worsening |
| `adherence` | Amber | Possible adherence concern |

**Escalation:** `gap_urgent` and `deterioration` unacknowledged for >24 hours get an "Escalated" badge.

**Off-hours badge:** Alerts triggered 6 PM–8 AM UTC or weekends are tagged "Off-hours".

**Acknowledge / Undo:** 24-hour undo window. Audit event on acknowledge.

**Calibration suppression:** When a clinician has accumulated ≥ 4 dismissals of the same detector type and an approved `calibration_rules` row exists, the alert inbox write is suppressed for that patient-detector pair. Detection still runs and findings still appear in the briefing — only the inbox alert is suppressed. An `alert_suppressed_by_calibration` audit event is written instead.

---

## 9. The Patient App — CuffLink

CuffLink is ARIA's home monitoring layer. The name decouples ARIA's intelligence engine from any single device vendor and gives the platform a patient-facing identity.

### Patient PWA

An installable Progressive Web App (Next.js 14, port 3001) — iOS, Android, and desktop from one codebase.

**Three screens:**

**Login** — JWT-based patient authentication using a separate `patient_jwt_secret`. 8-hour token expiry. Role="patient".

**BP Submit (`/submit`)** — Patient enters:
- Systolic and diastolic (two readings per session)
- Session type (morning / evening / ad hoc)
- Optional symptoms (headache, dizziness, chest pain, shortness of breath)
- Whether medication was taken (yes / no / partial)

Clinically plausible range validation before submission. Submitted to `POST /api/readings`.

**Medication Confirm (`/confirm`)** — Pending medication doses with scheduled times. One-tap confirmation records `confirmed_at`. Missed doses feed the adherence rate calculation.

**Calendar reminders (.ics)** — Downloadable medication schedule reminders for Apple Calendar, Google Calendar, and Outlook. Generated server-side by `ics_generator.py`.

### What Patients Never See
- Their own BP readings (clinical boundary)
- Their risk tier or risk score
- Any clinical interpretation

The patient experience is purely data submission. All analysis is on the clinician side.

### CuffLink Device Pathways

| Pathway | Status |
|---|---|
| Manual entry via PWA | **Current** |
| BLE Bluetooth cuff (GATT 0x1810 — Omron, iHealth, Withings, Beurer) | **Roadmap** |
| Vendor cloud webhook (Omron Connect, Withings API) | **Roadmap** — fastest path |
| SMS submission for feature phones | **Deferred post-MVP** |

---

## 10. Ask ARIA — Clinical Chatbot

An LLM-powered conversational interface embedded in every patient briefing page. The GP asks natural-language questions; ARIA queries the patient's data and answers.

**Example questions:**
- "Why is this patient's risk score so high?"
- "What was their adherence like over the last month?"
- "Summarise the last 3 alerts for this patient"
- "Is there a drug interaction concern I should know about?"

**Three-layer guardrail system:**
1. Off-topic detection — non-clinical topics declined
2. Scope check — questions about other patients or system administration blocked
3. Output validation — no prescriptive language, dose-change instructions, or patient-directed advice

**Tool calls:**
- `get_briefing(patient_id)` — fetch current structured briefing
- `get_readings(patient_id, days)` — query recent readings
- `get_alerts(patient_id)` — list active alerts

The chatbot leads with findings and never asks for clarification when a tool call can answer directly.

---

## 11. EHR Integration

### FHIR R4 Bundle Import

The only source-specific code is the EHR adapter. Everything downstream only sees FHIR resources.

**Two-layer architecture:**
- **Adapter** (`adapter.py`): Converts iEMR JSON → FHIR Bundle
- **Ingestion** (`ingestion.py`): Validates Bundle, populates 12 tables, never sees iEMR field names

### FHIR Resources Extracted

| Resource | What ARIA Extracts |
|---|---|
| Patient | Demographics: id, gender, birthDate, name |
| Condition | Problem list: ICD-10/SNOMED code, status, onset |
| MedicationRequest | Current regimen: drug name, RxNORM, dosage, date |
| AllergyIntolerance | Active allergies with reaction manifestation |
| Observation (vitals) | In-clinic BP (LOINC 55284-4), pulse, weight, SpO2 |
| ServiceRequest | Pending lab orders (PLAN_NEEDS_FOLLOWUP=YES) |
| `_aria_med_history` | Full medication timeline (non-FHIR extension key) |
| `_aria_problem_assessments` | Per-visit physician HTN concern assessments |
| `_aria_visit_dates` | All visit dates for accurate last_visit_date |

### Auto-Overrides at Ingestion

Four system conditions produce immovable High Risk overrides: CHF (I50), Haemorrhagic Stroke (I61), Ischaemic Stroke (I63/I64), TIA (G45). These are hard rules — no algorithm or clinician API call can bypass them.

**Re-ingestion is now safe:** The two-step upsert (replacing `ON CONFLICT DO NOTHING`) ensures that a CHF diagnosis added to the EHR mid-care will correctly promote the patient to High Risk on the next ingestion.

### Demo Patient: 1091

- 124 clinic visits (11 years, 2002–2015)
- 65 in-clinic BP readings (mean ~134 mmHg, SD ~16 mmHg)
- 14 active medications (including Voltaren → triple whammy interaction)
- 17 coded problems (CHF I50.9, T2DM E11, CAD I25, HTN I10, and others)
- Full medication history across all visits

---

## 12. Synthetic Data Engine

ARIA generates clinically realistic home BP readings and medication confirmations to simulate a connected monitoring device. Principled extrapolation from real clinic readings using peer-reviewed variance parameters.

### Clinical Realism Rules

| Parameter | Rule | Failure Mode |
|---|---|---|
| Day-to-day systolic SD | 8–12 mmHg | Flat variance (<5 mmHg) is a spreadsheet, not a patient |
| Morning/evening differential | Morning 5–10 mmHg higher | Must be visible every week |
| Round numbers | Never exactly round | Real readings: 153, 161, 148, 167 |
| Two-reading session | Readings 1 and 2 differ by 2–6 mmHg | Reading 2 typically slightly lower |
| Diastolic | Systolic × 0.60–0.66 | Diastolic doesn't move independently in treated hypertension |
| Heart rate | 64–82 bpm, negative correlation with systolic | Reflects beta-blocker effect |
| Device outage | 1–2 episodes of 2–4 days per interval | Absent rows — never null values |
| White-coat dip | 10–15 mmHg drop over 3–5 days before appointment | Must recover to elevated baseline post-appointment |

### Full Care Timeline

The generator produces synthetic readings across the patient's full care history by interpolating between consecutive clinic BP anchor points with Gaussian noise. For patient 1091: 1,800+ home readings across the 2008–2015 period.

**Parametric baseline:** `median(clinical_context.historic_bp_systolic)` — not hardcoded. Falls back to 163 mmHg for the demo window (~158 mmHg avg in the 2011–2013 period).

### Medication Confirmations

Beta distribution (α=6.5, β=0.65, mean ≈ 91%) with ±10–15 pp variation per medication. For patient 1091: 1,092 scheduled doses, ~91% confirmation rate.

---

## 13. Validation — Shadow Mode

Shadow mode replays the alert engine at each historical clinic visit using only the data available before that visit, then compares ARIA's output against the physician's own HTN concern assessment (`PROBLEM_STATUS2_FLAG`).

**Ground truth:** Physician-labeled concern levels from visit notes:
- 3 = stable, 2 = concerned, 1 = urgent

**Current result:** The PRESENTATION_GUIDE (code-traced) reports 78.4% agreement (37 labeled evaluation points, 6 false negatives, 2 false positives) — just below the 80% target. CLAUDE.md records an earlier 94.3% result (33/35 points) from a prior ingestion run. The 78.4% figure reflects the re-run after the `historic_bp_systolic` ingestion was corrected, which changed the patient_threshold calculation across all evaluation points. The honest presentation number is **78.4%**.

**False negatives (6):** All clinically explainable — cold start (insufficient data), active treatment response (ARIA correctly silent), same-day medication changes. Zero false negatives from system bugs.

**False positives (2):** Documented in AUDIT.md. Both involve rising trends the physician assessed as stable.

**What shadow mode does not measure:** Patient outcomes. A prospective study with outcome data is required for outcome validation.

---

## 14. Technology Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11, FastAPI, SQLAlchemy 2.0 async (never `session.query()`), Pydantic v2 |
| **Database** | PostgreSQL via Supabase, asyncpg driver, 12 tables |
| **Background Jobs** | APScheduler (7:30 AM briefings, midnight sweeps) + processing_jobs table + pg_notify |
| **AI (Layer 3 only)** | Anthropic claude-sonnet-4-20250514 (gpt-4o-mini in current demo — one-line reversion) |
| **Frontend** | Next.js 14, TypeScript strict mode, Tailwind CSS, recharts |
| **Patient PWA** | Next.js 14 PWA (@ducanh2912/next-pwa), port 3001 |
| **Auth** | JWT with separate clinician and patient secrets (blast-radius isolation), 8h patient expiry |
| **Testing** | 601 unit tests, pytest, `ruff check` clean |
| **Deployment** | Supabase (DB), environment variable secrets, TLS in transit |

### Database — 12 Tables

```
patients               — demographics, name, risk tier, tier_override_source,
                         tier_override_suppressed_until, risk score, monitoring status
clinical_context       — pre-computed EHR context (one row per patient)
readings               — home and clinic BP readings
medication_confirmations — dose adherence records
alerts                 — detector-fired flags
alert_feedback         — clinician dispositions on alerts
briefings              — structured pre-visit briefing payload (includes drug_interactions,
                         trend_avg_systolic)
processing_jobs        — background job queue with idempotency
audit_events           — immutable access and action log
gap_explanations       — patient-submitted gap reasons
calibration_rules      — clinician-approved threshold adjustments
outcome_verifications  — 30-day retrospective outcome tracking
```

### Audit Logging

Every significant action creates an `audit_events` row:
- `bundle_import` — FHIR ingestion
- `reading_ingested` — every home or clinic reading
- `briefing_viewed` — clinician opens a patient briefing (+ `read_at`)
- `alert_acknowledged` — clinician action on alert
- `llm_validation` — every Layer 3 validation attempt
- `tier_reclassified` — nightly job changes a patient's tier
- `alert_suppressed_by_calibration` — calibration rule suppresses an alert inbox write

---

## 15. Clinical Boundaries — Non-Negotiable

These boundaries are **enforced at code level**, not as guidelines:

| What ARIA does NOT do | Enforcement |
|---|---|
| Recommend specific medications | Forbidden phrase check in LLM validator; absent from all deterministic outputs |
| Recommend dose changes | Regex blocks "increase X mg", "decrease X mg" |
| Send alerts directly to patients | No patient notification pathway exists |
| Display raw readings to patients | Patient app has no readings display screen |
| Make clinical decisions | All outputs framed as decision support |
| Label patients as "non-adherent" | String-level enforcement in `_INTERPRETATIONS` dict and LLM validator |
| Use "hypertensive crisis" or "medication failure" | LLM validator blocks; absent from deterministic text |
| Demote a system-override patient via API | `PATCH /api/patients/{id}/tier` returns 409 for CHF/Stroke/TIA patients |

ARIA uses "possible adherence concern", not "non-adherent."
ARIA uses "treatment review warranted", not "medication failure."
ARIA uses "sustained elevated readings", not "hypertensive crisis."

---

## 16. Limitations — Honest Assessment

### Limitation 1: Shadow mode agreement is 78.4%, below the 80% target

The current shadow mode result (37 labeled evaluation points, 78.4% agreement, 6 false negatives, 2 false positives) is 1.6 percentage points below the target. All false negatives are clinically explained: cold start, active treatment response, same-day medication changes. Zero false negatives from system bugs. Expanding the evaluation dataset to the full 71+ no-vitals visits where physicians explicitly assessed HTN would likely improve this figure.

### Limitation 2: Layer 3 is currently gpt-4o-mini, not claude-sonnet-4-20250514

Both `summarizer.py` and `chat/agent.py` use OpenAI gpt-4o-mini marked `# TEMP`. The architecture specifies Anthropic claude-sonnet-4-20250514. The validation layer works identically regardless of model. Reversion is a one-line change per file.

### Limitation 3: One demo patient tested end to end

The entire pipeline has been validated on a single de-identified patient (1091). Multi-patient validation with diverse demographics is required before any clinical deployment. Weight calibration, threshold tuning, and false positive characterisation all benefit from a larger population.

### Limitation 4: Adherence data is synthetic

Medication confirmations are generated from a Beta distribution. A real deployment requires data from a smart pill dispenser, app-based confirmation, or pharmacist dispensing records.

### Limitation 5: No patient outcome validation

Shadow mode measures agreement with physician concern labels — not whether acting on ARIA's briefings improves BP control or reduces cardiovascular events. A prospective clinical study is required.

### Limitation 6: Risk score weights not empirically calibrated

The five weights (30/25/20/15/10) are expert-informed clinical judgment, not ML-optimised. The `calibration_rules` table and infrastructure exist — the longitudinal clinician feedback data does not yet.

### Limitation 7: Single EHR source (LOF iEMR)

The FHIR adapter is written for the LOF iEMR JSON structure. Connecting a different EHR requires a new adapter. Epic, Cerner, EMIS, and SystmOne integrations are each substantial projects.

### Limitation 8: Clinician tier demotion suppression window is 28 days (fixed)

The suppression window is currently a fixed constant (`_CLINICIAN_SUPPRESSION_DAYS = 28`) aligned to NICE NG136. A production system would allow the clinician to set a custom review date per patient.

---

## 17. Future Work — Product Roadmap

### Near Term — CuffLink Hardware
- BLE Bluetooth cuff integration (GATT 0x1810)
- Vendor cloud webhook (Omron Connect, Withings) — fastest path
- Per-observation idempotency gate (prerequisite for hardware deduplication)

### Clinical Intelligence
- Transition from gpt-4o-mini to claude-sonnet-4-20250514 for Layer 3 and chatbot (one-line change)
- Extend drug interaction ruleset beyond four current rules (STOPP/START criteria)
- Patient-adjustable review date for clinician tier demotion (replace fixed 28-day window)
- Lab values ingestion: creatinine, potassium, HbA1c, eGFR (FHIR Observation LOINC codes)
- SpO2 <92% in CHF patients → visit agenda alert (infrastructure present, FHIR mapping done)

### Feedback Loop
- **Alert disposition:** structured clinician response on acknowledge: agree_acting / agree_monitoring / disagree
- **Calibration recommendations:** after ≥4 dismissals of the same detector type, surface a threshold adjustment recommendation. Clinician approves explicitly — no automatic self-modification
- **30-day outcome verification:** when a clinician dismisses an alert, track the patient for 30 days; prompt retrospectively if deterioration follows

### Scale
- Multi-patient shadow mode validation (currently patient 1091 only)
- Risk score weight recalibration via clinician feedback data
- Dashboard search, filter, and export features

### Infrastructure
- SMART on FHIR launch (Epic, Cerner, EMIS) — live EHR connectivity
- TOTP MFA via Supabase Auth
- API rate limiting (slowapi: 60/min readings, 5/min ingest)
- TheraCare integration (LOF): CuffLink as a TheraCare screen

---

## 18. Demo Guide

### 3-Minute Live Demo Script

**[Open dashboard at localhost:3000]**

"This is ARIA's clinical dashboard. Patients are sorted by risk tier first, then priority score within each tier. High Risk patients appear at the top."

**[Point to Patient 1091]**

"Patient 1091 is High Risk. That's an automatic override because CHF is in the EHR problem list — it is not an AI estimate. The nightly algorithm cannot demote a CHF patient to Medium Risk. A clinician also cannot override it via the API — they'd need to update the EHR and re-ingest."

**[Point to BP Trend column]**

"The BP Trend column shows High, Stable, or Low against 140 mmHg — the NICE clinical target. This value comes directly from the briefing payload so the dashboard and briefing always show the same number."

**[Click patient → briefing page opens]**

"The 3-sentence AI summary at the top was generated by Layer 3 after the deterministic briefing was complete. Every word went through 11 validation checks before it reached this page."

**[Point to Drug Interactions section]**

"This is new — a deterministic drug interaction detector. Patient 1091 has Voltaren alongside ramipril and furosemide. That's a triple whammy: NSAID + ACE inhibitor + diuretic. With active CHF and CKD, severity escalates to critical. No LLM involved — this is a rule check against the medication list."

**[Point to Visit Agenda]**

"The visit agenda puts critical drug interactions first. Then urgent alerts. Then inertia, adherence, labs. Those 8 minutes have a structure before the patient walks in."

**[Point to Medication Status]**

"Layer 1: last medication change was 2013. No antihypertensive adjustment in 12 years while BP remains elevated — that's therapeutic inertia. The titration window check rules out a recent change explaining it."

**[Switch to patient app at localhost:3001]**

"The CuffLink patient app. One-tap BP submission, medication confirmation, and calendar reminders. The patient never sees their own readings."

**[Close]**

"ARIA doesn't diagnose. It doesn't prescribe. It doesn't talk to patients. It takes what the GP already has and structures it — 30 seconds before the patient walks in."

---

### Key Numbers to Know

| Metric | Value |
|---|---|
| Clinic visits in demo dataset | 124 (patient 1091, 2002–2015) |
| Clinic BP readings | 65 (mean ~134 mmHg, SD ~16 mmHg) |
| Synthetic home readings | 1,800+ (full care timeline) |
| Active medications (demo patient) | 14 |
| Active problems (demo patient) | 17 |
| Drug interactions detected (demo patient) | Triple whammy (Voltaren + ramipril + furosemide) — critical with CHF+CKD |
| Adherence rate (synthetic) | ~91% |
| Shadow mode agreement | 78.4% (37 evaluation points, below 80% target) |
| False negatives | 6 (all clinically explained, 0 from system bugs) |
| Unit tests passing | 601 |
| Layer 3 validation checks | 11 |
| Drug interaction rules | 4 (nsaid_antihypertensive, triple_whammy, k_sparing_ace_arb, bb_non_dhp_ccb) |
| Risk tier suppression window (clinician demotion) | 28 days (NICE NG136) |

---

### Questions to Be Ready For

**"Where is the AI?"**
Three layers. Layer 1 is deterministic rules — correct by design. Layer 2 is a five-signal weighted formula. Layer 3 is Claude generating a 3-sentence readable summary. The drug interaction detector is also deterministic — not AI. The AI adds narrative fluency; it does not generate the clinical findings.

**"Your shadow mode is below target. Why should we trust this?"**
78.4% is honest — 1.6 percentage points below target. The 6 false negatives are clinically explained. Zero false negatives from bugs. An earlier run showed 94.3% against a different ingestion baseline. We present the most recent, most rigorous figure.

**"What's the triple whammy?"**
NSAID + ACE inhibitor/ARB + diuretic. This combination significantly elevates acute kidney injury risk. Patient 1091 has Voltaren (NSAID), ramipril (ACE inhibitor), and furosemide (diuretic) — with active CHF and CKD, severity escalates to critical. This is caught by a pure rule check with no LLM involvement.

**"How do you prevent hallucination?"**
11 validation checks before any LLM output reaches the UI. If any check fails, the summary is retried once then stored as null. The full deterministic briefing is always shown. The LLM cannot corrupt the clinical data layer.

**"Can a clinician override the risk tier?"**
Yes, via `PATCH /api/patients/{id}/tier` with a reason string. Demotions are suppressed for 28 days (NICE NG136). But a patient with CHF, Stroke, TIA, or haemorrhagic stroke returns a 409 — those overrides require updating the EHR and re-ingesting. The system won't let a clinician API call remove a CHF patient from High Risk.

**"Why not just use 140 mmHg as the threshold?"**
The patient-adaptive threshold uses the patient's own historical mean plus 1.5 standard deviations. For patient 1091: ~158 mmHg effective threshold before comorbidity adjustment. Using 140 mmHg for everyone generates constant false positives for patients who have always run moderately elevated.

---

*ARIA v4.3 — Master Product Document*
*Leap of Faith Technologies | IIT CS 595 | May 2026*
*Updated 2026-05-02 — reflects: risk tier reclassification system, drug interaction detector,*
*briefing lifecycle filter, BP Trend single source of truth, alert calibration suppression, 601 tests*
