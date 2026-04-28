# ARIA — Full Change Report
## Current System + Patient App Plan | All Recommendations
**Date:** 2026-04-27
**Author:** Kush Patel (based on ARIA_PatientApp_BLE_Plan.docx authored by Sahil Khalsa)
**Scope:** AUDIT.md Fix 43 + Fix 44 + downstream system impact

---

## Table of Contents

1. [Part 1 — Existing ARIA System Changes](#part-1--existing-aria-system-changes)
   - 1.1 Database Schema
   - 1.2 Briefing JSON Specification
   - 1.3 Briefing Composer
   - 1.4 Pattern Engine — Symptom-Based Alert Logic
   - 1.5 Shared Utilities
   - 1.6 Configuration
   - 1.7 CORS Configuration
   - 1.8 Audit Compliance
   - 1.9 LLM Validator Updates
2. [Part 2 — Patient App Plan Changes](#part-2--patient-app-plan-changes)
   - 2.1 Architecture
   - 2.2 Patient PWA — Submission Form
   - 2.3 Acceptance Criteria — Additions
   - 2.4 Effort Estimate Adjustment
3. [Priority Order for Implementation](#priority-order-for-implementation)

---

## Part 1 — Existing ARIA System Changes

These are changes to the current backend/frontend that must happen regardless of whether the patient app lands.

---

### 1.1 Database Schema

#### readings table — add symptoms column

```sql
ALTER TABLE readings ADD COLUMN IF NOT EXISTS symptoms TEXT[];
```

Required by the patient app plan but also relevant to the BLE bridge path — any reading ingestion point may eventually carry symptom context. Add this migration to `setup_db.py` using the existing `ADD COLUMN IF NOT EXISTS` pattern so it is safe to re-run.

#### alerts table — add new alert_type value

The existing `alert_type` enum-by-convention is:
```
gap_urgent | gap_briefing | inertia | deterioration | adherence
```

Add `symptom_urgent` to cover patient-reported escalations (chest pain, shortness of breath in CHF). Without this, the clinician alert inbox cannot distinguish a pattern-detector alert from a patient-reported emergency. This is a data-level change — update any application code that switches on `alert_type`, including `AlertInbox.tsx` on the frontend.

---

### 1.2 Briefing JSON Specification

`CLAUDE.md` defines the briefing JSON structure with these top-level keys: `trend_summary`, `medication_status`, `adherence_summary`, `active_problems`, `problem_assessments`, `overdue_labs`, `visit_agenda`, `urgent_flags`, `risk_score`, `data_limitations`.

#### Add `patient_reported_symptoms` field

```json
"patient_reported_symptoms": [
  {
    "symptom": "chest_pain",
    "reported_at": "2026-04-27T06:42:00Z",
    "concurrent_systolic": 185,
    "concurrent_diastolic": 110
  }
]
```

Without this field, symptom data collected via the PWA exists in the DB but is invisible to the Layer 1 briefing payload and therefore also invisible to Layer 3. The LLM summary has no way to mention patient-reported symptoms faithfully if they are not in the composer payload. Update `CLAUDE.md` briefing JSON spec to include this field.

---

### 1.3 Briefing Composer

**File:** `backend/app/services/briefing/composer.py`

Must be updated to:

1. Query `readings.symptoms` for the adaptive window period and aggregate any non-null symptom arrays
2. Pair each symptom occurrence with the concurrent `systolic_avg` and `effective_datetime` from the same reading row
3. Populate `patient_reported_symptoms` in the briefing payload
4. Surface any `symptom_urgent` alerts in `urgent_flags` (same mechanism as existing gap/inertia alerts) — include concurrent BP value in the flag text
5. Add dizziness entries to `visit_agenda` (not `urgent_flags`) with the text:
   > *"Patient reported dizziness — consider positional BP assessment, may indicate over-treatment"*

---

### 1.4 Pattern Engine — Symptom-Based Alert Logic

**Files:** `backend/app/api/readings.py`, `backend/app/services/worker/processor.py`

#### Chest pain → immediate Alert (plan proposal — correct)

When `POST /api/readings` receives `symptoms` containing `chest_pain`, insert an `alerts` row with `alert_type="symptom_urgent"` before the response returns. This must bypass the 30-second worker polling loop — it is synchronous and immediate, inline in the endpoint handler.

#### Shortness of breath + CHF → immediate Alert (missing from plan)

When `symptoms` contains `shortness_of_breath` AND the patient has `I50` (CHF) in `clinical_context.problem_codes`, apply the same immediate alert logic. This is the most clinically significant gap in the current plan. The patient app does not need to know the patient's diagnoses — the backend queries `clinical_context.problem_codes` at submission time and decides escalation server-side.

#### Cold-start suppression must not apply to symptom alerts

`processor.py` currently suppresses inertia, deterioration, and adherence detectors for patients enrolled fewer than 21 days. Symptom-based alerts must be explicitly excluded from this gate — a patient on day 2 reporting chest pain needs the same escalation as one on day 200. Add an explicit guard comment at the suppression check block to make this boundary clear to future maintainers.

---

### 1.5 Shared Utilities

**File:** `backend/app/utils/datetime_utils.py`

Extract `_is_off_hours()` from `backend/app/services/worker/processor.py` into `datetime_utils.py` as a shared utility. The plan calls this out correctly. This is also a prerequisite for the symptom alert path in `POST /api/readings` — the readings endpoint needs to tag `off_hours` on the inserted alert row and currently that logic only lives in the worker. Without this extraction, the symptom alert will either duplicate the logic or leave `off_hours` unset.

---

### 1.6 Configuration

**Files:** `backend/app/config.py`, `backend/.env`

Add `PATIENT_JWT_SECRET` as a separate secret from the clinician `JWT_SECRET` for blast-radius isolation (plan proposal — correct). Update the Pydantic `Settings` model:

```python
patient_jwt_secret: str = Field(..., env="PATIENT_JWT_SECRET")
```

The plan calls for a separate secret but does not list `config.py` as a file to modify. It must be added before the auth endpoint can be implemented.

---

### 1.7 CORS Configuration

**File:** `backend/app/main.py`

The current CORS `allow_origins` is configured for the clinician dashboard (localhost:3000 / production clinician URL). The patient PWA on Vercel is a different origin. Add the Vercel deployment URL to `allow_origins` before the PWA is deployed — otherwise every patient submission will be blocked by the browser before reaching the backend.

---

### 1.8 Audit Compliance

**File:** `backend/app/api/readings.py`

CLAUDE.md absolute rule: every `reading_ingested` must write to `audit_events`. When a patient submits via the PWA using the patient JWT role, the reading flows through the existing `POST /api/readings` endpoint. Verify that the audit write fires regardless of the JWT role — it must not be gated on `clinician` role only. If it is, patient submissions will be unaudited, which violates the audit requirement. Add an explicit test case covering the patient-role submission → audit_events write path.

---

### 1.9 LLM Validator Updates

**File:** `backend/app/services/briefing/llm_validator.py`

Add two new faithfulness checks following the existing contextual validation pattern:

1. **Symptom grounding check:** If the LLM output mentions a symptom (e.g. "chest pain", "breathlessness"), it must appear in `patient_reported_symptoms` in the Layer 1 payload — otherwise block as fabrication
2. **Symptom absence check:** If `patient_reported_symptoms` is empty or absent, the LLM must not mention any patient-reported symptom — same fabrication gate applies in both directions

Both failures follow the existing pattern: log warning, retry once, then store `readable_summary=None`. Write audit_events row with `action="llm_validation"`, `outcome="failure"`, `details=failed_check + reason`.

---

## Part 2 — Patient App Plan Changes

Changes to the plan before implementation begins.

---

### 2.1 Architecture

#### BLE Bridge secrets management — currently unresolved

Open Questions 2 (hosting) and 5 (HMAC scheme) in the plan must be resolved before implementation starts. The bridge needs its own `.env` with at minimum:

```
OMRON_HMAC_SECRET=
WITHINGS_HMAC_SECRET=
ARIA_BACKEND_URL=
BLE_INTERNAL_API_KEY=
```

`BLE_INTERNAL_API_KEY` is critical — without it, the BLE bridge calls `POST /api/ble-webhook` unauthenticated. That endpoint inserts rows into the readings table, making it a write-access hole from an external service. The main backend must validate this key on the `ble_webhook.py` route.

#### Rate limiting on patient token endpoint

`POST /api/auth/patient-token` validates only that the research ID exists — no password. Without rate limiting, a brute-force sweep of sequential research IDs takes seconds. Add `slowapi` (one-liner FastAPI middleware) to the backend and apply a per-IP limit (e.g. 5 requests/minute) to this endpoint. This is the minimal viable protection for a demo deployment accessible via a public Vercel URL.

---

### 2.2 Patient PWA — Submission Form

#### Offline queue must capture effective_datetime at measurement time

The service worker queues offline readings and submits them when connectivity returns — potentially hours later. If the submission timestamps with `now()` at flush time, the reading's `effective_datetime` reflects submission time, not measurement time. The existing unique index on `(patient_id, effective_datetime, source)` provides idempotency only if the correct measurement time is sent. The PWA form must capture the timestamp at the moment the reading is taken and include it as `effective_datetime` in the payload.

#### Shortness of breath escalation scope (missing from plan)

The plan escalates only chest pain to an immediate Alert. Shortness of breath should be a second immediate-escalation symptom — the CHF-conditional logic lives entirely on the server side. No client-side change is needed beyond confirming `shortness_of_breath` is a valid value in the symptoms checkbox list, which the plan already includes.

#### Safety notice must cover shortness of breath

**Current (plan):**
> "If you are experiencing severe chest pain, call 999 immediately."

**Recommended:**
> "If you are experiencing chest pain or sudden shortness of breath, call 999 immediately."

#### Dizziness needs a non-alarming UI note

Dizziness is clinically relevant (possible over-treatment / orthostatic hypotension) but is not an emergency. The form should display a subtle secondary label next to the dizziness checkbox:
> "Your doctor will be informed at your next visit."

This prevents the patient from feeling they must act immediately while ensuring the symptom is captured and surfaced in the visit agenda.

---

### 2.3 Acceptance Criteria — Additions

The plan's existing 10 acceptance criteria are missing the following:

| #     | Criterion                                                                                                                            |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------ |
| AC-11 | Patient submits chest pain → next briefing for that patient includes it in `urgent_flags` with concurrent BP value                   |
| AC-12 | Patient with CHF (`I50` in `problem_codes`) submits shortness of breath → `alerts` row inserted with `alert_type="symptom_urgent"`   |
| AC-13 | Patient-submitted reading writes `audit_events` row: `action="reading_ingested"`, `actor_type="system"`, `outcome="success"`         |
| AC-14 | PWA on Vercel origin can POST to backend without CORS error (browser network tab confirms no CORS rejection)                         |
| AC-15 | Patient enrolled 5 days ago who submits chest pain generates an alert — symptom alerts bypass the 21-day cold-start suppression gate |
| AC-16 | `BLE_INTERNAL_API_KEY` mismatch on `POST /api/ble-webhook` returns 401                                                               |
| AC-17 | `POST /api/auth/patient-token` returns 429 after 5 requests in 60 seconds from the same IP                                           |

---

### 2.4 Effort Estimate Adjustment

The plan estimates ~7 days for a single engineer. The missing items add approximately 1.75 days:

| Item                                                 | Original     | Adjustment     |
| ---------------------------------------------------- | ------------ | -------------- |
| Backend: patient JWT auth                            | 0.5 day      | no change      |
| Backend: symptom flags + chest pain escalation       | 0.5 day      | no change      |
| Backend: shortness of breath + CHF escalation        | not included | +0.25 day      |
| Backend: `symptom_urgent` alert_type + inbox display | not included | +0.25 day      |
| Backend: extract `_is_off_hours` to shared utils     | 0.25 day     | no change      |
| Backend: symptoms column migration                   | 0.1 day      | no change      |
| Backend: rate limiting on patient token endpoint     | not included | +0.25 day      |
| Backend: BLE bridge internal API key auth            | not included | +0.25 day      |
| Backend tests                                        | 0.5 day      | no change      |
| Briefing composer: `patient_reported_symptoms` field | not included | +0.5 day       |
| LLM validator: symptom faithfulness checks           | not included | +0.25 day      |
| Patient PWA (full)                                   | 3 days       | no change      |
| BLE bridge microservice                              | 2 days       | no change      |
| Webhook simulator script                             | 0.25 day     | no change      |
| Integration testing + demo dry-run                   | 0.5 day      | no change      |
| **Adjusted total**                                   | **7 days**   | **~8.75 days** |

Still fits within a single engineer sprint. Parallelised across 2 engineers: ~4.5 days.

---

## Priority Order for Implementation

| Priority                     | Item                                                            | File(s)                             | Reason                                                         |
| ---------------------------- | --------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------------------- |
| **P0 — Before any code**     | Resolve Open Questions 2 & 5 (BLE bridge hosting + HMAC scheme) | Plan document                       | Blocker for bridge implementation                              |
| **P0 — Before any code**     | Add `PATIENT_JWT_SECRET` to config + `.env`                     | `config.py`, `backend/.env`         | Blocker for auth endpoint                                      |
| **P1 — First**               | Extract `_is_off_hours()` to shared utils                       | `datetime_utils.py`, `processor.py` | Shared dependency for symptom alerts and BLE path              |
| **P1 — First**               | `symptoms TEXT[]` migration + `symptom_urgent` alert_type       | `setup_db.py`                       | Foundation everything else builds on                           |
| **P1 — First**               | `POST /api/auth/patient-token` + rate limiting                  | `backend/app/api/auth.py`           | Gate for PWA — nothing works without auth                      |
| **P1 — First**               | Chest pain + shortness of breath immediate Alert logic          | `backend/app/api/readings.py`       | Core clinical safety requirement                               |
| **P2 — Second**              | Briefing composer + `patient_reported_symptoms` JSON field      | `composer.py`                       | Required to show full loop on demo day                         |
| **P2 — Second**              | LLM validator symptom faithfulness checks                       | `llm_validator.py`                  | Required before Layer 3 runs on any briefing with symptom data |
| **P2 — Second**              | CORS config update                                              | `main.py`                           | Required before PWA is deployed anywhere                       |
| **P3 — Before demo dry-run** | Audit compliance verification (patient role path)               | `readings.py`, tests                | Demo-blocking if missed                                        |
| **P3 — Before demo dry-run** | All new acceptance criteria (AC-11 through AC-17)               | tests                               | Must pass before sign-off                                      |

---

## Clinical Boundary Checklist

All changes must be verified against CLAUDE.md clinical boundary rules before the demo dry-run:

- [ ] `symptom_urgent` alerts go to clinician only — no patient-facing notification
- [ ] Briefing language for symptom context uses "reported" not "diagnosed" (e.g. "patient reported chest pain" not "patient experienced chest pain")
- [ ] Dizziness surfaced in `visit_agenda` uses language: "consider positional BP assessment" — not "patient has orthostatic hypotension"
- [ ] PWA safety notice uses 999 (UK emergency number) and does not instruct clinical action
- [ ] LLM validator blocks any phrase that constitutes a diagnostic claim (`diagnos` pattern already in guardrails)
- [ ] No symptom data is displayed back to the patient in any PWA view

---

*Report generated from: `ARIA_PatientApp_BLE_Plan.docx` + `CLAUDE.md` cross-reference*
*Plan author: Sahil Khalsa | Report scope: Fix 43 + Fix 44 + system impact*
