# ARIA — Personal Presentation Prep Reference
> Every functionality, backend formula, and code-level detail you need to know.

---

## 1. EHR INGESTION PIPELINE

**What it is:** The entry point for all real clinical data. Before ARIA can analyze anything, it needs to know who the patient is — their medications, problems, vitals history, allergies, and past visits. This pipeline takes the raw iEMR JSON from Leap of Faith Technologies and converts it into a standardized FHIR R4 format before writing it to the database.

**How to say it in a presentation:** *"We don't touch raw iEMR fields anywhere in our analysis code. Everything goes through an adapter that converts the source data into FHIR R4 — an international healthcare standard — so if we ever swap the EHR vendor, we only need to rewrite the adapter, not the rest of the system."*

### Flow
```
data/raw/iemr/1091_data.json
    → scripts/run_adapter.py
    → services/fhir/adapter.py        (iEMR JSON → FHIR R4 Bundle)
    → scripts/run_ingestion.py
    → services/fhir/ingestion.py      (FHIR Bundle → 12 DB tables)
```

### What the adapter does
- `PROBLEM.value + PROBLEM_CODE` → FHIR `Condition` (clinicalStatus=active, ICD-10)
- `MEDICATIONS (all visits)` → `_aria_med_history` (non-standard bundle key → `clinical_context.med_history` JSONB)
- `MEDICATIONS (current)` → FHIR `MedicationRequest` (most-recent-wins by MED_CODE)
- `VITALS SYSTOLIC+DIASTOLIC` → FHIR `Observation` LOINC 55284-4 — effectiveDateTime from `VITALS_DATETIME` (NOT admit date)
- `VITALS PULSE/WEIGHT/SpO2` → Observation LOINC 8867-4 / 29463-7 / 59408-5
- `ALLERGY` → `AllergyIntolerance` (ALLERGY_STATUS=="Active" only)
- `PLAN where PLAN_NEEDS_FOLLOWUP=YES` → `ServiceRequest`
- `_aria_visit_dates` → max(all ADMIT_DATE) → `clinical_context.last_visit_date`
- `_aria_problem_assessments` → `problem_assessments` JSONB

### Ingestion-time auto-overrides (immovable)
| ICD-10 | Condition | Override |
|--------|-----------|----------|
| I50.* | CHF | risk_tier='high', tier_override_source='system' |
| I61.* | Haemorrhagic Stroke | same |
| I63.* / I64 | Ischaemic Stroke | same |
| G45.* | TIA | same |

> Code normalises ICD-10 to UPPERCASE, dots and dashes removed before matching — so `i50.9` and `I50` both match.

### Two-step upsert (re-ingestion safe)
- Demographics: always update
- Tier: only updates if new ingestion carries a system condition OR no active override exists
- This ensures a mid-care CHF diagnosis on re-ingest correctly promotes a patient to High Risk

---

## 2. SYNTHETIC DATA GENERATOR

**What it is:** The iEMR dataset only has clinic BP readings — there's no home monitoring data because the patients weren't using connected devices. So we built a generator that creates clinically realistic home readings and medication confirmation records by interpolating between the real clinic anchor points. This is what gives ARIA something to detect patterns in.

**How to say it in a presentation:** *"We had real clinic data but no home monitoring data — which is exactly the gap ARIA is designed to close. So we built a synthetic generator that produces home readings by interpolating between real clinic measurements, with clinical realism rules baked in: realistic variance, morning/evening differences, device outages, and white-coat dips before appointments. A clinical reviewer could look at these readings and not be able to tell they're synthetic."*

### Flow
```
scripts/run_generator.py --patient 1091 --mode full-timeline
    → services/generator/reading_generator.py
    → services/generator/confirmation_generator.py
```

### Reading generation rules (clinical realism — never violate)
- **Baseline**: `median(historic_bp_systolic)` — NOT hardcoded. Falls back to 163.0 only if <2 clinic readings
- **Interpolation**: linear between consecutive clinic anchor pairs + Gaussian noise per day
- **Day-to-day SD**: 8–12 mmHg (flat variance < 5 mmHg is rejected as clinically unrealistic)
- **Morning/evening diff**: morning is 5–10 mmHg HIGHER than evening, every week
- **Two-reading session**: reading 2 is 2–6 mmHg LOWER than reading 1
- **Diastolic**: `systolic × 0.60–0.66`
- **Heart rate**: 64–82 bpm; slight negative correlation with systolic when beta-blocker in regimen
- **Device outage**: 1–2 episodes of 2–4 days per inter-visit interval → ABSENT ROWS, never null values
- **White-coat dip**: systolic drops 10–15 mmHg over 3–5 days before appointment, returns to elevated baseline after
- **Round numbers**: NEVER exactly round — real readings: 153, 161, 148, 167 (never 150 or 160)

