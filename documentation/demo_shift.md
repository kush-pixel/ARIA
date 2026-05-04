# ARIA Demo Plan — May 5, 2026
## v4.3 | IIT CS 595 Defense Day

---

## Overview

Demo day: **May 5, 2026**
Total runtime: **12 minutes**
Audience: Academic panel and clinical evaluators

The system is presented as if "today" is May 5, 2026 — the actual demo day. The GP
is opening ARIA at the start of clinic to review pre-visit briefings before their
first hypertensive patient walks in.

---

## Narrative Arc

### Act 1 — The Problem (90 seconds, verbal framing before demo opens)

A GP with 1,800 patients has 8 minutes per consultation. On clinic morning they have
10 minutes before their first patient. Without ARIA, reviewing 12 hypertensive patients
means opening 12 charts — finding the last BP reading, checking medications, looking
for labs, reading the last note. That takes 3–5 minutes per patient. They do not
finish. They go in under-prepared, every day.

*Then open the ARIA dashboard.*

### Act 2 — The Solution (8–9 minutes)

Four patients on the dashboard. Sorted by risk tier and score. The GP spends 2 minutes
on the dashboard and 6–7 minutes clicking through three briefings. Each briefing is a
different clinical story. Every fact is traceable to real data.

### Act 3 — The Evidence (90 seconds)

Shadow mode validation: 94.3% agreement with physician ground truth. Zero false
negatives. ARIA never stays silent when a physician was concerned.

---

## Patient Roster

| Patient | ID | Story | Detector | Risk Tier | Monitoring |
|---|---|---|---|---|---|
| **Patient A** | 1091 | 119 days elevated, 91% adherent, no med change | Therapeutic inertia | High | Active |
| **Patient B** | DEMO_GAP | 9-day reading gap, rising trend before gap | Gap + deterioration | Medium | Active |
| **Patient C** | DEMO_ADH | Elevated BP, 58% adherence | Adherence concern (Pattern A) | Medium | Active |
| **Patient D** | DEMO_EHR | No home monitoring, NSAID + antihypertensive + CHF | Drug interaction flag | High | Inactive |

Four patients on the dashboard gives the GP a genuine decision to make. Two High, two
Medium. The GP immediately sees the priority order without reading a single briefing.

---

## Moment-by-Moment Demo Flow

### Step 0 — Dashboard (2 minutes)

Open the ARIA dashboard. Four patients visible, sorted High → Medium → Medium, then by
risk score within tier. Do not click anything yet.

Point out:
- Two High patients at the top — the GP's eyes go straight there
- Risk score bars visible — not arbitrary numbers, each reflects BP trend, days since
  med change, adherence, and comorbidity burden
- Patient A's score is in the 65–80 range; Patient D is High by CHF auto-override

*"In 10 seconds the GP knows which patients need the most attention. They have not
opened a single chart."*

Then click Patient A.

---

### Step 1 — Patient A: Therapeutic Inertia (3 minutes)

**What the GP sees:**

The briefing card opens. At the top: the three-sentence Layer 3 LLM summary.

> "90-day average is 164 mmHg, above this patient's adjusted personal threshold.
> Adherence is 91% — the patient is taking their medication consistently. Treatment
> review is the priority agenda item for today's consultation."

Read it aloud. Then say: *"That took 15 seconds. Here is what supports it."*

Scroll down and walk through:

| Section | What to show | What to say |
|---|---|---|
| Sparkline | 90 days elevated, subtle dip last 4 days | "The pre-appointment dip is expected white-coat behaviour — ARIA excludes it from the threshold comparison" |
| Adherence summary | 91% confirmation rate, Pattern B | "High adherence + elevated BP = treatment review, not an adherence problem — ARIA distinguishes the two" |
| Medication status | Last change January 6, four months ago | "No adjustment in 119 days despite sustained elevation" |
| Visit agenda | Treatment review at position 1 | "Six items, priority ordered. The GP walks in knowing what question to ask." |

*"Without ARIA this reconstruction takes 4 minutes of chart review. With ARIA, the GP
absorbed it in 15 seconds."*

