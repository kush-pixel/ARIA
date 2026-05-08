# ARIA — Summer 2026 Development Plan
## Leap of Faith Technologies × Illinois Institute of Technology
## Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma
## Duration: 13 Weeks | Budget: $5,000

---

## What ARIA Is Today

A between-visit clinical intelligence platform for hypertension management. A GP with 1,800 patients has 8 minutes per consultation and no structured view of what happened to their hypertensive patients since the last appointment. ARIA fixes this by:

- Ingesting patient EHR data via FHIR R4 Bundle
- Generating clinically realistic synthetic home BP readings and medication confirmations
- Running three-layer AI analysis — deterministic rules → risk scoring → LLM explanation
- Delivering a structured pre-visit briefing at 7:30 AM on appointment days
- Providing a clinician chatbot for real-time patient data queries
- Collecting home BP readings and medication confirmations via a patient PWA

**Current state:** Four demo patients, 94.3% shadow mode accuracy, full three-layer pipeline operational, patient PWA live, clinician chatbot live.

---

## What ARIA Will Be After 13 Weeks

- Clinically accurate detection covering diastolic BP, heart rate, symptoms, orthostatic hypotension, and masked hypertension — signals the current system ignores entirely
- ML-grade algorithms replacing simple statistical rules: CUSUM, Gaussian Process adaptive thresholds, Isolation Forest anomaly scoring
- A chatbot that cites its sources, persists conversations, and proactively surfaces hypotheses rather than waiting to be asked
- A patient app that guides clinically valid measurements, sends push notifications, and works offline
- One validated wearable device integration eliminating manual transcription
- Real GP validation — briefings reviewed by practising clinicians against real clinical judgement
- Production-grade infrastructure: Alembic migrations, GitHub Actions CI/CD, Celery task queue, real-time SSE dashboard

---

## Week 1 — Critical Fixes (Ships Before Anything Else)

These are bugs and security issues in the current system. Nothing else starts until these are closed.

### 1.1 Wrong Model in Summarizer
`backend/app/services/briefing/summarizer.py` line 34 has `_MODEL_VERSION = "gpt-4o-mini"`. Every Layer 3 summary since deployment has used the wrong model with different guardrail behaviour. The `briefings.model_version` audit column is incorrect in production.

**Fix:** Revert to `claude-sonnet-4-20250514`. Audit all existing `briefing` rows for guardrail violations before closing.

**Effort:** 30 minutes + 4-hour audit.

### 1.2 Missing Guardrails on Clinical Note Endpoint
`POST /api/chat/summary` generates clinical notes using `claude-sonnet-4-20250514` with no call to `validate_llm_output()`. All 15 guardrail checks are bypassed. A clinician could receive a note containing medication dose recommendations or non-compliant language.

**Fix:** Wire `validate_llm_output()` into the summary endpoint. Retry once on failure, return structured error on second failure.

**Effort:** 2 hours.

### 1.3 Credential Rotation + Secrets Management
The `.env` file contains live Supabase credentials, Anthropic API key, Groq API key, and OpenAI API key. If this file has ever been committed to the repository, this is a potential GDPR notifiable breach under UK GDPR Article 33 (72-hour ICO notification window).

**Actions:**
- Rotate every credential in `.env` immediately
- Migrate to Doppler (free tier, 5 projects, unlimited secrets) — inject at runtime via Railway environment
- Add `.env` to `.gitignore`
- Add `git-secrets` pre-commit hook to block future credential commits

**Effort:** 2 hours.

### 1.4 Real Health Check Endpoint
Current `/health` returns `{"status": "ok"}` statically. A database outage is invisible to the load balancer.

```python
@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": True}
    except (OperationalError, DatabaseError):
        return JSONResponse({"status": "degraded", "db": False}, status_code=503)
```

**Effort:** 1 hour.

### 1.5 API Rate Limiting
No rate limiting exists on any endpoint. The chatbot, briefing, and reading submission endpoints are open to unlimited requests per JWT. A single bad actor can exhaust the Anthropic API budget in minutes.

**Fix:** `slowapi` middleware with sliding window limits per JWT:
- `POST /api/chat/message`: 30 requests/minute
- `POST /api/readings`: 20 requests/minute
- `POST /api/briefings/generate`: 10 requests/minute

**Effort:** 4 hours.

---

## Sprint 1 — Infrastructure Hardening (Weeks 2–3)

### 1.1 Alembic Database Migrations

**Problem:** All schema changes are raw `ALTER TABLE ADD COLUMN IF NOT EXISTS` statements in `setup_db.py`. No rollback capability. Breaks if two instances start simultaneously. 17 existing `ADD COLUMN` statements across 6 tables need reconciliation.

**Deliverable:**
- `alembic init migrations/` with `target_metadata = Base.metadata`
- Baseline migration reconciling all 17 existing `ADD COLUMN` statements
- All future schema changes go through `alembic revision` — `setup_db.py` raw SQL retired
- `alembic upgrade head` as first step in CI/CD pipeline

**Effort:** 2 days (baseline reconciliation requires careful testing against real Supabase).

### 1.2 GitHub Actions CI/CD Pipeline