### Confirmation generation rules
- Per-interval adherence from `Beta(alpha=6.5, beta=0.65)` → mean ~91%, ±10–15 pp per medication per interval
- Medications derived from `clinical_context.med_history` JSONB (not a hardcoded list)
- Idempotency: unique constraint on `(patient_id, medication_name, scheduled_time)`

### Patient 1091 scope
- 65 clinic BP anchor points (2008-01-21 to 2013-09-26, mean 133.8, SD 16.2)
- Demo window shifted +5,654 days from 2010-2013 source → appears as 2026 data
- ~1,800+ home readings generated across full care history
- ~2,100 confirmation records in the 2026 demo window

---

## 3. LAYER 1 — FIVE DETERMINISTIC DETECTORS

**What it is:** The core analytical engine of ARIA. These are five rule-based detectors that run every night for every monitoring-active patient. No AI, no probabilities — pure clinical logic translated into code. They look at the patient's home readings, medication history, and adherence records and produce structured findings: is there a gap? Is BP persistently elevated with no medication change? Is adherence low? Is BP worsening? Is variability high?

**How to say it in a presentation:** *"Layer 1 is entirely deterministic — no black box, no model weights. Every finding is traceable to a specific threshold, a specific reading, and a specific clinical rule. A clinician could look at the logic and verify it themselves. This is intentional — we want clinicians to trust the output, and that means they need to be able to understand exactly why something fired."*

> File: `backend/app/services/pattern_engine/`
> All five use the **adaptive window** and **patient-adaptive threshold**.

### Adaptive Window (used by ALL detectors)
**What it is:** Instead of always looking back a fixed 28 days, ARIA calculates a window based on the actual gap between the patient's last visit and next appointment. This means a patient seen every 2 weeks gets a tighter window than a patient seen every 3 months.

```python
window_days = min(90, max(14, (next_appointment - last_visit_date).days))
# Falls back to 28 if either date is NULL or interval <= 0
# Floor 14: prevents degenerate windows
# Cap 90: bounds computation
```

### Patient-Adaptive Threshold (used by inertia + deterioration)
**What it is:** Instead of applying a single 140 mmHg target to every patient, ARIA derives a personal threshold from the patient's own stable historic readings. A patient who has always run at 155 mmHg is treated differently from one who normally runs at 125 mmHg.

```python
patient_threshold = max(130, stable_baseline_mean + 1.5 × SD)
patient_threshold = min(patient_threshold, 145)

# Comorbidity adjustment (CHF / Stroke / TIA active):
patient_threshold = max(130, patient_threshold - 7)
```
> "stable visits" = filtered historic_bp_systolic; falls back to 140 if <3 stable readings

---

### 3a. Gap Detector (`gap_detector.py`)

**What it is:** The simplest but most time-sensitive detector. It checks how many days have passed since the patient's last home reading. If a High Risk patient with CHF stops sending readings, we flag it after just one day — because for that patient, a missed day of monitoring is a clinical risk in itself.

**How to say it in a presentation:** *"The gap detector is our first line of defence. It doesn't need to see a pattern — it just asks: when did this patient last check in? And the threshold for what counts as too long depends on the patient's risk tier."*

**Formula:**
```python
gap_days = today - MAX(effective_datetime) WHERE source != 'clinic'
```

**Thresholds by tier:**
| Tier | Flag threshold | Urgent threshold |
|------|---------------|-----------------|
| High | 1 day | 3 days |
| Medium | 3 days | 5 days |
| Low | 7 days | 14 days |

- `gap_days > urgent` → `alert_type='gap_urgent'`
- `flag < gap_days <= urgent` → `alert_type='gap_briefing'`
- Gap detector runs from **day one** (not suppressed by cold-start)

---

### 3b. Therapeutic Inertia Detector (`inertia_detector.py`)

**What it is:** Therapeutic inertia is when a patient's BP stays persistently high and the clinician doesn't change anything. This detector identifies that pattern — but with four strict gates to make sure it doesn't fire on noise. It uses the patient's own medication history to confirm that no recent medication change could explain the elevation.

**How to say it in a presentation:** *"Therapeutic inertia is one of the biggest contributors to uncontrolled hypertension. Our detector catches it by requiring four things to all be true at once: the average is high, there are at least five elevated readings, the pattern has persisted for more than a week, and there's been no medication change that could explain it. If BP is already coming down, it doesn't fire."*

**ALL four gates must pass simultaneously:**
```
Gate 1: rolling avg systolic over adaptive window >= patient_threshold
Gate 2: COUNT(readings >= patient_threshold) >= 5
Gate 3: (last_elevated_date - first_elevated_date) > 7 days
Gate 4: MAX(med_history date <= first_elevated_reading) < window_start OR NULL
         (uses med_history JSONB, NOT the stale last_med_change snapshot)
```

**Additional checks:**
- **White-coat exclusion**: filter out readings where `effective_datetime >= (next_appointment - 5 days)` before threshold comparison
- **Slope check**: if 7-day recent avg < patient_threshold (BP declining) → do NOT fire

