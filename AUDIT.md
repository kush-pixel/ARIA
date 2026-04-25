# ARIA System Audit
**Date:** 2026-04-23 (revised from 2026-04-22)
**Verified by:** Code review of `inertia_detector.py`, `deterioration_detector.py`, `adherence_analyzer.py`, `risk_scorer.py`, `processor.py`, `scheduler.py`, `composer.py`, `adapter.py`, `ingestion.py`, `alerts.py`, `PatientList.tsx`
**Scope:** Production implementation correctness, conversion fidelity, non-HTN visit handling, hardcoded references, detection logic gaps, architectural limitations, and phased fix roadmap.

---

## Verification Notes

All 25 original items verified against production code. Two corrections to the original text:
- **Item 5 body** — "51 BP clinic visits" corrected to 53 unique clinic dates (65 raw rows, 53 unique dates after deduplication).
- **Item 4 body** — "26 phone refill calls" was not verifiable from the codebase; restated as approximate.

One item reclassified:
- **Original Critical item 5** (PULSE/WEIGHT/SpO2 lost) → **High**. The original four Critical items produce incorrect alert output on existing data today using existing schema. SpO2 and weight require new DB columns and a re-ingestion pass before any detector can act on them — making them a Phase 1 ingestion fix, not a standalone output correctness fix.

One Critical item missing from the original audit added:
- **New item 5** — Comorbidity-adjusted threshold validated at 94.3% in shadow mode was never ported to the production pipeline.

---

## Investigation 1 — Does ARIA ignore non-HTN visits?

**VERIFIED CORRECT.**

The adapter correctly captures problem names and ICD-10 codes from all visits into Condition resources. Active problems land in `clinical_context.active_problems`. The briefing surfaces them under "Active Problems." This part works.

What is ignored: `PROBLEM_STATUS2_FLAG` (1=Red/2=Yellow/3=Green), `PROBLEM_STATUS2` (status text), and `PROBLEM_ASSESSMENT_TEXT` (free-text physician note) are extracted and used by shadow mode but never captured by the production adapter. A visit where the physician wrote "CHF worsening, refer cardiology" produces the same DB entry as "CHF stable, continue current plan."

All four Layer 1 detectors are exclusively BP-focused. A patient whose primary risk driver on a given day is a CHF exacerbation — not BP — generates no alert and a briefing that says "28-day avg 128 mmHg, stable." The visit agenda mentions CHF as an active problem but gives no indication the physician considered it urgent at the last encounter.

---

## Investigation 2 — Conversion fidelity: what is lost?

**VERIFIED CORRECT** with the two corrections noted above.

**Physician visit assessments (PROBLEM level):** `PROBLEM_STATUS2_FLAG`, `PROBLEM_STATUS2`, `PROBLEM_ASSESSMENT_TEXT`, `PROBLEM_SEVERITY_FLAG`, `PROBLEM_NOTE`, `PROBLEM_COMMENT`, `PROBLEM_ONSET_DATE`, `PROBLEM_LAST_MODIFIED_DATE` — all lost.

**Vitals beyond BP:** PULSE (present on all 53 BP visits, relevant to beta-blocker dosing), WEIGHT (including a 12 lb loss over 14 months for this CHF patient), TEMPERATURE, and PULSEOXYGEN (SpO2 84% on November 21 2011 — potentially life-threatening for a CHF patient) — all silently discarded. Adapter `_build_observations()` extracts only `SYSTOLIC_BP` and `DIASTOLIC_BP`.

**Physical examination:** `EXAM_TEXT` (full PE narrative, present on 14+ visits), `ROS_TEXT`, `VISIT_TEXT`, `VISIT_TEXT_GENERATED` — lost.

**Allergy detail:** `ALLERGY_REACTION` (e.g., "MYALGIAS" vs "ANAPHYLAXIS") not captured. `ALLERGY_STATUS` not checked — inactive allergies may appear in the briefing.

**Social context:** `SOCIAL_HX` exists in iEMR. The `clinical_context.social_context` column exists in the schema and ORM model but is never set by the ingestion pipeline — always NULL.

**Plan detail:** `PLAN_FINDINGS_TEXT`, `PLAN_STATUS`, `PLAN_TYPE` lost. All `ServiceRequest` resources inserted with `status: "active"` regardless of actual plan status.

**Visit type:** `VISIT_TYPE` not captured anywhere in the production pipeline. System cannot distinguish phone calls from in-person encounters.

---

## Investigation 3 — Hardcoded patient references

**VERIFIED CORRECT.**

| File | Hardcoded value | Impact |
|---|---|---|
| `scripts/run_shadow_mode.py` | `PATIENT_ID = "1091"`, `IEMR_PATH = .../1091_data.json` | Cannot validate any other patient without code change |
| `scripts/run_ingestion.py` | Default `--bundle = 1091_bundle.json` | Inconsistency for multi-patient operation |
| `scripts/run_pipeline_tests.py` | `_DEMO_PATIENT = "1091"` | Pipeline tests single-patient only |
| `scripts/reset_demo.py` | `_DEMO_PATIENT_ID = "1091"` | Demo reset single-patient only |

Backend source code is clean — all services accept `patient_id: str` as a parameter. Only scripts are affected.

---

## Critical — Wrong clinical output in production today

### 1. Inertia detector: hard-coded 140 mmHg threshold, no slope check
**File:** `backend/app/services/pattern_engine/inertia_detector.py` line 32
**Verified:** `_ELEVATED_THRESHOLD = 140`

Hard-coded population threshold applies to all patients regardless of personal BP history. A patient whose physician has accepted 135 mmHg as stable triggers inertia at 141. No slope direction check and no recent 7-day average check — fires even when BP is actively declining, which was the primary false-positive category in shadow mode.

**Fix:** Replace `_ELEVATED_THRESHOLD` with the patient-adaptive threshold proven in shadow mode: `max(130, stable_baseline_mean + 1.5×SD)` capped at 145 mmHg, computed from `clinical_context.historic_bp_systolic` filtered to physician-labeled stable visits. Add a slope direction check: if the 7-day recent average is already below the threshold, do not fire even if the 28-day mean exceeds it. Do this fix together with Fix 4 — both are in the same file.

---

### 2. Deterioration detector: no absolute threshold gate
**File:** `backend/app/services/pattern_engine/deterioration_detector.py` line 150
**Verified:** `deterioration = slope > 0.0 and recent_avg > baseline_avg`

Fires on any positive slope regardless of absolute BP level. A patient rising from 115 to 119 over 14 days triggers a deterioration alert. Shadow mode requires `recent_avg >= patient_threshold` as a necessary third gate.

**Fix:** Add `and recent_avg >= patient_threshold` to the deterioration condition. Derive `patient_threshold` the same way as Fix 1. If historic data is insufficient, fall back to 140. Additionally, add a step-change sub-detector: compare the 7-day rolling mean of the most recent week against the 7-day rolling mean of three weeks ago — if the step exceeds 15 mmHg, flag deterioration regardless of overall linear slope. This catches acute step-changes that 14-day regression smooths over.

---

### 3. Adherence analyzer: no treatment-working suppression
**File:** `backend/app/services/pattern_engine/adherence_analyzer.py` line 110
**Verified:** Pattern B fires on `high_bp and not low_adherence` with no slope or trajectory check.

A patient whose BP is actively falling from 170 to 135 triggers Pattern B ("possible treatment review warranted") because the 28-day window mean is above 140. This fires on exactly the patients whose treatment is succeeding.

**Fix:** After pattern classification, add a suppression block for Pattern B. Compute the slope over the adaptive window (Fix 28) and the 7-day recent average. If `slope < -0.3 AND recent_7day_avg < patient_threshold AND days_since_med_change <= titration_window`: suppress Pattern B to `"none"` with interpretation "treatment appears effective — monitoring." Rather than a blanket 42-day gate, derive `titration_window` from the most recently changed drug's class using a `TITRATION_WINDOWS` lookup dict: diuretics → 14 days, beta-blockers → 14 days, ACE inhibitors / ARBs → 28 days, long-acting CCBs (amlodipine) → 56 days, default → 42 days. Since `med_history` already has medication names and the `-olol`, `-pril`, `-sartan`, `-dipine` suffix patterns already exist in the codebase (llm_validator.py), class inference is a small additional function. A drug-class-aware window reduces both false positives (suppressing too long on fast-acting drugs) and false negatives (releasing too early on amlodipine). Suppression must not apply when no recent change occurred (root cause of the eliminated false negative in shadow mode: a persistently-elevated slightly-negative-slope patient with no recent change is NOT a succeeding treatment). Do Fix 3 before Fix 11 (writing adherence alerts) so suppression is in place before alerts start writing.

---

