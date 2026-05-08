# ARIA — Summer 2026 Development Plan
## Leap of Faith Technologies × Illinois Institute of Technology
## Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma
## Duration: 16 Weeks | Budget: $5,000

---

## What ARIA Is Today

A between-visit clinical intelligence platform for hypertension management. A PCP with 1,800 patients has 8 minutes per consultation and no structured view of what happened to their hypertensive patients since the last appointment. ARIA fixes this by:

- Ingesting patient EHR data via FHIR R4 Bundle
- Generating clinically realistic synthetic home BP readings and medication confirmations
- Running three-layer AI analysis — deterministic rules → risk scoring → LLM explanation
- Delivering a structured pre-visit briefing at 7:30 AM on appointment days
- Providing a clinician chatbot for real-time patient data queries
- Collecting home BP readings and medication confirmations via a patient PWA

**Current state:** Four demo patients, 94.3% shadow mode accuracy, full three-layer pipeline operational, patient PWA live, clinician chatbot live.

**Regulatory context:** ARIA is built for the US healthcare market. Clinical guidelines follow ACC/AHA 2017. Compliance framework is HIPAA. De-identified patients are used for the summer physician validation study — HIPAA does not apply to de-identified data. Before onboarding real US patients, Supabase must be upgraded to Team plan ($599/month) for HIPAA BAA, and all vendors handling PHI (Railway, Sentry, Anthropic, Doppler) must sign BAAs.

---

## What ARIA Will Be After 16 Weeks

- Clinically accurate detection covering diastolic BP, heart rate, symptoms, orthostatic hypotension, masked hypertension, circadian patterns, and polypharmacy — signals the current system ignores entirely
- Per-comorbidity ACC/AHA-aligned thresholds replacing the flat adjustment across all detectors
- ML-grade algorithms: CUSUM replacing linear slope, Isolation Forest anomaly scoring, CausalImpact medication response assessment
- A chatbot that cites its sources, persists conversations, proactively surfaces hypotheses, and communicates uncertainty explicitly
- A patient app that guides clinically valid measurements, supports OCR input from older monitors, sends push notifications, works offline, and integrates directly with Google and Apple Calendar
- One validated wearable device integration eliminating manual transcription
- Clinician workflow tools: 30-second briefing layout, tablet-responsive dashboard, one-click actions, medication adjustment from dashboard, post-appointment feedback
- Multi-practice infrastructure: Row-Level Security, practice admin role, practice-level analytics
- Real physician validation — briefings reviewed by practising clinicians against real clinical judgement
- Production-grade infrastructure: Alembic migrations, GitHub Actions CI/CD, Celery task queue, real-time SSE dashboard

---

## Week 1 — Critical Fixes (Ships Before Anything Else)

These are bugs, security issues, and an architectural safety gap. Nothing else starts until these are closed.

### 1.1 Wrong Model in Summarizer
`backend/app/services/briefing/summarizer.py` line 34 has `_MODEL_VERSION = "gpt-4o-mini"`. Every Layer 3 summary since deployment has used the wrong model with different guardrail behaviour. The `briefings.model_version` audit column is incorrect in production.

**Fix:** Revert to `claude-sonnet-4-20250514`. Audit all existing `briefing` rows for guardrail violations before closing.

**Effort:** 30 minutes + 4-hour audit.

### 1.2 Missing Guardrails on Clinical Note Endpoint
`POST /api/chat/summary` generates clinical notes using `claude-sonnet-4-20250514` with no call to `validate_llm_output()`. All 15 guardrail checks are bypassed.

**Fix:** Wire `validate_llm_output()` into the summary endpoint. Retry once on failure, return structured error on second failure.

**Effort:** 2 hours.

### 1.3 Credential Rotation + Secrets Management
The `.env` file contains live Supabase credentials, Anthropic API key, Groq API key, and OpenAI API key. If this file has ever been committed to the repository, assess whether a HHS OCR breach notification is required (60-day window).

**Actions:**
- Rotate every credential in `.env` immediately
- Migrate to Doppler (free tier) — inject at runtime via Railway environment
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

### 1.6 Drug Interaction Immediate Alert
Currently `medication_safety.py` only runs when a briefing is generated. If a FHIR re-ingestion adds a new medication that creates a dangerous combination (triple whammy, k-sparing + ACE/ARB etc.), the combination sits in the database undetected until the next briefing — potentially days.

**Fix:** Whenever `clinical_context.current_medications` is updated by any pathway, enqueue a `pattern_recompute` job. The worker re-runs drug interaction detection and fires a `drug_interaction` alert immediately if a new dangerous combination is detected.

**Alert behaviour:**
- New `alert_type = "drug_interaction"` in the alerts table
- Deduplication check — only fire if no unacknowledged drug interaction alert already exists for this patient
- `off_hours = TRUE` if triggered 6PM–8AM or weekend — clinician sees it at start of next working day
- SSE push delivers it to the dashboard in real time (Sprint 1.5 infrastructure)

