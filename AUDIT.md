# ARIA System Audit
**Date:** 2026-04-22  
**Author:** Claude Code  
**Scope:** Full system audit comparing production implementation against shadow mode validated behaviour, conversion fidelity, non-HTN visit handling, and hardcoded patient references.  
**Groq/Layer 3 model choice excluded** — intentional design decision by the team.

---

## Investigation 1 — Does ARIA ignore non-HTN visits?

**Short answer: partially, and in ways that matter clinically.**

The adapter correctly captures all active conditions from all 124 iEMR visits into Condition resources — CHF, Diabetes, CAD, PVD, etc. all land in `clinical_context.active_problems`. The briefing composer surfaces them in the "Active Problems" section. This part works.

**What is ignored:**

The physician's clinical assessment of each non-HTN problem per visit is never captured. Each PROBLEM entry in iEMR contains `PROBLEM_STATUS2_FLAG` (1=Red/2=Yellow/3=Green), `PROBLEM_STATUS2` (e.g. "Doing Well"), and `PROBLEM_ASSESSMENT_TEXT` (free text, e.g. "CHF stable, no oedema"). Shadow mode extracts and displays these for non-HTN problems. The production FHIR bundle does not — only the problem name and ICD-10 code survive the conversion. A visit where the physician wrote "CHF worsening, refer cardiology" generates the same DB entry as one where they wrote "CHF stable, continue current plan."

The four Layer 1 detectors are exclusively BP-focused. None of them consider:
- CHF deterioration signals (weight trend, SpO2)
- Diabetes control (no HbA1c tracking)
- Non-HTN medication adherence per condition
- Physician concern level for any non-HTN condition

For a patient whose primary risk driver on any given day is a CHF exacerbation — not BP — ARIA would fire no alert and generate a briefing that says "28-day avg 128 mmHg, stable." The visit agenda would mention CHF as an active problem but give no indication the physician considered it urgent at the last encounter.

---

## Investigation 2 — Conversion fidelity: what is lost?

The iEMR data contains far more clinical information than what survives into the FHIR bundle and the database. The following fields exist in the raw iEMR JSON and are **silently discarded** by the adapter.

### Clinically significant losses

**Physician visit assessments (PROBLEM level):**
- `PROBLEM_STATUS2_FLAG` — physician severity rating (1/2/3) per problem per visit. Lost entirely. Shadow mode proved this is the primary ground-truth signal for clinical agreement.
- `PROBLEM_STATUS2` — status text ("Doing Well", "Under Evaluation", etc.) per problem per visit. Lost.
- `PROBLEM_ASSESSMENT_TEXT` — free-text physician note per problem per visit. Lost.
- `PROBLEM_SEVERITY_FLAG`, `PROBLEM_NOTE`, `PROBLEM_COMMENT` — additional clinical context. Lost.
- `PROBLEM_ONSET_DATE`, `PROBLEM_LAST_MODIFIED_DATE` — when each problem was added and last changed. Lost.

**Vitals beyond BP:**
Every clinic visit with BP also has PULSE recorded. Many visits have WEIGHT, TEMPERATURE, and PULSEOXYGEN. None of these are captured in the FHIR bundle. Specific examples of what is lost:

- **PULSEOXYGEN (SpO2):** On November 21, 2011, this patient had SpO2 of **84%** — a potentially life-threatening finding for a CHF patient. This is completely absent from the database. SpO2 was 84 in November and 93 in December 2011. This trajectory is significant and invisible to ARIA.
- **WEIGHT:** Patient weighed 170 lbs in July 2010 and 158 lbs in September 2011 — a 12-pound loss over 14 months. For a CHF patient, unexpected weight loss of this magnitude is clinically significant. Completely lost.
- **PULSE:** All 51 BP clinic visits record the patient's pulse rate (ranging 60–82 bpm). Lost. Directly relevant to metoprolol dosing and arrhythmia surveillance.
- **TEMPERATURE:** Intermittently recorded. Lost.