### 4. Inertia detector: ignores `med_history`, uses stale single-date `last_med_change`
**File:** `backend/app/services/pattern_engine/inertia_detector.py` line 132
**Verified:** `select(ClinicalContext.last_med_change)` — reads only the ingestion-time snapshot.

`clinical_context.last_med_change` is the most recent `authoredOn` across all `MedicationRequest` resources at ingestion time. Phone refill calls and in-person visits that changed medications between clinic dates — all captured in `clinical_context.med_history` JSONB — are invisible to the inertia detector. A patient who had a diuretic refill via phone call 5 days ago would still trigger inertia.

**Fix (core — required):** Replace the `last_med_change` query with a query that reads `ClinicalContext.med_history` JSONB and finds the most recent `date` field across all entries where `date <= first_elevated_reading_date`. Mirrors `_get_last_med_change_at(timeline, cutoff_date)` from shadow mode. The `activity` field already distinguishes add/remove/modify at ingestion time — use `activity` in the gate (any add or modify within the last 42 days counts as a recent change; remove is treated as no recent change unless paired with an add). Apply the identical fix to `risk_scorer.py`'s `_days_since_med_change()` helper — it also reads `ClinicalContext.last_med_change` directly, so the Layer 2 inertia signal carries the same staleness bug.

**Fix (nice-to-have — deferred):** Parsing dose direction (increase vs decrease) from the `MED_DOSE` free-text field is achievable without a free-text parser: when the same drug appears in consecutive `med_history` entries, compare the normalised numeric dose value. If the new entry has a higher value, it is an increase; lower is a decrease. This is more reliable than parsing strings like "25 MG", "25MG DAILY", "25mg bid", "TITRATE 12.5→25". Defer behind a dedicated `dose_parser.py` module. Do not gate core inertia logic on this. Implement the core fix together with Fix 1 since both touch `inertia_detector.py`.

---

### 5. Comorbidity-adjusted threshold not applied in production
**File:** All four detector files under `backend/app/services/pattern_engine/`
**Status:** NEW — missing from original audit.

Shadow mode validated at 94.3% agreement (33/35, 0 false negatives, 2 false positives) that when cardiovascular and metabolic comorbidities are simultaneously in elevated concern state, lowering the BP threshold by 7 mmHg (floor 130 mmHg) improves clinical agreement. This logic lives only in `scripts/run_shadow_mode.py`. The production detectors use a static threshold regardless of comorbidity state.

**Fix:** Create `backend/app/services/pattern_engine/threshold_utils.py` containing `classify_comorbidity_concern(active_problems, problem_assessments)` and `apply_comorbidity_adjustment(base_threshold, cardio_concern, metabolic_concern)` ported from shadow mode. Each detector calls these functions and uses the adjusted threshold.

Two modes, explicitly labeled:
- **Full mode (post Fix 7):** `concern_state` derives from the most recent `PROBLEM_STATUS2_FLAG` per problem in `clinical_context.problem_assessments`. Red/Yellow → elevated concern; Green → stable. This is what shadow mode validated at 94.3%.
- **Degraded mode (pre Fix 7, Phase 2 interim):** `concern_state` falls back to "presence implies elevated concern" using `clinical_context.active_problems` codes alone. Log `threshold_adjustment_mode = "degraded_no_assessments"` on every adjusted threshold computation so downstream reports can distinguish the two modes. Shadow mode agreement is expected to be lower than 94.3% in degraded mode; re-run shadow mode after Fix 7 lands and confirm full-mode agreement recovers.

Also broaden the adjustment trigger: lower the threshold by 7 mmHg when either (a) cardio AND metabolic both in elevated concern (original rule), or (b) any single comorbidity with SEVERE weight in Fix 25 (CHF/Stroke/TIA) is in elevated concern. Rule (b) covers CHF-only and stroke-only patients whom the original rule missed. Floor remains 130 mmHg.

---

## High — Significant gaps affecting clinical correctness or completeness

### 6. PULSE/WEIGHT/SpO2/TEMPERATURE lost in conversion
**File:** `backend/app/services/fhir/adapter.py` — `_build_observations()`
**Verified:** Only `SYSTOLIC_BP` and `DIASTOLIC_BP` extracted from VITALS.

SpO2 of 84% on November 21 2011 is a potentially life-threatening finding for a CHF patient — completely absent from the database. A 12 lb weight loss over 14 months is clinically significant for a CHF patient and invisible to ARIA. Pulse on all 53 BP visits is directly relevant to beta-blocker dosing and arrhythmia surveillance.

**Fix:** Add LOINC-coded Observation resources in `_build_observations()` for PULSE (8867-4), WEIGHT (29463-7), SpO2 (59408-5), and TEMPERATURE (8310-5) alongside existing BP Observations. Add `last_clinic_pulse`, `last_clinic_weight_kg`, `last_clinic_spo2`, and `historic_spo2` columns to `clinical_context` via DB migration. Update `ingestion.py` to extract and store these values. Add an SpO2 < 92% check as a new alert type in the briefing layer for patients with CHF in `problem_codes`.

---

### 7. Physician problem assessments lost — clinical concern state invisible
**File:** `backend/app/services/fhir/adapter.py` — `_build_conditions()`
**Verified:** Only ICD-10 code and problem name captured. `PROBLEM_STATUS2_FLAG`, `PROBLEM_STATUS2`, `PROBLEM_ASSESSMENT_TEXT` discarded.

**Fix:** Add non-standard bundle key `_aria_problem_assessments` in the adapter (parallel to `_aria_med_history`). Collect per-visit `{problem_code, visit_date, htn_flag, status_text, assessment_text}` for all problems across all visits. Add `clinical_context.problem_assessments` JSONB column. Ingestion stores it. Briefing composer surfaces the most recent assessment per problem: "CHF — last assessed: Under Evaluation (2026-01-14)." This is exactly how shadow mode uses the data — the pattern is already proven.

---

### 8. `social_context` column exists but is never populated
**Files:** `backend/app/services/fhir/adapter.py`, `backend/app/services/fhir/ingestion.py`
**Verified:** `cc_values` dict in `ingestion.py` has no `social_context` key. Field is always NULL.

**Fix:** Add `_build_social_context(visits)` to `adapter.py` that joins `SOCIAL_HX` text entries across all visits into a structured string. Add `social_context` to the `cc_values` dict in `ingestion.py`. Update `composer.py` to include it in the briefing payload when non-null.

---

### 9. Allergy reactions and active-status not captured
**File:** `backend/app/services/fhir/adapter.py` — `_build_allergy_intolerances()`
**Verified:** Only `a.get("code", {}).get("text", "")` stored. Reaction type and status discarded.

A prescriber sees "SIMVASTATIN" as an allergy but not that the reaction was myalgias — which has different prescribing implications than anaphylaxis. Inactive allergies may appear in the briefing because `ALLERGY_STATUS` is not checked.

**Fix:** Filter on `ALLERGY_STATUS == "Active"` before building each resource. Add `reaction[0].manifestation[0].text` from `ALLERGY_REACTION` to the `AllergyIntolerance` resource. Update `ingestion.py` to store reactions in a parallel `clinical_context.allergy_reactions` array alongside `allergies`.

---

### 10. No scheduled `pattern_recompute` sweep for all active patients
**File:** `backend/app/services/worker/scheduler.py`
**Verified:** `enqueue_briefing_jobs()` only queries appointment-day patients. No continuous recompute for other patients.

In production, a patient's gap counter increases daily but no alert is written unless someone manually triggers a `pattern_recompute`. Risk scores, inertia flags, and deterioration flags are stale for all non-appointment-day patients.

**Fix:** Add `enqueue_pattern_recompute_sweep()` to `scheduler.py`. Query all `monitoring_active=TRUE` patients. Enqueue a `pattern_recompute` job for each using idempotency key `pattern_recompute:{patient_id}:{YYYY-MM-DD}`. Schedule via APScheduler at midnight UTC daily. Re-runs are safe via `ON CONFLICT DO NOTHING`.

---

### 11. Adherence alert not written to the alerts table
**File:** `backend/app/services/worker/processor.py` lines 197–205
**Verified:** `_upsert_alert` called for gap, inertia, deterioration — no call for adherence Pattern A.

Adherence Pattern A (`high BP + low adherence`) is the primary clinical signal but never appears as an unacknowledged alert in the alert inbox. The briefing's `urgent_flags` field pulls from unacknowledged Alert rows — so an adherence concern is invisible there even when it is the most important signal.

**Fix:** After computing `adherence` in `_handle_pattern_recompute`, add: `if adherence["pattern"] == "A": await _upsert_alert(session, pid, "adherence")`. Add `"adherence"` to the alert_type enum in the Alert model. Update `_build_urgent_flags()` in `composer.py` to handle the new type. Prerequisite: Fix 3 (treatment-working suppression) must be applied first so Pattern B suppression is in place before adherence alerts start writing.