**Problem:** No automated pipeline. Every deploy is manual. Lint failures reach production.

**Deliverable:**
```yaml
on: [push, pull_request]
jobs:
  lint-test:
    - ruff check app/
    - ruff format app/ --check
    - alembic upgrade head        # against CI test DB
    - pytest tests/ -v -m "not integration"
    - pytest tests/ -v -m integration

  security:
    - pip-audit
    - bandit -r app/ -c .bandit
    - trivy fs .                  # secret scan — blocks if .env found

  deploy:
    needs: [lint-test, security]
    if: branch == main
```

CI uses a dedicated Supabase free-tier project. Never touches production.

**Effort:** 1 day.

### 1.3 Celery + Redis Worker

**Problem:** The current 30-second polling loop means:
- Jobs wait up to 30 seconds before pickup
- No priority queue — 7:30 AM briefings compete equally with background recomputes
- Cannot scale horizontally

**Deliverable:**
- Redis via Upstash (free tier sufficient at demo scale)
- Three queues: `critical` (briefing on appointment day), `high` (pattern recompute on new reading), `default` (bulk recomputes)
- `processing_jobs` table retained as audit log — Celery writes status back
- APScheduler replaced with Celery Beat for 7:30 AM scheduler and midnight recompute
- `run_worker.py` updated to start Celery worker instead of polling loop

**Effort:** 2 days.

### 1.4 Sentry Error Tracking

**Problem:** If Layer 3 silently degrades or the pattern engine throws a partial exception, nobody knows until a clinician complains.

**Deliverable:**
- `sentry_sdk.init()` in `main.py` with `traces_sample_rate=0.1`
- Captures all unhandled exceptions in FastAPI, Celery worker, and Layer 3 LLM calls
- Alerts if `briefing_generation` fails between 7:00–9:00 AM
- Sentry free tier: 5,000 errors/month — sufficient

**Effort:** 2 hours.

### 1.5 Real-Time Dashboard — Server-Sent Events

**Problem:** Dashboard polls every 10 seconds via `setInterval`. Clinician sees stale data between polls.

**Deliverable:**
- `GET /api/stream/patients` SSE endpoint (JWT validated via query param since `EventSource` does not support custom headers)
- Server pushes patient list updates when risk scores change or alerts fire
- `PatientList.tsx` migrated from `setInterval` to `EventSource`
- Server-side connection cleanup on disconnect via asyncio cancellation

**Effort:** 1 day.

### 1.6 Integration Test Suite

**Problem:** Current tests mock the database. Clinical accuracy regressions are only caught by manual shadow mode.

**Deliverable:**
- Dedicated CI Supabase project seeded with 4 demo patients
- Integration tests asserting:
  - Inertia fires for Patient A (1091)
  - Gap alert fires at day 9 for Patient C (DEMO_GAP)
  - Layer 3 passes all 15 validation checks
  - No guardrail strings in `readable_summary`
- Playwright E2E: login → dashboard → patient detail → briefing renders → alert acknowledge → audit event created

**Effort:** 3 days.

---

## Sprint 2 — Detection Engine (Weeks 4–6)

Every detector currently only examines systolic BP. Sprint 2 fixes this across the board.

### 2.1 Diastolic BP Across All Detectors

Diastolic hypertension is an independent cardiovascular risk factor, especially in patients under 50. A patient presenting 128/95 mmHg triggers zero alerts in the current system.

**Changes:**

| Detector | Change |
|---|---|
| Inertia | Fire if `diastolic_avg >= 90` sustained, even when systolic is within range |
| Deterioration | Diastolic step-change sub-detector: ≥10 mmHg jump over 3 weeks |
| Variability | Separate ARV calculation for diastolic — elevated diastolic variability indicates arterial stiffness |
| Risk scorer | Add `_DIASTOLIC_WEIGHT = 0.05`, reduce systolic weight from 0.30 to 0.20 |

**Effort:** 2 days.

### 2.2 Heart Rate Analysis

`heart_rate_avg` is stored in every reading. No detector reads it.

**New signals:**

| Signal | Logic | Output |
|---|---|---|
| Beta-blocker underdose | Patient on beta-blocker AND resting HR consistently > 85 bpm | Supporting evidence for inertia |
| Resting tachycardia | HR > 100 on 3+ readings in 7 days | "Resting tachycardia noted — consider ECG" |
| AF screening | `irregular_pulse = TRUE` on 2+ sessions within 14 days | "Possible atrial fibrillation — 12-lead ECG recommended" (NICE NG196) |

**Schema addition:** `readings.irregular_pulse BOOLEAN` — patient reports whether device showed irregular heartbeat indicator.

**Effort:** 2 days.

### 2.3 Symptom Burden Detector

The `symptoms` array (`headache|dizziness|chest_pain|shortness_of_breath`) is collected at reading time and never analysed.

**New deterministic rules:**
- `chest_pain` or `shortness_of_breath` with systolic ≥ 160 → urgent flag in visit agenda regardless of tier
- `dizziness` clustering within 2 days of a BP dip → orthostatic hypotension signal
- Symptom frequency: "patient reported headache on 6 of last 14 readings" → surfaced in `adherence_summary`
- Suppress inertia "sustained elevated" language if patient is reporting dizziness (may already be symptomatic)

