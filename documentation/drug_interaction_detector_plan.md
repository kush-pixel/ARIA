# Drug Interaction Detector — Implementation Plan
## ARIA v4.3 | Hypertension-Specific Scope

---

## Overview

ARIA currently has no mechanism to detect drug interactions. The briefing JSON has no
`drug_interactions` field and none of the pattern engine detectors check for medication
safety conflicts.

This plan adds a hypertension-scoped drug interaction checker that runs at briefing
generation time. It detects four clinically significant interaction patterns using data
already present in `clinical_context`. It does not require an external drug database.
It works for both monitoring-active and EHR-only patients, making it the primary
clinical value driver for the EHR-only demo scenario (Patient D).

---

## Scope Boundary

This detector covers interactions **involving the patient's antihypertensive regimen only**.

| In scope | Out of scope |
|---|---|
| NSAIDs + antihypertensives | Interactions between non-antihypertensive drugs |
| Triple whammy (NSAID + ACE/ARB + diuretic) | OTC medications not in the medication list |
| K-sparing diuretic + ACE inhibitor or ARB | Interactions requiring lab values (e.g. warfarin + aspirin INR) |
| Beta-blocker + non-DHP calcium channel blocker | Full BNF interaction database |
| Comorbidity severity amplification (CHF, CKD) | Cross-patient analysis |

This boundary must be stated explicitly in the briefing's `data_limitations` field so the
GP understands ARIA's interaction check is a targeted safety surface, not a comprehensive
prescribing check.

---

## The Four Interaction Rules

### Rule 1 — NSAID + Any Antihypertensive

**Clinical basis:**
NSAIDs cause sodium and water retention via COX-2 inhibition of prostaglandin-mediated
renal blood flow. This directly counteracts most antihypertensive drug classes:
- Diuretics (furosemide, bendroflumethiazide) lose effectiveness — less sodium excretion
- ACE inhibitors and ARBs lose vasodilatory effect — BP rises
- The net result is an average systolic rise of 3–5 mmHg, more in susceptible patients

In a patient with active CHF (I50), NSAIDs additionally worsen fluid overload by
promoting sodium and water retention, and are specifically contraindicated per NICE NG106.

**Detection logic:** NSAID present in `current_medications` AND at least one antihypertensive
present in `current_medications`.

**Severity:**
- Default: `warning`
- Escalated to `concern` if CHF (I50) or CKD (N18) is in `problem_codes`

---

### Rule 2 — Triple Whammy (NSAID + ACE inhibitor or ARB + Diuretic)

**Clinical basis:**
When an NSAID, an ACE inhibitor or ARB, and a diuretic are all present simultaneously,
the combined haemodynamic effect significantly elevates acute kidney injury (AKI) risk.
This is a named interaction pattern in MHRA guidance (2017 Drug Safety Update) and NICE
prescribing guidance. The mechanism:
- NSAID reduces renal prostaglandin synthesis → renal vasoconstriction
- ACE inhibitor/ARB reduces efferent arteriole tone → reduced glomerular filtration
- Diuretic reduces circulating volume → compound renal hypoperfusion

This is not a separate check from Rule 1 — it is a severity escalation when all three
drug classes are simultaneously present. The visit agenda item should be more urgent
than a standalone NSAID + antihypertensive flag.

**Detection logic:** Rule 1 fires AND ACE inhibitor or ARB present AND diuretic (any
class) present.

**Severity:** Always `concern` regardless of comorbidity status.

---

### Rule 3 — Potassium-Sparing Diuretic + ACE Inhibitor or ARB

**Clinical basis:**
Both potassium-sparing diuretics (spironolactone, amiloride, eplerenone) and ACE
inhibitors/ARBs raise serum potassium — ACE inhibitors/ARBs by reducing aldosterone,
K-sparing diuretics by blocking the aldosterone receptor or the epithelial sodium
channel directly. Combined, they carry a clinically significant hyperkalaemia risk,
particularly in elderly patients and those with CKD.