**Physical examination and review of systems:**
- `EXAM_TEXT` — full physical examination narrative ("Neck - No carotid bruits, No JVD. Heart - Normal RRR..."). Present on 14+ visits. Lost. For a CHF patient, JVD and heart sounds are primary assessment findings.
- `ROS_TEXT` — review of systems ("Negative for Night Sweats, Weight Loss, Fatigue..."). Lost.
- `VISIT_TEXT`, `VISIT_TEXT_GENERATED` — free-text visit notes. Lost.

**Allergy detail:**
- `ALLERGY_REACTION` — the specific reaction to each allergen is not captured. The DB stores "SIMVASTATIN" as an allergy but not that the reaction was "MYALGIAS", or that "BYETTA" caused "Generalized Bad Feeling and Feeling of Dread." These are clinically important for prescribing decisions.
- `ALLERGY_STATUS` — the adapter captures all allergy entries without checking `ALLERGY_STATUS == "Active"`. Inactive allergies could be included.

**Social and contextual data:**
- `SOCIAL_HX` — Social history exists in the iEMR but is not mapped. The `social_context` column exists in the DB schema and the ORM model but is never populated by the ingestion pipeline. It is always NULL.
- `IMMUNE_HX` — Immunization history. Not captured.
- `PATIENT_INSTRUCTIONS_TEXT` — What the patient was told at each visit. Not captured.

**Plan/follow-up detail:**
- `PLAN_FINDINGS_TEXT`, `PLAN_ADJUD_TEXT`, `PLAN_FINDINGS_COMMENT_TEXT` — Test results and adjudication notes. Lost.
- `PLAN_STATUS`, `PLAN_TYPE` — Whether a plan item is completed or pending. Lost. ServiceRequests are inserted as `status: "active"` regardless.

### Non-clinical losses (lower priority)

- `VISIT_TYPE` — visit category (in-person, phone, annual physical, etc.). Not used anywhere in the pipeline. Shadow mode uses it for context but the production system is blind to whether a visit was a phone call or a face-to-face encounter.
- `DISCH_DATE` — discharge time (same-day for clinic visits). Not used.
- `PATIENT_RX_TEXT` — prescription text given to patient. Not used.

---

## Investigation 3 — Hardcoded patient references

### In scripts (operational, not tests)

| File | Line | Hardcoded value | Impact |
|---|---|---|---|
| `scripts/run_shadow_mode.py` | 60 | `PATIENT_ID = "1091"` | Script only works for patient 1091. Cannot validate a second patient without code change. |
| `scripts/run_shadow_mode.py` | 62 | `IEMR_PATH = .../1091_data.json` | Same — IEMR file path hardcoded. |
| `scripts/run_ingestion.py` | 73 | Default `--bundle` = `1091_bundle.json` | Second patient requires explicit flag; first patient runs without argument. Inconsistency for multi-patient operation. |
| `scripts/run_pipeline_tests.py` | 37 | `_DEMO_PATIENT = "1091"` | Pipeline tests run against only this patient. |
| `scripts/reset_demo.py` | 37 | `_DEMO_PATIENT_ID = "1091"` | Demo reset only resets one patient. Acceptable for demo script but will need updating for multi-patient demo. |

`run_adapter.py` and `run_generator.py` already accept `--patient` arguments and are clean. The shadow mode and pipeline test scripts must accept a `--patient` argument to be usable for any patient beyond 1091.

### In backend source code (not tests)

All references to "1091" in backend source are in docstring examples — not in runtime logic. The services themselves take `patient_id: str` as a parameter and are not hardcoded. The backend is clean.

---

## Critical — These will cause wrong clinical output in production

### 1. Production inertia detector uses hard-coded 140 mmHg threshold with no slope check
**File:** `backend/app/services/pattern_engine/inertia_detector.py` line 32

Hard-coded population threshold (`_ELEVATED_THRESHOLD = 140`) applies the same standard to all patients regardless of their personal BP history. A patient whose physician has accepted 135 mmHg as their stable level triggers inertia at 141. Shadow mode demonstrated the correct approach is patient-adaptive: `max(130, stable_baseline_mean + 1.5×SD)` capped at 145.