**Effort:** 1 day.

### 2.4 `medication_taken` Field

When a patient submits a reading they report whether they took their medication (`yes|no|partial|null`). The adherence analyser ignores this entirely.

**Changes:**

| Detector | Change |
|---|---|
| Adherence | `medication_taken=no` or `partial` correlating with elevated readings weighted more strongly than missed confirmation |
| Inertia | Consistent `medication_taken=yes` on elevated readings rules out adherence as confound — strengthens inertia conclusion |
| Deterioration | `medication_taken=no` preceding a BP spike → adherence-driven deterioration, distinct from treatment failure |

**Effort:** 1 day.

### 2.5 Orthostatic Hypotension Detector

`bp_position` (`seated|standing`) exists but no detector compares across positions. A drop ≥20 mmHg systolic on standing affects ~20% of elderly hypertensive patients and is a leading cause of falls.

**Logic:** When seated and standing readings exist in the same session, compute delta. If sustained across 3+ sessions: "Possible orthostatic hypotension — review antihypertensive dosing and timing."

**Comorbidity amplification:** Escalate concern when diabetes (E11), alpha-blocker, diuretic, or ACE inhibitor present.

**Patient app dependency:** Guided reading flow (Sprint 5) adds "Take standing reading" option. Without this the detector fires rarely.

**Effort:** 1 day.

### 2.6 White-Coat vs Masked Hypertension Classifier

ARIA uses the clinic vs home comparison only to exclude pre-appointment readings from inertia. The comparison should drive a formal classification stored on each patient.

| | Home High (≥135 mmHg) | Home Normal (<135 mmHg) |
|---|---|---|
| **Clinic High (≥140 mmHg)** | Sustained hypertension | White-coat (possibly overtreated) |
| **Clinic Normal (<140 mmHg)** | **Masked hypertension — high stroke risk** | Controlled |

Masked hypertension is the most dangerous quadrant — clinic BP looks fine so the clinician does not act, but home BP is elevated. ARIA has all the data to detect this. No other system in the standard GP workflow does.

**Schema addition:** `clinical_context.bp_classification TEXT` (values: `sustained|white_coat|masked|controlled|insufficient_data`).

**Thresholds:** Home uses NICE NG136 home target `135/85 mmHg`, not clinic target `140/90`. Using the wrong threshold misclassifies the masked hypertension quadrant by NICE definition.

**Effort:** 1 day.

### 2.7 ARV Replacing CV in Variability Detector

**Problem:** Coefficient of Variation is statistically flawed for BP. It conflates magnitude with sequential variability.

**Replacement:** Average Real Variability:
```
ARV = mean(|SBP[i] - SBP[i-1]|)  for consecutive readings ≤2 days apart
```
ARV > 10 mmHg is the validated clinical threshold (Rothwell et al., Lancet 2010).

**Gap handling:** Pairs separated by 3+ days (device outage) excluded from ARV — avoids inflating variability due to normal between-outage drift.

**Risk scorer:** Add `_VARIABILITY_WEIGHT = 0.10`, reduce other weights proportionally. Import `variability_detector` in `risk_scorer.py`.

**Effort:** 1 day.

### 2.8 Detector Audit Trail

Every detector run must log not just the result but the evidence used.

```json
{
  "algorithm": "cusum",
  "cusum_value": 23.4,
  "control_limit": 20.0,
  "patient_threshold": 151.2,
  "readings_evaluated": 34,
  "window_days": 42,
  "white_coat_excluded": 3,
  "comorbidity_adjustment": -7,
  "baseline_source": "historic_mean_cap_145"
}
```

Stored in `audit_events.details`. Required for clinical governance review.

**Effort:** 1 day.

---

## Sprint 3 — ML Algorithms (Weeks 7–8)

### 3.1 Gaussian Process Regression — Personalised Threshold

**Replaces:** Hardcoded `max(130, mean + 1.5×SD)` cap at 145 mmHg.

**Problem:** A patient who has always run at 165 mmHg gets a threshold of 145 — below their actual stable baseline — causing constant false-positive flags.

**Implementation:** `scikit-learn GaussianProcessRegressor` fitted on `historic_bp_systolic`. Posterior mean becomes the patient's personalised baseline. Threshold = `posterior_mean + 2×posterior_SD`.

**Cold-start:** Patients with fewer than 14 clinic readings use existing `max(130, mean + 1.5×SD)` fallback. `baseline_source` audit field distinguishes the two paths.

**Effort:** 3 days.

### 3.2 CUSUM — Replaces Linear Slope

**Replaces:** Simple linear regression slope in inertia and deterioration detectors.

```
S_high[t] = max(0, S_high[t-1] + (reading - (baseline + k)))
Alert when S_high[t] >= h
```

Where `k` = 5 mmHg (minimum shift to detect), `h` = 4×patient SD. Applied to both systolic and diastolic.

More sensitive than rolling averages. Naturally handles variable reading frequency and gaps. Detects sustained shifts rather than noisy individual readings.

