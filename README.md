<div align="center">

<img src="frontend/public/ARIA_LIGHT LOGO.jpg" alt="ARIA Logo" width="150" style="border-radius: 20px;" />

# ARIA
### Adaptive Real-time Intelligence Architecture

<p>
  <img src="https://img.shields.io/badge/Python-3.11-3776ab?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge&logo=fastapi&logoColor=white" />
  <img src="https://img.shields.io/badge/Next.js-14-black?style=for-the-badge&logo=next.js&logoColor=white" />
  <img src="https://img.shields.io/badge/TypeScript-5-3178c6?style=for-the-badge&logo=typescript&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-Supabase-336791?style=for-the-badge&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/FHIR-R4-e8734a?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Tests-583_Passing-brightgreen?style=for-the-badge&logo=pytest&logoColor=white" />
  <img src="https://img.shields.io/badge/Shadow_Mode-94.3%25_Accuracy-brightgreen?style=for-the-badge" />
</p>

**ARIA** is a full-stack, between-visit clinical intelligence platform for hypertension management. It ingests patient EHR data via FHIR R4, generates a daily picture of each patient from home blood pressure readings and medication confirmations, runs a three-layer AI analysis every night, and delivers a structured pre-visit briefing to the GP at 7:30 AM on every appointment day — so they walk in already knowing.

> A GP managing 1,800 patients has 8 minutes per consultation. ARIA makes those 8 minutes count.