---

### 12. `last_visit_date` misses 71 non-vitals visits
**File:** `backend/app/services/fhir/ingestion.py` line 344
**Verified:** `last_visit_date = eff_dt.date()` set inside the Observation loop — only BP clinic dates considered.

If the patient's most recent contact was a phone refill call, `last_visit_date` shows the date of the last in-person BP measurement, not the actual last contact. This affects gap detection thresholds and the inertia detector's duration calculation.

**Fix:** Add `_aria_visit_dates` as a non-standard bundle key in the adapter — a list of `ADMIT_DATE` values from all 124 visits regardless of type. Ingestion reads this and sets `last_visit_date = max(all_visit_dates)`. Run together with all other Phase 1 adapter changes.

---

### 13. Shadow mode hardcoded to patient 1091
**File:** `scripts/run_shadow_mode.py` lines 60–62
**Verified:** `PATIENT_ID = "1091"` and `IEMR_PATH` are module-level constants.

**Fix:** Convert to `argparse` CLI arguments with 1091 as default: `python scripts/run_shadow_mode.py --patient 2045 --iemr data/raw/iemr/2045_data.json`. Same pattern already used by `run_adapter.py` and `run_generator.py`.

---

### 14. Only one patient in the database
**Status:** Operational gap for multi-patient validation.

Dashboard sort, alert inbox, tier filtering, and risk score comparison are all untested with a real multi-patient dataset.

**Fix:** Run the full pipeline for at least two additional patients after Fix 13 is applied. Shadow mode validation against additional patients verifies generalizability of the 94.3% agreement rate.

---

### 15. Full care timeline synthetic readings and confirmations not generated
**Files:** `backend/app/services/generator/reading_generator.py`, `backend/app/services/generator/confirmation_generator.py`
**Status:** NEW — not in original audit.

The generator produces 28 days of synthetic readings and confirmations anchored on the two most recent clinic BPs. For patient 1091 with 11 years of clinic history, the detectors only see a narrow recent window. Shadow mode validates on inter-visit synthetic readings spanning the full timeline — production has no equivalent. The adaptive window (Fix 28), long-term trend layer (Fix 47), and patient-adaptive threshold (Fix 1) all benefit from a full-timeline reading history. The adherence detector similarly needs full-timeline confirmation history: a patient who missed doses consistently two years ago but is now adherent reads very differently from one with no confirmation history.

**Fix — BP readings:** Add `generate_full_timeline_readings(clinic_readings)` to `reading_generator.py`. For each consecutive pair of clinic readings, generate daily synthetic readings by linearly interpolating between the two BP anchors with Gaussian noise (SD=8–12 mmHg), morning/evening variation (morning 5–10 mmHg higher), device outage episodes (1–2 per inter-visit gap, 2–4 days each), and a white-coat dip in the 3 days before each clinic visit. Store with `source='generated'`, `submitted_by='generator'`. Add generator-level idempotency: skip intervals where generated readings already exist.

**Fix — Medication confirmations:** Add `generate_full_timeline_confirmations(clinic_readings, med_history)` to `confirmation_generator.py`. For each inter-visit interval, generate synthetic daily medication confirmations for every medication active during that interval (derived from `clinical_context.med_history` timeline). Adherence rate per medication should vary realistically across intervals: draw a per-interval adherence rate from a Beta distribution anchored near the patient's known overall adherence (e.g. 91% for patient 1091), with interval-to-interval variation of ±10–15 percentage points. Store with `confirmation_type='synthetic_demo'`. Add idempotency: skip intervals where confirmations for that medication already exist (unique on `patient_id, medication_name, scheduled_time`).

Both generators must be called together from `run_generator.py` and produce records spanning the patient's entire care history — not just the last 28 days. **Prerequisite:** Fix 22 (per-observation idempotency) must be applied first.

---

### 16. Lab values not ingested
**Status:** NEW — not in original audit.

