# Future Work Report
## Extended Medication Response Validation Layer
### ARIA v4.3 — Adaptive Real-time Intelligence Architecture
**Leap of Faith Technologies | IIT CS 595 | Spring 2026**
**Author: Nesh Rochwani**
**Date: April 28, 2026**
**Status: Proposed — Not Implemented**

---

## 1. Executive Summary

ARIA currently answers one question: *is this patient's blood pressure trending in the right direction?* This enhancement asks a harder question: *is the patient's body responding the way their prescribed medication should be producing?*

The Extended Medication Response Validation Layer introduces a consistency check between what a drug class is expected to do physiologically and what the observed readings actually show. It is deterministic, lightweight, and explainable — fitting naturally into the existing three-layer pipeline without requiring new data collection infrastructure. It does not expand ARIA beyond hypertension management and does not make treatment recommendations.

This report documents the rationale, proposed design, and integration path so the team can evaluate it as a candidate for a future sprint.

---

## 2. Current Limitation

ARIA's Layer 1 detectors address four clinical patterns:

| Detector | Question answered |
|---|---|
| Gap detector | Is the patient monitoring regularly? |
| Inertia detector | Is BP elevated with no medication change? |
| Deterioration detector | Is BP getting worse over time? |
| Adherence analyzer | Is the patient taking their medication? |

Each detector is clinically valid and well-implemented. However, all four share a structural blind spot: **they treat medication as a binary — taken or not taken — without validating whether the medication class is producing the expected physiological response.**

Two patients can both score Pattern B (high BP, high adherence) for entirely different clinical reasons:

- Patient A: adherent, but on a drug class that has limited efficacy in their phenotype
- Patient B: adherent, but receiving an inappropriately low dose for their comorbidity burden

ARIA currently cannot distinguish these cases. A clinician seeing "treatment review warranted" without knowing *why* the treatment is falling short still has to reconstruct this reasoning in the 8-minute window.

---

## 3. Proposed Enhancement

### 3.1 Core Idea

Map each prescribed antihypertensive medication class to its expected physiological signature, then evaluate whether observed readings are consistent with that signature. Generate a deterministic signal if they are not.

This does not require new hardware, new data sources, or a new model. The medication classes are already extracted at ingestion (`clinical_context.med_history`). Heart rate is already captured in `readings.heart_rate_avg`. The only new component is the consistency evaluation logic.

### 3.2 Medication Context (Patient 1091 — Demo Patient)

The following antihypertensives are active in the demo patient's record and would feed into this layer:

| Medication | Class | Expected Primary Effect |
|---|---|---|
| Lisinopril 10 | ACE Inhibitor | BP reduction via vasodilation; first-line for CHF + diabetes |
| Metoprolol | Beta-blocker | BP reduction + heart rate lowering |
| Amlodipine | Calcium Channel Blocker (CCB) | Gradual BP reduction; 56-day titration window |
| Sular 20 (Nisoldipine) | Calcium Channel Blocker | Additional BP reduction when monotherapy insufficient |
| Lasix 20 (Furosemide) | Loop Diuretic | BP reduction via volume/fluid balance |
| Isosorbide Mononitrate 30 | Nitrate | Vascular resistance reduction; primarily for angina/CHF |

This is a five-class, six-drug regimen. The presence of two CCBs (Amlodipine + Sular), a beta-blocker (Metoprolol), an ACE inhibitor (Lisinopril), and a diuretic (Lasix) simultaneously is itself a clinically significant signal — one that ARIA cannot currently surface.

---

## 4. How the Layer Works

### Step 1 — Input Collection

No new data sources required for the core version:

- BP readings → already in `readings` table (`systolic_avg`, `diastolic_avg`)
- Heart rate readings → already in `readings` table (`heart_rate_avg`)
- Medication classes → already in `clinical_context.med_history` (mapped to RxNorm codes)
- Titration windows → already computed in `threshold_utils.get_titration_window()`

### Step 2 — Expected Effect Mapping

Each drug class is assigned expected signatures:

| Class | Expected BP effect | Expected HR effect |
|---|---|---|
| ACE Inhibitor | Reduction | Neutral |
| Beta-blocker | Reduction | Significant reduction (>10 bpm) |
| CCB (dihydropyridine) | Reduction | Neutral to slight increase |
| Loop Diuretic | Reduction | Neutral |
| Nitrate | Reduction (vasodilation) | Reflex increase possible |

These mappings are fixed clinical knowledge — not learned, not configurable per patient. They would live in a `med_class_effects.py` constants file.

### Step 3 — Consistency Evaluation

For each patient, after the adaptive detection window closes:

1. Identify all active antihypertensive classes from `med_history`
2. Compute observed 28-day average BP and average heart rate
3. Compare against the expected effect given the combined regimen
4. Evaluate regimen composition: flag overlap (two CCBs), complexity (4+ classes simultaneously), or contradiction (nitrate + inadequate beta-blocker coverage)

### Step 4 — Signal Generation

The layer produces one of five deterministic output signals:

| Signal | Trigger condition |
|---|---|
| `expected_response` | BP within target range; no anomalies in HR where applicable |
| `limited_treatment_response` | BP elevated despite multi-class therapy, high adherence, and past titration window |
| `possible_over_treatment` | BP trending below 110 mmHg systolic under strong multi-drug regimen |
| `regimen_complexity` | 4+ antihypertensive classes active simultaneously |
| `hr_response_mismatch` | Beta-blocker present; HR not reduced relative to pre-beta-blocker baseline |

### Step 5 — Integration into Existing Pipeline