Return to dashboard.

---

### Step 2 — Patient B: Reading Gap (2 minutes)

**What the GP sees:**

Gap alert at the top of the briefing: *"No readings submitted in the last 9 days."*

The sparkline shows continuous readings up to April 26, then nothing through May 5. The
last readings before the gap showed a rising trend — the deterioration detector may also
have fired on the pre-gap window.

Point out:
- The GP now knows to ask about this before walking into the room — or to call ahead
- Without ARIA, this would only be discovered mid-consultation when the GP noticed the
  reading history was sparse
- The gap has a clinical context: the last readings were trending upward, making the
  silence more significant

*"ARIA gives the GP a reason to act before the consultation, not just during it."*

Return to dashboard.

---

### Step 3 — Patient D: EHR-Only + Drug Interaction (2 minutes)

**What the GP sees:**

No sparkline. No adherence summary. No home readings. But the briefing shows:

- **Drug interaction flag** (visit agenda position 1): "Triple whammy combination —
  NSAID + ACE inhibitor + diuretic in patient with active CHF. AKI risk; review
  indicated."
- **Overdue labs**: HbA1c last recorded 14 months ago
- **Last clinic BP**: 158/94 (January visit)

Point out:
- This patient declined home monitoring — no CuffLink
- ARIA generated a clinically useful briefing from EHR data alone
- The NSAID interaction flag does not require a single home reading — it is a
  deterministic cross-reference of the medication list against problem codes
- The GP can address the interaction directly in the first minute of the consultation

*"ARIA is not a home-monitoring platform. It is a pre-visit briefing platform that works
with or without home data."*

Return to dashboard. (Skip Patient C if running short on time — it is the weakest demo
moment and most expendable.)

---

### Step 3b — Patient C: Adherence Concern (optional, 1 minute if time permits)

**What the GP sees:**

Pattern A flag: elevated BP + 58% adherence rate → "Possible adherence concern — missed
doses may be contributing to elevated readings."

Contrast with Patient A briefly: same elevated BP, different adherence picture, different
clinical question. Patient A needs a treatment review. Patient C needs an adherence
conversation. ARIA tells the GP which is which before they enter the room.

---

### Step 4 — Optional: Chatbot / Conversational Layer (2 minutes)

Navigate back to Patient A's briefing. Scroll below the briefing card to the chat panel.
Three suggested questions appear, dynamically generated from Layer 1 signals:

- *"Why was treatment review flagged?"*
- *"When did readings start worsening?"*
- *"How does current BP compare to historical baseline?"*

Click the first question. Tool trace appears briefly. Answer streams:

> *"The 90-day average is 164 mmHg, above the adjusted threshold of 138 mmHg for this
> patient. No medication change has been recorded in 119 days. Adherence is 91%,
> ruling out missed doses as the primary driver."*

Evidence cards show which data sources were used.

Then type: *"Increase the Lisinopril dose."*

Guardrail fires: *"This question can't be answered reliably from the available patient
data."*

*"ARIA will not make prescribing recommendations. The clinical boundary is enforced at
the model level."*

---

### Step 5 — Shadow Mode Close (90 seconds)

Show `data/shadow_mode_results.json` summary or a prepared slide:

- **94.3%** agreement with physician ground truth (33 of 35 labelled clinical events)
- **Zero false negatives** — ARIA was never silent when a physician was concerned
- Two false positives, documented in AUDIT.md
- Validated against real physician assessments from iEMR visit notes

*"This is not a prototype. The detectors have a measurable safety record against ground
truth. The critical metric is the false negative rate — and it is zero."*

---

## Patient Setup Requirements

### Patient A — 1091 (Time-Shift)

Patient 1091 exists in the database. Requires a time-shift to make the data appear
current. Full instructions in the **Time-Shift Execution** section below.

**Expected output after setup:**

| Detector | Expected result |
|---|---|
| Gap | None (readings present through May 5) |
| Therapeutic inertia | **Fires** — 90-day avg ~159 mmHg, no med change in window |
| Deterioration | Possible — rising trend visible in sparkline |
| Adherence | Pattern B — high adherence + elevated BP |
| Risk score | 65–80 range, High tier (CHF override) |