Additionally, there is no slope direction check and no recent 7-day average check. The production detector fires even when BP is actively declining — the exact false-positive category that shadow mode identified and fixed.

---

### 2. Production deterioration detector has no threshold gate
**File:** `backend/app/services/pattern_engine/deterioration_detector.py` line 150

`deterioration = slope > 0.0 and recent_avg > baseline_avg`

Fires on any positive slope regardless of absolute BP level. A patient whose BP rises from 115 to 119 over 14 days triggers a DET alert. Shadow mode requires `recent_avg >= patient_threshold` as a necessary gate. Without it, well-controlled patients will generate false deterioration alerts whenever readings drift slightly upward within normal range.

---

### 3. Production adherence analyzer has no treatment-working suppression
**File:** `backend/app/services/pattern_engine/adherence_analyzer.py` line 110

Pattern B fires whenever avg_sys ≥ 140 AND adherence ≥ 80%, with no slope check. Shadow mode proved this is a false positive on declining trajectories. A patient whose BP is actively falling from 170 to 135 over 28 days will still trigger Pattern B because the 28-day window mean is above 140, even though treatment is visibly working. The production analyzer fires "possible treatment review" on exactly the patients whose treatment is succeeding.

---

### 4. Production inertia detector ignores `med_history` — uses stale single-date `last_med_change`
**File:** `backend/app/services/pattern_engine/inertia_detector.py` line 132

`clinical_context.last_med_change` is set at ingestion time from the most recent `authoredOn` across all MedicationRequest resources. Shadow mode revealed the iEMR has 26 phone refill calls and in-person visits that changed medications between clinic dates — all captured correctly in `clinical_context.med_history` (JSONB), but `inertia_detector.py` never reads it. A patient who had a Lasix refill via phone call 5 days ago, where the DB shows `last_med_change = 2013-09-01`, would trigger inertia even though their medication changed last week.

---

### 5. Clinically significant vitals lost in conversion
**File:** `backend/app/services/fhir/adapter.py` — `_build_observations()`

Only SYSTOLIC_BP and DIASTOLIC_BP are extracted. PULSE, WEIGHT, TEMPERATURE, and PULSEOXYGEN are silently discarded. For a CHF patient like 1091, a recorded SpO2 of 84% at a clinic visit and a 12-pound weight loss over 14 months are clinically important signals that ARIA is completely blind to. These fields cannot be used in any briefing, alert, or risk scoring because they never reach the database.

---

## High — Significant gaps affecting correctness or completeness

### 6. Physician problem assessments lost in conversion — non-HTN visits invisible to detectors
**File:** `backend/app/services/fhir/adapter.py` — `_build_conditions()`

Only the ICD-10 code and problem name are captured. `PROBLEM_STATUS2_FLAG`, `PROBLEM_STATUS2`, and `PROBLEM_ASSESSMENT_TEXT` are not mapped to any FHIR field and are lost. The DB has no record of the physician's assessment of any condition at any visit. The briefing shows "CHF" in the active problems list but gives no indication whether the physician last assessed it as "Doing Well" or "Under Evaluation."

All four Layer 1 detectors analyze BP readings and medication confirmations only. They do not trigger on non-HTN clinical deterioration.

---

### 7. `social_context` column exists in schema but is never populated
**Files:** `backend/app/services/fhir/adapter.py`, `backend/app/services/fhir/ingestion.py`

`SOCIAL_HX` data exists in the iEMR. The `clinical_context.social_context` column exists in the ORM model and DB schema. The adapter has no `_build_social_context()` function and the ingestion pipeline never sets this field. It is always NULL. The briefing composer also never includes it even when set.

---

### 8. Allergy reactions and active-status not captured
**File:** `backend/app/services/fhir/adapter.py` — `_build_allergy_intolerances()`

Only the allergen name is stored. `ALLERGY_REACTION` (e.g. "MYALGIAS", "LEG CRAMPS", "Generalized Bad Feeling") is discarded. A prescriber sees "SIMVASTATIN" as an allergy but not that the reaction was myalgias — which has different prescribing implications than a rash or anaphylaxis. Additionally, `ALLERGY_STATUS` is not checked, so inactive allergies could appear in the briefing.