This combination is extremely common in CHF patients because spironolactone (an
aldosterone antagonist) is recommended by NICE NG106 for heart failure with reduced
ejection fraction — the same patients who are typically on lisinopril or ramipril.
Patient 1091 has active CHF and an ACE inhibitor; if spironolactone were ever added,
this rule would fire.

**Detection logic:** K-sparing diuretic present AND (ACE inhibitor present OR ARB
present) in `current_medications`.

**Severity:**
- Default: `warning`
- Escalated to `concern` if CKD (N18) is in `problem_codes`

---

### Rule 4 — Beta-Blocker + Non-Dihydropyridine Calcium Channel Blocker

**Clinical basis:**
Non-dihydropyridine calcium channel blockers (verapamil, diltiazem) are rate-limiting —
they slow heart rate and AV conduction via calcium channel blockade in cardiac tissue.
Beta-blockers (metoprolol, atenolol, bisoprolol) also slow heart rate and AV conduction
via beta-1 blockade. The additive effect of combining both can cause bradycardia,
AV block, or asystole. This is a named contraindication in the BNF.

This rule is distinct from dihydropyridine CCBs (amlodipine, nifedipine, felodipine)
which act predominantly on vascular smooth muscle, not cardiac conduction. The
detection must distinguish between the two CCB subclasses.

**Detection logic:** Beta-blocker present AND non-DHP CCB (verapamil or diltiazem)
present in `current_medications`.

**Severity:** Always `concern`. No comorbidity amplification — the cardiac conduction
risk is present regardless.

---

## Drug Class Classification

The existing `threshold_utils.py` already infers ACE inhibitors, ARBs, beta-blockers,
and dihydropyridine CCBs by name suffix for titration window lookup. The interaction
detector extends this with additional drug classes using the same approach: name-suffix
patterns where reliable, explicit name lists where suffixes are not distinctive.

### Extended Classification Table

| Drug class | Detection method | Drug names / suffixes |
|---|---|---|
| ACE inhibitor | Suffix `-pril` | lisinopril, ramipril, perindopril, enalapril, captopril |
| ARB | Suffix `-sartan` | losartan, candesartan, valsartan, irbesartan, olmesartan |
| Beta-blocker | Suffix `-olol` | metoprolol, atenolol, bisoprolol, carvedilol, nebivolol |
| DHP CCB | Suffix `-dipine` | amlodipine, nifedipine, felodipine, lercanidipine |
| Non-DHP CCB | Explicit list | verapamil, diltiazem |
| Loop diuretic | Explicit list | furosemide, bumetanide, torasemide |
| Thiazide diuretic | Explicit list | bendroflumethiazide, indapamide, hydrochlorothiazide, chlortalidone |
| K-sparing diuretic | Explicit list | spironolactone, amiloride, eplerenone, triamterene |
| NSAID | Explicit list | ibuprofen, diclofenac, naproxen, celecoxib, meloxicam, indometacin, piroxicam, ketoprofen, mefenamic acid |

**Matching approach:** Case-insensitive substring match against `current_medications`
TEXT array entries. RxNorm code matching against `med_rxnorm_codes` as a secondary
check where codes are populated. Name matching is sufficient for the demo and for
realistic FHIR bundle data.

**Combination drugs:** Entries like "co-amilofruse" (amiloride + furosemide) must be
recognised. These combination names should be added to both the K-sparing and loop
diuretic lists explicitly.

---

## Comorbidity Severity Amplification

Comorbidities do not add new interaction rules — they escalate the severity of existing
ones. The relevant comorbidity codes come from `clinical_context.problem_codes` (ICD-10).

| Comorbidity | ICD-10 | Effect on rules |
|---|---|---|
| Congestive Heart Failure | I50 | NSAID interactions escalate to `concern`; add CHF-specific warning text |
| Chronic Kidney Disease | N18 | NSAID interactions escalate to `concern`; K-sparing + ACE/ARB escalates to `concern` |
| Both CHF and CKD | I50 + N18 | Triple whammy escalates to `critical` |