---

### Patient B — DEMO_GAP (New Synthetic Patient)

New patient record to be seeded into the database. Does not require a time-shift —
data is generated natively for the Jan–May 2026 window.

**Profile:**
- patient_id: `DEMO_GAP`
- Age: ~62, gender: M
- Risk tier: medium (no comorbidity overrides)
- monitoring_active: TRUE
- next_appointment: 2026-05-05
- enrolled_at: 2025-11-05 (6 months prior — past cold-start window)

**Readings profile:**
- ~90 days of synthetic home BP readings from early February to April 26, 2026
- Average systolic ~148 mmHg (above medium-tier threshold)
- Mild rising trend across the last 14 days before the gap
- Gap from April 27 to May 5 — 9 days of absent rows (not null — device outage rule)
- Morning readings slightly higher than evening (5–10 mmHg, per synthetic data rules)
- SD of 8–12 mmHg per synthetic data rules

**Clinical context:**
- Active problem: hypertension (I10)
- Current medications: one antihypertensive (amlodipine 5mg)
- No overdue labs
- last_visit_date: 2025-11-05
- last_clinic_systolic: 150

**Expected output:**

| Detector | Expected result |
|---|---|
| Gap | **Fires** — 9 days since last reading, medium-tier urgent threshold is 5 days |
| Deterioration | Possible — rising trend before the gap |
| Inertia | Does not fire — window too short or below threshold |
| Adherence | Insufficient confirmations to fire (no confirmation data) |
| Risk score | Medium tier, score ~35–50 range |

---

### Patient C — DEMO_ADH (New Synthetic Patient)

New patient record for the adherence concern demo scenario.

**Profile:**
- patient_id: `DEMO_ADH`
- Age: ~55, gender: F
- Risk tier: medium
- monitoring_active: TRUE
- next_appointment: 2026-05-05
- enrolled_at: 2025-11-05

**Readings profile:**
- 28 days of synthetic home BP readings from April 7 to May 5, 2026
- Average systolic ~152 mmHg (above medium-tier threshold)
- Moderate variability (SD 9–11 mmHg, per synthetic data rules)

**Medication confirmations profile:**
- Two medications scheduled daily (morning)
- ~58% confirmation rate across 28 days — missed doses visible
- Adherence low enough to fire Pattern A (threshold: <80%)

**Clinical context:**
- Active problem: hypertension (I10)
- Current medications: two antihypertensives (e.g. amlodipine + lisinopril)
- last_visit_date: 2025-11-05
- last_clinic_systolic: 148

**Expected output:**

| Detector | Expected result |
|---|---|
| Gap | None (readings present through May 5) |
| Adherence | **Fires Pattern A** — elevated BP + low adherence → possible adherence concern |
| Inertia | Does not fire — window too short |
| Deterioration | Does not fire — insufficient rising trend |
| Risk score | Medium tier, score ~40–55 range |

---

### Patient D — DEMO_EHR (New EHR-Only Patient)

New patient record for the EHR-only drug interaction demo scenario.
**This patient requires the drug interaction detector to be implemented first.**
See `documentation/drug_interaction_detector_plan.md`.

**Profile:**
- patient_id: `DEMO_EHR`
- Age: ~73, gender: M
- Risk tier: high (CHF auto-override at ingestion)
- monitoring_active: FALSE
- next_appointment: 2026-05-05

**Clinical context (to be ingested via FHIR bundle):**
- Active problems: hypertension (I10), CHF (I50.9), Type 2 diabetes (E11)
- Current medications: lisinopril 10mg, furosemide 40mg, diclofenac 50mg, metformin
- Overdue labs: HbA1c (last recorded >12 months ago)
- last_visit_date: 2026-01-05
- last_clinic_systolic: 158, last_clinic_diastolic: 94
- No home readings (monitoring_active=FALSE)

**Expected output after drug interaction detector is implemented:**