---

### 9. No scheduled `pattern_recompute` sweep for all active patients
**File:** `backend/app/services/worker/scheduler.py`

The 7:30 AM scheduler only enqueues `briefing_generation` for appointment-day patients. There is no daily or continuous sweep that runs `pattern_recompute` for all `monitoring_active` patients. Risk scores, alerts, and gap flags are only updated when manually triggered via the admin endpoint. In production, a patient's gap counter increases daily but the alert is never written unless someone manually enqueues a pattern_recompute job.

---

### 10. Adherence alert type not written to the alerts table
**File:** `backend/app/services/worker/processor.py` line 197

When adherence Pattern A fires (high BP + low adherence), no Alert row is written. Gap, deterioration, and inertia all write alerts. The briefing's `urgent_flags` section pulls from unacknowledged Alert rows — so an adherence concern never appears as an urgent flag in the briefing even when it is the primary clinical signal.

---

### 11. `last_visit_date` misses all 71 non-vitals visits
**File:** `backend/app/services/fhir/ingestion.py` line 344

`last_visit_date` is set from the last BP Observation's `effectiveDateTime`. For patient 1091, there are 71 visits with no BP reading (phone calls, in-person without vitals). If the most recent contact was a phone refill call, `last_visit_date` would show the date of the last in-person BP measurement — not the actual last contact date.

---

### 12. Shadow mode hardcoded to patient 1091 — cannot validate any other patient
**File:** `scripts/run_shadow_mode.py` lines 60–62

`PATIENT_ID = "1091"` and `IEMR_PATH = .../1091_data.json` are constants, not CLI arguments. Once additional patients are added, shadow mode validation cannot be run against them without modifying source code.

---

### 13. Only one patient in the database
There is only one patient in the system (1091). The dashboard sort, alert inbox, tier filtering, and risk score comparison are all functionally untested with a real multi-patient dataset.

---

## Medium — Gaps affecting demo quality or reliability

### 14. Briefing composer re-implements inertia logic independently with wrong threshold
**File:** `backend/app/services/briefing/composer.py` line 358

The visit agenda's "Review treatment plan" item checks `avg_sys >= 140 AND days_since_med_change > 7` — independent of the Layer 1 inertia detector output, and with the same hard-coded threshold problem as item 1. The composer should consume Layer 1 detector output rather than re-implementing the check.

---

### 15. Reading generator only supports a single fixed scenario
**File:** `backend/app/services/generator/reading_generator.py` line 361

Only `scenario="patient_a"` is supported. Generation is hardcoded to a baseline of ~163 mmHg. For any second patient with different clinic BP anchors, the generator must be extended with a parametric baseline from the patient's own `historic_bp_systolic`. Adding more patients is currently blocked by this.

---

### 16. Briefing appointment date parsed from idempotency key rather than patient record
**File:** `backend/app/services/worker/processor.py` line 247

`date_str = job.idempotency_key[-10:]` — the appointment date is today's date (set by the scheduler when it enqueues the job). If the admin trigger is used on a day other than the patient's actual appointment date, the briefing is recorded with the wrong `appointment_date`. The composer should read `patient.next_appointment.date()`.

---

### 17. `next_appointment` has no update mechanism
After a patient's appointment passes, `next_appointment` is not updated. The scheduler will never fire for them again. `reset_demo.py` manually patches this but only for patient 1091.

---

### 18. Readings ingestion uses batch-level idempotency
**File:** `backend/app/services/fhir/ingestion.py` line 383

If any clinic readings exist for a patient, the entire batch is skipped. Adding a new clinic visit to an existing patient's record requires manually deleting all existing clinic readings first.

---

### 19. PatientList shows briefing icon only for today's appointments
**File:** `frontend/src/components/dashboard/PatientList.tsx` line 69

The `FileText` icon is active only when `isToday(patient.next_appointment)`. A clinician reviewing the next day's schedule sees no briefing indicator even if briefings exist for those patients.