---

### 3c. Deterioration Detector (`deterioration_detector.py`)

**What it is:** Catches worsening BP trends before they become crises. It requires three simultaneous gates to confirm a genuine upward trend — not just a noisy day. It also has a separate step-change sub-detector that catches sudden jumps even if the overall slope is gradual.

**How to say it in a presentation:** *"Deterioration is different from inertia — inertia is about sustained elevation with no response, deterioration is about BP that's actively getting worse. We require three gates to fire simultaneously to avoid false alarms, plus a separate detector that catches sudden step-changes — like a patient's BP jumping 15 mmHg in a week."*

**Three gates (all must pass):**
```
Gate 1: positive linear slope across adaptive window >= 0.3 mmHg/day
         (computed via least-squares regression)
Gate 2: recent 3-day avg > baseline days 4-10 avg
Gate 3: recent_avg >= patient_threshold
```

**Step-change sub-detector (fires independently):**
```python
step_change = (7-day rolling mean this week) - (7-day rolling mean 3 weeks ago)
if step_change >= 15 mmHg → fire regardless of slope gate
```

- White-coat exclusion applies identically to inertia detector
- When `next_appointment IS NULL` → no exclusion applied

---

### 3d. Adherence-BP Correlation Detector (`adherence_analyzer.py`)

**What it is:** This detector does something no single clinic reading can do — it connects medication-taking behaviour to blood pressure outcomes. By comparing adherence rate to BP level, it classifies each patient into one of three clinically distinct patterns, each requiring a different clinical response.

**How to say it in a presentation:** *"This is the detector that answers the question every clinician asks but can't answer without data: is this patient's BP high because they're not taking their medications, or because the medications aren't working? Pattern A is low adherence with high BP — possible adherence concern. Pattern B is high adherence with high BP — the treatment itself may need reviewing. Pattern C is low adherence with normal BP — contextual, but worth noting."*

**Adherence formula:**
```python
adherence_pct = COUNT(confirmed_at IS NOT NULL) / COUNT(scheduled) × 100
# Joined: readings + confirmations by date within adaptive window
```

**Three patterns:**
| Pattern | Condition | Output |
|---------|-----------|--------|
| A | systolic_avg >= threshold AND adherence < 80% | "possible adherence concern" → alert row |
| B | systolic_avg >= threshold AND adherence >= 80% | "treatment review warranted" |
| C | systolic_avg < threshold AND adherence < 80% | "contextual review" |

**Pattern B suppression** (suppress to "none" when ALL of):
```python
slope < -0.3  AND  recent_7day_avg < threshold  AND  days_since_med_change <= titration_window
# Suppression must NOT apply when no recent med change exists
```

**Titration windows** (from most recently changed drug in med_history):
| Drug class | Window |
|-----------|--------|
| Diuretics / Beta-blockers | 14 days |
| ACE inhibitors / ARBs | 28 days |
| Amlodipine | 56 days |
| Default | 42 days |

---

### 3e. Variability Detector (`variability_detector.py`)

**What it is:** High BP variability — readings swinging wildly up and down — is an independent cardiovascular risk factor. This detector computes the coefficient of variation across the adaptive window and flags it in the visit agenda. It doesn't create an alert but gives the clinician something to discuss.

**How to say it in a presentation:** *"Even if a patient's average BP looks acceptable, high variability in readings is independently associated with worse outcomes. This detector surfaces that pattern in the visit agenda so the clinician can bring it up — without generating a noisy alert."*

**Formula:**
```python
CV = (population_SD / mean) × 100  # coefficient of variation on systolic readings

# CV >= 15.0% → high variability
# CV >= 12.0% and < 15.0% → moderate variability
# Requires minimum 7 readings — fewer suppresses detection entirely
```
- Result appears in visit agenda
- **No separate alert row** created in the alerts table

---

### Cold-Start Suppression (all detectors except gap)

**What it is:** A new patient doesn't have enough data for pattern detection to be meaningful. Running the detectors on 3 days of readings would generate false positives. So we suppress inertia, deterioration, and adherence detection for the first 21 days — long enough to have at least 7 home readings.

```python
if (now - enrolled_at).days < 21:
    skip inertia, deterioration, adherence
    data_limitations = "Patient enrolled N days ago — minimum 21-day monitoring period required"
```

---

## 4. LAYER 2 — RISK SCORING

**What it is:** After Layer 1 runs, every patient gets a numeric priority score from 0 to 100. This score controls the secondary sort order on the dashboard — within the High tier, the sickest patient appears first. It's a weighted formula combining five clinical signals, each normalised to the same 0–100 scale before weighting.

**How to say it in a presentation:** *"Risk tier tells you which bucket a patient is in — High, Medium, or Low. But within High Risk, you might have 200 patients. The risk score tells you which of those 200 needs attention most urgently today. It's computed from five signals: how elevated their BP is, how long it's been since their medication was changed, their adherence rate, how long they've gone without a reading, and the severity of their comorbidities."*