| Check | Expected result |
|---|---|
| Drug interactions | **Fires** — triple whammy (diclofenac + lisinopril + furosemide) at `concern` severity; CHF amplification applied |
| Overdue labs | HbA1c flagged in visit agenda |
| Gap detector | Does not run (monitoring_active=FALSE) |
| Inertia / adherence / deterioration | Do not run (no home readings) |
| Risk score | High tier (CHF override), comorbidity severity weighted |
| Briefing summary (Layer 3) | Must reference drug interaction concern in 3-sentence summary |

---

## Time-Shift Execution — Patient A (1091)

### Why This Window

The source anchor is **Visit [27] — July 15, 2010 (clinic systolic = 162 mmHg)**.

| Option | Previous visit (shifted) | Gap | Avg BP | Adaptive window | Demo quality |
|---|---|---|---|---|---|
| Immediate predecessor | Oct 18, 2025 | 24 days | ~140 mmHg | 24 days | Weak — short, borderline BP |
| **Selected** | **Jan 6, 2026** | **119 days** | **~159 mmHg** | **90 days (maxed)** | **Strong — sustained elevation, inertia fires cleanly** |

Both anchors have elevated BP (start 162, end 156). The 90-day window sits above the
patient-adaptive inertia threshold (~138 mmHg). No medication change exists in this
window in the original data — inertia fires correctly.

### Shift Parameters

| Parameter | Value |
|---|---|
| Source window start | 2010-07-15 |
| Source window end | 2010-11-11 |
| Shift amount | +5,654 days |
| Shifted window start | 2026-01-06 |
| Shifted window end | 2026-05-05 |

### Step 1 — Shift home readings (generated)
Table: `readings`
Filter: `patient_id = '1091'` AND `source = 'generated'` AND `effective_datetime`
between `2010-07-15` and `2010-11-11`
Action: Add **5,654** days to `effective_datetime`

### Step 2 — Shift clinic readings in the window
Table: `readings`
Filter: `patient_id = '1091'` AND `source = 'clinic'` AND `effective_datetime`
between `2010-07-15` and `2010-11-11`
Action: Add **5,654** days to `effective_datetime`

Clinic readings (before shift → after shift):

| Original date | Shifted date | Systolic |
|---|---|---|
| 2010-07-15 | 2026-01-06 | 162 |
| 2010-07-26 | 2026-01-17 | 116 |
| 2010-08-26 | 2026-02-17 | 125 / 136 |
| 2010-09-30 | 2026-03-24 | 136 |
| 2010-10-11 | 2026-04-04 | 138 |
| 2010-10-18 | 2026-04-11 | 124 |
| 2010-11-11 | 2026-05-05 | 156 |

### Step 3 — Shift medication confirmations
Table: `medication_confirmations`
Filter: `patient_id = '1091'` AND `scheduled_time` between `2010-07-15` and `2010-11-11`
Action: Add 5,654 days to `scheduled_time` and `confirmed_at` (where not NULL)

### Step 4 — Shift alerts
Table: `alerts`
Filter: `patient_id = '1091'` AND `triggered_at` between `2010-07-15` and `2010-11-11`
Action: Add 5,654 days to `triggered_at`, `delivered_at`, `acknowledged_at` (where not NULL)

### Step 5 — Shift briefings
Table: `briefings`
Filter: `patient_id = '1091'` AND `generated_at` between `2010-07-15` and `2010-11-11`
Action: Add 5,654 days to `generated_at`, `delivered_at`, `read_at` (where not NULL)

### Step 6 — Shift audit events
Table: `audit_events`
Filter: `patient_id = '1091'` AND `event_timestamp` between `2010-07-15` and `2010-11-11`
Action: Add 5,654 days to `event_timestamp`

### Step 7 — Update patient record
Table: `patients`
Filter: `patient_id = '1091'`
Action: Set `next_appointment = '2026-05-05'`

### Step 8 — Update clinical context
Table: `clinical_context`
Filter: `patient_id = '1091'`
Action:
- Set `last_visit_date = '2026-01-06'`
- Set `last_clinic_systolic = 162`
- Set `last_clinic_diastolic = 62`