**Effort:** 2 days.

### 3.3 Isolation Forest — Anomaly Scoring

Per-patient anomaly scoring on each new reading. Score stored as `readings.anomaly_score NUMERIC(3,2)`.

Anomalous readings are **flagged in the briefing but never silently excluded** — a 198 mmHg reading scoring high on Isolation Forest may be a genuine hypertensive urgency. The briefing surfaces it: "One high-anomaly reading (198 mmHg, April 23) included in trend — review for measurement error or clinical event."

**Cold-start:** Fewer than 30 readings → simple `|reading − patient_mean| > 3×SD` rule.

**Effort:** 1 day.

### 3.4 Unified Risk Scorer Weight Update

Incorporating diastolic (Sprint 2.1), variability (Sprint 2.7), and GP regression threshold (Sprint 3.1) requires a single coherent weight redistribution:

| Component | Current | Updated |
|---|---|---|
| Systolic vs baseline | 30% | 20% |
| Diastolic vs threshold | 0% | 5% |
| Days since med change | 25% | 20% |
| Adherence rate | 20% | 20% |
| Gap duration | 15% | 15% |
| Comorbidity severity | 10% | 10% |
| Variability score | 0% | 10% |

**Effort:** 4 hours (update constants in `risk_scorer.py`).

---

## Sprint 4 — Chatbot & Layer 3 (Weeks 9–11)

### 4.1 Persistent Conversation History

**Problem:** Conversation history stored in a process-level Python dict. Lost on server restart. Cannot run multiple workers.

**New table:**
```sql
CREATE TABLE chat_sessions (
    session_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    clinician_id TEXT NOT NULL,
    messages JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT now(),
    last_message_at TIMESTAMPTZ DEFAULT now(),
    turn_count SMALLINT DEFAULT 0
);
```

Conversation survives navigation, page refresh, and server restarts. Every message is audited.

**Effort:** 1 day.

### 4.2 Evidence Cards — Citation-Grounded Responses

Every factual claim the chatbot makes must show its source data. The agent returns a structured `citations` array alongside text. Frontend renders each citation as a collapsible card.

```
"The 28-day average systolic is 163 mmHg."
  → [Source: 47 readings, April 9 – May 7, 2026]

"No medication change since October 2013."
  → [Source: medication history — last entry: Amlodipine 5mg, 2013-10-03]
```

This is the difference between a chatbot that provides comfort and one that provides clinical confidence.

**Effort:** 2 days.

### 4.3 Expanded Tool Set

Six new tools added to the chatbot agent:

| Tool | Clinical Use Case |
|---|---|
| `get_symptom_timeline` | "Did headaches correlate with the April spike?" |
| `get_medication_response` | "Did the amlodipine dose change in March actually work?" |
| `compare_periods` | "Was February better controlled than March?" |
| `get_risk_score_breakdown` | "Why is his risk score 78?" |
| `get_reading_detail` | "What exactly was recorded on April 12?" |
| `get_circadian_pattern` | "Is there a morning surge pattern?" |

**Effort:** 3 days.

### 4.4 Proactive Hypothesis Surfacing

After the briefing loads, the chatbot initiates rather than waiting:

> "I noticed the BP elevation on April 12 coincides with a 3-day medication confirmation gap the previous week. Would you like me to show the correlation?"

Generated from Layer 1 signals — intelligence surfaced as a question, not an assertion. Respects clinical autonomy while making the system proactive.

**Effort:** 2 days.

### 4.5 Clinical Note Generator

Transform `POST /api/chat/summary` (currently unguarded) into a validated clinical note formatted for direct paste into EMIS or SystmOne.

```
ARIA Between-Visit Summary — David Patel — Generated 07/05/2026 09:12

Objective: 47 home BP readings (09/04 – 07/05). 28-day mean 163/94 mmHg.
           Adherence 91% (41/45 confirmations).

Assessment: Sustained BP above target despite current regimen (no medication
            change since October 2013 — possible treatment review warranted).
            Possible adherence concern noted week of 28/04 (4 missed confirmations).

Actions discussed: [clinician completes]

Data limitations: Home readings only — no clinic attendance since last appointment.
ARIA version: 4.3 | Validated: passed (15/15 checks)
```

All 15 guardrail checks before return. Footer uses abstracted version identifier, not raw model name — surfacing `claude-sonnet-4-20250514` in the clinical record creates medico-legal exposure if the model changes.

**Effort:** 2 days.

### 4.6 Prompt Caching

System prompt and patient context snapshot cached with `cache_control: {"type": "ephemeral"}`. Conversation history appended per turn without caching.

Expected 40–70% token cost reduction for consultations with 3+ turns.

**Effort:** 4 hours.

---

## Sprint 5 — Patient App Redesign (Weeks 12–13)

### 5.1 Home Hub

Replaces login → submit as the post-login landing page. Answers the three questions every patient has:

```
┌──────────────────────────────────────────┐
│  Good morning, David                     │
│  Wednesday, 7 May                        │
├──────────────────────────────────────────┤
│  TODAY'S TASKS                           │
│  ✓  Morning medications confirmed        │
│  ○  Morning BP reading         [Take →]  │
├──────────────────────────────────────────┤
│  YOUR STREAK                             │
│  14 days — keep going                    │
├──────────────────────────────────────────┤
│  THIS WEEK                               │
│  Readings:      5 / 7  ████████░░        │
│  Medications:   6 / 7  █████████░        │
├──────────────────────────────────────────┤
│  NEXT APPOINTMENT                        │
│  Wednesday, 14 May — 7 days away         │
└──────────────────────────────────────────┘
```

**Effort:** 2 days.

### 5.2 Guided BP Measurement — Most Clinically Important Change

Current app presents all fields simultaneously with no measurement guidance. NICE NG136 requires 5 minutes of quiet sitting, two readings 1 minute apart, same arm, before morning medications.

**Five-step guided flow:**

**Step 1 — Preparation:** `medication_taken` (yes/not yet/partial) captured here — before the reading, when clinically relevant. Illustrated sitting instructions.

**Step 2 — First reading:** Systolic, diastolic, heart rate. Optional: irregular heartbeat indicator checkbox.

**Step 3 — 1-minute countdown:** Large visible timer. "Stay seated and relaxed."

**Step 4 — Second reading:** Same fields.

**Step 5 — Symptoms:** Checkboxes. Chest pain / shortness of breath triggers safety banner immediately. Does not disable submit.

The 1-minute countdown is the most clinically important step — it makes the two readings valid under NICE guidelines and ensures the data driving clinical decisions is accurate.

**Effort:** 2 days.

### 5.3 Biometric Login + Refresh Tokens

**Problem:** JWT expires after 8 hours. Patients must re-login for both morning and evening readings. Research ID retyped every login.

**Redesign:**
- Research ID entered once on first login
- Patient creates a 6-digit PIN
- Face ID / Touch ID via Web Authentication API (iOS Safari 16.4+ as PWA, Android Chrome any version)
- 30-day refresh token alongside 8-hour access token — silently auto-refreshed
- JWT access token moved from `localStorage` to `sessionStorage`
- Auto-lock after 10 minutes of inactivity
- Sign Out button in Settings (currently absent from the entire app)

**Effort:** 3 days (WebAuthn requires backend public key storage + challenge-response in `auth.py`).

### 5.4 Push Notifications

`.ics` download requires: download file → locate file → tap file → import to calendar. Many elderly patients fail at step 3. Web Push API is the correct primary reminder mechanism.

**Notification types:**
- Medication time: "Time for your morning medications — tap to confirm"
- BP reminder: "Don't forget your morning reading" (sent at 9:30 AM if no reading submitted)
- Appointment reminder: "Your appointment is in 3 days — keep up your readings"
- Milestone: "You've hit a 30-day streak!"

**Never send:** clinical values, risk scores, alert content, BP readings.

**Consent:** Explicit push notification consent before subscribing. Consent timestamp stored alongside subscription endpoint.

**Effort:** 2 days.

### 5.5 Missed Dose + Late Confirmation

**Problem:** Past doses silently disappear at midnight. A patient who forgot but took their medication has no recourse.

**Fix:**
- Missed doses visible (greyed), with "I took this late" option up to 4 hours after scheduled time
- Late confirmation records accurate `minutes_from_schedule`
- Weekly adherence bar — number only, no clinical judgment language

**Schema addition:** `medication_confirmations.missed_at TIMESTAMPTZ`

**Effort:** 1 day.

### 5.6 Meaningful Submission Feedback

Current: *"Reading submitted. Your doctor will see this at your next visit."*

New personalised acknowledgment based on context:
- "That's your 47th reading. Your care team will have a detailed picture at your next appointment."
- "You're on a 14-day streak — this consistency really helps your care team."
- "Your appointment is in 7 days. Consistent readings help your doctor prepare."
- If symptoms checked: "Your care team will be aware of your symptoms at your next appointment."

**Never mention:** BP values, whether the reading was high or low, risk scores.

**Effort:** 4 hours.

### 5.7 Offline Support

**What works offline:**
- `/home` page loads from cached state
- `/reading` form works entirely offline — reading saved to IndexedDB queue (`aria_reading_queue` namespace)
- On reconnect: IndexedDB queue flushed to backend via Background Sync API
- Offline indicator banner: "You're offline — your data will sync when you reconnect."

**Effort:** 2 days.

---

## Sprint 6 — Wearable Integration + Clinical Validation (Week 13)

### 6.1 Withings BPM Connect Integration

One device, done properly. Withings is the most common clinically-validated home BP monitor.

**Architecture:**
```
Patient authorises Withings (OAuth2 in patient PWA)
        ↓
wearable_enrollments row created
        ↓
Celery job: wearable_sync (polls Withings API every 15 minutes or receives webhook)
        ↓
Readings inserted: source="wearable_api", device_type="withings_bpm_connect"
        ↓
Existing UNIQUE idx on (patient_id, effective_datetime, source) prevents duplication
        ↓
Existing ingestion pipeline handles the rest — same detectors, same audit trail
```