`critical` severity produces a different visit agenda item text and sits at priority
position 1, above even urgent alerts.

---

## Architecture Placement

### Where It Does NOT Live

The drug interaction check is not a Layer 1 pattern detector in the same sense as gap,
inertia, adherence, or deterioration. Those detectors analyse time-series patterns
across an adaptive window of readings data. Drug interaction detection is a point-in-time
check on the medication list — it does not involve readings at all.

It therefore does not belong in the `pattern_engine/` service. Adding it there would
misrepresent the architecture and create confusion about what a "pattern" is.

### Where It Lives

The check belongs in the **briefing composer** (`backend/app/services/briefing/composer.py`)
as a dedicated private function `_check_drug_interactions()`, called during briefing
generation alongside the existing detector result aggregation.

A helper module `backend/app/services/briefing/medication_safety.py` should contain:
- The drug class lookup tables (constants, not hardcoded inline)
- The `classify_drug_classes()` function (extends threshold_utils logic)
- The four rule functions
- The severity escalation logic

The composer calls `medication_safety.check_interactions(clinical_context)` and receives
a structured list of interaction findings that it inserts into the briefing payload.

This placement means:
- The check runs for ALL patients at briefing generation time — both monitoring-active
  and EHR-only
- It receives the same `clinical_context` object already loaded by the composer
- No new DB queries required
- It is testable in isolation from the rest of the briefing pipeline

---

## Briefing JSON Changes

### New Field: `drug_interactions`

Added to the briefing JSON structure in `composer.py`. Position: after `overdue_labs`,
before `visit_agenda`.

```
drug_interactions: [
  {
    "rule": "nsaid_antihypertensive" | "triple_whammy" | "k_sparing_ace_arb" | "bb_non_dhp_ccb",
    "severity": "warning" | "concern" | "critical",
    "drugs_involved": ["diclofenac", "lisinopril", "furosemide"],
    "description": "Plain-language description for the GP",
    "comorbidity_amplified": true | false
  }
]
```

If no interactions are detected, `drug_interactions` is an empty list `[]`. It is never
`null` — an empty list is unambiguous.

`description` is a short, clinician-facing string generated deterministically from the
rule and severity. It does not use the LLM. Examples:

- Rule 1 (warning): "NSAID use may reduce effectiveness of antihypertensive regimen"
- Rule 1 (concern, CHF): "NSAID use in a patient with active CHF — sodium retention
  risk; review indicated"
- Rule 2 (concern): "Triple whammy combination (NSAID + ACE inhibitor + diuretic) —
  AKI risk; review renal function"
- Rule 3 (warning): "Potassium-sparing diuretic with ACE inhibitor — monitor for
  hyperkalaemia"
- Rule 4 (concern): "Beta-blocker combined with rate-limiting CCB — bradycardia risk"

---

## Visit Agenda Integration

Each detected interaction adds a visit agenda item. Priority positioning:

| Severity | Agenda position | Item text prefix |
|---|---|---|
| `critical` | Position 1 (above urgent alerts) | "CRITICAL: " |
| `concern` | Position 2 (alongside urgent alerts) | "Drug interaction — " |
| `warning` | Position 4 (after inertia, before overdue labs) | "Medication review — " |

If multiple interactions are detected, each gets its own agenda item. The triple whammy
(`concern`) and a standalone NSAID flag (`warning`) on the same patient would produce
two separate items — the triple whammy at position 2, the NSAID-only flag suppressed
(Rule 2 supersedes Rule 1 when both fire simultaneously).

---

## Layer 3 Integration

The LLM summary in Layer 3 (`summarizer.py`) already generates its 3-sentence summary
from the full briefing JSON payload. Because `drug_interactions` is a new field in that
payload, the Layer 3 prompt and validator need two small updates:

### Prompt update (`prompts/briefing_summary_prompt.md`)

Add `drug_interactions` to the list of fields the model is instructed to reference.
Instruct the model: if `drug_interactions` is non-empty and contains a `concern` or
`critical` severity item, it must be mentioned in the summary.