> File: `backend/app/services/pattern_engine/risk_scorer.py`

### Formula
```python
score = (
    (systolic_vs_baseline    × 0.30) +
    (days_since_med_change   × 0.25) +
    ((100 - adherence_pct)   × 0.20) +
    (gap_days_norm           × 0.15) +
    (comorbidity_severity    × 0.10)
)
# All components normalised to 0–100 before weighting
# Final score clamped to 0.0–100.0
```

### Normalisation details
```python
sig_gap     = clamp(gap_days / window_days × 100.0)        # NOT hardcoded /14
sig_inertia = clamp(days_since_med_change / 180.0 × 100.0) # saturates at 6 months
```

### Comorbidity severity (severity-weighted, clamped 0–100)
| Condition | Points |
|-----------|--------|
| CHF (I50), Stroke (I63/I64), TIA (G45) | 25 pts each |
| Diabetes (E11), CKD (N18), CAD (I25) | 15 pts each |
| Any other coded problem | 5 pts each |
> NEVER raw count / 5 — must use severity-weighted scheme above

### Storage
```python
patients.risk_score = computed score
patients.risk_score_computed_at = now()
# Dashboard shows 'Score outdated' badge if risk_score_computed_at > 26 hours ago
```

### Dashboard sort
```sql
ORDER BY risk_tier (High > Medium > Low), risk_score DESC
```

---

## 5. NIGHTLY TIER RECLASSIFICATION

**What it is:** Risk tiers aren't static. A patient's condition changes over time, and the tier should reflect that. Every night, after Layer 2 scoring runs, the system evaluates whether each patient's tier should change. But it doesn't just flip tiers based on scores — it uses a hysteresis band and a strict guard order to prevent patients from oscillating between tiers and to respect clinical overrides.

**How to say it in a presentation:** *"We don't want a patient moving from High to Medium and back to High every few days based on noisy score fluctuations. So we use a hysteresis band — you need a score above 75 to promote to High, but below 40 to demote from High. And if a clinician has manually overridden a patient's tier, we respect that for 28 days before the algorithm can reverse it — unless the score hits 85, which triggers a safety override."*

> Called as `_apply_tier_reclassification()` in `processor.py` after every pattern_recompute

### Guard order (first match wins — stop evaluating once matched)
```
1. tier_override_source == 'system'     → return immediately (immovable, CHF/Stroke/TIA)
2. tier_override_source == 'clinician'
   AND now < suppressed_until
   AND score < 85                       → skip (clinician demotion respected)
3. tier_override_source == 'clinician'
   AND now < suppressed_until
   AND score >= 85                      → break-glass: promote only
4. Apply hysteresis transition table:
```

### Hysteresis table
| Transition | Score condition | Additional gates |
|-----------|----------------|-----------------|
| medium → high | score >= 75 | None |
| high → medium | score < 40 | source == 'system_score' ONLY |
| medium → low | score < 25 | enrolled >= 90 days + no SEVERE/MODERATE comorbidity + no active urgent alerts |
| low → medium | score >= 40 | None |

**Comorbidity block for medium→low:**
- SEVERE: I50, I61, I63, I64, G45
- MODERATE: E11, N18, I25

### Clinician override endpoint
```
PATCH /api/patients/{patient_id}/tier
Body: { "risk_tier": "high"|"medium"|"low", "reason": str (1-500 chars) }

Demotion → sets tier_override_suppressed_until = now + 28 days (NICE NG136 §1.6.3)
Promotion → clears tier_override_suppressed_until
409 if tier_override_source == 'system'
```

---

## 6. DRUG INTERACTION DETECTOR

**What it is:** A deterministic rule checker that runs at every briefing generation and evaluates the patient's current medication list against four known dangerous combinations. No LLM involved — these are hard-coded clinical rules. Severity is assigned based on the combination itself and escalated based on active comorbidities.

**How to say it in a presentation:** *"Our demo patient 1091 is on Voltaren — a common over-the-counter NSAID for joint pain — plus ramipril and furosemide. That's a triple whammy: NSAID plus ACE inhibitor plus diuretic. It significantly elevates the risk of acute kidney injury. Because he also has active CHF and CKD, the severity escalates to critical. This would have gone unnoticed across multiple visits without ARIA flagging it at the top of every briefing."*

> File: `backend/app/services/briefing/medication_safety.py`
> Called from `composer.py` — no LLM, pure deterministic

### Four rules
| Rule ID | Combination | Base severity | Escalates to |
|---------|------------|--------------|-------------|
| nsaid_antihypertensive | NSAID + any antihypertensive | warning | concern (if CHF or CKD) |
| triple_whammy | NSAID + ACE/ARB + any diuretic | concern | critical (if CHF AND CKD both active) |
| k_sparing_ace_arb | K-sparing diuretic + ACE/ARB | warning | concern (if CKD) |
| bb_non_dhp_ccb | beta-blocker + verapamil or diltiazem | concern | no escalation |