**Alert card in clinician inbox:**
```
🔴 DRUG INTERACTION — David Patel
   New medication detected: Ibuprofen 400mg (added 14 May, 14:32)
   Triple whammy combination active:
   Ibuprofen + Ramipril + Furosemide → AKI risk
   Severity escalated: CKD (N18) present

   [Acknowledge]  [View full medication list]
```

**Effort:** 1 day.

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
- Server pushes patient list updates when risk scores change, alerts fire, or drug interaction alerts are created
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

All BP thresholds in this sprint follow **ACC/AHA 2017**: clinic target <130/80 mmHg, home target <130/80 mmHg. There is no age-based threshold exception in ACC/AHA — patients aged ≥80 use the same 130/80 target.

### 2.1 Diastolic BP Across All Detectors

Diastolic hypertension is an independent cardiovascular risk factor, especially in patients under 50. A patient presenting 128/95 mmHg triggers zero alerts in the current system.

**Changes:**

| Detector | Change |
|---|---|
| Inertia | Fire if `diastolic_avg >= 80` sustained, even when systolic is within range |
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
| AF screening | `irregular_pulse = TRUE` on 2+ sessions within 14 days | "Possible atrial fibrillation — 12-lead ECG recommended" (ACC/AHA/HRS 2019) |

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

| | Home High (≥130 mmHg) | Home Normal (<130 mmHg) |
|---|---|---|
| **Clinic High (≥130 mmHg)** | Sustained hypertension | White-coat (possibly overtreated) |
| **Clinic Normal (<130 mmHg)** | **Masked hypertension — high stroke risk** | Controlled |

Masked hypertension is the most dangerous quadrant — clinic BP looks fine so the physician does not act, but home BP is elevated. ARIA has all the data to detect this. No other system in the standard PCP workflow does.

**Schema addition:** `clinical_context.bp_classification TEXT` (values: `sustained|white_coat|masked|controlled|insufficient_data`).

**Thresholds:** Both home and clinic use ACC/AHA 2017 target of 130/80 mmHg. Home and clinic thresholds are identical under ACC/AHA — unlike NICE which uses different values for each setting.

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
  "comorbidity_adjustment": -10,
  "baseline_source": "historic_mean_cap_130"
}
```

Stored in `audit_events.details`. Required for clinical governance review.

**Effort:** 1 day.

### 2.9 Contextual Severity Modulation

Replace the flat −7 mmHg comorbidity adjustment in `threshold_utils.py` with per-condition adjustments based on ACC/AHA 2017 and AHA/ACC Heart Failure guidelines. Applied centrally in `threshold_utils.py` — propagates automatically to all four detectors.

| Comorbidity | Threshold adjustment | Gap urgency |
|---|---|---|
| CHF (I50) | −10 mmHg (target <120) | ×2 |
| Stroke / TIA (I61–I64, G45) | −10 mmHg (target <120) | ×1.5 |
| CKD (N18) | −7 mmHg (target <120) | ×1.5 |
| Diabetes (E11) | −5 mmHg (target <130) | Standard |
| None | 0 | Standard |

**Note:** ACC/AHA 2017 has no age-based threshold exception. Remove the NICE over-80 (145 mmHg) constant from `threshold_utils.py` entirely.

**Effort:** 1 day.

### 2.10 Circadian Pattern Analysis — Briefing Output

The chatbot already has `get_circadian_pattern` as a tool. Add it as a formal briefing detector output so physicians see it without needing to query the chatbot.

**New detector outputs:**
- Morning systolic consistently ≥20 mmHg above evening → "Morning surge pattern detected — discuss dosing schedule and timing with patient." Do **not** recommend bedtime dosing — the TIME trial (2022, NEJM, n=21,104) found no significant difference in cardiovascular outcomes between morning and bedtime dosing.
- Evening BP exceeds morning (reverse dipping) → "Possible nocturnal hypertension — consider ambulatory BP monitoring referral"

**Briefing display:** Separate morning and evening sparklines with delta annotated.

**Effort:** 2 days.

---

## Sprint 3 — ML Algorithms (Weeks 7–8)

**Note:** Gaussian Process Regression (personalised threshold) is deferred to post-summer. CUSUM using the existing adaptive threshold baseline is a major improvement on its own. GP regression will be evaluated once the physician validation study produces enough data to validate personalised thresholds.

### 3.1 CUSUM — Replaces Linear Slope

**Replaces:** Simple linear regression slope in inertia and deterioration detectors.

```
S_high[t] = max(0, S_high[t-1] + (reading - (baseline + k)))
Alert when S_high[t] >= h
```

Where `k` = 5 mmHg systolic / 3 mmHg diastolic (minimum shift to detect), `h` = 4×patient SD derived from the same adaptive window used by the detector.

Applied to both systolic and diastolic, consistent with Sprint 2.1. More sensitive than rolling averages. Naturally handles variable reading frequency and gaps.

**Effort:** 2 days.

### 3.2 Isolation Forest — Anomaly Scoring

Per-patient anomaly scoring on each new reading. Score stored as `readings.anomaly_score NUMERIC(3,2)`.

Anomalous readings are **flagged in the briefing but never silently excluded** — a 198 mmHg reading scoring high on Isolation Forest may be a genuine hypertensive urgency. The briefing surfaces it: "One high-anomaly reading (198 mmHg, April 23) included in trend — review for measurement error or clinical event."

**Cold-start:** Fewer than 30 readings → simple `|reading − patient_mean| > 3×SD` rule.

**Effort:** 1 day.

### 3.3 Unified Risk Scorer Weight Update

Incorporating diastolic (Sprint 2.1) and variability (Sprint 2.7) requires a single coherent weight redistribution:

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

### 3.4 CausalImpact — Medication Response Assessment

**New capability:** Objectively evaluates whether a medication change worked.

For every medication change in `med_history`, models the counterfactual BP trajectory — what would BP have been if the medication had not changed? Pre-intervention period trains a Bayesian structural time series model. Post-intervention actuals vs. predicted counterfactual yield a causal effect estimate with a 95% confidence interval.

**Requirements:**
- Minimum 21 days of readings before the medication change to fit the BSTS model
- Drug-class titration window must elapse before analysis runs — reuses existing `TITRATION_WINDOWS` constants (diuretics/beta-blockers 14d, ACE/ARBs 28d, amlodipine 56d, default 42d)
- Only surfaces to physician when posterior probability of meaningful effect (≥5 mmHg) is ≥75%

**Clinical output (when threshold met):**
```
Medication: Amlodipine 5mg → 10mg (2026-03-15)
Posterior mean effect: -8.4 mmHg systolic (95% CI: -13.1 to -3.7)
Probability of causal effect: 92%
Assessment: BP responded to medication change — within expected titration window
```

This answers the physician's most common post-prescription question: "Did it work?"

**Effort:** 2 days.

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

Transform `POST /api/chat/summary` (currently unguarded) into a validated clinical note formatted for direct paste into Epic or athenahealth.

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

### 4.7 Uncertainty Communication

Hard rules for what the chatbot must acknowledge before any trend statement. Overconfident AI during a physician validation study is a direct safety risk.

- Enrolled < 21 days → "I only have N days of data — the trend may not be reliable"
- Fewer than 10 readings in window → flag before any trend statement
- Sparse medication history → "I can only confirm what was recorded in the FHIR bundle"
- `readable_summary = null` (Layer 3 failed) → "I was unable to generate a validated summary — showing raw Layer 1 data instead"

**Effort:** 1 day.

---

## Sprint 5 — Patient App Redesign (Weeks 12–13)

### 5.1 Home Hub

Replaces login → submit as the post-login landing page.

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
├──────────────────────────────────────────┤
│  💡 TODAY'S TIP                          │
│  Reducing sodium by 1 tsp/day can lower  │
│  systolic BP by 5–8 mmHg on average.     │
└──────────────────────────────────────────┘
```