### Validator update (`llm_validator.py`)

Add a faithfulness check: if `drug_interactions` contains a `concern` or `critical`
item and the summary does not reference a drug interaction or medication safety concern,
the validation fails. This prevents the LLM from silently ignoring a critical flag in
its summary.

Existing guardrails already block drug name recommendations and dosage instructions,
so no new guardrails are needed — the model can mention the interaction type without
risk of crossing the clinical boundary.

---

## Data Limitations Text

When `drug_interactions` is non-empty, append the following to `data_limitations`:

> "Drug interaction check covers hypertension-related interactions only (NSAIDs,
> potassium-sparing diuretics, rate-limiting calcium channel blockers). OTC medications
> and interactions not involving the antihypertensive regimen are not assessed."

When `drug_interactions` is empty, no addition to `data_limitations` is needed.

---

## Implementation Steps

### Step 1 — Create `medication_safety.py`

New file: `backend/app/services/briefing/medication_safety.py`

Contains:
- Drug class lookup constants (the classification table above)
- `classify_drug_classes(medications: list[str], rxnorm_codes: list[str]) -> dict[str, list[str]]`
  Returns a dict mapping drug class name to the matched medication names.
- `check_interactions(clinical_context: ClinicalContext) -> list[dict]`
  Runs all four rules and returns the structured interaction list.
- Private rule functions: `_rule_nsaid_antihypertensive`, `_rule_triple_whammy`,
  `_rule_k_sparing_ace_arb`, `_rule_bb_non_dhp_ccb`
- `_escalate_severity(base_severity, problem_codes)` — applies comorbidity amplification

### Step 2 — Extend `threshold_utils.py`

The existing `_infer_drug_class()` function should be refactored or extended so that
`medication_safety.py` can call the same drug class inference logic without duplication.
The lookup tables in Step 1 and the suffix patterns in `threshold_utils.py` should not
diverge.

### Step 3 — Update `composer.py`

Add `drug_interactions` field to the briefing payload. Call
`medication_safety.check_interactions(clinical_context)` during briefing assembly.
Insert visit agenda items for each interaction finding per the priority rules above.
Add `data_limitations` text when interactions are found.

### Step 4 — Update briefing JSON schema

Any TypeScript types in `frontend/src/lib/types.ts` for the briefing payload need a
`drug_interactions` field added. The `BriefingCard` component needs to render the
interactions section if the list is non-empty.

### Step 5 — Update Layer 3

Update `prompts/briefing_summary_prompt.md` to reference `drug_interactions`.
Add the faithfulness check to `llm_validator.py`.

### Step 6 — Tests

Unit tests for `medication_safety.py`:
- Each rule fires correctly with the matching drug combination
- Each rule does not fire when only one drug class is present
- Comorbidity escalation applies correctly
- Triple whammy supersedes standalone NSAID rule
- Empty list returned when no interactions detected
- Case-insensitive matching works
- Combination drug names (co-amilofruse) are classified correctly

Integration test: full briefing generation for Patient D (EHR-only, NSAID + lisinopril
+ furosemide + CHF) produces briefing with `drug_interactions` containing the triple
whammy at `concern` severity.

---

## Demo Dependency

Patient D in the demo (EHR-only scenario) depends on this detector being implemented
and producing output. The demo cannot show the NSAID interaction flag until Steps 1–4
are complete and the briefing for Patient D has been regenerated.

Patient D setup requirements are documented in `documentation/demo_shift.md`.

---

## What This Does NOT Replace

This detector does not replace:
- Point-of-prescribing interaction checking (that is the prescribing system's job)
- Lab-value-dependent interaction risk (e.g. warfarin + NSAIDs requires INR; out of scope)
- Allergy checking (already handled via `clinical_context.allergies`)
- Monitoring of actual drug levels or renal function trends

ARIA's interaction check is a **pre-visit briefing surface** — it surfaces known risks
so the GP can discuss them in the consultation. It does not take clinical action and
does not produce prescribing recommendations.