> Triple whammy **supersedes** nsaid_antihypertensive — deduplication prevents overlapping findings

### Visit agenda priority order
```
0. Critical drug interactions (before everything)
1. Urgent alerts
1a. Concern-level drug interactions
2. Inertia
3. Adherence concern
3a. Warning-level drug interactions
4. Variability
5. Overdue labs
6. Active problems review
```

---

## 7. LAYER 3 — LLM NARRATIVE

**What it is:** After the full deterministic briefing is stored, Layer 3 converts it into a three-sentence plain-English summary using an LLM. This is the AI summary card at the top of every briefing page. The LLM never sees raw patient data — it only gets the structured output of Layer 1. And before the summary is stored, eleven validation checks must pass.

**How to say it in a presentation:** *"Layer 3 is where the LLM comes in — but only after all the hard work is done deterministically. The LLM's only job is to turn a structured JSON payload into three readable sentences. It can't make things up — every claim in the summary has to be traceable back to the Layer 1 payload. If validation fails, we retry once, and if it fails again, we just show the full deterministic briefing without an AI summary. The system never goes down because of an LLM failure."*

> Files: `services/briefing/summarizer.py` + `services/briefing/llm_validator.py`
> Model: `claude-sonnet-4-20250514` (currently `gpt-4o-mini` as demo substitute)
> Prompt: `prompts/briefing_summary_prompt.md` — SHA-256 hash stored in `briefings.prompt_hash`

### Flow
```python
# DB connection returned to pool BEFORE LLM call (prevents pool exhaustion)
briefing = store_deterministic_briefing()
release_db_connection()
summary = call_llm(briefing_payload)
run_11_validation_checks(summary)
# Pass → store in briefings.readable_summary
# Fail → retry once → still fail → readable_summary = None
```

### 11 validation checks
**Guardrail checks (absolute — payload irrelevant):**
- No: "non-adherent", "non-compliant", "hypertensive crisis", "medication failure"
- No: "increase.*mg" / "decrease.*mg" / "prescribe" / "diagnos" / "emergency"
- No: "tell the patient"
- No: patient ID verbatim (PHI)
- No: "[INST]", "system:", "ignore previous" (injection)

**Faithfulness checks (vs Layer 1 payload):**
- Exactly 3 sentences
- risk_score cited within ±10 of actual
- Adherence language grounded in adherence_summary
- "titration" requires titration notice in medication_status
- "urgent" requires urgent_flags
- Overdue lab claims require overdue_labs
- Conditions grounded in active_problems
- Drug names in medication_status
- BP values 60–250 mmHg, within ±20 of trend data

> Every attempt (pass or fail) → `audit_events` row with `action='llm_validation'`

---

## 8. BRIEFING COMPOSITION

**What it is:** The briefing composer assembles everything Layer 1 found — plus medication status, drug interactions, adherence summary, active problems, overdue labs, and visit agenda — into a single structured JSON payload. This is what gets stored in the database and displayed on the clinician's briefing page. It's the central output of the entire ARIA pipeline.

**How to say it in a presentation:** *"The briefing is what the clinician actually sees. It's not a raw data dump — it's a structured, prioritized summary of everything clinically relevant about this patient right now. Drug interactions appear first if they're critical. Then urgent alerts. Then inertia findings. Then adherence. The visit agenda tells the clinician the three to six most important things to discuss, in the order they should discuss them."*

> File: `backend/app/services/briefing/composer.py`

### What goes into `briefings.llm_response` JSONB
```json
{
  "trend_summary":         "adaptive-window BP pattern + 90-day trajectory",
  "trend_avg_systolic":    float | null,
  "medication_status":     "current regimen + last change + titration notice if within window",
  "adherence_summary":     "per-med rate + Pattern A/B/C",
  "active_problems":       ["list from clinical_context"],
  "problem_assessments":   {"problem_name": "most_recent_assessment_text"},
  "overdue_labs":          ["from clinical_context + abnormal recent_labs flags"],
  "drug_interactions":     [{"rule", "severity", "drugs_involved", "description", "comorbidity_amplified"}],
  "visit_agenda":          ["3-6 items in priority order"],
  "urgent_flags":          ["active unacknowledged alerts"],
  "risk_score":            float,
  "data_limitations":      "monitoring availability / cold-start notice",
  "patient_context":       "social_context from clinical_context"
}
```

### trend_avg_systolic — single source of truth
- Computed in `composer.py`, embedded in briefing payload
- `GET /api/patients` and `GET /api/patients/{id}` extract it from active briefing → `Patient.trend_avg_systolic`
- Frontend uses this directly; falls back to live 28-day computation ONLY when null