**Effort:** 2 days.

### 5.2 Guided BP Measurement — Most Clinically Important Change

Current app presents all fields simultaneously with no measurement guidance. ACC/AHA requires 5 minutes of quiet sitting, two readings 1 minute apart, same arm, before morning medications.

**Five-step guided flow:**

**Step 1 — Preparation:** `medication_taken` (yes/not yet/partial) captured here — before the reading, when clinically relevant. Illustrated sitting instructions.

**Step 2 — First reading:** Systolic, diastolic, heart rate. Optional: irregular heartbeat indicator checkbox.

**Step 3 — 1-minute countdown:** Large visible timer. "Stay seated and relaxed."

**Step 4 — Second reading:** Same fields.

**Step 5 — Symptoms:** Checkboxes. Chest pain / shortness of breath triggers safety banner immediately. Does not disable submit.

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
- "Your appointment is in 7 days. Consistent readings help your physician prepare."
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

### 5.8 OCR Camera Scan Input

Elderly patients with older non-BLE monitors have no way to avoid manual transcription. Patient points phone camera at the BP monitor screen — Google ML Kit reads the three numbers on-device, offline-capable, free. Reading goes through the same guided flow and 60–250 mmHg range validation before submission. Adds `source="ocr_scan"` to readings.

**Note:** This is distinct from the "Vision AI for medication photos" out-of-scope item — this is OCR of three digits from a screen, not AI identification of medication pills.

**Effort:** 2 days.

### 5.9 Daily Tips (1-Minute Bite-Sized Insights)

Replaces static health education articles. A rotating daily tip displayed on the home hub. Pre-written content, no LLM, offline-capable. Topics: sodium reduction, potassium-rich foods, breathing techniques, reading posture, sleep and BP, medication timing. Slightly personalised by comorbidity — diabetic-hypertension patients see blood sugar guidance.

**Effort:** 1 day.

### 5.10 Food Suggestions

A simple "Foods to Watch" section in the patient app. Two columns: foods to reduce (sodium, processed meats, alcohol, liquorice) and foods to increase (potassium, magnesium, oily fish, calcium). Static content, no AI, no clinical language, no BP values. Appropriate clinical boundary for the patient side.

**Effort:** 4 hours.

### 5.11 Secure Patient Messaging

Patient sends a short message (max 500 characters) to their care team. Not two-way real-time chat. Not AI-mediated. Physician sees it in the alert inbox alongside clinical alerts. Mandatory non-emergency disclaimer before typing — "For urgent concerns, call the practice or dial 911." Full audit trail in `audit_events`.