---

### 20. Alert API has no patient_id filter
**File:** `backend/app/api/alerts.py` line 25

`GET /api/alerts` returns all unacknowledged alerts system-wide. With multiple patients, this is a firehose. No `?patient_id=` filter exists.

---

### 21. Comorbidity risk score signal saturates far too early
**File:** `backend/app/services/pattern_engine/risk_scorer.py` line 161

`sig_comorbidity = clamp(problem_count / 5.0 × 100)` — any patient with 5+ problems scores 100 on this signal. Patient 1091 has 17 coded problems. Every complex patient maxes out this signal, making it useless for differentiation.

---

## Low — Polish and minor issues

### 22. `social_context` never surfaced in briefing even when populated
**File:** `backend/app/services/briefing/composer.py`

The column exists, the ORM model exposes it, but `compose_briefing()` never includes it in any briefing field.

### 23. `delivered_at` on alerts is never set
**Files:** `backend/app/services/worker/processor.py`, `backend/app/api/alerts.py`

Always NULL despite being in the schema and serializer.

### 24. Frontend and backend both sort the patient list independently
**Files:** `backend/app/api/patients.py`, `frontend/src/components/dashboard/PatientList.tsx`

Duplicate logic — if they ever diverge the sort order changes silently.

### 25. `run_ingestion.py` default bundle path hardcoded to 1091
**File:** `scripts/run_ingestion.py` line 73

Default `--bundle` argument is `data/fhir/bundles/1091_bundle.json`. With multiple patients, there is no neutral default.

---

## Summary Table

| # | Category | Severity | File |
|---|---|---|---|
| 1 | Inertia: hard-coded 140 threshold, no slope check | Critical | inertia_detector.py |
| 2 | Deterioration: no threshold gate | Critical | deterioration_detector.py |
| 3 | Adherence: no treatment-working suppression | Critical | adherence_analyzer.py |
| 4 | Inertia: ignores med_history, uses stale last_med_change | Critical | inertia_detector.py |
| 5 | PULSE/WEIGHT/SpO2/TEMPERATURE lost in conversion | Critical | adapter.py |
| 6 | Physician problem assessments (flag/status/text) lost — non-HTN invisible | High | adapter.py |
| 7 | social_context column never populated | High | adapter.py / ingestion.py |
| 8 | Allergy reactions and active-status not captured | High | adapter.py |
| 9 | No scheduled pattern_recompute sweep | High | scheduler.py |
| 10 | Adherence alert not written to DB | High | processor.py |
| 11 | last_visit_date misses 71 non-vitals visits | High | ingestion.py |
| 12 | Shadow mode hardcoded to patient 1091 | High | run_shadow_mode.py |
| 13 | Only one patient in the database | High | data |
| 14 | Briefing composer re-implements inertia with wrong threshold | Medium | composer.py |
| 15 | Reading generator: only one fixed scenario, can't support new patients | Medium | reading_generator.py |
| 16 | Briefing appointment date from idempotency key, not patient record | Medium | processor.py |
| 17 | No next_appointment update mechanism | Medium | patients API |
| 18 | Readings batch-level idempotency blocks adding new clinic visits | Medium | ingestion.py |
| 19 | Briefing icon reflects today-only, not briefing existence | Medium | PatientList.tsx |
| 20 | Alert API has no patient_id filter | Medium | alerts.py |
| 21 | Comorbidity score saturates at 5 problems | Medium | risk_scorer.py |
| 22 | social_context never used in briefing | Low | composer.py |
| 23 | delivered_at never set on alerts | Low | processor.py / alerts.py |
| 24 | Double sort (backend + frontend) | Low | PatientList.tsx |
| 25 | run_ingestion.py default bundle hardcoded to 1091 | Low | run_ingestion.py |

The five critical items (1–5) affect clinical output correctness in live production. Items 6–8 represent meaningful data that already exists in the iEMR but is silently discarded at conversion time. Items 9–13 are operational gaps that would prevent the system from functioning correctly beyond a single-patient, single-day demo.