### Active briefing filter
```python
# GET /api/briefings/{patient_id}
WHERE appointment_date >= today OR appointment_date IS NULL
# Past appointment briefings excluded
# Mini-briefings (appointment_date IS NULL) always active
```

---

## 9. BACKGROUND WORKER + SCHEDULER

**What it is:** ARIA's analysis pipeline doesn't run inside API requests — it runs asynchronously in the background. A polling worker checks a job queue every 30 seconds and processes queued jobs. APScheduler handles two scheduled operations: briefing generation at 7:30 AM for patients with appointments that day, and a midnight pattern recompute for all monitoring-active patients.

**How to say it in a presentation:** *"Nothing analytical happens in the HTTP request path. Every patient analysis runs through a job queue — the clinician never waits for a detector to run. The 7:30 AM briefing generation means that by the time the clinician sits down in the morning, every patient's briefing is already ready. The midnight sweep means risk scores are always fresh. And we stagger the midnight jobs using a deterministic hash of the patient ID to spread load across a 2-hour window."*

> Files: `services/worker/processor.py` + `services/worker/scheduler.py`

### Poll loop
```python
# processor.py — every 30 seconds
SELECT * FROM processing_jobs WHERE status='queued' ORDER BY queued_at
→ set status='running'
→ execute job
→ set status='succeeded' or 'failed' + error_message
```

### Job types
| Job type | Trigger | Idempotency key |
|---------|---------|----------------|
| pattern_recompute | midnight sweep | `pattern_recompute:{patient_id}:{YYYY-MM-DD}` |
| briefing_generation | 7:30 AM | `briefing_generation:{patient_id}:{YYYY-MM-DD}` |
| bundle_import | POST /api/ingest | `bundle_import:{patient_id}:{bundle_hash}` |

### Midnight sweep stagger
```python
offset_minutes = int(hashlib.md5(patient_id.encode()).hexdigest(), 16) % 120
# Each patient gets a deterministic 0-119 min offset — spreads load across 2 hours
```

### Escalation logic (in processor.py)
```python
# gap_urgent and deterioration alerts:
if not acknowledged_at AND (now - triggered_at) > 24 hours:
    escalated = True
```

### off_hours flag (important detail)
```python
# Stamped from PATTERN-ONSET reading datetime, NOT job execution time
# Prevents all nightly sweep alerts from appearing as off-hours
off_hours = is_off_hours(first_elevated_reading.effective_datetime)
# is_off_hours: 6PM–8AM UTC OR weekend → True
```

---

## 10. ALERT FEEDBACK LOOP + CALIBRATION

**What it is:** When a clinician acknowledges an alert, they don't just dismiss it — they record a structured disposition explaining what they're doing about it. That disposition feeds a feedback loop: repeated disagreements with a specific detector for a specific patient surface a calibration recommendation, and a 30-day outcome check follows every dismissal to see if the clinician was right.

**How to say it in a presentation:** *"This is how ARIA learns from clinician behaviour without retraining a model. If a clinician keeps dismissing gap alerts for a specific patient — because that patient always takes a holiday in April — after four dismissals, the system surfaces a recommendation to suppress that alert for that patient. The clinician isn't fighting the system forever; the system adapts. And every dismissal gets a 30-day retrospective check: was that finding actually irrelevant? That's how you close the feedback loop."*

### Acknowledge flow
```
POST /api/alerts/{id}/acknowledge
Body: { disposition: "agree_acting"|"agree_monitoring"|"disagree", reason_text?: str }
→ sets alerts.acknowledged_at
→ writes alert_feedback row
→ if disposition == "disagree":
    outcome_tracker.py creates outcome_verifications row
    check_after = acknowledged_at + 30 days
```

### Calibration suppression trigger
```python
# When a patient-detector pair accumulates 4+ Disagree dispositions:
GET /api/admin/calibration-recommendations  # surfaces the pair
POST /api/admin/calibration-rules           # admin approves suppression

# Effect: inbox alert writes suppressed for that pair
# Detection still runs; findings still appear in briefing
```

### 30-day outcome verification
```
After check_after date:
GET /api/admin/outcome-verifications
POST /api/admin/outcome-verifications/{id}/respond
Body: { clinician_response: "relevant"|"not_relevant"|"unsure", response_notes?: str }
```

---

## 11. ASK ARIA CHATBOT

**What it is:** An LLM-powered clinical chatbot embedded in every briefing page. The clinician can ask natural-language questions about the patient — "Why is their score so high?", "What's their adherence been like?", "Explain the drug interaction" — and get data-grounded answers. Three layers of guardrails prevent off-topic, cross-patient, or prescriptive responses.

**How to say it in a presentation:** *"The chatbot is scoped to one patient at a time. It has access to three tools — it can pull the briefing, pull recent readings, or pull the alert history — and it decides which ones to call based on the question. The clinician can see exactly which tools were called, so there's no black box. And it can never tell the clinician what medication to prescribe or what dose to change — those guardrails are enforced on the output before anything reaches the screen."*