**New table:**
```sql
CREATE TABLE patient_messages (
    message_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    message_text TEXT NOT NULL,
    sent_at TIMESTAMPTZ DEFAULT now(),
    read_at TIMESTAMPTZ,
    read_by TEXT
);
```

**Effort:** 2 days.

### 5.12 Direct Google + Apple Calendar Integration

Replaces `.ics` download as primary reminder mechanism. Patient connects Google or Apple account once in Settings (OAuth2 for Google, CalDAV for Apple). Medication reminders pushed directly as recurring calendar events. When medication schedule changes, existing events are updated not duplicated. `.ics` download retained as fallback for patients without Google/Apple accounts.

**Schema addition:**
```sql
ALTER TABLE patients ADD COLUMN calendar_integration TEXT; -- google|apple|ics_only|null
ALTER TABLE patients ADD COLUMN calendar_oauth_token TEXT; -- encrypted at application layer
```

**Effort:** 2 days.

### 5.13 Full Engagement and Streaks System

Home hub shows a streak number but the complete system is missing. Engagement and streaks have demonstrated efficacy in improving medication adherence in mHealth research.

**New table:**
```sql
CREATE TABLE patient_engagement (
    patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id),
    reading_streak_current INTEGER DEFAULT 0,
    reading_streak_best INTEGER DEFAULT 0,
    total_readings_submitted INTEGER DEFAULT 0,
    total_doses_confirmed INTEGER DEFAULT 0,
    last_reading_date DATE,
    milestones_achieved TEXT[] DEFAULT '{}'
);
```

**Milestones:** First reading, 7-day streak, 30 readings, 30-day streak, 100 readings. Monthly calendar heatmap on `/progress`. Computed nightly from existing readings and confirmations — no new clinical data exposed to patients.

**Effort:** 2 days.

### 5.14 Accessibility — Section 508 / WCAG 2.1 AA

Required under the Americans with Disabilities Act. Practically essential for the elderly patient demographic in the physician validation study.

**Required changes:**
- System font size respected (Tailwind currently overrides system preferences)
- All interactive elements ≥44×44px touch target
- High contrast mode respecting `prefers-contrast: high` media query
- `+` / `−` stepper buttons alongside all numeric inputs
- `aria-label` on all inputs, descriptive text on all icon buttons
- Specific error messages ("Systolic must be between 60 and 250") not generic ("Invalid input")
- Colour never used as the sole means of conveying information
- **Simplified mode (Settings toggle):** Two large buttons on the home screen — "Take BP Reading" and "Confirm Medications" — for patients with cognitive difficulties

**Effort:** 3 days.

---

## Sprint 6 — Wearable Integration + Clinical Validation (Week 14)

### 6.1 Withings BPM Connect Integration

One device, done properly. Withings is a widely used clinically-validated home BP monitor.

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

### 6.2 Polypharmacy and Medication Burden Flag

If a patient is on ≥4 antihypertensive drug classes AND adherence <75%, surface as a **separate** visit agenda item — distinct from the adherence alert. Without this flag, the briefing implies the patient needs more monitoring — the exact wrong clinical response when the real problem is medication overload.

**Output:**
```
Visit Agenda:
⚠ Medication Burden — 4 antihypertensive drug classes
  Adherence: 58% (last 28 days)
  High medication burden may be contributing to adherence 
  difficulty — consider simplification review
```

**Logic:** Match `clinical_context.current_medications` against a drug class map, count distinct antihypertensive classes, check adherence rate. Deterministic — no ML, no LLM.

**Effort:** 1 day.

### 6.3 `medication_safety.py` Re-Trigger

Whenever `clinical_context.current_medications` is updated by any pathway (FHIR re-ingestion, wearable sync, clinician-recorded adjustment from Sprint 6.4), enqueue a `pattern_recompute` job. The worker re-runs drug interaction detection as part of the recompute — not only at briefing generation time.

This is the architectural fix that makes the Week 1.6 drug interaction alert reliable across all pathways.

**Effort:** 1 day.

### 6.4 Medication Adjustment from Dashboard

Clinician records an intended medication change directly from the dashboard without leaving ARIA. ARIA is an intent record — the clinician still prescribes in Epic. A persistent banner flags the discrepancy until FHIR re-ingestion confirms the change exists in the EMR.

**Clinician form:**
```
Medication Adjustment — David Patel

Current regimen:
  Ramipril 5mg      [Change dose ▾]  [Discontinue]
  Furosemide 40mg   [Change dose ▾]  [Discontinue]
  Amlodipine 5mg    [Change dose ▾]  [Discontinue]

  [+ Add medication]

Reason (required): ________________________

[ Cancel ]  [ Save ]
```

**On Save:**
1. `pending_med_changes` JSONB updated in `clinical_context`
2. `medication_safety.py` re-runs — fires drug interaction alert if new combination is dangerous
3. `days_since_med_change` resets — inertia detector window restarts from today
4. Titration window starts — adherence Pattern B suppression activates
5. Audit event written: `action="medication_adjustment_recorded"`, actor, old dose, new dose, reason
6. Persistent banner on all screens for this patient:

```
⚠ Medication change recorded in ARIA — 14 May — Dr. Smith
  Ramipril 5mg → 10mg
  Confirm in Epic to complete the prescription.
```

Banner persists until the next FHIR re-ingestion confirms the change in Epic/Cerner.

**Schema addition:**
```sql
ALTER TABLE clinical_context
ADD COLUMN pending_med_changes JSONB DEFAULT '[]';
-- [{medication, old_dose, new_dose, action, reason, recorded_by, recorded_at}]
```

**Note:** Option B (FHIR write-back — pushing a MedicationRequest directly to Epic) requires SMART on FHIR write permissions and Epic App Orchard approval. That is post-summer.

**Effort:** 4 days.

### 6.5 Physician Validation Study

The most important deliverable of the summer. Getting practising physicians to review ARIA briefings against real clinical judgement transforms this from a demo into validated clinical software. De-identified patients are used throughout — no HIPAA obligations apply to the study data.

**Target:** 2–3 primary care practices, 10–20 patients each, 4-week observation period.

**Protocol:**
- Physician reviews ARIA briefing before consultation
- Physician records: did they agree with each flag? Was information useful? What would they have missed without it?
- Post-appointment feedback loop (Sprint 7.6) captures: did they change medication? order a test? make a referral?
- Output: precision and recall against real physician clinical judgement

**What this produces:** Real numbers. "ARIA briefings agreed with physician clinical assessment in X% of cases across Y patients." This is fundable and publishable.

**Effort:** 2 weeks of data collection running in parallel with other sprints. Protocol design and physician outreach: 3 days.

---

## Sprint 7 — Clinician Workflow (Week 15)

None of these items exist in the current system. They directly determine whether physicians can use ARIA efficiently during the validation study.

### 7.1 30-Second Briefing Rule

Physicians have 8 minutes per consultation. The current briefing layout puts the AI summary at the top. Urgent flags must be first in plain English — AI summary moves below the fold as supporting detail.

**New layout priority:**
```
┌─────────────────────────────────────────────┐
│  DAVID PATEL  ·  High Risk  ·  Score: 78   │
│  Next appointment: TODAY 10:30 AM           │
├─────────────────────────────────────────────┤
│  ⚠  TREATMENT REVIEW WARRANTED              │
│  BP avg 163/94 mmHg · no med change 2013   │
│  ⚠  MONITORING GAP: 12 days (since Apr 24) │
├─────────────────────────────────────────────┤
│  [90-day sparkline]       Adherence: 91%   │
├─────────────────────────────────────────────┤
│  "Why was treatment review flagged?"        │
│  "What happened during the gap?"           │
└─────────────────────────────────────────────┘
```

**Effort:** 2 days.

### 7.2 Mobile-First / Tablet Responsive

The hardware budget includes an iPad ($599) specifically for clinician tablet testing, but the current fixed-column layout breaks at tablet width. PCPs use tablets on rounds and home visits.

**Changes:**
- Responsive breakpoints: briefing and chat panel stack vertically on tablet and phone
- Service worker pre-caches today's appointment briefings at 7:00 AM
- One-tap alert acknowledgment from the patient list

**Effort:** 2 days.

### 7.3 Alert Triage Inbox Redesign

With 40 patients across multiple practices in the validation study, the current inbox requires clicking every alert individually. This turns ARIA into a burden rather than a tool.

**New features:**
- Batch acknowledge: "Mark all inertia alerts as reviewed"
- Urgency sort: `drug_interaction` and `gap_urgent` always first regardless of when they fired
- Snooze: "Remind me at next appointment" — removes from inbox until then
- Filter by type: view only adherence alerts across all patients

**Effort:** 2 days.

### 7.4 One-Click Dashboard Actions

Three buttons on every patient card in the dashboard. Reduces context switching in an 8-minute consultation.

- **Send Message** — opens compose window to send a message to the patient
- **Schedule Call** — pre-fills a callback note or links to practice scheduling system
- **Flag for Review** — dropdown: Dosage review / Lab test needed / Urgent callback

**Note on "Adjust Dosage":** The medication adjustment form (Sprint 6.4) is the full implementation. These one-click buttons are the quick-access shortcuts from the patient list view.

**Effort:** 1 day.

### 7.5 Practice-Level Morning Dashboard

Before the first patient of the day, the lead physician sees:

```
Morning Summary — Wednesday, 7 May 2026

Today's appointments: 12 patients with ARIA monitoring
  ├── 3 with urgent flags (action required today)
  ├── 2 with drug interaction alerts (unacknowledged)
  ├── 5 with briefings ready (review before appointment)
  ├── 2 with stale risk scores (> 26h — recomputing now)
  └── 2 monitoring_active=FALSE (EHR only)

Practice alerts this week:
  ├── 7 gap alerts (3 new)
  ├── 4 inertia flags (1 new)
  ├── 2 adherence concerns (2 new)
  └── 1 drug interaction (1 new)
```

All data already in the database. Requires a new API endpoint and a dashboard component.

**Effort:** 3 days.

### 7.6 Post-Appointment Feedback Loop

