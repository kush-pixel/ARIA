# ARIA FHIR Service Context
## adapter.py | validator.py | ingestion.py

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Files in This Directory

```
adapter.py    — iEMR JSON → FHIR R4 Bundle conversion
validator.py  — structural pre-validation of any FHIR Bundle
ingestion.py  — FHIR Bundle → PostgreSQL (patients, clinical_context, readings, audit_events)
```

---

## adapter.py — Public API

```python
def convert_iemr_to_fhir(
    iemr_data: dict[str, Any],
    patient_id: str | None = None,
) -> dict[str, Any]
```

Converts a raw iEMR patient JSON blob to a FHIR R4 Bundle.
- `iemr_data`: Top-level iEMR dict with a `VISIT` array (array of visit objects).
- `patient_id`: Override for `MED_REC_NO`. If `None`, reads from `iemr_data["MED_REC_NO"]`.
- Returns: A FHIR Bundle dict with `resourceType="Bundle"`, `entry` array, and `_aria_med_history` key.

### Output: 6 FHIR Resource Types

| Resource type        | iEMR source           | Dedup strategy                   |
|----------------------|-----------------------|----------------------------------|
| Patient              | VISIT[].GENDER, AGE   | singleton (one per bundle)       |
| Condition            | VISIT[].PROBLEM[]     | by PROBLEM_CODE, most-recent wins|
| MedicationRequest    | VISIT[].MEDICATIONS[] | by MED_CODE + secondary by name  |
| Observation          | VISIT[].VITALS[]      | no dedup — every entry included  |
|                      | Includes BP (LOINC 55284-4), pulse (8867-4), weight (29463-7), SpO2 (59408-5), temp (8310-5) |
| AllergyIntolerance   | VISIT[].ALLERGY[]     | by ALLERGY_CODE, most-recent wins|
|                      | Filter: ALLERGY_STATUS == "Active" only; include ALLERGY_REACTION in manifestation |
| ServiceRequest       | VISIT[].PLAN[]        | by PLAN_CODE, most-recent wins   |

### Non-standard Bundle Keys (appended to bundle root, NOT in bundle["entry"])

```python
bundle["_aria_med_history"]          = _build_med_history(visits)
bundle["_aria_problem_assessments"]  = _build_problem_assessments(visits)
bundle["_aria_visit_dates"]          = _build_visit_dates(visits)
```

`_aria_med_history`: full medication timeline across all visits.
  → ingestion stores in clinical_context.med_history JSONB.

`_aria_problem_assessments`: per-visit HTN/problem assessment data.
  Format: [{problem_code, visit_date, htn_flag, status_text, assessment_text}, ...]
  → ingestion stores in clinical_context.problem_assessments JSONB.
  Surfaced by briefing composer as "CHF — last assessed: Under Evaluation (2026-01-14)"

`_aria_visit_dates`: ADMIT_DATE from all 124 visits regardless of visit type.
  → ingestion sets clinical_context.last_visit_date = max(all_visit_dates)
  Currently last_visit_date is set only from BP clinic dates (misses 71 non-vitals visits)

### _build_med_history — Return Shape

```python
[{"name": str, "rxnorm": str | None, "date": str | None, "activity": str | None}, ...]
```

Sorted chronologically ascending by `date` (null dates last).
Deduped by `(name, date, activity)` tuple — not by MED_CODE.
Patient 1091: **104 entries** in med_history.

---

## adapter.py — 5 Noise Filters (applied in order)

### Filter 1: Discontinued medications (cross-MED_CODE)

**Sentinel/tombstone pattern** — two-pass deduplication:
- Pass 1: For each `MED_ACTIVITY == "Discontinue"` entry, set `seen[MED_CODE] = None` (tombstone) and add the normalised drug name to `discontinued_names: set[str]`.
- Pass 2 (secondary dedup): Skip any resource whose `medicationCodeableConcept.text.upper()` is in `discontinued_names`.

Required because iEMR assigns a new MED_CODE to each refill. A drug discontinued under code B will not tombstone code A without this name-propagation step.
Patient 1091 example: BYETTA discontinued under MED_CODE 26604040, active under 26592850 — the name-propagation catches it.
Drugs removed from patient 1091: BYETTA, SIMVASTATIN, CRESTOR, DICLOXACILLIN, PENICILLIN.

### Filter 2: Non-drug MEDICATIONS entries

Supply and test items stored alongside pharmaceuticals are filtered by:
```python
_NON_DRUG_MARKERS: tuple[str, ...] = (
    "RX FOR ",       # device/therapy scripts ("Rx for Compression Stockings")
    "SYRINGE",       # injection supply containers
    "SHARPS",        # sharps disposal bins
    " CONTAINER",    # generic supply containers (space-prefixed to avoid "Retainer")
    "PEN NEEDLE",    # insulin pen needles
    " TEST",         # diagnostic tests (space-prefixed to avoid "Attest")
)
_NON_DRUG_EXACT: frozenset[str] = frozenset({"VNG"})
```
Matched against `upper(MED_NAME + " " + MED_DOSE)`.

### Filter 3: Z00.xx encounter codes from conditions

```python
if icd10 and icd10.startswith("Z00"):
    continue
```

Z00 is the ICD-10 encounter-type series (e.g. Z00.00 = "General adult medical examination"). These are administrative billing codes, not clinical problems.
Patient 1091: 18 raw conditions → **17 conditions** after this filter.

### Filter 4: Non-clinical ServiceRequest items

```python
if "Dr." in description or "XXXXXXXXX" in description:
    continue
if description.startswith(("Instructions for", "General Advice")):
    continue
```

