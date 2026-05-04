<div align="center">

<img src="frontend/public/ARIA_LIGHT LOGO.jpg" alt="ARIA Logo" width="150" style="border-radius: 20px;" />

# ARIA
### Adherence Risk Intelligence Agent

<p>
  <img src="https://img.shields.io/badge/Python-3.11-3776ab?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Next.js-14-black?style=for-the-badge&logo=next.js&logoColor=white" />
  <img src="https://img.shields.io/badge/TypeScript-5-3178c6?style=for-the-badge&logo=typescript&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-Supabase-336791?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/FHIR-R4-e8734a?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Tests-583_Passing-brightgreen?style=for-the-badge&logo=pytest&logoColor=white" />
</p>

**ARIA** is a full-stack, between-visit clinical intelligence platform for hypertension management. It ingests patient EHR data via FHIR R4, generates a daily picture of each patient from home blood pressure readings and medication confirmations, runs a three-layer AI analysis every night, and delivers a structured pre-visit briefing to the clinician at 7:30 AM on every appointment day so they walk in already knowing.

> A clinician managing hundreds of patients has 8 minutes per consultation. ARIA makes those 8 minutes count.

[Architecture](#system-architecture) · [Three-Layer AI](#three-layer-ai-pipeline) · [Features](#features) · [Demo Patients](#demo-patients) · [Quick Start](#quick-start)

</div>

---

## What Makes This Different

Most clinical dashboards are read-only EHR viewers. ARIA is an active intelligence layer.

| Typical Clinical Dashboard | ARIA |
|---|---|
| Shows what already happened | Detects what is happening between visits |
| Manual review by clinician | Every patient analysed automatically, every night |
| One-size threshold (140 mmHg for everyone) | Patient-adaptive thresholds derived from personal baseline |
| No medication correlation | Adherence-BP correlation with drug-class-aware titration windows |
| Raw data dump | Ranked, reasoned, ready with a three-sentence briefing per patient |
| Clinician searches for problems | High-risk patients rise to the top automatically |
| No drug safety checks | Deterministic drug interaction detection in every briefing |
| No patient engagement loop | Patient PWA closes the loop: reading submitted, data flows, clinician briefed |

---

## System Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║                        ARIA: Nightly Data Flow                           ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  Hospital EHR (iEMR)                  Patient PWA (port 3001)            ║
║  ─────────────────────                ──────────────────────             ║
║  FHIR R4 Bundle                       Home BP readings (2/day)           ║
║  Conditions · Medications             Medication confirmations            ║
║  Vitals · Labs · Allergies            Symptom reports                    ║
║         │                                      │                         ║
║         ▼                                      ▼                         ║
║  ┌─────────────────┐              ┌───────────────────────┐             ║
║  │  FHIR Ingestion │              │  Synthetic Generator   │             ║
║  │  (adapter.py)   │              │  fills inter-visit     │             ║
║  │                 │              │  gaps with realistic   │             ║
║  │  iEMR to FHIR   │              │  home BP + med conf    │             ║
║  └────────┬────────┘              └──────────┬────────────┘             ║
║           │                                   │                          ║
║           └────────────┬──────────────────────┘                         ║
║                        ▼                                                 ║
║              ┌─────────────────┐                                         ║
║              │   PostgreSQL    │  12 tables · 19 indexes                 ║
║              │   (Supabase)    │  patients · readings · confirmations     ║
║              │                 │  alerts · briefings · audit_events       ║
║              └────────┬────────┘                                         ║
║                        │   midnight UTC, all monitoring_active patients   ║
║                        ▼                                                 ║
║  ┌───────────────────────────────────────────────────────────────────┐  ║
║  │                       LAYER 1: Rule Engine                          │  ║
║  │   Gap detector · Therapeutic inertia · Adherence-BP correlation    │  ║
║  │   Deterioration detector · Variability detector                    │  ║
║  │   Pure SQL, no AI, no LLM. Must pass before Layer 2 runs          │  ║
║  └───────────────────────────┬───────────────────────────────────────┘  ║
║                               │  verified Layer 1 output                 ║
║                               ▼                                          ║
║  ┌───────────────────────────────────────────────────────────────────┐  ║
║  │                       LAYER 2: Risk Scorer                          │  ║
║  │   Weighted numeric score 0.0 to 100.0 per patient                  │  ║
║  │   Systolic vs baseline · Med change lag · Adherence rate           │  ║
║  │   Gap days · Comorbidity severity                                   │  ║
║  │   Stored on patients table, dashboard sorts by tier then score     │  ║
║  └───────────────────────────┬───────────────────────────────────────┘  ║
║                               │  score stored + confirmed                ║
║                               ▼                                          ║
║  ┌───────────────────────────────────────────────────────────────────┐  ║
║  │                       LAYER 3: LLM Briefing                         │  ║
║  │   Anthropic claude-sonnet-4 converts Layer 1 output to             │  ║
║  │   3-sentence clinical narrative · Hard guardrails enforced         │  ║
║  │   Faithfulness validated before storage · Retry once on failure    │  ║
║  └───────────────────────────┬───────────────────────────────────────┘  ║
║                               │                                          ║
║                               ▼                                          ║
║  ┌────────────────────────────────────────────────────────────────┐     ║
║  │   7:30 AM Scheduler: appointment-day briefing delivery          │     ║
║  │   Pre-visit briefing delivered to clinician dashboard           │     ║
║  └───────────────────────────┬────────────────────────────────────┘     ║
║                               │                                          ║
║               ┌───────────────┼───────────────┐                         ║
║               ▼               ▼               ▼                         ║
║         Clinician        Alert Inbox      AI Chatbot                     ║
║         Dashboard        (real-time)      (natural lang                  ║
║         (port 3000)      unacknowledged   patient Q&A)                   ║
║                          flags                                           ║
╚══════════════════════════════════════════════════════════════════════════╝
```

Everything runs via a **background worker polling processing_jobs every 30 seconds** with no Celery and no Redis. APScheduler handles the 7:30 AM and midnight triggers. The worker, API server, and scheduler are the three moving parts.

---

## Three-Layer AI Pipeline

### Layer 1: Deterministic Rule Engine

No black boxes. Pure clinical logic. Runs first, always. Layer 2 and Layer 3 never run until Layer 1 is verified.

**Five detectors, run nightly on every monitoring-active patient:**

| Detector | What It Catches | Key Logic |
|---|---|---|
| **Gap** | Patient has gone silent with no home readings | Days since last reading vs tier-based threshold (High: flag at 1 day, urgent at 3) |
| **Therapeutic Inertia** | Sustained elevated BP with no medication change | 5 or more readings above patient-adaptive threshold over adaptive window with no med change |
| **Adherence-BP Correlation** | Medication confirmation rate vs systolic trend | Pattern A (high BP + low adherence), Pattern B (high BP + high adherence, titration case), Pattern C (normal BP + low adherence) |
| **Deterioration** | Rising BP trend across the monitoring window | Positive slope + 3-day avg above baseline avg + step-change sub-detector (15 mmHg shift in 3 weeks) |
| **Variability** | Unstable readings despite average appearing controlled | SD across window exceeds patient-specific variability threshold |

**Clinical precision features baked into every detector:**

- **Patient-adaptive thresholds** derived from each patient's own historic clinic readings, not hardcoded at 140 mmHg for everyone
- **Comorbidity adjustment** drops the threshold 7 mmHg (floor 130) when CHF, Stroke, or TIA is active
- **White-coat exclusion** removes readings within 5 days of appointment from inertia and deterioration checks
- **Adaptive detection window** scales to `min(90, max(14, days_between_visits))` so it fits each patient's visit rhythm
- **Drug-class-aware titration windows** suppress inertia for diuretics/beta-blockers (14 days), ACE/ARBs (28 days), amlodipine (56 days)
- **Cold-start suppression** holds detectors back for 21 days after a patient enrolls

### Layer 2: AI Risk Scoring

After Layer 1, every patient receives a numeric priority score (0.0 to 100.0).

| Component | Weight | Normalisation |
|---|---|---|
| 28-day avg systolic vs personal baseline | **30%** | Linear vs baseline |
| Days since last medication change | **25%** | Saturates at 180 days |
| Medication adherence rate (inverse) | **20%** | 100 minus adherence percentage |
| Gap duration | **15%** | gap_days divided by window_days, adaptive not hardcoded |
| Active comorbidity severity | **10%** | Severity-weighted, clamped 0 to 100 |

**Comorbidity severity weights:**

| Condition | Points |
|---|---|
| CHF (I50) · Stroke (I63/I64) · TIA (G45) | 25 pts each |
| Diabetes (E11) · CKD (N18) · CAD (I25) | 15 pts each |
| Any other coded problem | 5 pts |

Score and `risk_score_computed_at` are stored directly on the `patients` table. The dashboard sorts by `risk_tier` first (High, then Medium, then Low), then by `risk_score DESC` within each tier. A staleness badge appears when the score is older than 26 hours.

### Layer 3: LLM Clinical Briefing

A large language model converts the deterministic Layer 1 payload into a readable 3-sentence clinical briefing. It only runs after Layer 1 is verified.

**Output is validated against two classes of checks before storage:**

*Guardrails (absolute):*
- Forbidden language: "non-adherent", "non-compliant", "hypertensive crisis", "medication failure"
- Forbidden actions: dosage change recommendations, prescribing language, diagnosis, emergency declarations
- No patient identifiers (PHI) in generated text
- Prompt injection patterns blocked: "[INST]", "system:", "ignore previous"

*Faithfulness checks (against Layer 1 payload):*
- Exactly 3 sentences
- Risk score referenced within 10 points of actual score
- Adherence language grounded in the adherence summary
- Drug names present in medication status
- BP values within 20 mmHg of trend data

Every validation result writes an `audit_events` row with `action="llm_validation"`.

---

## Features

### Clinician Dashboard

- **Risk-ranked patient list** with high-risk always at the top, sorted by AI score within each tier
- **28-day BP trend column** with a live sparkline per patient row using home readings only, white-coat excluded, single source of truth via `trend_avg_systolic` from the briefing
- **Active alert inbox** showing unacknowledged gap, inertia, adherence, and deterioration flags with patient names and timestamps, plus a 24-hour undo window after acknowledgment
- **Drug interaction detector** running four deterministic safety rules in every briefing with severity escalated by comorbidity (CHF/CKD amplification), no LLM involved
- **Tier override** letting clinicians promote or demote a patient's risk tier with a 28-day NICE NG136 suppression window; system overrides for CHF, Stroke, and TIA are immovable floors
- **Guided product tour** for first-visit walkthrough
- **Alert disposition** with agree/acting, agree/monitoring, and disagree options written to `alert_feedback` and triggering a 30-day outcome verification

### Pre-Visit Briefings

Generated at 7:30 AM on appointment days. Mini-briefings generated between visits when urgent alerts fire.

| Section | Contents |
|---|---|
| **BP Trend** | Adaptive-window pattern (14 to 90 days) plus 90-day trajectory from clinic history |
| **Medication Status** | Current regimen, last change date, titration notice when within drug-class window |
| **Adherence Signal** | Per-medication confirmation rate with Pattern A/B/C interpretation |
| **Active Problems** | ICD-10 coded conditions with most recent clinician assessment text |
| **Overdue Labs** | Missing investigations flagged from EHR plus abnormal recent lab flags |
| **Drug Interactions** | Four deterministic safety rules with no LLM involvement |
| **Visit Agenda** | 3 to 6 items in clinical priority order (critical interactions first, then urgent alerts, inertia, adherence, variability, labs) |
| **Readable Summary** | 3-sentence LLM narrative, validated before display |

### Drug Interaction Detection

ARIA runs a deterministic drug interaction check as part of every briefing, with no LLM involvement. Four rules are evaluated against the patient's current medication list:

| Rule | Severity | Description |
|---|---|---|
| **NSAID + Antihypertensive** | Warning | NSAIDs can blunt antihypertensive effect and raise BP |
| **Triple Whammy** | Critical | NSAID + ACE/ARB + diuretic combination increases acute kidney injury risk |
| **K-Sparing + ACE/ARB** | Concern | Potassium-sparing diuretics combined with ACE/ARBs risk hyperkalaemia |
| **Beta-blocker + Non-DHP CCB** | Concern | Combination risks bradycardia and heart block |

Severity escalates automatically when CHF (I50) or CKD (N18) comorbidities are present. Critical interactions appear at the top of the visit agenda before all other items.

### AI Clinical Chatbot

- Answers clinician questions about specific patients in natural language
- Tool-use loop queries live patient data, readings, briefings, alerts, and medications
- Three-layer guardrail system: pre-flight keyword check, system prompt boundary, post-LLM validator
- Social phrases such as greetings and thanks handled instantly with no LLM call
- Session memory maintained within each conversation
- Blocked answers never shown and never stored to session history
- Hard tool-round cap prevents runaway API cost on edge cases

### Patient Progressive Web App

No download required. Opens in any mobile browser.

- **Home BP submission** with two-reading support, morning/evening session tagging, timestamp captured at form open per clinical spec
- **Medication confirmation** with one tap per dose, timestamped and `minutes_from_schedule` computed
- **Symptom reporting** covering headache, dizziness, chest pain, shortness of breath, and other with free text
- **Emergency safety banner** for chest pain or shortness of breath with immediate 911 prompt
- **Medication reminders** via `.ics` calendar file download for iOS and Android
- **Personalised greeting** that is time-aware and pulls the patient name live from the database
- **Daily motivational message** rotating across 5 messages, one per day, consistent all day
- **Patient JWT auth** with a separate secret from clinician auth, 8-hour expiry, blast-radius isolated

### Background Infrastructure

- **30-second poll worker** processing the `processing_jobs` queue for pattern_recompute, briefing_generation, and bundle_import jobs
- **Midnight UTC nightly run** of pattern_recompute for all monitoring-active patients via APScheduler
- **7:30 AM scheduler** for briefing_generation on patients with today's appointment
- **Escalation logic** for unacknowledged urgent alerts after 24 hours, with `off_hours` flagged for alerts triggered outside business hours
- **Full audit trail** where every bundle_import, reading_ingested, briefing_viewed, alert_acknowledged, and llm_validation writes an `audit_events` row

---

## Database Schema (12 Tables)

```
patients              : demographics, risk tier, risk score, next appointment
clinical_context      : one row per patient with medications, problems, labs, vitals, history
readings              : home BP readings (generated, manual, BLE, clinic)
medication_confirmations : scheduled doses and tap-confirmation timestamps
alerts                : gap, inertia, deterioration, adherence, symptom_urgent
briefings             : Layer 1 payload, Layer 3 LLM summary, prompt hash
processing_jobs       : background job queue with idempotency key enforced
audit_events          : immutable log of every clinical action
alert_feedback        : clinician disposition per alert (agree/disagree)
gap_explanations      : clinician-logged reason for reading gaps
calibration_rules     : per-patient detector sensitivity adjustments
outcome_verifications : 30-day follow-up check after alert dismissal
```

Critical indexes: `UNIQUE (patient_id, effective_datetime, source)` on readings prevents duplicate ingestion. `UNIQUE (patient_id, medication_name, scheduled_time)` on confirmations enforces idempotent generation. Dashboard sort uses a `(risk_tier, risk_score DESC)` composite index.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11, FastAPI, SQLAlchemy 2.0 async, Pydantic v2 |
| **Database** | PostgreSQL (Supabase), asyncpg driver |
| **Clinician Frontend** | Next.js 14, TypeScript strict, Tailwind CSS, Recharts |
| **Patient PWA** | Next.js 14, @ducanh2912/next-pwa, standalone display mode |
| **AI (Layer 3)** | Anthropic claude-sonnet-4-20250514 |
| **AI (Chatbot)** | OpenAI gpt-4o-mini |
| **Auth** | JWT with separate clinician and patient secrets |
| **EHR Integration** | Custom iEMR to FHIR R4 adapter |
| **Background Jobs** | APScheduler and processing_jobs queue table with 30s poll worker |
| **Rate Limiting** | slowapi at 5 requests per minute on the patient auth endpoint |

---

## Project Structure

```
ARIA/
├── backend/
│   └── app/
│       ├── api/                     # 14 FastAPI routers
│       │   ├── patients.py          # dashboard list and tier override
│       │   ├── briefings.py         # briefing lifecycle and active filter
│       │   ├── alerts.py            # inbox, acknowledged history, escalation
│       │   ├── auth.py              # patient JWT exchange
│       │   ├── confirmations.py     # pending, confirm, .ics, /me profile
│       │   ├── readings.py          # BP submission
│       │   ├── ingest.py            # FHIR bundle import
│       │   └── admin.py             # scheduler trigger
│       ├── models/                  # 12 SQLAlchemy models
│       └── services/
│           ├── fhir/                # iEMR adapter, ingestion engine, validator
│           ├── generator/           # Synthetic BP reading and confirmation generators
│           ├── pattern_engine/      # 5 Layer 1 detectors and Layer 2 risk scorer
│           ├── briefing/            # Layer 1 composer, Layer 3 LLM, validator,
│           │                        # and medication safety (drug interactions)
│           ├── chat/                # Chatbot agent with tool-use loop and guardrails
│           └── worker/              # Background processor and 7:30 AM scheduler
├── frontend/                        # Clinician dashboard on port 3000
│   └── src/
│       ├── app/                     # Next.js pages
│       ├── components/
│       │   ├── dashboard/           # PatientList, RiskTierBadge, AlertInbox, MiniSparkline
│       │   ├── briefing/            # BriefingCard, ChatPanel, AdherenceSummary
│       │   └── shared/              # PatientHeader, LoadingSpinner
│       └── lib/                     # api.ts, types.ts, auth.ts
├── patient-app/                     # Patient PWA on port 3001
│   └── src/app/
│       ├── page.tsx                 # Login with Research ID and ARIA logo
│       ├── submit/page.tsx          # BP submission form
│       └── confirm/page.tsx         # Medication confirmation dashboard
├── scripts/                         # setup_db, setup_demo, run_generator,
│                                    # run_worker, run_scheduler
└── prompts/                         # briefing_summary_prompt.md, chat_system_prompt.md
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL (Supabase recommended, free tier works)
- Anthropic API key (Layer 3 briefings)
- OpenAI API key (chatbot)

### 1. Backend

```bash
conda create -n aria python=3.11 && conda activate aria
cd backend
pip install -r requirements.txt

cp .env.example .env
# Fill in: DATABASE_URL, ANTHROPIC_API_KEY, OPENAI_API_KEY,
#          JWT_SECRET, PATIENT_JWT_SECRET

python scripts/setup_db.py

uvicorn app.main:app --reload --port 8000
```

### 2. Clinician Dashboard

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

### 3. Patient PWA

```bash
cd patient-app
npm install
npm run dev        # http://localhost:3001
```

### 4. Seed Demo Data

```bash
python scripts/setup_demo.py

python scripts/setup_demo.py --verify-only
# Expected: ALL CHECKS PASSED
```

### 5. Start Background Worker

```bash
python scripts/run_worker.py
```

---

## Demo Patients

| Patient ID | Name | Scenario |
|---|---|---|
| `1091` | John Doe | **Therapeutic inertia:** 65 clinic readings over 5 years with sustained elevated BP and no medication change since 2013 |
| `DEMO_GAP` | David Patel | **Reading gap:** 82 days of consistent readings followed by a 12-day silence with urgent gap alert fired on day 9 |
| `DEMO_ADH` | Sarah Mitchell | **Adherence concern:** 58% medication confirmation rate correlated with 152 mmHg average systolic (Pattern A) |
| `DEMO_EHR` | Robert Clarke | **EHR-only:** no home monitoring, overdue labs, NSAID and antihypertensive drug interaction flagged |

---

## Running Tests

```bash
cd backend
python -m pytest tests/ -v -m "not integration"
# 583 unit tests passing
```

Integration tests hit a live database and are tagged `@pytest.mark.integration`. Unit tests use fixtures with no database or API keys required.

---

## Key Engineering Decisions

**1. Patient-adaptive thresholds over hardcoded 140 mmHg**
Hardcoding 140 mmHg as the treatment threshold is clinically wrong. A patient with a personal baseline of 125 mmHg is at risk at 138 mmHg, while a patient with a baseline of 160 mmHg may have a different agreed target. Every threshold in ARIA is derived from the patient's own history.

**2. Deterministic Layer 1 before any AI**
The LLM in Layer 3 only ever summarises what Layer 1 has already proven. It never discovers insights on its own. Every clinical claim in a briefing is traceable to a database query. The LLM adds readability, not reasoning.

**3. Separate patient JWT secret**
Patient tokens and clinician tokens are signed with different secrets. A compromised patient token cannot call any clinician endpoint. The blast radius of a patient-side breach is bounded to that patient's own data.

**4. Adaptive detection window**
Fixing the detection window at 28 days is wrong for a patient seen every 6 months (too short) and wrong for a patient seen every 2 weeks (too long). ARIA scales the window to fit each patient's care rhythm using `min(90, max(14, days_between_visits))`.

**5. Drug-class-aware titration suppression**
Flagging therapeutic inertia when a medication was changed 10 days ago is a false alarm. Some drugs need 56 days to show their full effect, such as amlodipine. Suppression windows are keyed to drug class, not a single arbitrary cutoff.

**6. Absent rows for device outages, never NULLs**
When a patient's device is offline, no reading row is created for that day. NULL-filled rows would corrupt trend calculations and make gap detection unreliable. The gap detector counts days since the last row, and the absence of data is itself the signal.

---

## Clinical Boundaries

| ARIA does | ARIA never does |
|---|---|
| Flag sustained elevated BP trends | Recommend specific medications or dosages |
| Surface adherence patterns | Contact or alert patients directly |
| Generate pre-visit briefings | Display raw readings to patients |
| Rank patients by clinical priority | Make clinical decisions |
| Detect drug interactions | Diagnose conditions |
| Provide decision support | Override clinician judgment |

Removing ARIA from the workflow leaves the clinician exactly where they were before: informed by their EHR and making their own decisions. ARIA only compresses the time it takes to reach that informed state.

**The clinician always decides. ARIA only informs.**

---

## Future Scope

ARIA is built on a foundation designed to scale. The following directions represent the natural next phases of the platform.

### Real EHR Integration
The current iEMR adapter converts a structured JSON export into FHIR R4. The next step is live bidirectional integration with real EHR systems such as Epic, Cerner, or NHS EMIS using SMART on FHIR OAuth2. This would eliminate the export step entirely and allow ARIA to operate as a continuous passive layer on top of existing clinical infrastructure.

### Bluetooth Device Integration
The patient app currently accepts manual BP submissions. Integrating Bluetooth Low Energy support for consumer-grade devices such as Omron and Withings cuffs would allow readings to flow automatically with no manual entry. The backend webhook infrastructure for BLE is already in place. The missing piece is the vendor SDK integration and device pairing flow in the PWA.

### Expanding Beyond Hypertension
The three-layer architecture is condition-agnostic. The detectors, risk scorer, and briefing composer could be extended to other chronic conditions such as Type 2 diabetes (HbA1c trending, insulin adherence), heart failure (weight monitoring, fluid retention signals), and COPD (peak flow tracking, exacerbation detection). Each condition would require its own Layer 1 ruleset but would share the same infrastructure.

### Machine Learning Risk Scoring
Layer 2 currently uses a clinician-designed weighted formula. With sufficient longitudinal outcome data, a supervised model trained on real clinical outcomes (hospitalisation, adverse events, medication changes) could replace or complement the formula. The existing `outcome_verifications` table already captures the ground truth needed for this training pipeline.

### Population-Level Analytics
ARIA currently operates at the individual patient level. A population dashboard would surface aggregate trends across an entire patient list: how many patients are in the therapeutic inertia window, what percentage have subthreshold adherence, which drug classes are most commonly associated with non-response. This gives practice managers and clinical leads a system-level view.

### Direct Patient Communication
Currently ARIA does not communicate with patients directly. A future release could introduce clinician-approved nudges: a reminder message when a gap alert fires, a prompts when adherence drops below threshold, or a pre-appointment checklist. All messages would require clinician authorisation before sending, preserving the clinical boundary.

### Native Mobile Applications
The patient PWA works well on mobile browsers. A native iOS and Android app would enable push notifications for medication reminders, background sync of BP readings, and deeper integration with Apple Health and Google Fit for automatic data ingestion.

### Clinician Mobile App
A lightweight clinician-facing mobile view for reviewing the daily briefing and acknowledging alerts without opening the full dashboard. Designed for ward rounds and on-the-go review rather than deep case analysis.

### Pharmacy and Dispensing Integration
Linking ARIA to pharmacy dispensing records would provide an objective signal for medication collection (whether the patient picked up their prescription) alongside the subjective confirmation data from the patient app. This would strengthen the adherence model significantly.

---

## Team: Neura Care Nexus

| Name |
|---|
| Krishna Patel |
| Kush Patel |
| Sahilsingh Khalsa |
| Nesh Rochwani |
| Prakriti Sharma |
| Yash Sharma |

---

<div align="center">

**Illinois Institute of Technology · CS 595 · Spring 2026**

*Neura Care Nexus*

<sub>Python · FastAPI · Next.js · TypeScript · PostgreSQL · FHIR R4 · Anthropic · OpenAI · Tailwind CSS · Recharts</sub>

</div>