Currently a physician acknowledges an alert and ARIA never learns what happened. This is the most important missing loop for the validation study — without it there is no outcome data, no precision/recall against real clinical decisions, and nothing fundable.

**Structured 30-second prompt after each appointment:**
```
After appointment with David Patel:
□ Medication was changed
□ Investigation ordered
□ Referral made
□ No action — ARIA flag was not clinically relevant
□ Patient declined intervention
```

This data feeds: calibration engine, alert quality metrics, and validation study analysis.

**Effort:** 2 days.

---

## Sprint 8 — Product (Week 16)

### 8.1 Multi-Tenancy + Row-Level Security

Required before the physician validation study runs across multiple practices. Without RLS, Practice A can see Practice B's patients.

**Staged migration (safe to run against existing data):**
```sql
-- Stage 1: add as nullable
ALTER TABLE patients ADD COLUMN practice_id TEXT;

-- Stage 2: backfill all existing rows
UPDATE patients SET practice_id = 'default_practice' WHERE practice_id IS NULL;

-- Stage 3: add NOT NULL constraint
ALTER TABLE patients ALTER COLUMN practice_id SET NOT NULL;

-- Stage 4: RLS policy
CREATE POLICY patient_isolation ON patients
    USING (practice_id = current_setting('app.practice_id'));
```

Every JWT includes `practice_id` as a claim. FastAPI middleware sets `SET LOCAL app.practice_id` at the start of each request. No query changes required — RLS enforces isolation at the database level.

**Effort:** 3 days.

### 8.2 Practice Admin Role

Each practice in the validation study needs to manage their own patients and clinicians without system admin access.

**Capabilities:**
- Enrol and discharge patients
- Add and remove clinician accounts for their practice
- View their practice-level analytics
- Export audit logs for compliance review

**Effort:** 2 days.

### 8.3 Practice Analytics Dashboard

Makes the physician validation study results presentable to a funder.

| Metric | Description |
|---|---|
| Panel risk distribution | % of monitored patients in High / Medium / Low tier |
| Briefing read rate | % of appointment briefings read before the appointment |
| Alert response time | Median time from alert delivery to acknowledgment |
| Inertia prevalence | Patients with no medication change in > 180 days |
| Engagement rate | % of patients submitting ≥ 4 readings per week |
| Detector accuracy | Alert disposition breakdown from `alert_feedback` |
| Drug interaction alerts | Count by type, acknowledgment rate |

**Effort:** 3 days.

---

## New Features Not in Original IMPROVEMENTS.md

### Webhook System for EMR Push

ARIA currently only does batch FHIR ingestion — the physician manually triggers it. A webhook endpoint lets EMR systems push updates when a new medication is prescribed, a problem is added, or a visit is recorded.

`POST /api/webhooks/fhir` — receives a FHIR Bundle, validates it, enqueues a `bundle_import` job. Also triggers `medication_safety.py` re-run via the Sprint 6.3 re-trigger mechanism.

This is receiving data only, not writing back to the EMR. No regulatory complications.

**Effort:** 1 day.

### Medication Response Tracker

For every medication change in `med_history`, ARIA knows the date but never evaluates whether it worked. CausalImpact (Sprint 3.4) is the ML version — this is the deterministic version.

**For every medication change, track:**
- Pre-change 14-day average vs post-change 14-day average (respecting drug-class titration window)
- Response classification: `good_response` (>10 mmHg drop) | `partial_response` (5–10 mmHg) | `no_response` (<5 mmHg) | `worsening`

**Briefing output:** "BP did not respond to the medication change from March 15. Consider dose review."

**Effort:** 2 days.

### Lab Result Clinical Rules

`recent_labs` JSONB is stored but no detector reads it. Five deterministic rules requiring no LLM. LOINC code mapping stored as named constants in `threshold_utils.py`.

| Lab finding | Clinical rule |
|---|---|
| K+ < 3.5 mEq/L on diuretic | "Hypokalaemia confirmed — review diuretic dose" |
| Creatinine rising >20% in 3 months on ACE/ARB | "Possible ACEi-induced AKI — consider dose review" |
| HbA1c > 75 mmol/mol in diabetic patient | Elevate risk score, add to visit agenda |
| eGFR < 30 | Flag all nephrotoxic medications, elevate risk tier |
| Sodium < 130 mEq/L on thiazide | "Possible hyponatraemia — urgent review" |

**Effort:** 2 days.

---

## Deferred to Post-Summer

These items were considered and deliberately deferred — not forgotten.

