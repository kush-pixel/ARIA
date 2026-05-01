# ARIA — Risk Tier Reclassification System
## Technical Change Record
**Version 1.0 | May 2026 | Branch: Kush**

---

## 1. Overview

This document describes the clinical logic overhaul to ARIA's risk tier system, implemented in May 2026. The change resolves six interconnected problems with how patient risk tier is assigned, updated, and protected. All changes are live on the `Kush` branch.

**Risk tier** is the categorical label (`high` | `medium` | `low`) shown on the clinician dashboard. It is distinct from **risk score** (the continuous 0.0–100.0 float computed by Layer 2 scoring). Both live on the `patients` table. The dashboard sorts by tier first, then by risk score descending within each tier.

---

## 2. Problems Resolved

| # | Problem | Impact |
|---|---|---|
| 1 | `patients` INSERT used `ON CONFLICT DO NOTHING` — tier never updated on re-ingestion | CHF added to EHR mid-care would not promote the patient to high |
| 2 | `_determine_risk_tier()` missing ICD-10 I61 (haemorrhagic stroke) | Patient with haemorrhagic stroke history left at medium tier |
| 3 | `low` tier was unreachable by any automated path | Ingestion only ever produced `high` or `medium` — low was inert |
| 4 | A medium patient at risk score 95 sorted below a stable high CHF patient at score 15 | Within-tier sorting was meaningless without promoting the score-driven outlier |
| 5 | No clinician override endpoint — tier could only change via direct DB write | No auditable, guarded path for clinical judgement to override the algorithm |
| 6 | No `tier_override_source` distinction — nightly job could not tell system override from clinician override | Nightly reclassification could silently undo a deliberate clinical decision |

---

## 3. New Database Columns

Two columns added to the `patients` table:

```sql
ALTER TABLE patients ADD COLUMN IF NOT EXISTS tier_override_source TEXT;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS tier_override_suppressed_until TIMESTAMPTZ;
```

Migration script: `python scripts/setup_db.py` — idempotent, safe to re-run.

### `tier_override_source` — valid values

| Value | Set by | Meaning |
|---|---|---|
| `"system"` | FHIR ingestion | Auto-override from CHF / Stroke / TIA / haemorrhagic stroke. **Immovable floor** — the nightly job and the clinician endpoint both refuse to touch it. Requires updating the EHR problem list and re-ingesting. |
| `"system_score"` | Nightly reclassification job | Tier promoted or demoted by Layer 2 score crossing a threshold band. Can be overridden by clinician. |
| `"clinician"` | `PATCH /api/patients/{id}/tier` endpoint | Manually set by a clinician with a reason string. Protected by a 14-day suppression window. |
| `NULL` | Default | No active override. Patient is at default medium with no special classification. |

### `tier_override_suppressed_until`

Set when a clinician **demotes** a patient (e.g. high → medium). The nightly reclassification job will not promote the patient back until this timestamp expires. Break-glass at score ≥ 85 overrides the suppression.

---

## 4. FHIR Ingestion Changes

**File:** `backend/app/services/fhir/ingestion.py`

### 4.1 I61 Haemorrhagic Stroke Added

`_determine_risk_tier()` now checks for ICD-10 `I61` (haemorrhagic stroke) between I50 and I63/I64:

```
I50  → CHF → high (system)
I61  → Haemorrhagic stroke → high (system)   ← NEW
I63/I64 → Ischaemic stroke → high (system)
G45  → TIA → high (system)
default → medium (no source)
```

The function now returns a triple `(tier, tier_override_reason, tier_override_source)`.

### 4.2 Re-ingestion Tier Update (Two-Step Upsert)

The old `ON CONFLICT DO NOTHING` was replaced with a two-step approach that correctly handles all four `tier_override_source` states:

**Step A — Demographics update (always runs)**
```python
INSERT INTO patients (patient_id, gender, age, ...)
ON CONFLICT (patient_id) DO UPDATE SET gender=..., age=...
```
Demographics are always safe to overwrite. Tier columns are excluded here.

**Step B — Conditional tier update**
```python
UPDATE patients SET risk_tier=..., tier_override=..., tier_override_source=...
WHERE patient_id = ?
  AND (
      -- FHIR computed a system override → always apply (CHF added/removed)
      new_source == "system"
      OR
      -- No active override to protect → apply freely
      current_source IS NULL OR current_source == "system"
  )
```

**What this preserves:**

| Current state | Re-ingest with system condition | Re-ingest without system condition |
|---|---|---|
| `tier_override_source = NULL` | Promotes to high, sets `"system"` | Updates to computed tier |
| `tier_override_source = "system"` | Updates to new system tier (e.g. CHF removed → medium) | Updates to medium, clears source |
| `tier_override_source = "clinician"` | Promotes to high, sets `"system"` (safety wins) | **Preserved** — clinician override protected |
| `tier_override_source = "system_score"` | Promotes to high, sets `"system"` | **Preserved** — score-driven tier protected |

---

## 5. Nightly Reclassification

**File:** `backend/app/services/worker/processor.py`

A new function `_apply_tier_reclassification(session, patient_id, score)` runs at the end of every `pattern_recompute` job, after Layer 2 scoring completes.

### 5.1 Hysteresis Thresholds

| Transition | Score condition | Additional gates |
|---|---|---|
| `medium → high` | score ≥ 75 | None |
| `high → medium` | score < 40 | `tier_override_source == "system_score"` only — never demotes a `"system"` patient |
| `medium → low` | score < 25 | Enrolled ≥ 90 days + no significant comorbidity + no active urgent alerts |
| `low → medium` | score ≥ 40 | None |