> File: `backend/app/services/chat/agent.py`
> System prompt: `prompts/chat_system_prompt.md`

### Three-layer guardrail sequence
```
Layer 1 (off-topic): non-clinical question → decline politely
Layer 2 (scope):     cross-patient query or admin question → block
Layer 3 (output):    response contains prescriptive language → reject
```

### Tool-call routing
```python
tools = [
    get_briefing(patient_id),   # fetches full briefing payload
    get_readings(patient_id),   # fetches recent home readings
    get_alerts(patient_id),     # fetches active/acknowledged alerts
]
```

### UI features (frontend)
- Confidence badges on responses
- Tool-thinking chips during tool calls (visible to clinician)
- Follow-up question chips (distinct from asked question)
- Numbered source citations, copy button, timestamps
- Conversation summary (POST /api/chat/summary/{patient_id})
- Thumbs up/down feedback → logged to audit_events

---

## 12. PATIENT PWA — CUFFLINK

**What it is:** The patient-facing side of ARIA. An installable Progressive Web App that patients can add to their phone's home screen like a native app. They log in, submit their morning and evening BP readings, and confirm their medication doses with a single tap. Their data flows into the readings and medication_confirmations tables, which Layer 1 then analyzes overnight.

**How to say it in a presentation:** *"CuffLink is what closes the loop between the clinic and the home. The patient doesn't see any of their analysis — no risk scores, no trend charts, no clinical language. They just submit readings and confirm medications. But that data is what makes the entire ARIA pipeline meaningful. Without it, we're just looking at clinic readings taken every few months."*

> `patient-app/` — Next.js 14, port 3001
> Auth: separate `PATIENT_JWT_SECRET`, 8h expiry, role="patient"

### Pages
- `/` — login (patient credentials)
- `/submit` — BP reading submission (two readings per session, symptoms, medication_taken)
- `/confirm` — medication dose confirmation (one-tap)

### Reading validation (server-side)
```python
systolic: 60–250 mmHg
diastolic: 40–150 mmHg
# Out of range → 422 response, not stored
```

### What patients NEVER see
- Their BP readings or trends
- Risk tier or risk score
- Any clinical interpretation or analysis

---

## 13. SHADOW MODE VALIDATION

**What it is:** A way to measure how well ARIA's detectors agree with real physician clinical judgment — without needing a prospective trial. We replay the detector logic at each historical clinic visit in the iEMR dataset using only the data that was available before that visit, then compare ARIA's output against the physician's recorded HTN concern level at that visit.

**How to say it in a presentation:** *"Shadow mode is our ground-truth validation. We replay ARIA's detectors at every historical clinic visit — pretending we only know what the system would have known at that point — and compare our output against what the physician actually wrote in the record. We achieved 78.4% agreement across 37 labeled evaluation points. The six false negatives are all clinically explained — cold-start suppression, BP already declining after a medication change, that kind of thing. Zero are caused by bugs in our logic."*

> Script: `scripts/run_shadow_mode.py`
> Results: `data/shadow_mode_results.json`

### What it does
```
For each historical clinic visit in iEMR dataset:
1. Use ONLY data available before that visit
2. Run all 5 Layer 1 detectors
3. Compare ARIA output vs physician's PROBLEM_STATUS2_FLAG
   (1=urgent, 2=concerned, 3=stable)
4. Agreement = ARIA fired ↔ physician concerned/urgent
```

### Final result
- **78.4% agreement** across 37 labeled evaluation points
- 6 false negatives (all clinically explained):
  - Cold-start suppression (insufficient data at early visits)
  - Active treatment response (BP declining after med change — ARIA correctly silent)
  - Same-day medication changes (ambiguity in window boundary)
- 2 false positives (rising trends physician assessed as stable)
- **Zero false negatives caused by system logic errors**

---

## 14. DEMO PATIENTS

**What it is:** Four carefully constructed patients that each demonstrate a distinct clinical scenario ARIA is designed to detect. All four are seeded from real iEMR data (or constructed to be clinically plausible), and all four produce predictable, reproducible findings on demo day.

**How to say it in a presentation:** *"We have four demo patients, each showing a different scenario. Patient 1091 is our most complex — CHF, triple whammy drug interaction, therapeutic inertia. DEMO_EHR shows what happens when there's no home monitoring. DEMO_GAP has stopped sending readings. DEMO_ADH has low adherence with high BP. Together they cover the full range of what ARIA detects."*

| Patient | ID | Scenario | Key findings |
|---------|-----|---------|-------------|
| A | 1091 | Therapeutic Inertia + Drug Interaction | Triple whammy (critical), inertia alert, CHF system override |
| B | DEMO_EHR | EHR-Only | NSAID+antihypertensive, overdue labs, data limitations banner |
| C | DEMO_GAP (David Patel) | Reading Gap | Urgent gap alert 9-12 days, inertia alert, escalation badge |
| D | DEMO_ADH | Adherence Concern | Pattern A alert (~58% adherence), inertia alert |