| Item | Reason for deferral |
|---|---|
| Gaussian Process Regression (personalised threshold) | CUSUM alone is a major improvement. GP regression needs physician validation study data to validate personalised thresholds before deploying. |
| FHIR R4 Validation (HAPI test server) | Not blocking the physician study. Schedule after study data confirms adapter correctness. |
| BP Reading PDF Export | Low clinical urgency relative to other additions. |
| BOCPD (Bayesian Online Changepoint Detection) | CUSUM covers the deterioration detection need. BOCPD adds complexity without enough data to tune it. |
| XGBoost Adherence Prediction | Requires 200 patient-episodes minimum before clinical use. Data collection starts during study. |
| HMM Adherence State Detection | Requires 60-day minimum per patient. Start data collection during study. |
| Prophet BP Forecasting | Nice capability, not clinically urgent this summer. |
| SMART on FHIR Medication Write-Back (Option B) | Requires Epic App Orchard approval and SMART write permissions. Multi-month process. |
| NHS Login / Epic MyChart Integration | Regulatory pathway: 3–6 months. Initiate in parallel but do not block on it. |
| Multi-Condition Expansion (CHF, COPD, T2D) | Hypertension detection must be fully validated before expanding to other conditions. |
| Omron / Apple HealthKit / Samsung Galaxy Watch integration | Hardware is in the device lab. One device (Withings) done properly this summer. Others follow. |
| PCE (Pooled Cohort Equations) score | Clinically valuable. Deferred to give physician study time to confirm which inputs are reliably available from FHIR bundles. |

---

## 16-Week Timeline

| Week | Focus | Key Deliverables |
|---|---|---|
| 1 | Critical fixes | Wrong model fixed, guardrails on chat/summary, credentials rotated, rate limiting, drug interaction alert system |
| 2 | Infrastructure | Alembic migrations, GitHub Actions pipeline, Sentry |
| 3 | Infrastructure | Celery + Redis worker, SSE dashboard, integration test suite |
| 4 | Detection engine | Diastolic BP across all detectors, heart rate analysis, contextual severity modulation |
| 5 | Detection engine | Symptom detector, `medication_taken` field, orthostatic hypotension |
| 6 | Detection engine | Masked HTN classifier, ARV variability, detector audit trail, circadian pattern analysis |
| 7 | ML algorithms | CUSUM, CausalImpact medication response |
| 8 | ML algorithms | Isolation Forest, risk scorer weight update, medication response tracker |
| 9 | Chatbot | Persistent history, evidence cards, uncertainty communication |
| 10 | Chatbot | 6 new tools, proactive hypothesis surfacing |
| 11 | Chatbot | Clinical note generator, prompt caching, lab result rules |
| 12 | Patient app | Home hub, guided BP measurement, OCR scan, daily tips, food suggestions |
| 13 | Patient app | Biometric login, push notifications, offline support, Google/Apple calendar, streaks, accessibility, secure messaging |
| 14 | Wearable + validation | Withings integration, polypharmacy flag, medication safety re-trigger, medication adjustment dashboard |
| 15 | Clinician workflow | 30-second briefing redesign, tablet responsive, alert inbox, one-click actions, practice dashboard, post-appointment feedback |
| 16 | Product | Multi-tenancy + RLS, practice admin role, practice analytics dashboard |

**Physician validation study runs in parallel across Weeks 8–16.**

---

## Infrastructure & Budget

### Category 1 — Infrastructure (Paid Tiers, 3 Months)

| Service | Tier | Why Paid | Monthly | 3-Month |
|---|---|---|---|---|
| Supabase Pro (production) | Pro | PITR backup, no pausing risk, 8GB storage | $25 | $75 |
| Supabase Pro (CI/test) | Pro | Dedicated test project, isolated from production | $25 | $75 |
| Railway Pro (backend + worker) | Pro | Dedicated resources, no cold starts, required for 7:30 AM scheduler reliability | $20 | $60 |
| Redis — Upstash Pay-as-you-go | Paid | 50k commands/day for Celery queue under physician validation load | $10 | $30 |
| Vercel Pro (frontend + patient PWA) | Pro | Advanced analytics, 1TB bandwidth, password-protected preview deployments | $20 | $60 |
| GitHub Team (6 members) | Team | Unlimited Actions minutes — free tier exhausted in week 1 with full CI | $24 | $72 |
| Sentry Team | Team | 50k errors/month, alerting rules, 90-day retention | $26 | $78 |
| Doppler Team | Team | Secret audit logs, access control per member | $10 | $30 |
| Cloudflare Pro | Pro | WAF rules, DDoS protection — production-grade for clinician-facing deployment | $20 | $60 |
| Resend (email alerts) | Paid | 50k emails/month — free tier insufficient once practices are onboarded | $20 | $60 |
| Domain (.com, 1 year) | — | Production URL for physician validation study | — | $12 |
| **Infrastructure total** | | | | **$612** |

**HIPAA note:** Supabase Pro does not include a HIPAA Business Associate Agreement. The summer study uses de-identified patients so this is not required. Before onboarding real US patients post-summer, upgrade to Supabase Team ($599/month) and obtain BAAs from all vendors handling PHI.

---

### Category 2 — AI / API (3 Months)

| Service | Usage | Cost |
|---|---|---|
| Anthropic Claude — Layer 3 briefings | ~5,000 calls: daily briefings for demo patients + physician validation study patients (20 patients × 90 days) | $150 |
| Anthropic Claude — Chatbot (Sprint 4 dev + production) | ~15,000 conversation turns across development iteration, physician reviewer sessions, and validation testing | $250 |
| Groq API — Paid tier | Production chatbot inference at physician validation scale | $100 |
| **AI / API total** | | **$500** |

---

### Category 3 — Hardware Device Lab