The gap between 40 and 75 is intentional. A patient holding steady in that band does not change tier in either direction. This prevents oscillation without requiring a previous-score history column.

### 5.2 Guard Order (evaluated top-to-bottom, first match wins)

```
1. tier_override_source == "system"
       → return immediately. Immovable floor.

2. tier_override_source == "clinician" AND now < tier_override_suppressed_until
       AND score < 85
       → return. Clinician's decision is active.

3. tier_override_source == "clinician" AND now < tier_override_suppressed_until
       AND score >= 85
       → break-glass. Clear suppressed_until. Apply promotion check only.

4. Apply hysteresis transition table above.

5. On any tier change: write AuditEvent + commit.
```

### 5.3 Comorbidity Block for Medium → Low

Demotion to `low` is blocked if `clinical_context.problem_codes` contains any code starting with:

| Prefix | Condition | Severity |
|---|---|---|
| I50 | CHF | Severe |
| I61 | Haemorrhagic stroke | Severe |
| I63, I64 | Ischaemic stroke | Severe |
| G45 | TIA | Severe |
| E11 | Diabetes | Moderate |
| N18 | CKD | Moderate |
| I25 | CAD | Moderate |

A patient with any of these active cannot reach `low` tier regardless of score.

### 5.4 Audit Trail

Every tier change writes an `audit_events` row:

```
actor_type = "system"
actor_id   = "tier_reclassifier"
action     = "tier_reclassified"
details    = "tier=medium→high score=78.4 source=system_score"
```

---

## 6. Clinician Override Endpoint

**File:** `backend/app/api/patients.py`

```
PATCH /api/patients/{patient_id}/tier
```

**Request body:**
```json
{
  "risk_tier": "medium",
  "reason": "BP controlled over 6 months, patient stable"
}
```

`risk_tier` must be one of `"high"` | `"medium"` | `"low"`. `reason` is required, 1–500 characters.

**Response codes:**

| Code | Condition |
|---|---|
| 200 | Override applied — returns updated patient dict |
| 404 | Patient not found |
| 409 | Auto-override active (`tier_override_source == "system"`) — EHR update required |
| 422 | Validation error — invalid tier string or empty reason |

**Demotion (e.g. high → medium):** sets `tier_override_suppressed_until = now + 14 days`.

**Promotion (e.g. medium → high):** sets `tier_override_suppressed_until = NULL`.

**Note on system overrides:** A patient in high tier due to CHF, stroke, TIA, or haemorrhagic stroke (`tier_override_source = "system"`) cannot be demoted via this endpoint. The HTTP 409 response instructs the clinician to update the EHR problem list and re-ingest the FHIR bundle. This is intentional — the auto-override exists because missing these conditions has direct patient safety implications.

---

## 7. Patient Serialiser Fields Added

The `_serialise()` function in `patients.py` now includes:

| Field | Type | Description |
|---|---|---|
| `tier_override_source` | `string \| null` | One of `"system"` \| `"system_score"` \| `"clinician"` \| `null` |
| `tier_override_suppressed_until` | `ISO 8601 string \| null` | Expiry of clinician demotion suppression window |

---

## 8. Files Changed

| File | Change summary |
|---|---|
| `scripts/setup_db.py` | Added 2 migration blocks for new columns |
| `backend/app/models/patient.py` | Added `tier_override_source` and `tier_override_suppressed_until` mapped columns |
| `backend/app/services/fhir/ingestion.py` | I61 added; `_determine_risk_tier()` returns triple; two-step upsert replaces `ON CONFLICT DO NOTHING` |
| `backend/app/services/worker/processor.py` | `_apply_tier_reclassification()` added; called after `compute_risk_score()` in nightly job |
| `backend/app/api/patients.py` | `PATCH /tier` endpoint; `_serialise()` updated with 2 new fields |
| `backend/tests/test_ingestion.py` | I61 tests; mock sequences updated for two-step upsert |
| `backend/tests/test_api.py` | 6 tests covering all override endpoint paths |
| `backend/tests/test_pattern_engine.py` | 9 reclassification tests covering all transitions and edge cases |

---

## 9. Deployment Steps

1. Run database migration (safe to re-run — uses `IF NOT EXISTS`):
   ```bash
   conda activate aria
   python scripts/setup_db.py
   ```

2. Restart the backend server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

3. Verify migration applied:
   ```sql
   SELECT column_name FROM information_schema.columns
   WHERE table_name = 'patients'
   AND column_name IN ('tier_override_source', 'tier_override_suppressed_until');
   ```

No frontend changes required. The two new fields are additive to the patient JSON — the existing dashboard renders them as-is without breaking.

---

## 10. Test Coverage

| Test file | Tests added | Coverage |
|---|---|---|
| `test_ingestion.py` | I61 haemorrhagic stroke; re-ingest promotes tier; re-ingest preserves monitoring_active | Ingestion upsert logic |
| `test_api.py` | Clinician promotion; demotion + suppression window; 409 block; 404; 422 invalid tier; 422 empty reason | All endpoint branches |
| `test_pattern_engine.py` | medium→high; no change in band (score 55); high→medium system_score only; medium→low blocked by comorbidity; medium→low blocked by enrollment; low→medium; break-glass override | All reclassification paths |

All 601 unit tests pass. `ruff check app/` clean.