Removes physician names ("Dr. JONES"), redacted vendor names ("XXXXXXXXX"), and patient education boilerplate.
Patient 1091: 10 raw PLAN items → **6 follow-up items** after this filter.

### Filter 5: Secondary medication name deduplication

After MED_CODE-level dedup, a second pass is keyed by `medicationCodeableConcept.text.upper().strip()` to collapse the same drug appearing under multiple MED_CODEs (e.g. NAMENDA appearing 3 times → 1 entry).
Patient 1091: 38 raw medication entries → **14 current medications** after all 5 filters.

---

## adapter.py — CRITICAL Observation Rule

```
effectiveDateTime MUST come from VITALS_DATETIME.
NEVER use ADMIT_DATE for Observation timestamps.
```

Both fields exist on the same VITALS object. `ADMIT_DATE` is the administrative admission date; `VITALS_DATETIME` is when the BP was actually measured.

---

## validator.py — Public API

```python
def validate_fhir_bundle(bundle: dict[str, Any]) -> list[str]
```

- Returns a list of error strings. Empty list = bundle is valid.
- **Never raises.** All errors are collected and returned as strings.
- Checks performed (in order):
  1. `bundle` must be a `dict`.
  2. `bundle["resourceType"]` must equal `"Bundle"`.
  3. At least one Patient resource must exist in `bundle["entry"]`.
  4. The Patient resource must have a non-empty `"id"` field.

Usage pattern (always check before ingesting):
```python
errors = validate_fhir_bundle(bundle)
if errors:
    raise ValueError(f"Invalid bundle: {'; '.join(errors)}")
```

---

## ingestion.py — Public API

```python
async def ingest_fhir_bundle(
    bundle: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any]
```

Returns:
```python
{
    "patient_id":                str | None,
    "patients_inserted":         int,   # 0 (existing) or 1 (new)
    "clinical_context_upserted": int,   # always 1 on success
    "readings_inserted":         int,   # 0 on re-run (batch idempotency)
    "audit_events_inserted":     int,   # always 1
}
```

Raises:
- `ValueError`: Bundle has no Patient resource.
- Any `SQLAlchemyError` is re-raised after rollback. A failure audit event is committed before re-raise.

### Idempotency Strategy

| Table             | Strategy                                                           |
|-------------------|--------------------------------------------------------------------|
| patients          | `INSERT … ON CONFLICT DO NOTHING` on patient_id PK               |
| clinical_context  | `INSERT … ON CONFLICT DO UPDATE` — refreshed each run             |
| readings          | Per-observation `ON CONFLICT DO NOTHING` on UNIQUE (patient_id, effective_datetime, source) |
|                   | NEW clinic readings from subsequent visits insert cleanly alongside existing ones |
|                   | OLD strategy (batch COUNT check) blocked ALL inserts if any clinic readings existed |
| audit_events      | Always appended — one row per ingestion attempt                    |

Patient 1091: **65 clinic readings** on first run; subsequent runs add only new observations.
UNIQUE INDEX required: `CREATE UNIQUE INDEX idx_readings_patient_datetime_source ON readings (patient_id, effective_datetime, source)`

### Risk Tier Auto-Overrides (applied at ingestion)

```python
def _determine_risk_tier(problem_codes: list[str]) -> tuple[str, str | None]:
```

| ICD-10 prefix | tier_override           | risk_tier |
|---------------|-------------------------|-----------|
| I50           | "CHF in problem list"   | "high"    |
| I63 or I64    | "Stroke history"        | "high"    |
| G45           | "TIA history"           | "high"    |
| (no match)    | None                    | "medium"  |

Patient 1091: I50.9 (CHF) triggers `risk_tier="high"`, `tier_override="CHF in problem list"`.

### Processing Order (respects FK dependencies)

```
1. Patient resource   → patients table
2. All resources      → clinical_context (pre-computed from all resource types + _aria_med_history)
3. Observation        → readings table (source="clinic", submitted_by="clinic", session="ad_hoc")
4. Audit event        → audit_events (always, even on failure)
```

---

## Dependencies

- `adapter.py` → called by `scripts/run_adapter.py` and `processor.py` (_handle_bundle_import)
- `validator.py` → called by `app/api/ingest.py` and `processor.py` before any ingest
- `ingestion.py` → called by `app/api/ingest.py` (POST /api/ingest) and `processor.py`
- `_aria_med_history` key: written by `adapter.py`, consumed by `ingestion.py` → `clinical_context.med_history`

---

## DO NOT

- Do NOT use `ADMIT_DATE` for Observation `effectiveDateTime` — always `VITALS_DATETIME`
- Do NOT deduplicate Observations — every clinic vitals entry becomes its own Reading row
- Do NOT use `continue` alone for discontinued medications — must also tombstone via `seen[key] = None`
- Do NOT add physician names ("Dr.") to any clinical list (problems, follow-ups, medications)
- Do NOT call `ingest_fhir_bundle()` without first calling `validate_fhir_bundle()`
- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()` only
- Do NOT use most-recent-wins for Observations — they are never deduplicated
- Do NOT use batch COUNT check for readings idempotency — use per-row ON CONFLICT DO NOTHING
- Do NOT include inactive allergies (ALLERGY_STATUS != "Active") in the bundle
- Do NOT omit _aria_problem_assessments — physician assessment data is required for briefing composer
- Do NOT set last_visit_date from BP dates only — use _aria_visit_dates to get max across all 124 visits
- Do NOT discard PULSE, WEIGHT, SpO2, TEMPERATURE from vitals — all four must become Observation resources