**New schema:**
```sql
CREATE TABLE wearable_enrollments (
    enrollment_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    device_type TEXT NOT NULL,
    device_id TEXT,
    oauth_access_token TEXT,     -- encrypted at application layer
    oauth_refresh_token TEXT,    -- encrypted at application layer
    oauth_expires_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ,
    sync_status TEXT DEFAULT 'active',
    enrolled_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(patient_id, device_type, device_id)
);
```

OAuth tokens encrypted at application layer — plain TEXT column provides no protection.

**Effort:** 3 days.

### 6.2 BP Reading PDF Export

Patients frequently want to share their BP history with family or bring a printed summary to an appointment. One endpoint, one PDF template.

`GET /api/patients/{patient_id}/readings/export` returns a PDF with:
- 28-day reading table (date, systolic, diastolic, session, medication taken)
- Simple sparkline chart
- Patient name, date generated, "Data collected via ARIA home monitoring"
- No risk scores, no tier, no clinical flags — patient-facing only

**Library:** `reportlab` or `weasyprint`

**Effort:** 1 day.

### 6.3 FHIR R4 Validation

The iEMR → FHIR adapter is custom-built and validated only against synthetic data. Running it against the public HAPI FHIR test server verifies R4 conformance and identifies any mapping errors before real EMR integration.

**Actions:**
- Submit generated bundles to `https://hapi.fhir.org/baseR4` (free public test server)
- Fix any validation failures returned
- Add FHIR validation step to CI pipeline

**Effort:** 1 day.

### 6.4 Real GP Validation Study

The most important deliverable of the summer — and the one not in the original IMPROVEMENTS.md. Getting practising clinicians to review ARIA briefings against real clinical judgement transforms this from a demo into validated clinical software.

**Target:** 2–3 GP practices, 10–20 patients each, 4-week observation period.

**Protocol:**
- GP reviews ARIA briefing before consultation
- GP records: did they agree with each flag? Was information useful? What would they have missed without it?
- Compare ARIA flags against GP clinical decision (did they change medication? order a test?)
- Output: precision and recall against real GP clinical judgement (extends the current 94.3% shadow mode result to real-world validation)

**What this produces for LOF:** Real numbers. "ARIA briefings agreed with GP clinical assessment in X% of cases across Y patients." This is fundable and publishable.

**Effort:** 2 weeks of data collection running in parallel with other sprints. Protocol design and GP outreach: 3 days.

---

## New Features Not in Original IMPROVEMENTS.md

### Webhook System for EMR Push

ARIA currently only does batch FHIR ingestion — the GP manually triggers it. A webhook endpoint lets EMR systems push updates when a new medication is prescribed, a problem is added, or a visit is recorded.

`POST /api/webhooks/fhir` — receives a FHIR Bundle, validates it, enqueues a `bundle_import` job.

Different from bidirectional write-back (Section 8.8 of IMPROVEMENTS.md) — this is only receiving data, not writing back. No clinical governance issues.

**Effort:** 1 day.

### Medication Response Tracker

For every medication change in `med_history`, ARIA knows the date but never evaluates whether it worked.

**For every medication change, track:**
- Pre-change 14-day average vs post-change 14-day average (respecting drug-class titration window)
- Response classification: `good_response` (>10 mmHg drop) | `partial_response` (5–10 mmHg) | `no_response` (<5 mmHg) | `worsening`

**Briefing output:** "BP did not respond to the medication change from March 15. Consider dose review."

**Effort:** 2 days.

### Lab Result Clinical Rules

`recent_labs` JSONB is stored but no detector reads it. Five deterministic rules requiring no LLM:

| Lab finding | Clinical rule |
|---|---|
| K+ < 3.5 mEq/L on diuretic | "Hypokalaemia confirmed — review diuretic dose" |
| Creatinine rising >20% in 3 months on ACE/ARB | "Possible ACEi-induced AKI — consider dose review" |
| HbA1c > 75 mmol/mol in diabetic patient | Elevate risk score, add to visit agenda |
| eGFR < 30 | Flag all nephrotoxic medications, elevate risk tier |
| Sodium < 130 mEq/L on thiazide | "Possible hyponatraemia — urgent review" |

**Effort:** 2 days.

---

## 13-Week Timeline

| Week | Focus | Key Deliverables |
|---|---|---|
| 1 | Critical fixes | Wrong model fixed, guardrails on chat/summary, credentials rotated, rate limiting |
| 2 | Infrastructure | Alembic migrations, GitHub Actions pipeline, Sentry |
| 3 | Infrastructure | Celery + Redis worker, SSE dashboard, integration test suite |
| 4 | Detection engine | Diastolic BP across all detectors, heart rate analysis |
| 5 | Detection engine | Symptom detector, `medication_taken` field, orthostatic hypotension |
| 6 | Detection engine | Masked HTN classifier, ARV variability, detector audit trail |
| 7 | ML algorithms | Gaussian Process adaptive threshold, CUSUM |
| 8 | ML algorithms | Isolation Forest, risk scorer weight update, medication response tracker |
| 9 | Chatbot | Persistent history, evidence cards, lab result rules |
| 10 | Chatbot | 6 new tools, proactive hypothesis surfacing |
| 11 | Chatbot | Clinical note generator, prompt caching, FHIR validation |
| 12 | Patient app | Home hub, guided BP measurement, offline support |
| 13 | Patient app + wearable | Biometric login, push notifications, Withings integration, PDF export |