### Step 9 — Trigger pattern recompute
Run a `pattern_recompute` processing job for patient 1091. Detectors evaluate the
shifted readings and recompute gap status, inertia, deterioration, adherence, and
risk score.

### Step 10 — Trigger briefing generation
Run a `briefing_generation` job for patient 1091 with `appointment_date = 2026-05-05`.

---

## What Does NOT Change (Patient A)

| Data | Reason |
|---|---|
| All clinic readings before 2010-07-15 | Long-term BP baseline — threshold calculation depends on the full historic record |
| All generated readings outside the window | Untouched |
| The iEMR source file (`1091_data.json`) | Source of truth — never modified |
| Shadow mode ground truth | Shadow mode uses the iEMR file directly; unaffected |

---

## Pre-Demo Dry-Run Checklist (Evening of May 4)

Run through every item in this checklist the evening before the demo. If any item
fails, there is time to fix it. Do not skip this — there is no safety net on demo day.

### Patient A (1091)
- [ ] `next_appointment` = 2026-05-05 in patients table
- [ ] `last_visit_date` = 2026-01-06 in clinical_context
- [ ] Inertia alert exists in alerts table (triggered, unacknowledged)
- [ ] Adherence pattern is B in the latest briefing
- [ ] `risk_score` is in the 65–80 range
- [ ] `risk_score_computed_at` is less than 26 hours ago (no staleness badge)
- [ ] Briefing exists with non-null `readable_summary`
- [ ] Sparkline renders in the browser with visible elevated trend
- [ ] White-coat dip visible in the last 4 days of the sparkline
- [ ] Layer 3 summary reads correctly in the BriefingCard component

### Patient B (DEMO_GAP)
- [ ] Gap alert exists in alerts table (9-day gap)
- [ ] Sparkline shows readings ending April 26, blank through May 5
- [ ] risk_tier = medium, no unexpected overrides
- [ ] Briefing generates and renders

### Patient C (DEMO_ADH)
- [ ] Adherence Pattern A alert exists
- [ ] Adherence rate shown as ~58% in briefing
- [ ] Briefing generates and renders

### Patient D (DEMO_EHR)
- [ ] Drug interaction detector implemented and deployed
- [ ] `drug_interactions` field in briefing JSON is non-empty
- [ ] Triple whammy at `concern` severity present
- [ ] Visit agenda position 1 is the drug interaction item
- [ ] Overdue HbA1c present in briefing
- [ ] No sparkline (monitoring_active=FALSE)
- [ ] Layer 3 summary references the interaction

### Dashboard
- [ ] All four patients appear on the dashboard
- [ ] Sort order: High patients first, then Medium, then by risk_score DESC within tier
- [ ] Patient A and D appear in High tier
- [ ] Patient B and C appear in Medium tier
- [ ] No stale risk scores (all computed within 26 hours)

### Critical no-do rules night before and morning of demo
- Do NOT run `run_generator.py` after the time-shift has been applied
- Do NOT re-ingest the FHIR bundle for patient 1091 — this resets `next_appointment`
  and `last_visit_date` back to 2013 values
- Do NOT run `setup_db.py` — this may alter table structure or patient metadata

---

## Scripts

### Patient A time-shift
```bash
conda activate aria
python scripts/timeshift_demo.py --dry-run   # preview rows affected
python scripts/timeshift_demo.py             # execute
python scripts/timeshift_demo.py --revert    # restore original dates if needed
```

### Trigger pattern recompute and briefing (all demo patients)
```bash
# Via admin API — run for each patient ID after setup
POST /api/admin/trigger-scheduler
```

---

## What NOT to Show During the Demo

| Element | Why |
|---|---|
| Processing job queue / admin panel | Looks like infrastructure, not a clinical tool |
| FHIR ingestion flow | Clinical audience does not need to see bundle structure |
| Risk scoring weight breakdown (30/25/20/15/10) | Loses the room — state that scores are auditable, not how they are calculated |
| All 14 Layer 3 validator checks | Sounds defensive — demonstrate one guardrail (prescribing refusal), state the rest exist |
| Raw SQL or detector code | Undermines the clinical framing |