### Setup command
```bash
python scripts/setup_demo.py        # seeds all 4, applies +5654 day shift to patient 1091
python scripts/setup_demo.py --verify-only   # checks only — use on demo day
# Success signal: ALL CHECKS PASSED ✓ for all four patients
```

---

## 15. AUDIT LOGGING

**What it is:** An immutable record of every significant action in the system. Every bundle import, every reading stored, every briefing viewed, every alert acknowledged, every LLM validation attempt — all of it gets a row in the audit_events table with a strict outcome of 'success' or 'failure'. This is a clinical compliance requirement, not an afterthought.

**How to say it in a presentation:** *"In a real clinical deployment, you need to be able to answer the question: who did what, to which patient, and when? Our audit log answers that for every action in the system. A clinician viewed a briefing — logged. The LLM failed validation — logged. A tier changed overnight — logged. The outcome field is always 'success' or 'failure' — never null, never omitted."*

| Action | resource_type | Trigger |
|--------|--------------|---------|
| bundle_import | Bundle | POST /api/ingest |
| reading_ingested | Reading | patient app submit / generator |
| briefing_viewed | Briefing | GET /api/briefings/{id} (updates read_at) |
| alert_acknowledged | Alert | POST /api/alerts/{id}/acknowledge |
| llm_validation | Briefing | every Layer 3 attempt, pass or fail |
| tier_reclassified | Patient | every nightly reclassification change |

```python
outcome = "success" | "failure"   # never omit, never any other value
```

---

## 16. DATABASE — 12 TABLES QUICK REFERENCE

**What it is:** A single PostgreSQL database on Supabase shared by all system components. 12 tables covering patients, clinical context, readings, confirmations, alerts, briefings, jobs, audit events, and the feedback loop tables. Every table has purpose-built indexes for the queries that hit it most.

| Table | Key purpose |
|-------|------------|
| patients | Demographics + risk_tier + risk_score (primary dashboard sort) |
| clinical_context | Pre-computed EHR context, med_history JSONB, problem_assessments JSONB |
| readings | Home + clinic BP; unique on (patient_id, effective_datetime, source) |
| medication_confirmations | Per-dose adherence; unique on (patient_id, medication_name, scheduled_time) |
| alerts | Detector findings; gap_urgent + deterioration escalate after 24h |
| alert_feedback | Clinician dispositions (agree_acting/agree_monitoring/disagree) |
| briefings | Full deterministic briefing + readable_summary + prompt_hash |
| processing_jobs | Job queue; unique idempotency_key prevents duplicate runs |
| audit_events | Immutable action log; outcome must be 'success' or 'failure' |
| gap_explanations | Clinician-submitted gap reasons |
| calibration_rules | Approved inbox suppression per patient-detector pair |
| outcome_verifications | 30-day retrospective checks auto-created on Disagree |

---

## 17. TECH STACK QUICK REFERENCE

| Layer | Tech |
|-------|------|
| Backend | Python 3.11, FastAPI async, SQLAlchemy 2.0 async (select() only, never session.query()) |
| Database | PostgreSQL via Supabase, asyncpg driver |
| Validation | Pydantic v2 — `model_config = SettingsConfigDict(env_file=".env", extra="ignore")` |
| Scheduler | APScheduler |
| Linting | ruff check app/ (must pass before anything is done) |
| Frontend | Next.js 14, TypeScript strict, Tailwind CSS, recharts |
| Patient PWA | Next.js 14 + @ducanh2912/next-pwa |
| LLM (spec) | Anthropic claude-sonnet-4-20250514 |
| LLM (demo) | OpenAI gpt-4o-mini (one-line revert per file) |

---

## 18. CLINICAL LANGUAGE RULES (enforce at code level)

**What it is:** ARIA is decision support, not a diagnostic tool. The language it uses is controlled at the code level — not as a style guide but as a hard constraint enforced in both the deterministic output and the LLM validation layer. These rules protect the clinical boundary and protect ARIA from being misused.

**How to say it in a presentation:** *"We spent a lot of time thinking about language. ARIA never says a patient is 'non-adherent' — it says 'possible adherence concern'. It never says 'medication failure' — it says 'treatment review warranted'. These aren't just word choices — they reflect the clinical reality that ARIA doesn't have enough information to make a diagnosis. It's surfacing signals for a clinician to interpret, not replacing that clinician's judgment."*

| Say this | Never say this |
|----------|---------------|
| "possible adherence concern" | "non-adherent" / "non-compliant" |
| "treatment review warranted" | "medication failure" |
| "sustained elevated readings" | "hypertensive crisis" |
| (no dose instructions ever) | "increase X mg" / "decrease X mg" |
| (decision support only) | "diagnose" / "prescribe" / "emergency" |

---

*Generated for personal presentation prep — May 2026*