**GP validation study runs in parallel across Weeks 6–13.**

---

## Team Roles

| Member | Primary Responsibility |
|---|---|
| **Kush Patel** | Infrastructure (Alembic, CI/CD, Celery), ML algorithms (CUSUM, GP regression, Isolation Forest) |
| **Sahil Khalsa** | Chatbot (evidence cards, new tools, clinical notes, prompt caching), API layer, wearable integration |
| **Nesh Rochwani** | Detection engine (diastolic, heart rate, symptom detector, masked HTN classifier), integration tests |
| **Krishna Patel** | Patient app redesign (home hub, guided flow, push notifications, offline), GP validation study coordination |
| **Prakriti Sharma** | Detection engine (orthostatic hypotension, ARV, medication_taken signal), lab result rules |
| **Yash Sharma** | Risk scorer updates, medication response tracker, FHIR validation, PDF export |

---

## Infrastructure & Budget

### Category 1 — Infrastructure (Paid Tiers, 3 Months)

Free tiers are used where scale genuinely permits. Paid tiers are used where reliability or limits matter for a production-grade system being reviewed by real clinicians.

| Service | Tier | Why Paid | Monthly | 3-Month |
|---|---|---|---|---|
| Supabase Pro (production) | Pro | PITR backup, no pausing risk, 8GB storage for growing reading history | $25 | $75 |
| Supabase Pro (CI/test) | Pro | Dedicated test project, isolated from production, full migration testing | $25 | $75 |
| Railway Pro (backend + worker) | Pro | Dedicated resources, no cold starts, SLA uptime — required for 7:30 AM scheduler reliability | $20 | $60 |
| Redis — Upstash Pay-as-you-go | Paid | 50k commands/day for Celery queue under real GP validation load | $10 | $30 |
| Vercel Pro (frontend + patient PWA) | Pro | Advanced analytics, 1TB bandwidth, password-protected preview deployments for GP review | $20 | $60 |
| GitHub Team (6 members) | Team | Unlimited Actions minutes — free tier (2,000 min/month) exhausted in week 1 with full CI | $24 | $72 |
| Sentry Team | Team | 50k errors/month, alerting rules, 90-day retention — free tier (5k) too low for GP validation period | $26 | $78 |
| Doppler Team | Team | Secret audit logs, access control per member, required for GDPR compliance demonstration | $10 | $30 |
| Cloudflare Pro | Pro | WAF rules, advanced DDoS protection, analytics — production-grade for clinician-facing deployment | $20 | $60 |
| Resend (email alerts) | Paid | 50k emails/month — free tier (3k) insufficient once GP practices are onboarded | $20 | $60 |
| Domain (.com, 1 year) | — | Production URL for GP validation study | — | $12 |
| **Infrastructure total** | | | | **$612** |

---

### Category 2 — AI / API (3 Months)

| Service | Usage | Cost |
|---|---|---|
| Anthropic Claude — Layer 3 briefings | ~5,000 calls: daily briefings for demo patients + GP validation study patients (20 patients × 90 days) | $150 |
| Anthropic Claude — Chatbot (Sprint 4 dev + production) | ~15,000 conversation turns across development iteration, GP reviewer sessions, and validation testing | $250 |
| Groq API — Paid tier | Production chatbot inference at GP validation scale — free tier rate limits too restrictive for concurrent GP sessions | $100 |
| **AI / API total** | | **$500** |

---

### Category 3 — Hardware Device Lab

Real hardware is non-negotiable for wearable integration. API behaviour between manufacturers differs in OAuth2 token refresh, webhook payload format, and reading schema — these differences cannot be mocked.