This layer inserts between Layer 1 completion and Layer 2 risk scoring:

```
Layer 1 (Detectors)
    → [NEW] Medication Response Validator
        → alert row if signal is persistent (≥ 3 consecutive days)
        → signal passed to briefing composer
Layer 2 (Risk Scorer)
    → comorbidity_severity_score already accounts for CHF, CKD, Diabetes
    → regimen_complexity signal could optionally add weight here
Layer 3 (LLM Summary)
    → signal included in briefing payload for LLM to narrate
```

The briefing composer would add a `medication_response_assessment` field to the existing `llm_response` JSONB. No schema migration required if added to the existing JSONB payload.

---

## 5. Example Clinical Scenarios

### Scenario A — Limited Treatment Response
Patient 1091 is on Lisinopril, Metoprolol, Amlodipine, Sular, and Lasix simultaneously. BP average over 28 days: 158 mmHg systolic. Adherence: 91%. All titration windows have elapsed.

**Current ARIA output:** Pattern B — "treatment review warranted"

**With this layer:** "Limited treatment response detected — sustained elevated BP (avg 158 mmHg) despite five-class antihypertensive regimen and confirmed adherence. Regimen complexity signal also active (5 classes). Clinician review of treatment ceiling recommended."

The signal is more specific and actionable than Pattern B alone.

---

### Scenario B — Regimen Complexity Flag
Two CCBs (Amlodipine + Sular) active simultaneously. Both are dihydropyridine CCBs with overlapping mechanisms.

**Current ARIA output:** No signal. Both medications show as active in `medication_status` field.

**With this layer:** "Regimen overlap detected — two calcium channel blockers (Amlodipine, Nisoldipine) active simultaneously. Combined benefit unproven; increased side-effect risk. Clinician review of regimen rationalisation recommended."

---

### Scenario C — Beta-blocker HR Response Check
Metoprolol active for 30+ days (past 14-day titration window). Average heart rate: 78 bpm. Pre-Metoprolol HR baseline from earlier readings: 79 bpm. Change: <1 bpm.

**Current ARIA output:** No signal.

**With this layer:** "Beta-blocker response signal: expected heart rate reduction not observed after 30+ days of Metoprolol. Possible absorption, dosing, or adherence issue. Clinician review of beta-blocker effectiveness warranted."

---

## 6. Value Added

This enhancement strengthens ARIA's core objective without scope creep:

| Before | After |
|---|---|
| "Is BP improving?" | "Is the patient responding to their specific regimen?" |
| Treats all medications identically | Evaluates by drug class and expected effect |
| Pattern B = all treatment failures look the same | Distinguishes limited response, over-treatment, and regimen complexity |
| Heart rate collected but unused clinically | Heart rate used as a secondary validation signal |
| Voltaren + antihypertensives → no flag | NSAID-antihypertensive interaction detectable (Phase 2) |

The layer remains:
- **Deterministic** — no LLM, no ML, no black box
- **Explainable** — every signal has a named condition and threshold
- **Safe** — signals are decision support only; language follows the same clinical boundary rules as the rest of ARIA
- **Lightweight** — no new infrastructure, no new data collection required for core version

---

## 7. What This Is Not

To be explicit about scope:

- It does **not** recommend specific dose changes
- It does **not** recommend adding or removing medications
- It does **not** expand ARIA beyond hypertension management
- It does **not** require patient-facing changes
- It does **not** require new hardware or vitals collection for the core version

Language rules carry over from existing ARIA clinical boundaries:
- "limited treatment response" not "medication failure"
- "regimen complexity signal" not "too many medications"
- "HR response mismatch" not "Metoprolol is not working"

---

## 8. Implementation Estimate

| Component | Effort estimate |
|---|---|
| `med_class_effects.py` — drug class → expected effect mapping | 1 day |
| `response_validator.py` — consistency evaluation logic | 2–3 days |
| Integration into `processor.py` `_handle_pattern_recompute()` | 0.5 days |
| Briefing composer — add `medication_response_assessment` field | 0.5 days |
| Unit tests (fixture-based, no real DB) | 1 day |
| **Total** | **~5–6 days** |

This is realistically a two-sprint effort if done properly, including clinical language review and edge-case testing. It is not feasible within the current presentation timeline, which is why it is proposed as documented future work rather than a live feature.

---

## 9. Why Not Now

The current presentation deadline does not allow time to:

1. Define and validate the HR baseline computation (requires access to pre-beta-blocker readings, which exist in the DB but need a dedicated query)
2. Clinically review the expected-effect mappings with a domain expert
3. Write sufficient unit tests to validate edge cases (sparse HR data, new prescriptions with no baseline, overlapping titration windows)
4. Integrate and test against the live demo patient without risk of breaking the existing working briefing pipeline

Shipping it half-implemented would undermine trust in the existing deterministic layer, which is already working correctly. A clearly documented proposal is more credible than a partially built feature.

---

## 10. Recommendation

Propose this layer as **Phase 8 — Post-Demo** on the project roadmap. The prerequisites are all already in place:

- Medication classes are parsed and stored
- Heart rate is captured per reading
- Titration window logic exists in `threshold_utils.py`
- Briefing JSONB is extensible without a schema migration

The only work required is the validation logic itself. This is a strong candidate for a future sprint because it directly addresses the most common critique of any clinical decision support system: "it tells me BP is high, but I already knew that — tell me *why* the treatment isn't working."

---

*Prepared for internal team review. Not for external distribution.*
*ARIA v4.3 | Leap of Faith Technologies | IIT CS 595 | Spring 2026*