[Architecture](#system-architecture) · [Three-Layer AI](#three-layer-ai-pipeline) · [Features](#features) · [Demo Patients](#demo-patients) · [Quick Start](#quick-start) · [Validation](#shadow-mode-validation)

</div>

---

## What Makes This Different

Most clinical dashboards are read-only EHR viewers. ARIA is an active intelligence layer.

| Typical Clinical Dashboard | ARIA |
|---|---|
| Shows what already happened | Detects what is happening between visits |
| Manual review by clinician | Every patient analysed automatically, every night |
| One-size threshold (e.g. 140 mmHg for everyone) | Patient-adaptive thresholds derived from personal baseline |
| No medication correlation | Adherence-BP correlation with drug-class-aware titration windows |
| Raw data dump | Ranked, reasoned, ready — three-sentence briefing per patient |
| Clinician searches for problems | High-risk patients rise to the top automatically |
| No patient engagement loop | Patient PWA closes the loop — reading submitted, data flows, doctor briefed |

---

## System Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║                        ARIA — Nightly Data Flow                          ║
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
║  │  (adapter.py)   │              │  (fills inter-visit    │             ║
║  │                 │              │   gaps with realistic  │             ║
║  │  iEMR → FHIR R4 │              │   home BP + med conf)  │             ║
║  └────────┬────────┘              └──────────┬────────────┘             ║
║           │                                   │                          ║
║           └────────────┬──────────────────────┘                         ║
║                        ▼                                                 ║
║              ┌─────────────────┐                                         ║
║              │   PostgreSQL    │  12 tables · 19 indexes                 ║
║              │   (Supabase)    │  patients · readings · confirmations     ║
║              │                 │  alerts · briefings · audit_events       ║
║              └────────┬────────┘                                         ║
║                        │   midnight UTC — all monitoring_active patients  ║
║                        ▼                                                 ║
║  ┌───────────────────────────────────────────────────────────────────┐  ║
║  │                       LAYER 1 — Rule Engine                        │  ║
║  │   Gap detector · Therapeutic inertia · Adherence-BP correlation    │  ║
║  │   Deterioration detector · Variability detector                    │  ║
║  │   Pure SQL — no AI, no LLM — must pass before Layer 2 runs        │  ║
║  └───────────────────────────┬───────────────────────────────────────┘  ║
║                               │  verified Layer 1 output                 ║
║                               ▼                                          ║
║  ┌───────────────────────────────────────────────────────────────────┐  ║
║  │                       LAYER 2 — Risk Scorer                        │  ║
║  │   Weighted numeric score 0.0–100.0 per patient                     │  ║
║  │   Systolic vs baseline · Med change lag · Adherence rate           │  ║
║  │   Gap days · Comorbidity severity                                   │  ║
║  │   Stored on patients table → dashboard sorts by tier then score    │  ║
║  └───────────────────────────┬───────────────────────────────────────┘  ║
║                               │  score stored + confirmed                ║
║                               ▼                                          ║
║  ┌───────────────────────────────────────────────────────────────────┐  ║
║  │                       LAYER 3 — LLM Briefing                       │  ║
║  │   Anthropic claude-sonnet-4 converts Layer 1 output to             │  ║
║  │   3-sentence clinical narrative · Hard guardrails enforced         │  ║
║  │   Faithfulness validated before storage · Retry once on failure    │  ║
║  └───────────────────────────┬───────────────────────────────────────┘  ║
║                               │                                          ║
║                               ▼                                          ║
║  ┌────────────────────────────────────────────────────────────────┐     ║
║  │   7:30 AM Scheduler — appointment-day briefing delivery         │     ║
║  │   Pre-visit briefing delivered to GP dashboard before rounds    │     ║
║  └───────────────────────────┬────────────────────────────────────┘     ║
║                               │                                          ║
║               ┌───────────────┼───────────────┐                         ║
║               ▼               ▼               ▼                         ║
║         Clinician        Alert Inbox      AI Chatbot                     ║
║         Dashboard        (real-time)     (natural lang                   ║
║         (port 3000)      unacknowledged   patient Q&A)                   ║
║                          flags                                           ║
╚══════════════════════════════════════════════════════════════════════════╝
```

Everything runs via a **background worker polling processing_jobs every 30 seconds** — no Celery, no Redis. APScheduler handles the 7:30 AM and midnight triggers. The worker, API server, and scheduler are the three moving parts.

---

## Three-Layer AI Pipeline

### Layer 1 — Deterministic Rule Engine

No black boxes. Pure clinical logic. Runs first, always. Layer 2 and Layer 3 never run until Layer 1 is verified.

**Five detectors, run nightly on every monitoring-active patient:**

| Detector | What It Catches | Key Logic |
|---|---|---|
| **Gap** | Patient has gone silent — no home readings | Days since last reading vs tier-based threshold (High ≥1 flag, ≥3 urgent) |
| **Therapeutic Inertia** | Sustained elevated BP with no medication change | ≥5 readings above patient-adaptive threshold over adaptive window, no med change in window |
| **Adherence-BP Correlation** | Medication confirmation rate vs systolic trend | Pattern A (high BP + low adherence), Pattern B (high BP + high adherence → titration case), Pattern C (normal BP + low adherence) |
| **Deterioration** | Rising BP trend across the monitoring window | Positive slope + 3-day avg > baseline avg + step-change sub-detector (≥15 mmHg in 3 weeks) |
| **Variability** | Unstable readings despite average appearing controlled | SD across window exceeds patient-specific variability threshold |

**Clinical precision features baked into every detector:**

- **Patient-adaptive thresholds** — derived from `median(historic_bp_systolic)` filtered to stable visits; not hardcoded at 140 mmHg for everyone
- **Comorbidity adjustment** — threshold drops 7 mmHg (floor 130) when CHF/Stroke/TIA active, or when both cardiovascular and metabolic comorbidities are elevated
- **White-coat exclusion** — readings within 5 days of appointment are excluded from inertia and deterioration checks (covers the synthetic generator's pre-appointment dip window)
- **Adaptive detection window** — `min(90, max(14, days_between_visits))` — scales to each patient's visit frequency, never degenerates below 14 days
- **Drug-class-aware titration windows** — inertia suppressed for diuretics/beta-blockers (14d), ACE/ARBs (28d), amlodipine (56d)
- **Cold-start suppression** — detectors wait 21 days after enrollment before firing

### Layer 2 — AI Risk Scoring

After Layer 1, every patient receives a numeric priority score (0.0–100.0).

| Component | Weight | Normalisation |
|---|---|---|
| 28-day avg systolic vs personal baseline | **30%** | Linear vs baseline |
| Days since last medication change | **25%** | Saturates at 180 days |
| Medication adherence rate (inverse) | **20%** | `(100 − adherence_pct)` |
| Gap duration | **15%** | `gap_days / window_days` — adaptive, not hardcoded |
| Active comorbidity severity | **10%** | Severity-weighted, clamped 0–100 |

**Comorbidity severity weights:**

| Condition | Points |
|---|---|
| CHF (I50) · Stroke (I63/I64) · TIA (G45) | 25 pts each |
| Diabetes (E11) · CKD (N18) · CAD (I25) | 15 pts each |
| Any other coded problem | 5 pts |

Score and `risk_score_computed_at` are stored directly on the `patients` table. The dashboard sort is: `risk_tier` first (High → Medium → Low), then `risk_score DESC` within each tier. A staleness badge appears when `risk_score_computed_at` is older than 26 hours.

### Layer 3 — LLM Clinical Briefing

A large language model converts the deterministic Layer 1 payload into a readable 3-sentence clinical briefing. It only runs after Layer 1 is verified.

**Output is validated against two classes of checks before storage:**

*Guardrails (absolute — payload is irrelevant):*
- `"non-adherent"`, `"non-compliant"`, `"hypertensive crisis"`, `"medication failure"`
- `"increase.*mg"` / `"decrease.*mg"` / `"prescribe"` / `"diagnos"` / `"emergency"`
- `"tell the patient"` / patient ID verbatim
- Prompt injection patterns: `"[INST]"`, `"system:"`, `"ignore previous"`

*Faithfulness checks (vs Layer 1 payload):*
- Exactly 3 sentences
- Risk score referenced within ±10 of actual score
- Adherence language grounded in `adherence_summary`
- `"titration"` requires a titration notice in `medication_status`
- Drug names present in `medication_status`
- BP values within ±20 mmHg of trend data (range 60–250)

Every validation result writes an `audit_events` row with `action="llm_validation"`.

---

## Features

### Clinician Dashboard

- **Risk-ranked patient list** — high-risk always at top, sorted by AI score within tier
- **28-day BP trend column** — live sparkline per patient row, home-readings only, white-coat excluded, single source of truth via `trend_avg_systolic` from briefing
- **Active alert inbox** — unacknowledged gap, inertia, adherence, and deterioration flags with patient names and timestamps; 24-hour undo window after acknowledgment
- **Drug interaction detector** — four deterministic rules, severity escalated by comorbidity (CHF/CKD amplification); runs in briefing composer, no LLM
- **Tier override** — clinician can promote or demote a patient's tier; demotion sets a 28-day NICE NG136 suppression window; `system` overrides (CHF, Stroke, TIA) are immovable floors
- **Guided product tour** — first-visit walkthrough for new clinicians
- **Alert disposition** — agree/acting · agree/monitoring · disagree, written to `alert_feedback` table and triggers a 30-day outcome verification

### Pre-Visit Briefings

Generated at 7:30 AM on appointment days. Mini-briefings generated between visits when urgent alerts fire.

| Section | Contents |
|---|---|
| **BP Trend** | Adaptive-window pattern (14–90 days) + 90-day trajectory from clinic history |
| **Medication Status** | Current regimen, last change date, titration notice when within window |
| **Adherence Signal** | Per-medication confirmation rate, Pattern A/B/C interpretation |
| **Active Problems** | ICD-10 coded conditions with most recent GP assessment text |
| **Overdue Labs** | Missing investigations flagged from EHR + abnormal recent lab flags |
| **Drug Interactions** | Four deterministic safety rules — no LLM involvement |
| **Visit Agenda** | 3–6 items in clinical priority order (critical interactions first, then urgent alerts, inertia, adherence, variability, labs) |
| **Readable Summary** | 3-sentence LLM narrative, validated before display |

Briefing API lifecycle: `GET /api/briefings/{patient_id}` returns the most-recent *active* briefing only. Past appointment briefings are excluded — the clinician always sees current state between visits.

### AI Clinical Chatbot

- Answers clinician questions about specific patients in natural language
- Tool-use loop queries live patient data, readings, briefings, alerts, and medications
- **Three-layer guardrail system:** pre-flight keyword check → system prompt boundary → post-LLM validator
- Social phrases (greetings, thanks, farewells) handled instantly — no LLM call
- Session memory maintained within each conversation
- Blocked answers never shown and never stored to session history (prevents repeated-ask pressure)
- Hard `MAX_TOOL_ROUNDS` cap prevents runaway API cost on edge cases

### Patient Progressive Web App

No download required. Opens in any mobile browser.

- **Home BP submission** — two-reading support, morning/evening session tagging, timestamp captured at form open (not at submit) per clinical spec
- **Medication confirmation** — one tap per dose, timestamped, `minutes_from_schedule` computed and stored
- **Symptom reporting** — headache, dizziness, chest pain, shortness of breath, other (with free text input)
- **Emergency safety banner** — chest pain or shortness of breath triggers immediate 911 prompt
- **Medication reminders** — `.ics` calendar file download for any calendar app (iOS and Android)
- **Personalised greeting** — time-aware (morning/afternoon/evening), patient name fetched live from DB
- **Daily motivational message** — rotates across 5 messages, one per day, deterministic (same message all day)
- **Patient JWT auth** — separate secret from clinician auth, 8-hour expiry, blast-radius isolated

### Background Infrastructure

- **30-second poll worker** — processes `processing_jobs` queue (pattern_recompute · briefing_generation · bundle_import)
- **Midnight UTC** — `pattern_recompute` for all `monitoring_active = TRUE` patients via APScheduler
- **7:30 AM scheduler** — `briefing_generation` for patients with today's appointment
- **Escalation logic** — unacknowledged `gap_urgent` and `deterioration` alerts escalate after 24 hours, `off_hours` flag set for alerts triggered 6PM–8AM UTC or weekends
- **Full audit trail** — every `bundle_import`, `reading_ingested`, `briefing_viewed`, `alert_acknowledged`, and `llm_validation` writes an `audit_events` row with actor, outcome, and timestamp

---

## Shadow Mode Validation

ARIA was validated in blind shadow mode against real historical patient records — ARIA's output compared against ground-truth clinical classifications with no feedback.

| Metric | Result |
|---|---|
| Overall accuracy | **94.3%** (33/35 points) |
| False negatives | **0 — no high-risk patient was missed** |
| False positives | 2 (documented and resolved in production detectors) |
| Ground truth source | `PROBLEM_STATUS2_FLAG` (3 = stable, 2 = concerned, 1 = urgent) |

```bash
python scripts/run_shadow_mode.py --patient 1091 --iemr data/raw/iemr/1091_data.json
# Results written to: data/shadow_mode_results.json
```

---

## Database Schema — 12 Tables

```
patients              — demographics, risk tier, risk score, next appointment
clinical_context      — one row per patient: medications, problems, labs, vitals, history
readings              — home BP readings (generated + manual + BLE + clinic)
medication_confirmations — scheduled doses + tap-confirmation timestamps
alerts                — gap · inertia · deterioration · adherence · symptom_urgent
briefings             — Layer 1 payload + Layer 3 LLM summary + prompt hash
processing_jobs       — background job queue (idempotency key enforced)
audit_events          — immutable log of every clinical action
alert_feedback        — clinician disposition per alert (agree/disagree)
gap_explanations      — clinician-logged reason for reading gaps
calibration_rules     — per-patient detector sensitivity adjustments
outcome_verifications — 30-day follow-up check after alert dismissal
```

Critical indexes: `UNIQUE (patient_id, effective_datetime, source)` on readings prevents duplicate ingestion. `UNIQUE (patient_id, medication_name, scheduled_time)` on confirmations enforces idempotent generation. Dashboard sort uses `(risk_tier, risk_score DESC)` composite index.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11 · FastAPI · SQLAlchemy 2.0 async · Pydantic v2 |
| **Database** | PostgreSQL (Supabase) · asyncpg driver |
| **Clinician Frontend** | Next.js 14 · TypeScript strict · Tailwind CSS · Recharts |
| **Patient PWA** | Next.js 14 · @ducanh2912/next-pwa · standalone display mode |
| **AI — Layer 3** | Anthropic `claude-sonnet-4-20250514` |
| **AI — Chatbot** | OpenAI `gpt-4o-mini` |
| **Auth** | JWT · separate clinician and patient secrets |
| **EHR Integration** | Custom iEMR → FHIR R4 adapter |
| **Background Jobs** | APScheduler + `processing_jobs` queue table · 30s poll worker |
| **Rate Limiting** | slowapi — 5 req/min on patient auth endpoint |

---

## Project Structure

```
ARIA/
├── backend/
│   └── app/
│       ├── api/                     # 14 FastAPI routers
│       │   ├── patients.py          # dashboard list + tier override
│       │   ├── briefings.py         # briefing lifecycle + active filter
│       │   ├── alerts.py            # inbox + acknowledged history + escalation
│       │   ├── auth.py              # patient JWT exchange
│       │   ├── confirmations.py     # pending · confirm · .ics · /me profile
│       │   ├── readings.py          # BP submission
│       │   ├── ingest.py            # FHIR bundle import
│       │   └── admin.py             # scheduler trigger, shadow mode
│       ├── models/                  # 12 SQLAlchemy models
│       └── services/
│           ├── fhir/                # iEMR adapter + ingestion engine + validator
│           ├── generator/           # Synthetic BP reading + confirmation generators
│           ├── pattern_engine/      # 5 Layer 1 detectors + Layer 2 risk scorer
│           ├── briefing/            # Layer 1 composer + Layer 3 LLM + validator
│           │                        # + medication safety (drug interactions)
│           ├── chat/                # Chatbot agent (tool-use loop + guardrails)
│           └── worker/              # Background processor + 7:30 AM scheduler
├── frontend/                        # Clinician dashboard — port 3000
│   └── src/
│       ├── app/                     # Next.js pages
│       ├── components/
│       │   ├── dashboard/           # PatientList · RiskTierBadge · AlertInbox · MiniSparkline
│       │   ├── briefing/            # BriefingCard · ChatPanel · AdherenceSummary
│       │   └── shared/              # PatientHeader · LoadingSpinner
│       └── lib/                     # api.ts · types.ts · auth.ts
├── patient-app/                     # Patient PWA — port 3001
│   └── src/app/
│       ├── page.tsx                 # Login (Research ID + ARIA logo)
│       ├── submit/page.tsx          # BP submission form
│       └── confirm/page.tsx         # Medication confirmation + dashboard
├── scripts/                         # setup_db · setup_demo · run_generator
│                                    # run_worker · run_shadow_mode · run_scheduler
└── prompts/                         # briefing_summary_prompt.md · chat_system_prompt.md
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL (Supabase recommended — free tier works)
- Anthropic API key (Layer 3 briefings)
- OpenAI API key (chatbot)

### 1 — Backend

```bash
# Create environment
conda create -n aria python=3.11 && conda activate aria
cd backend
pip install -r requirements.txt

# Configure
cp .env.example .env
# Fill in: DATABASE_URL · ANTHROPIC_API_KEY · OPENAI_API_KEY
#          JWT_SECRET · PATIENT_JWT_SECRET

# Create tables and run all migrations (safe to re-run)
python scripts/setup_db.py

# Start API server
uvicorn app.main:app --reload --port 8000
```

### 2 — Clinician Dashboard

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

### 3 — Patient PWA

```bash
cd patient-app
npm install
npm run dev        # http://localhost:3001
```

### 4 — Seed Demo Data

```bash
# From project root — idempotent, safe to re-run at any time
python scripts/setup_demo.py

# Verify without re-seeding
python scripts/setup_demo.py --verify-only
# Expected: ALL CHECKS PASSED ✓
```

### 5 — Start Background Worker

```bash
python scripts/run_worker.py
```

---

## Demo Patients

| Patient ID | Name | Scenario |
|---|---|---|
| `1091` | Patient A | **Therapeutic inertia** — 65 clinic readings over 5 years, sustained elevated BP, no medication change since 2013 |
| `DEMO_GAP` | David Patel | **Reading gap** — 82 days of consistent readings, then 12-day silence; urgent gap alert fired on day 9 |
| `DEMO_ADH` | Sarah Mitchell | **Adherence concern** — ~58% medication confirmation rate correlated with ~152 mmHg avg systolic (Pattern A) |
| `DEMO_EHR` | Robert Clarke | **EHR-only** — no home monitoring, overdue labs, NSAID + antihypertensive drug interaction flagged |

---

## Running Tests

```bash
cd backend
python -m pytest tests/ -v -m "not integration"
# 583 unit tests passing
```

Integration tests hit a live database and are tagged `@pytest.mark.integration`. Unit tests use fixtures — no database or API keys required.

---

## Key Engineering Decisions

**1. Patient-adaptive thresholds over hardcoded 140 mmHg**
Hardcoding 140 mmHg as the treatment threshold is clinically wrong — a patient with a personal baseline of 125 mmHg is at risk at 138 mmHg, while a patient baseline of 160 mmHg may have a different target agreed with their GP. Every threshold in ARIA is derived from the patient's own history.

**2. Deterministic Layer 1 before any AI**
The LLM (Layer 3) only ever summarises what Layer 1 has already proven — it never discovers insights. This means every clinical claim in a briefing is traceable to a database query. The LLM adds readability, not reasoning.

**3. Separate patient JWT secret**
Patient tokens and clinician tokens are signed with different secrets. A compromised patient token cannot call any clinician endpoint. The blast radius of a patient-side breach is bounded to the patient's own data.

**4. Adaptive detection window**
Fixing the detection window at 28 days is wrong for a patient seen every 6 months (too short) and wrong for a patient seen every 2 weeks (too long). ARIA scales the window to `min(90, max(14, days_between_visits))` — tailored to each patient's care rhythm.

**5. Drug-class-aware titration suppression**
Flagging therapeutic inertia when a medication was changed 10 days ago is a false alarm — some drugs need 56 days to show their full effect (amlodipine). Suppression windows are keyed to drug class, not a single arbitrary cutoff.

**6. Absent rows for device outages, never NULLs**
When a patient's device is offline, no reading row is created for that day. NULL-filled rows would corrupt trend calculations and make gap detection unreliable. The gap detector counts days since the last row — absence of data is the signal.

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

Removing ARIA from the workflow leaves the GP exactly where they were before — informed by their EHR, making their own decisions. ARIA only compresses the time it takes to reach that informed state.

**The clinician always decides. ARIA only informs.**

---

## Team — Neura Care Nexus

| Name | Contribution |
|---|---|
| **Krishna Patel** | Frontend lead — dashboard clinical UI redesign, BP sparklines, product tour, search bar, alert disposition, tier override modal |
| **Kush Patel** | Infrastructure lead — database schema, FHIR adapter, ingestion engine, synthetic generators, adaptive window, Pattern B scoring, demo setup |
| **Sahil Khalsa** | Full-stack — API layer, Layer 3 LLM pipeline, chatbot (guardrails + UX + session memory), Patient PWA, alert escalation, CORS security |
| **Nesh Rochwani** | Backend — background worker, scheduler, acknowledged alert history, adaptive patient thresholds, cold-start crash fix |
| **Prakriti Sharma** | Pattern engine — adaptive threshold v5.0, Pattern B suppression, fifth inertia condition, deterioration gates |
| **Yash Sharma** | Layer 2 risk scorer, briefing UI antihypertensive filter, inertia and deterioration detector fixes |

---

<div align="center">

**Illinois Institute of Technology · CS 595 · Spring 2026**

*Neura Care Nexus*

<sub>Python · FastAPI · Next.js · TypeScript · PostgreSQL · FHIR R4 · Anthropic · OpenAI · Tailwind CSS · Recharts</sub>

</div>