| Item | Qty | Unit Price | Total | Purpose |
|---|---|---|---|---|
| Withings BPM Connect Pro | 3 | $100 | $300 | Primary wearable — 3 units allows concurrent patient testing + 1 spare |
| Omron Connect (EVOLV) | 2 | $80 | $160 | Cross-device validation — Omron webhook format differs from Withings |
| Apple Watch Series 9 | 1 | $399 | $399 | HealthKit API testing — AF detection (FDA-cleared ECG), SpO2, HR stream |
| Samsung Galaxy Watch 6 | 1 | $250 | $250 | Android wearable platform — Samsung Health Connect API differs from HealthKit |
| iPad Air (10.9") | 1 | $599 | $599 | Clinician dashboard tablet testing — GPs use tablets for home visits and ward rounds |
| Android test phone (Samsung A-series) | 1 | $120 | $120 | Patient PWA testing — PWA behaviour on Android Chrome differs significantly from iOS Safari |
| USB charging hub + cable set | 1 | $50 | $50 | Device lab management |
| **Hardware total** | | | **$1,878** | |

---

### Category 4 — ML & Cloud Compute

The ML algorithms in scope (Gaussian Process regression, CUSUM parameter tuning, Isolation Forest batch training) are CPU-based and run on the Railway server. Cloud compute is required for:

| Item | Service | Usage | Cost |
|---|---|---|---|
| GP regression training runs | AWS EC2 t3.medium ($0.0416/hr) | 200 training runs × 30 min = 100 hours | $42 |
| Isolation Forest batch training | Same EC2 instance | Runs alongside GP regression | $0 additional |
| Model artifact storage | AWS S3 Standard | Trained model files, versioned per patient cohort | $15 |
| SageMaker Experiments (model tracking) | AWS SageMaker | Track hyperparameters, metrics, model versions across training runs | $80 |
| Cloud GPU for CUSUM parameter sweep | AWS EC2 g4dn.xlarge ($0.526/hr) | 40-hour parameter sweep to calibrate k and h values against real BP data | $21 |
| **ML compute total** | | | **$158** |

---

### Category 5 — Clinical Validation Study

The GP validation study is the most fundable deliverable. Running it properly requires compensating participants for their time.

| Item | Cost | Detail |
|---|---|---|
| GP participant compensation | $500 | 5 GPs × $100 per GP — standard rate for structured clinical feedback sessions (2 hours each) |
| Practice manager coordination | $100 | Admin time for scheduling, consent forms, data governance paperwork at each practice |
| Patient consent materials (printing, postage) | $50 | Informed consent forms for patients enrolled in the validation cohort |
| Ethics consultation (1 hour, university IRB advisor) | $150 | Initial review of study protocol before any real patient data is handled |
| **Clinical validation total** | | **$800** |

---

### Category 6 — Professional Tools & Development

| Item | Cost | Detail |
|---|---|---|
| Figma Professional ($15/month × 3) | $45 | Patient app redesign — branching, advanced prototyping, dev handoff |
| Linear (project management, $8/seat × 6 × 3) | $144 | Sprint tracking, issue management, roadmap — replaces ad-hoc GitHub issues |
| Clinical AI & Informatics course (Coursera) | $99 | Structured clinical informatics training for non-clinical team members — strengthens GP study credibility |
| FHIR R4 implementation guide (HL7 print edition) | $60 | Reference for adapter correctness during FHIR validation sprint |
| Postman Pro (API documentation + team sharing) | $24 | Shared API workspace for GP integration documentation |
| **Tools & development total** | | **$372** |

---

### Category 7 — Security

| Item | Cost | Detail |
|---|---|---|
| Automated penetration test (OWASP ZAP + manual review, 2 hours) | $200 | Basic security audit of patient-facing endpoints before GP validation begins — required to demonstrate data safety to GP practices |
| **Security total** | | **$200** |

---

### Category 8 — Conference & Dissemination

| Item | Cost | Detail |
|---|---|---|
| AMIA Annual Symposium student registration (1 member) | $280 | American Medical Informatics Association — primary venue for clinical AI research. Student rate. Present GP validation findings. |
| **Conference total** | | **$280** |

---

### Budget Summary

| Category | Amount |
|---|---|
| Infrastructure (paid tiers, 3 months) | $612 |
| AI / API | $500 |
| Hardware device lab | $1,878 |
| ML & cloud compute | $158 |
| Clinical validation study | $800 |
| Professional tools & development | $372 |
| Security audit | $200 |
| Conference & dissemination | $280 |
| **Total** | **$4,800** |
| **Buffer (held in reserve)** | **$200** |
| **Total from LOF funding** | **$5,000** |

---

### Where the Money Actually Goes

| Purpose | Amount | % of budget |
|---|---|---|
| Hardware (device lab) | $1,878 | 38% |
| Infrastructure (3 months, production-grade) | $612 | 12% |
| Clinical validation (GP study) | $800 | 16% |
| AI / API | $500 | 10% |
| Tools & professional development | $372 | 7% |
| ML compute | $158 | 3% |
| Security | $200 | 4% |
| Conference | $280 | 6% |
| Buffer | $200 | 4% |

The hardware investment (38%) is the largest single cost and the most defensible — it is the only category that cannot be substituted with developer time. Every other category can be reduced in scope; wearable device testing cannot be done without the physical hardware.

---

## What This Delivers for Leap of Faith

At the end of 13 weeks, ARIA will be:

1. **Clinically validated** — real GP feedback, not just shadow mode accuracy against historical flags
2. **Detection-complete** — diastolic, heart rate, symptoms, masked hypertension, orthostatic hypotension — signals no competitor system surfaces in a standard GP workflow
3. **ML-grade** — CUSUM and Gaussian Process replace statistical heuristics; Isolation Forest adds per-patient anomaly detection
4. **Production-ready infrastructure** — Alembic, CI/CD, Celery, SSE, proper secrets management
5. **Patient app clinically valid** — guided measurements meeting NICE NG136 protocol, push notifications, offline support
6. **Wearable-connected** — Withings BPM Connect readings flow automatically into the pipeline without manual transcription

The system will be demonstrable to a real GP practice with real patients, not just a four-patient demo.

---

*Document prepared: May 2026*
*Team: Leap of Faith Technologies | IIT CS 595 Spring 2026*
*For submission to: John Trzesniak, Leap of Faith Technologies*