| Item | Qty | Unit Price | Total | Purpose |
|---|---|---|---|---|
| Withings BPM Connect Pro | 3 | $100 | $300 | Primary wearable — 3 units allows concurrent patient testing + 1 spare |
| Omron Connect (EVOLV) | 2 | $80 | $160 | Cross-device validation (post-summer integration) |
| Apple Watch Series 9 | 1 | $399 | $399 | HealthKit API testing for post-summer integration |
| Samsung Galaxy Watch 6 | 1 | $250 | $250 | Android wearable platform for post-summer integration |
| iPad Air (10.9") | 1 | $599 | $599 | Clinician dashboard tablet testing — Sprint 7.2 mobile-first work |
| Android test phone (Samsung A-series) | 1 | $120 | $120 | Patient PWA testing — PWA behaviour on Android Chrome differs from iOS Safari |
| USB charging hub + cable set | 1 | $50 | $50 | Device lab management |
| **Hardware total** | | | **$1,878** | |

---

### Category 4 — ML & Cloud Compute

| Item | Service | Usage | Cost |
|---|---|---|---|
| CUSUM + Isolation Forest training runs | AWS EC2 t3.medium | Training runs across patient cohorts | $42 |
| CausalImpact BSTS model fitting | Same EC2 instance | Per-patient post-medication-change analysis | $0 additional |
| Model artifact storage | AWS S3 Standard | Trained model files, versioned per patient | $15 |
| SageMaker Experiments (model tracking) | AWS SageMaker | Track metrics and model versions | $80 |
| Cloud GPU for CUSUM parameter sweep | AWS EC2 g4dn.xlarge | Parameter sweep to calibrate k and h values against real BP data | $21 |
| **ML compute total** | | | **$158** |

---

### Category 5 — Clinical Validation Study

| Item | Cost | Detail |
|---|---|---|
| Physician participant compensation | $500 | 5 physicians × $100 per physician — structured clinical feedback sessions (2 hours each) |
| Practice manager coordination | $100 | Admin time for scheduling and paperwork at each practice |
| Patient consent materials (de-identified study) | $50 | Consent forms for patients in the validation cohort |
| Ethics consultation (1 hour, university IRB advisor) | $150 | Initial review of study protocol before any patient interaction |
| **Clinical validation total** | | **$800** |

---

### Category 6 — Professional Tools & Development

| Item | Cost | Detail |
|---|---|---|
| Figma Professional ($15/month × 3) | $45 | Patient app redesign — branching, prototyping, dev handoff |
| Linear (project management, $8/seat × 6 × 3) | $144 | Sprint tracking, issue management — replaces ad-hoc GitHub issues |
| Clinical AI & Informatics course (Coursera) | $99 | Structured clinical informatics training for non-clinical team members |
| FHIR R4 implementation guide (HL7 print edition) | $60 | Reference for adapter correctness |
| Postman Pro (API documentation + team sharing) | $24 | Shared API workspace for integration documentation |
| **Tools & development total** | | **$372** |

---

### Category 7 — Security

| Item | Cost | Detail |
|---|---|---|
| Automated penetration test (OWASP ZAP + manual review, 2 hours) | $200 | Basic security audit of patient-facing endpoints before physician validation begins |
| **Security total** | | **$200** |

---

### Category 8 — Conference & Dissemination

| Item | Cost | Detail |
|---|---|---|
| AMIA Annual Symposium student registration (1 member) | $280 | American Medical Informatics Association — primary venue for clinical AI research. Present physician validation findings. |
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

## What This Delivers for Leap of Faith

At the end of 16 weeks, ARIA will be:

1. **Clinically validated** — real physician feedback with structured outcome data from the post-appointment feedback loop, not just shadow mode accuracy
2. **Detection-complete** — diastolic, heart rate, symptoms, masked hypertension, orthostatic hypotension, circadian patterns, polypharmacy — signals no competitor system surfaces in a standard PCP workflow
3. **ML-grade** — CUSUM and CausalImpact replace statistical heuristics; Isolation Forest adds per-patient anomaly detection
4. **Production-ready infrastructure** — Alembic, CI/CD, Celery, SSE, proper secrets management
5. **Patient app clinically valid and accessible** — guided measurements meeting ACC/AHA protocol, push notifications, offline support, OCR input for older monitors, Google/Apple Calendar integration, WCAG 2.1 AA compliant
6. **Wearable-connected** — Withings BPM Connect readings flow automatically into the pipeline without manual transcription
7. **Multi-practice ready** — Row-Level Security, practice admin role, practice analytics dashboard
8. **Clinician workflow integrated** — 30-second briefing layout, tablet-responsive, one-click actions, medication adjustment from dashboard, post-appointment feedback

The system will be demonstrable to real physician practices with real de-identified patients, with quantified precision and recall against actual clinical decision-making.

---

*Document revised: May 2026*
*Team: Leap of Faith Technologies | IIT CS 595 Spring 2026*
*For submission to: John Trzesniak, Leap of Faith Technologies*
*Guidelines: ACC/AHA 2017 Hypertension | Compliance: HIPAA (de-identified summer study)*