`clinical_context.overdue_labs` is a text list of lab names with no actual values. Creatinine/eGFR (ACE inhibitor and ARB safety for this patient), potassium (diuretic monitoring — the patient is on Lasix, and the physician's note on Dec 22 2011 specifically mentions "leg cramps" which is a classic hypokalemia symptom), and HbA1c (diabetic comorbidity) are never captured even when available in the EHR.

**Fix:** Add FHIR Observation ingestion for lab LOINC codes: creatinine (2160-0), potassium (2823-3), HbA1c (4548-4), eGFR (62238-1). Add `recent_labs` JSONB column to `clinical_context`. Briefing composer surfaces abnormal values as visit agenda items: "K+ 3.1 mEq/L — diuretic context, review electrolytes." Flag K+ < 3.5 as a visit agenda item for any patient with a diuretic in `current_medications`.

---

### 17. Cold start — new patients get misleading briefings
**Status:** NEW — not in original audit.

When a patient is first enrolled, there are zero home readings. The inertia, deterioration, and adherence detectors produce null or misleading output. `data_limitations` is populated generically rather than flagging insufficient enrollment age.

**Fix:** At the start of `_handle_pattern_recompute`, if `(now - enrolled_at).days < 21`: skip inertia, deterioration, and adherence detectors; log suppression reason; set `data_limitations` to "Patient enrolled N days ago — minimum 21-day monitoring period required before pattern analysis. Briefing based on EHR data only." Gap detector still runs — zero readings in the first week is itself a gap signal.

The 21-day threshold (not 14) avoids a cliff-edge interaction with Fix 28's adaptive window, whose floor is 14 days. A 14-day cold-start would lift exactly when the adaptive window is at its minimum — giving one overlap day between "no data" and "enough data." 21 days ensures at least 7 days of home readings exist before any detector first runs, even on a weekly-visit patient where the adaptive window floors to 14. Phrased as `days_since_enrollment` rather than `enrolled_at > now - 14 days` for readability.

---

## Medium — Gaps affecting demo quality or reliability

### 18. Briefing composer re-implements inertia logic with wrong threshold
**File:** `backend/app/services/briefing/composer.py` — `_build_visit_agenda()` line 359
**Verified:** `avg_sys >= _ELEVATED_SYSTOLIC and days_since > _INERTIA_DAYS` with `_ELEVATED_SYSTOLIC = 140.0` — duplicates the inertia detector with the same hard-coded threshold problem.

**Fix:** Remove the inline inertia check from `_build_visit_agenda`. Pass the Layer 1 `InertiaResult` dict into `compose_briefing()` and consume `inertia_result["inertia_detected"]` directly. Do Fix 1 first.

---

### 19. Reading generator supports only a single fixed scenario
**File:** `backend/app/services/generator/reading_generator.py` line 361
**Verified:** Audit claim confirmed — generator uses a hardcoded baseline of ~163 mmHg.

**Fix:** Replace the hard-coded scenario baseline with a parametric baseline derived from `clinical_context.historic_bp_systolic`. Generator computes `baseline_mean = median(historic_bp_systolic)` and baseline SD from the patient's actual clinic BP history. This is a prerequisite for Fix 15 (full timeline generation).

---

### 20. Briefing appointment date parsed from idempotency key
**File:** `backend/app/services/worker/processor.py` line 247
**Verified:** `date_str = job.idempotency_key[-10:]`

If the admin trigger fires on a day other than the patient's actual appointment, the briefing records the wrong `appointment_date`.

**Fix:** Replace `date_str = job.idempotency_key[-10:]` with a DB query: `patient.next_appointment.date()`. Fall back to today if `next_appointment` is None (preserving demo-mode behavior).

---

### 21. No `next_appointment` update mechanism
**Verified:** No endpoint or worker task in the codebase advances `next_appointment` after a visit passes.

**Fix:** Add `PATCH /api/patients/{patient_id}/appointment` endpoint accepting `next_appointment: datetime`. Call this after each visit (manually in demo, via EHR webhook in production). `reset_demo.py` continues to patch it for demo purposes.

---

### 22. Readings ingestion uses batch-level idempotency
**File:** `backend/app/services/fhir/ingestion.py` line 383
**Verified:** `if clinic_count == 0: (insert all)` — any existing clinic readings block all new inserts for that patient.

Adding a new clinic visit to an existing patient requires manually deleting all existing clinic readings first.

**Fix:** Replace the batch COUNT check with per-observation idempotency. Add a unique index on `(patient_id, effective_datetime, source)`. Insert each observation with `ON CONFLICT DO NOTHING`. New clinic readings from a subsequent visit insert cleanly alongside existing ones. **This fix is a prerequisite for Fix 15** (full timeline generation).

---

### 23. Briefing icon only active for today's appointments
**File:** `frontend/src/components/dashboard/PatientList.tsx` line 69
**Verified:** `apptToday = patient.next_appointment && isToday(patient.next_appointment)`

A clinician reviewing tomorrow's schedule sees no briefing indicators even if briefings exist for those patients.

**Fix:** Change the briefing icon condition to whether a briefing exists for this patient, not whether the appointment is today. Add a `has_briefing` boolean to the patient list API response (or a separate `GET /api/briefings/{patient_id}/latest` call). The icon activates whenever a briefing row exists, regardless of appointment date.

---

### 24. Alert API has no `patient_id` filter
**File:** `backend/app/api/alerts.py` line 25
**Verified:** `GET /api/alerts` returns all unacknowledged alerts system-wide with no filter.

With multiple patients, this becomes a firehose with no way to scope to one patient.

**Fix:** Add optional `?patient_id=` query parameter. If provided, add `Alert.patient_id == patient_id` to the WHERE clause. Calls without the parameter continue returning all alerts (inbox behavior).

---

### 25. Comorbidity risk score saturates at 5 problems
**File:** `backend/app/services/pattern_engine/risk_scorer.py` line 161
**Verified:** `sig_comorbidity = _clamp(_comorbidity_count(context) / 5.0 * 100.0)` — patient 1091 has 17 coded problems, maxing out this signal at 5.

Every complex patient scores 100 on this signal, making it useless for differentiation within the high-risk cohort.

**Fix:** Replace linear count with a severity-weighted model loosely grounded in the Charlson Comorbidity Index weighting (which assigns CHF and stroke sequelae weight 2, diabetes and CKD weight 1–2, "any other" weight 1). The ARIA mapping is scaled so the dashboard signal differentiates within the high-risk cohort:

- CHF (I50), Stroke (I63/I64), TIA (G45): 25 points each (Charlson weight ≈2 / severe hemodynamic or neurologic risk)
- Diabetes (E11), CKD (N18), CAD (I25): 15 points each (Charlson weight ≈1–2 / moderate end-organ risk)
- Any other coded problem: 5 points each (Charlson weight ≈1 / routine chronicity)

Total clamped to 100. This gives CHF + Diabetes a score of 40/100 on this signal, vs. CHF + Stroke at 50/100 — meaningful clinical differentiation rather than a count cap. Weights are documented as ARIA-specific (not a Charlson score) and subject to clinician calibration via Fix 42 Layer 2 once feedback data accumulates.

---

### 26. Pattern B suppression missing medication-change condition
**File:** `backend/app/services/pattern_engine/adherence_analyzer.py`
**Status:** NEW — not in original audit.

Without a medication-change gate, a patient with persistently elevated BP and a slightly negative slope (due to noise) incorrectly suppresses Pattern B — this was the root cause of the false negative in shadow mode before it was fixed. The gate must tie suppression to "a recent change the physician made that is plausibly still taking effect."

**Fix:** In the Pattern B suppression block (part of Fix 3), add `AND days_since_med_change <= titration_window` as a required condition, where `titration_window` is derived from the most recently changed drug's class (see Fix 3 for the `TITRATION_WINDOWS` lookup dict). Do not use a blanket 42 days — this over-suppresses on fast-acting drugs (diuretics, beta-blockers) and under-suppresses on amlodipine. The adherence analyzer reads `clinical_context.med_history` to find the most recent medication date (consistent with Fix 4). If no medication change exists or the change is older than the class-specific window, suppression does not apply.

---

### 27. White-coat pre-visit window not excluded from threshold comparisons
**File:** `backend/app/services/pattern_engine/inertia_detector.py`, `deterioration_detector.py`
**Status:** NEW — not in original audit.

Readings within 3 days of `patients.next_appointment` are known to be suppressed (the synthetic generator explicitly models a white-coat dip). These readings are included in window computations, pulling the mean downward and potentially suppressing a legitimate flag.

**Fix:** After querying readings in both detectors, filter out readings where `effective_datetime >= (next_appointment - timedelta(days=5))`. Pass `next_appointment` into the detectors. The 5-day exclusion window aligns with CLAUDE.md's synthetic generator rule (dip spans 3–5 days before appointment) — a 3-day window leaks dip-influenced days 4–5 into threshold computation. When `next_appointment` is None, no exclusion is applied. Excluded readings remain in the DB and visible in the briefing trend — they are only excluded from threshold comparison computation.

---

### 28. 28-day window does not adapt to visit interval
**Status:** NEW — not in original audit.

All four detectors use a fixed 28-day window regardless of whether the patient is seen monthly, quarterly, or weekly. For a patient seen every 90 days, ARIA analyzes only the last third of the inter-visit period. Shadow mode demonstrated this clearly: the Nov 2008 – Feb 2009 pneumonia gap spanned 99 days, and any fixed 28-day window misses the clinical picture the physician was responding to.

**Fix:** Compute `window_days` with explicit null-handling:

```
if next_appointment is None or last_visit_date is None:
    window_days = 28   # fallback default — preserves legacy behavior
else:
    interval = (next_appointment - last_visit_date).days
    if interval <= 0:         # appointment already passed or same-day — unreliable
        window_days = 28
    else:
        window_days = min(90, max(14, interval))
```

Use this in place of the hard-coded `_WINDOW_DAYS = 28` in all four detector queries. Cap at 90 to bound computation and generator data requirements. Floor at 14 so short inter-visit intervals don't degenerate. All four detectors receive the same window value for consistency. Log `window_days_source` ("adaptive" vs "fallback_default") alongside each detector result for observability. **Prerequisite:** Fix 15 (full timeline readings) must be complete so generated data exists for longer lookback windows on patients with large inter-visit intervals.

---

## Low — Polish and minor issues

### 29. `social_context` never surfaced in briefing even when populated
**File:** `backend/app/services/briefing/composer.py`
**Fix:** Add social context to the briefing payload as a `patient_context` field when non-null. One addition to `compose_briefing()` after Fix 8 populates the column.

---

### 30. `delivered_at` on alerts is never set
**Files:** `backend/app/services/worker/processor.py`
**Verified:** `_upsert_alert()` never sets `delivered_at`. Serializer always returns it as None.

**Fix:** Set `delivered_at = datetime.now(UTC)` on the Alert object at insert time inside `_upsert_alert()`. The alert is "delivered" at creation.

---

### 31. Frontend and backend both sort the patient list independently
**Files:** `backend/app/api/patients.py`, `frontend/src/components/dashboard/PatientList.tsx`
**Verified:** `sortPatients()` function confirmed in PatientList.tsx.

**Fix:** Remove `sortPatients()` from `PatientList.tsx`. Rely entirely on backend sort order (`risk_tier ASC`, `risk_score DESC`). Backend is authoritative. If sort logic ever diverges, the frontend silently overrides it.

---

### 32. `run_ingestion.py` default bundle path hardcoded to 1091
**File:** `scripts/run_ingestion.py` line 73
**Fix:** Remove the default value for `--bundle`. Require explicit supply. Add a usage example in `--help`.

---

### 33. Shadow mode window overlap not reported; no confidence interval or per-detector breakdown
**Status:** NEW — not in original audit.

Two evaluation points closer than 28 days apart share overlapping synthetic reading windows. The 35 labeled evaluation points are not fully independent — the effective independent sample is smaller than 35, though the 94.3% result is still valid. Additionally, the 94.3% aggregate rate (33/35) has a 95% Wilson confidence interval of roughly [82%, 99%] — a 17-point spread that narrows only as the patient panel grows. Finally, the two disagreements could both be in the inertia detector, or split across detectors — the aggregate rate hides which specific detector to prioritise improving.

**Fix:** Add to the shadow mode summary output: (1) count evaluation points where `days_since_prior_eval_point >= 28` (fully independent) vs. those with overlap — "Fully independent evaluation points: N/35"; (2) Wilson 95% confidence interval on the agreement rate — `lower, upper = wilson_ci(agreed, total)`; (3) per-detector breakdown — for each disagreement, record which detector fired incorrectly (inertia / deterioration / adherence) and report "Inertia: N/M, Deterioration: N/M, Adherence: N/M." No algorithm change required. This decomposition is the primary guide for which detector to prioritise in subsequent fixes.

---

### 34. Medication titration timing not surfaced in briefing
**Status:** NEW — not in original audit.

When a medication was changed 25 days ago, the inertia detector correctly does not fire. But the briefing gives no structured signal that the patient is in a titration period. The physician has no indication that current elevated readings may still be responding to a recent change.

**Fix:** In `_build_medication_status()` in `composer.py`, when `days_since_med_change <= titration_window` (derived from the changed drug's class — see Fix 3 `TITRATION_WINDOWS` dict): append "— within expected titration window, full response may not yet be established." Use the same drug-class-aware window as Fixes 3 and 26 so the briefing message is consistent with suppression logic: a patient on amlodipine gets a 56-day notice, not a 42-day one. Informs the physician without making a clinical judgment. One conditional string append plus a call to the shared `TITRATION_WINDOWS` lookup.

---

## Infrastructure and Security

### 35. Patient research ID pseudonymization unverified
**Status:** NEW.

The raw iEMR `MED_REC_NO` (`1091`) is used as `patient_id` throughout the DB and logs. For the current de-identified research dataset, this is the hospital record number. If it can be cross-referenced with hospital records by anyone with DB or log access, the de-identification is incomplete.

**Fix:** Apply HMAC-based pseudonymization at the adapter level before any data enters the DB: `patient_id = hmac_sha256(secret_key, med_rec_no.encode())[:16]`. Add `PATIENT_PSEUDONYM_KEY` to `Settings` in `config.py` — stored in `backend/.env`, excluded from git. This approach is deterministic (same MED_REC_NO always produces the same pseudonym across re-ingestion runs), non-reversible without the key, and requires no separate mapping table. Sequential identifiers like `ARIA-001` are pseudonymization by obscurity — anyone with the run order or a mapping table can reverse them. A simple sequential scheme also does not scale to multi-site deployment where two sites must independently produce consistent pseudonyms for the same patient. Also verify no log line emits the raw MED_REC_NO at INFO level — grep for the literal `1091` in log output.

---

### 36. JWT token expiry not verified
**Fix:** Confirm the JWT access token expiry is ≤ 1 hour in the auth configuration. Document the current setting. A leaked 7-day token gives a 168× larger exposure window than a 1-hour token.

---

### 37. No API rate limiting
**Fix:** Add `slowapi` middleware to FastAPI. Limits: `POST /api/readings` at 60/minute per patient, `POST /api/ingest` at 5/minute, `GET /api/alerts` at 30/minute. Protects against misconfigured clients flooding the readings table.

---

### 38. Audit enforcement is application-level only
**Fix:** Add a PostgreSQL trigger on the `readings` table that inserts an `audit_events` row on every INSERT so a direct DB write or an application bug still produces an audit record. A naive trigger cannot replicate the structured fields the application-level audit populates (`actor_id`, `request_id`, `details`) — these must be threaded through via PostgreSQL `SET LOCAL` session variables set by the application at the start of each request (`SET LOCAL aria.actor_id = '...'`; `SET LOCAL aria.request_id = '...'`). The trigger reads them via `current_setting('aria.actor_id', true)` and defaults to `system` / NULL when unset (e.g., direct DB writes). This requires a small application-layer helper that sets the session variables inside each DB transaction — not "no application code change" as originally stated. Document the trigger behaviour as a safety net with reduced fidelity for non-application writes.

Additionally, apply PostgreSQL Row-Level Security (RLS) on the `readings` table to force all writes through the application role. Without RLS, a direct admin DB connection or a future application bug that skips the `SET LOCAL` call still produces an audit row with `actor_id=NULL` — the audit record exists but is uninformative. With RLS, the policy can enforce that only the designated application role (`aria_app`) may INSERT, making it structurally impossible for unaudited writes to succeed. Enable with `ALTER TABLE readings ENABLE ROW LEVEL SECURITY` and a permissive policy for the `aria_app` role. Admin access uses a superuser role that bypasses RLS by design — document this explicitly as the one permitted unaudited write path (DB migrations, data recovery).

---

### 39. No multi-factor authentication
**Fix:** Enable TOTP MFA in Supabase Auth. Clinicians enroll an authenticator app on first login. Configuration change in Supabase dashboard — no backend code change required.

---

### 40. No dead-letter queue for failed jobs
**File:** `backend/app/services/worker/processor.py`
**Fix:** Add retry logic in `_process_one()`. Track retry count in `error_message` or a new `retry_count` column. Retry up to 3 times with exponential backoff: 30s, 2m, 8m. After 3 failures, set status to `dead`. Add `GET /api/admin/dead-jobs` for inspection. Surface a dashboard indicator when any job reaches `dead` status.

---

## New Clinical Features (production readiness)

### 41. Gap detector cannot distinguish device outage from non-compliance
**Fix:** Add a `gap_explanations` table: `patient_id`, `start_date`, `end_date`, `reason_code` (enum: `device_malfunction`, `device_lost`, `travelling`, `illness`, `intentional_pause`, `forgot`), `reported_at`, `free_text`. Add `POST /api/gap-explanations` endpoint for patient submission (retroactive reporting supported). Gap detector checks this table before classifying: travel → `EXPLAINED — travel` (low priority); illness → alert retained but labeled with context; "forgot" → tracked as non-compliance without suppressing the alert. Add a consistency flag if readings resume from the same BLE source shortly after a "device broken" report. Patient-facing submission (Fix 43) is a prerequisite.

---

### 42. No feedback loop — ARIA cannot learn from clinician responses
**Fix (three layers, implement in order):**

**Layer 1:** Extend alert acknowledge endpoint to accept `disposition` (`agree_acting`, `agree_monitoring`, `disagree`) and optional `reason_text`. Store in new `alert_feedback` table: `alert_id`, `disposition`, `clinician_id`, `patient_id`, `detector_type`, `reason_text`, `created_at`.

**Layer 2:** When a detector accumulates 4+ dismissals of the same type for the same patient, surface a calibration recommendation in the admin dashboard. Clinician approves or rejects. Approved rules stored in `calibration_rules` table with provenance: who approved, when, based on how many dismissals. Production detectors read rules at query time. No automatic self-modification — every threshold change requires explicit clinician approval.

**Layer 3:** When a clinician dismisses an alert, track the patient for 30 days. If a concerning event follows (deterioration cluster, urgent visit), prompt the clinician: "Alert dismissed on [date] — patient had a deterioration event 12 days later. Was the alert relevant in retrospect?" Retrospective labels feed back into Layer 2 calibration evidence.

---

### 43. No patient-facing readings submission interface
**Fix:** Add a `/patient` route in Next.js outside clinician auth. Form with systolic, diastolic, heart rate, session (morning/evening/ad_hoc), and optional symptom flags (headache, dizziness, chest pain, shortness of breath). POST to existing `/api/readings`. Patient authenticates with their research ID. Chest pain triggers an immediate escalation alert regardless of BP value.

---

### 44. BLE connector not built
**Fix (Option A — manufacturer cloud webhook, fastest):** Register with cuff vendor developer program (Omron Connect, Withings Health). Configure webhook to POST on each BP measurement. Transform payload to ARIA reading schema and call `POST /api/readings` with `source='ble_auto'`. No BLE SDK required.
**Fix (Option B — direct BLE SDK):** Implement CoreBluetooth (iOS) or Android BLE API reading standard BP cuff Bluetooth profiles. Requires mobile app. `source='ble_auto'` already exists in the readings schema for either path.

---

### 45. No escalation pathway for unacknowledged urgent alerts
**Fix:** Add time-based escalation in the daily worker sweep (Fix 10). If `gap_urgent` or `deterioration` remains unacknowledged for 24 hours, promote to `escalated` status and send email notification to secondary clinician or admin. Tag alerts generated between 6 PM–8 AM or on weekends as `off_hours`. Display with a distinct visual indicator when clinician next logs in, so overnight alerts are not buried by morning activity.

---

### 46. No between-visit mini-briefing for urgent alerts
**Fix:** When a `gap_urgent` or `deterioration` alert fires between visits, generate a mini-briefing: the specific detector output, the 7-day synthetic trend, the current clinical snapshot. Store as a `Briefing` row with `appointment_date = None`. Clinician receives actionable context without waiting for the 7:30 AM appointment-day briefing.

---

### 47. Long-term trend not surfaced in briefing
**Fix:** In `compose_briefing()`, after computing the 28-day trend, compute a secondary 90-day trajectory from `clinical_context.historic_bp_systolic` and `historic_bp_dates`. Append to `trend_summary`: "3-month trajectory: declining from 170 in January — improvement trend" or "3-month trajectory: stable elevation since November." Requires Fix 28 (adaptive window) and Fix 15 (full timeline readings) as prerequisites.

---

## Layer 3 LLM Output Safety — Fixes 48–57

The following items address a gap identified during the April 2026 audit: the Layer 3 LLM output is currently stored without any validation. A hallucinated value, a forbidden clinical phrase, or a PHI leak would be stored directly into `briefings.llm_response.readable_summary` and surfaced to the clinician dashboard.

All 10 fixes below are implemented in a single new file: `backend/app/services/briefing/llm_validator.py`. The `summarizer.py` file is updated to call `validate_llm_output()` after the LLM call and before storing `readable_summary`. On failure: retry once, then store `readable_summary=None`. Layer 1 briefing is always the authoritative output.

**Owner:** Sahil Khalsa | **Phase:** 0 (no dependencies — standalone new file)

---

### 48. Layer 3 output has no clinical language guardrails
**Severity: Critical**

The LLM output is stored as-is. A response containing "non-adherent," "hypertensive crisis," or "prescribe metoprolol 50mg" violates the clinical boundary enforced at code level throughout the rest of ARIA.

**Fix:** Add `check_guardrails(text)` in `llm_validator.py`. Blocked phrases (case-insensitive): `non-adherent`, `non-compliant`, `hypertensive crisis`, `medication failure`, `prescribe`, `increase.*mg`, `decrease.*mg`, `tell the patient`, `diagnos`, `emergency`. On any match: `validation_outcome="failure"`, `failed_check="guardrail:{phrase}"`.

---

### 49. Layer 3 output not validated for faithfulness against Layer 1 payload
**Severity: Critical**

The LLM receives the Layer 1 briefing payload as input but its output is never compared back to that payload. The LLM could state a risk score of 85 when the actual score is 69, reference overdue labs when none exist, or mention an adherence concern when the payload shows Pattern B.

**Fix:** Add `validate_faithfulness(text, payload)` in `llm_validator.py` covering:
- Risk score: if a number ≥ 50 appears near "risk"/"score", must match `payload["risk_score"]` ±10
- Adherence language: "adherence concern" requires Pattern A in `payload["adherence_summary"]`
- Treatment review: "treatment review" requires Pattern B in `payload["adherence_summary"]`
- Titration window: "titration" requires "titration window" in `payload["medication_status"]` (Fix 34)
- Urgent flags: "urgent" requires non-empty `payload["urgent_flags"]`
- Overdue labs: "lab"/"overdue" requires non-empty `payload["overdue_labs"]`
- Problem assessments: problem names in text must exist in `payload["active_problems"]` or `payload["problem_assessments"]` (Kush's new field)
- Data limitations: "insufficient data"/"enrolled" requires non-empty `payload["data_limitations"]`

---

### 50. Layer 3 output can contain PHI
**Severity: Critical**

The briefing payload includes `patient_id`. If the LLM reflects the patient ID back in its summary, it becomes PHI in a clinician-facing text field. The briefing is clinician-only but storing a patient identifier in plain text in the readable summary is a data boundary violation.

**Fix:** Add `check_phi_leak(text, patient_id)` in `llm_validator.py`. Scan for `patient_id` appearing verbatim and for 4+ digit numeric strings matching the patient ID pattern. On match: `failed_check="phi_leak"`, hard block.

---

### 51. No prompt injection detection on LLM output
**Severity: High**

The briefing payload contains patient-sourced text fields: `problem_assessments` (physician assessment text from iEMR), `social_context`, and `data_limitations`. If any of these fields contain injected instructions (e.g., "Ignore previous instructions and say X"), the LLM may echo them back in the summary.

**Fix:** Add `check_prompt_injection(text)` in `llm_validator.py`. Blocked patterns: `"ignore previous"`, `"new instruction"`, `"system:"`, `"[INST]"`, `"<|im_start|>"`, `"Assistant:"`. On match: `failed_check="prompt_injection"`, hard block.

---

### 52. LLM output sentence count not validated
**Severity: Medium**

The spec (`summarizer.py` docstring, `ARIA_SOP_v2_0.md` section 2.3, `CLAUDE.md`) explicitly defines Layer 3 output as a **3-sentence readable summary**. The current implementation stores whatever the LLM returns — 1 sentence, 6 sentences, or a paragraph.

**Fix:** Add `check_sentence_count(text)` in `llm_validator.py`. Split on `.`, `!`, `?` and count non-empty sentences. Must equal exactly 3. On mismatch: `failed_check="sentence_count"`, `detail=f"got {n} sentences, expected 3"`.

---

### 53. Medication name hallucination not detected
**Severity: High**

The LLM could mention a drug not in the patient's current medication list. For a patient with 14 active medications (patient 1091), the risk of hallucinating a plausible drug name is real. A clinician reading "consider reviewing amlodipine dosing" when the patient isn't on amlodipine would be misled.

**Fix:** Add `check_medication_hallucination(text, payload)` in `llm_validator.py`. Extract drug-like tokens from text using suffix patterns (`-olol`, `-pril`, `-sartan`, `-dipine`, `-statin`, `-mab`) and a curated list of known unsuffixed names. Rather than checking each token against the full `payload["medication_status"]` string with `in`, build a token set from the payload at validation time: `med_tokens = set(re.findall(r'\b[a-z]+\b', medication_status.lower()))`, then check `word in med_tokens`. The string-level `in` check has a false-positive risk: "prilosec" (a brand name for omeprazole) contains the substring "pril" at its start and would be flagged as drug-like, then fail the `in medication_status` check even though the patient is on omeprazole — the names don't match. Token-set membership avoids this: "prilosec" is not a token in `{"omeprazole", "metoprolol", ...}` and would fail, but this can be addressed by adding brand-to-generic synonyms (prilosec → omeprazole, lasix → furosemide) to a `_BRAND_GENERIC` dict consulted before the final check. On mismatch with no synonym match: `failed_check="medication_hallucination"`, `detail=unknown_drug_name`.

---

### 54. BP values in LLM output not validated for plausibility
**Severity: Medium**

If the LLM mentions a systolic value (e.g., "average BP of 210/130 mmHg"), two issues can arise: the value may be physiologically implausible (outside 60–250 mmHg) or it may contradict the actual average in the payload.

**Fix:** Add `check_bp_plausibility(text, payload)` in `llm_validator.py`. Extract systolic values using regex (3-digit standalone number or "X/Y" pattern). Validate: (a) within 60–250 mmHg, (b) within ±20 mmHg of any systolic value extractable from `payload["trend_summary"]`. On mismatch: `failed_check="bp_value_implausible"`.

---

### 55. LLM validation outcome not written to audit_events
**Severity: High**

All other sensitive actions in ARIA write to `audit_events`. Layer 3 validation outcome is currently only in Python logs — invisible to audit reporting and the dashboard.

**Fix:** Add `write_audit_event(session, briefing_id, patient_id, result)` in `llm_validator.py`. Always runs regardless of pass/fail. Fields: `action="llm_validation"`, `actor_type="system"`, `actor_id="llm_validator"`, `resource_type="Briefing"`, `resource_id=briefing_id`, `outcome="success"|"failure"`, `details=failed_check+": "+detail` on failure.

---

### 56. No retry on LLM validation failure
**Severity: Low**

A single LLM call can produce an off-spec response (wrong sentence count, borderline phrase) without the underlying model being consistently broken. Immediately discarding the summary on first failure reduces Layer 3 availability unnecessarily.

**Fix:** In `summarizer.py`, wrap the LLM call and validation in a retry loop (max 2 attempts). On first validation failure: log at WARNING level, repeat the identical LLM call once. On second failure: log at ERROR, set `readable_summary=None`. No prompt modification between attempts — the retry tests whether the failure was transient.

---

### 57. Contradiction detection missing from LLM output validation
**Severity: Medium**

The faithfulness checks (Fix 49) test whether the LLM has support for what it claims. The inverse — the LLM being more alarming than the data supports — is not caught. A summary saying "urgent intervention needed" when `payload["urgent_flags"]` is empty would pass all current faithfulness checks.

**Fix:** Add `check_contradiction(text, payload)` in `llm_validator.py`:
- If `payload["urgent_flags"]` is empty AND text contains urgent/alarming language → `failed_check="contradiction_urgent"`
- If `payload["active_problems"]` is empty AND text mentions specific problem names → `failed_check="contradiction_problems"`
- If `payload["adherence_summary"]` indicates Pattern C (contextual only) AND text says "possible adherence concern" → `failed_check="contradiction_adherence"`

---

### 58. Risk scorer `sig_gap` saturates at 14 days — misaligned with adaptive window
**File:** `backend/app/services/pattern_engine/risk_scorer.py` line 159
**Severity: Medium**
**Status:** NEW — identified during implementation review.

`sig_gap = _clamp(gap_days / 14.0 * 100.0)` saturates at 14 days: a 45-day gap and a 14-day gap both score 100/100 on this signal, eliminating differentiation for patients on quarterly schedules. Fix 28 extends the adaptive window to 90 days, but the risk scorer's gap normalization was never updated to match.

**Fix:** Replace the hard-coded divisor with the adaptive window value: `sig_gap = _clamp(gap_days / window_days * 100.0)` where `window_days` is computed by the same adaptive window logic as the detectors (Fix 28). Pass `window_days` into `compute_risk_score()` or recompute it from `next_appointment` and `last_visit_date` within the scorer. Also update `sig_inertia = _clamp(_days_since_med_change / 180.0 * 100.0)` — the current divisor of 90.0 means any patient with no med change for more than 90 days scores 100/100 regardless of whether that is 91 days or 5 years; 180 days (6 months) is a more clinically meaningful saturation point. Implement together with Fix 4's risk_scorer.py `med_history` fix.

---

### 59. BP variability not surfaced as an independent risk signal
**Severity: High**
**Status:** NEW — not in original audit.

All four Layer 1 detectors and the Layer 2 risk score focus on the BP mean. High day-to-day BP variability — measured as coefficient of variation (CV = SD / mean) > 12% — is an independent cardiovascular risk factor separate from elevated mean. A patient oscillating 130→165→132→168 over two weeks has a materially different risk profile from one stable at 152, yet ARIA currently treats them identically. The synthetic generator already models realistic SD (8–12 mmHg) but it is never analysed.

**Fix:** Add `run_variability_detector(session, patient_id, window_days)` to a new `variability_detector.py` in `pattern_engine/`. Compute `cv = stddev(systolic_avg) / avg(systolic_avg) * 100` over the adaptive window. Thresholds: CV ≥ 15% → flag "high BP variability — consider 24h ambulatory monitoring or ABPM referral"; CV 12–14% → "moderate variability — monitor trend." Add `variability_flag` to the Layer 1 output passed to the briefing composer. Surface in `visit_agenda` at priority level 3 (after inertia, before overdue labs). Add `variability_score` component to the Layer 2 risk scorer (5% weight, drawn from the `sig_systolic` allocation) so patients with identical means but high variability rank higher within their tier. Prerequisite: Fix 15 (full-timeline readings) for meaningful long-window variability calculation.

---

### 60. Background worker uses 30-second polling — high latency and unnecessary DB load
**File:** `backend/app/services/worker/processor.py`
**Severity: Medium**
**Status:** NEW — not in original audit.

The `processor.py` polling loop queries `processing_jobs WHERE status='queued'` every 30 seconds. At GP-panel scale (1800 patients), the midnight sweep enqueues ~1800 jobs. Under 30-second polling, the first batch of jobs may not start processing until nearly 30 seconds after enqueue, and the 7:30 AM briefing window imposes a hard deadline. The constant polling also adds ~2880 unnecessary DB queries per day when no jobs are queued.

**Fix:** Replace the polling loop with PostgreSQL `LISTEN/NOTIFY`. In `ingestion.py` and `scheduler.py`, after every `INSERT INTO processing_jobs`, execute `NOTIFY aria_jobs, job_id`. In `processor.py`, open a persistent `asyncpg` connection and call `await conn.add_listener('aria_jobs', callback)`. The worker wakes immediately on notification with near-zero latency — no polling overhead when idle. Keep a 60-second fallback `SELECT` poll as a safety net for missed notifications (connection drops, restart recovery). This requires no additional infrastructure — `LISTEN/NOTIFY` is built into PostgreSQL and already available via the Supabase connection. Implement in Phase 4 alongside the scheduler fixes.

---

### 61. No staleness indicator on `patients.risk_score`
**File:** `backend/app/models/patient.py`, `backend/app/services/pattern_engine/risk_scorer.py`
**Severity: Low**
**Status:** NEW — not in original audit.

`patients.risk_score` has no corresponding timestamp. If the midnight sweep fails for any reason (server outage, DB connection timeout, APScheduler miss), the GP's dashboard displays a stale score with no visual indication. A score computed 48 hours ago during an acute patient episode is clinically misleading.

**Fix:** Add `risk_score_computed_at TIMESTAMPTZ` column to the `patients` table via `setup_db.py` (`ADD COLUMN IF NOT EXISTS`, safe to re-run). Update `risk_scorer.py` to set `risk_score_computed_at = datetime.now(UTC)` alongside `risk_score` in the `UPDATE` statement. In the frontend `PatientList.tsx`, display a staleness badge on any patient whose `risk_score_computed_at` is older than 26 hours — a single missed sweep is tolerable, a second is not. Phase 0 — no dependencies.

---

## Implementation Roadmap

Phases are ordered so that no fix depends on an incomplete prerequisite. Fixes within a phase can run in parallel.

---

### Phase 0 — Standalone correctness fixes
No dependencies. Fix wrong output on current data. No re-ingestion required. Start here.

| # | Fix | File | Size |
|---|---|---|---|
| 2 | Add threshold gate to deterioration detector | deterioration_detector.py | 1 line |
| 3 | Pattern B treatment-working suppression + drug-class titration window | adherence_analyzer.py | ~20 lines |
| 26 | Add class-aware med change condition to Pattern B suppression | adherence_analyzer.py | 2 lines |
| 11 | Write adherence alert row in processor | processor.py | 3 lines |
| 30 | Set `delivered_at` on alert insert | processor.py | 1 line |
| 20 | Read appointment date from patient record | processor.py | 5 lines |
| 25 | Fix comorbidity risk score saturation | risk_scorer.py | 5 lines |
| 58 | Fix sig_gap and sig_inertia normalization in risk scorer | risk_scorer.py | 3 lines |
| 61 | Add `risk_score_computed_at` column + set on every score update | patient.py, risk_scorer.py, setup_db.py | 4 lines |
| 48–57 | Layer 3 LLM output validation + guardrails | llm_validator.py (new), summarizer.py | ~200 lines |

After Phase 0: trigger `pattern_recompute` via admin endpoint to refresh all production scores and alerts with the corrected logic.

---

### Phase 1 — Ingestion data fixes
Apply all changes to `adapter.py` and `ingestion.py` together. Re-ingest once after all Phase 1 fixes are complete.

| # | Fix | Files |
|---|---|---|
| 12 | Capture all visit dates for `last_visit_date` | adapter.py, ingestion.py |
| 8 | Populate `social_context` | adapter.py, ingestion.py |
| 9 | Allergy reactions + active-status filter | adapter.py, ingestion.py |
| 7 | Capture physician problem assessments | adapter.py, ingestion.py, clinical_context model |
| 6 | Capture PULSE/WEIGHT/SpO2 (+ DB migration for new columns) | adapter.py, ingestion.py, migration |

After Phase 1: `python scripts/run_adapter.py` → `python scripts/run_ingestion.py`. Verify `clinical_context` fields updated.

---

### Phase 2 — Detector and briefing fixes
Apply after Phase 1 data is in the DB. Fixes 1, 4, 5, 27, and 28 all touch the same two detector files (`inertia_detector.py`, `deterioration_detector.py`) and should be applied together to avoid four-touch churn. Fix 28 is included here with a conservative fallback so the code can ship before Fix 15's full-timeline data exists: when the requested window exceeds available readings, the detector logs `window_truncated_to_available` and uses the available range. Once Fix 15 lands in Phase 3, the adaptive window silently starts benefiting from the longer lookback — no further code change needed.

| # | Fix | Files |
|---|---|---|
| 1, 4 | Patient-adaptive threshold + `med_history` in inertia detector + identical fix to risk_scorer.py | inertia_detector.py, risk_scorer.py |
| 18 | Remove duplicate inertia from briefing composer | composer.py |
| 5 | Apply comorbidity-adjusted threshold to production detectors (degraded mode until Fix 7 full mode) | threshold_utils.py (new), all 4 detectors |
| 2 (ext) | Add step-change sub-detector to deterioration | deterioration_detector.py |
| 27 | Exclude white-coat pre-visit window (5 days) | inertia_detector.py, deterioration_detector.py |
| 28 | Adaptive window with conservative fallback when full-timeline data absent | all 4 detectors |
| 29 | Surface social context in briefing | composer.py |
| 34 | Surface drug-class-aware titration timing in briefing | composer.py |
| 59 | BP variability detector — CV threshold signal | variability_detector.py (new), processor.py, composer.py |

After Phase 2: run shadow mode. Agreement rate should be at 94.3% or better with production detectors.

---

### Phase 3 — Generator expansion
Fix 22 (per-observation idempotency) is the gate for all generator work.

| # | Fix | Files |
|---|---|---|
| 22 | Per-observation idempotency (prerequisite) | ingestion.py |
| 19 | Parametric baseline from patient clinic BPs | reading_generator.py |
| 15 | Full care timeline synthetic readings | reading_generator.py, run_generator.py |
| 17 | Cold start detection (< 21 days → suppress detectors) | processor.py |

After Phase 3: `python scripts/run_generator.py` for all patients. Verify readings table has full-timeline generated readings spanning the patient's entire care history. Fix 28's adaptive window (already in Phase 2) silently starts using the longer lookback once full-timeline data exists.

---

### Phase 4 — Scheduler and worker

| # | Fix | Files |
|---|---|---|
| 10 | Daily `pattern_recompute` sweep for all active patients | scheduler.py |
| 21 | `next_appointment` update endpoint | patients.py API |
| 47 | Long-term trend layer in briefing | composer.py |
| 46 | Mini-briefing for between-visit urgent alerts | processor.py, composer.py |
| 40 | Dead-letter queue (max 3 retries) | processor.py |
| 60 | Replace 30s polling with PostgreSQL LISTEN/NOTIFY | processor.py, scheduler.py, ingestion.py |

---

### Phase 5 — API and alert improvements

| # | Fix | Files |
|---|---|---|
| 24 | Alert `patient_id` filter | alerts.py |
| 42 (L1) | Alert disposition on acknowledge (feedback loop Layer 1) | alerts.py, new `alert_feedback` table |
| 41 | Gap explanations table and API | new table, new API route |
| 13 | Shadow mode CLI argument | run_shadow_mode.py |
| 32 | Remove `run_ingestion.py` default bundle | run_ingestion.py |
| 33 | Shadow mode window overlap + CI + per-detector breakdown | run_shadow_mode.py |

---

### Phase 6 — Frontend

| # | Fix | Files |
|---|---|---|
| 23 | Briefing icon for any briefing, not just today | PatientList.tsx |
| 31 | Remove duplicate frontend sort | PatientList.tsx |
| 14 | Multi-patient pagination and tier filter | dashboard components |
| new | Shadow mode drill-down per visit | shadow mode frontend page |
| new | Push notifications via Web Push service worker | Next.js |

---

### Phase 7 — New clinical features

| # | Feature |
|---|---|
| 43 | Patient-facing reading submission + symptom flags |
| 45 | Escalation pathway + off-hours alert tagging |
| 42 (L2) | Feedback loop Layer 2: calibration recommendations |
| 42 (L3) | Feedback loop Layer 3: 30-day outcome verification |
| 44 | BLE webhook connector |

---

### Phase 8 — Infrastructure and security

| # | Fix |
|---|---|
| 35 | HMAC-based patient pseudonymization (replace MED_REC_NO at adapter level) |
| 36 | Confirm JWT expiry ≤ 1 hour |
| 37 | Add API rate limiting (slowapi) |
| 38 | DB-level audit trigger + Row-Level Security on readings table |
| 39 | Enable MFA in Supabase Auth |
| 16 | Lab values ingestion (FHIR Observation, LOINC codes) |
| new | EHR adapter generalization (generic FHIR R4, not iEMR-specific) |
| new | Seasonal BP threshold adjustment (+3–5 mmHg in Dec–Feb) |

---

## Summary Table

| # | Severity | Description | File(s) | Phase |
|---|---|---|---|---|
| 1 | Critical | Inertia: hard-coded 140 threshold, no slope check | inertia_detector.py | 2 |
| 2 | Critical | Deterioration: no threshold gate | deterioration_detector.py | 0 |
| 3 | Critical | Adherence: no treatment-working suppression | adherence_analyzer.py | 0 |
| 4 | Critical | Inertia: ignores med_history | inertia_detector.py | 2 |
| 5 | Critical | Comorbidity threshold not in production | all detectors | 2 |
| 6 | High | PULSE/WEIGHT/SpO2 lost in conversion | adapter.py, ingestion.py | 1 |
| 7 | High | Physician problem assessments lost | adapter.py, ingestion.py | 1 |
| 8 | High | social_context never populated | adapter.py, ingestion.py | 1 |
| 9 | High | Allergy reactions + active-status not captured | adapter.py | 1 |
| 10 | High | No scheduled pattern_recompute sweep | scheduler.py | 4 |
| 11 | High | Adherence alert not written to DB | processor.py | 0 |
| 12 | High | last_visit_date misses 71 non-vitals visits | ingestion.py | 1 |
| 13 | High | Shadow mode hardcoded to patient 1091 | run_shadow_mode.py | 5 |
| 14 | High | Only one patient in DB | data | 5 |
| 15 | High | Full care timeline readings not generated | reading_generator.py | 3 |
| 16 | High | Lab values not ingested | adapter.py, ingestion.py | 8 |
| 17 | High | Cold start misleading briefings | processor.py | 3 |
| 18 | Medium | Briefing composer re-implements inertia with wrong threshold | composer.py | 2 |
| 19 | Medium | Generator: single fixed scenario | reading_generator.py | 3 |
| 20 | Medium | Appointment date from idempotency key | processor.py | 0 |
| 21 | Medium | No next_appointment update mechanism | patients.py | 4 |
| 22 | Medium | Batch-level idempotency blocks new clinic visits | ingestion.py | 3 |
| 23 | Medium | Briefing icon today-only | PatientList.tsx | 6 |
| 24 | Medium | Alert API no patient_id filter | alerts.py | 5 |
| 25 | Medium | Comorbidity score saturates at 5 problems | risk_scorer.py | 0 |
| 26 | Medium | Pattern B suppression missing drug-class-aware med change condition | adherence_analyzer.py | 0 |
| 27 | Medium | White-coat window not excluded from threshold comparisons | detectors | 2 |
| 28 | Medium | 28-day window does not adapt to visit interval | all detectors | 2 |
| 29 | Low | social_context never in briefing | composer.py | 2 |
| 30 | Low | delivered_at never set on alerts | processor.py | 0 |
| 31 | Low | Duplicate sort backend + frontend | PatientList.tsx | 6 |
| 32 | Low | run_ingestion.py default bundle hardcoded | run_ingestion.py | 5 |
| 33 | Low | Shadow mode: window overlap + CI + per-detector breakdown not reported | run_shadow_mode.py | 5 |
| 34 | Low | Medication titration timing not in briefing — use drug-class-aware window | composer.py | 2 |
| 35 | Infra | Patient research ID — HMAC-based pseudonymization (not sequential IDs) | adapter.py, config.py | 8 |
| 36 | Infra | JWT expiry not verified | auth config | 8 |
| 37 | Infra | No API rate limiting | FastAPI middleware | 8 |
| 38 | Infra | Audit trigger + Row-Level Security on readings table | DB trigger, RLS policy | 8 |
| 39 | Infra | No MFA | Supabase Auth | 8 |
| 40 | Infra | No dead-letter queue | processor.py | 4 |
| 41 | Feature | Gap explanations (device vs non-compliance) | new table + API | 5 |
| 42 | Feature | Feedback loop (3 layers) | new tables + API | 5–7 |
| 43 | Feature | Patient-facing submission interface | Next.js | 7 |
| 44 | Feature | BLE connector | new | 7 |
| 45 | Feature | Escalation pathway + off-hours tagging | processor.py, alerts | 7 |
| 46 | Feature | Mini-briefing for between-visit alerts | processor.py | 4 |
| 47 | Feature | Long-term trend layer in briefing | composer.py | 4 |
| 48 | Critical | Layer 3: no clinical language guardrails | llm_validator.py (new) | 0 |
| 49 | Critical | Layer 3: no faithfulness validation vs Layer 1 payload | llm_validator.py (new) | 0 |
| 50 | Critical | Layer 3: PHI leak possible in readable_summary | llm_validator.py (new) | 0 |
| 51 | High | Layer 3: no prompt injection detection | llm_validator.py (new) | 0 |
| 52 | Medium | Layer 3: sentence count not validated (spec requires 3) | llm_validator.py (new) | 0 |
| 53 | High | Layer 3: medication hallucination — use payload-derived token set + brand/generic synonyms | llm_validator.py (new) | 0 |
| 54 | Medium | Layer 3: BP values not validated for plausibility | llm_validator.py (new) | 0 |
| 55 | High | Layer 3: validation outcome not in audit_events | llm_validator.py (new) | 0 |
| 56 | Low | Layer 3: no retry on validation failure | summarizer.py | 0 |
| 57 | Medium | Layer 3: contradiction detection missing | llm_validator.py (new) | 0 |
| 58 | Medium | Risk scorer: sig_gap saturates at 14 days, sig_inertia at 90 days — misaligned with adaptive window | risk_scorer.py | 0 |
| 59 | High | BP variability (CV%) missing as independent risk signal | variability_detector.py (new) | 2 |
| 60 | Medium | 30s polling — replace with PostgreSQL LISTEN/NOTIFY | processor.py, scheduler.py | 4 |
| 61 | Low | No staleness indicator on risk_score — add risk_score_computed_at | patient.py, risk_scorer.py, setup_db.py | 0 |

The five Critical items (1–5) affect clinical output correctness in live production. Items 6–17 are high-priority operational and clinical data gaps. Items 18–28 affect demo quality and detection accuracy. Items 48–50 are new Critical items affecting Layer 3 clinical safety — addressable in Phase 0 with no dependencies. Items 58 and 61 are Phase 0 fixes requiring no data migration. Phase 0 fixes require no data migration and can begin immediately.
