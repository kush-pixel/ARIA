# ARIA — Patient PWA: Manual Submission, Medication Confirmation & .ics Reminders

**Author:** Kush Patel
**Status:** Planning — approved, not yet started
**Scope:** Fix 43 (patient submission UI) — manual BP entry, medication confirmation, .ics alarm reminders
**Deferred to next phase:** Fix 44 (BLE bridge / inline vendor webhook routes)

---

## TL;DR

Three tightly coupled pieces that together close the patient data loop for the thesis demo:

1. **Patient JWT auth** — research-ID-based token endpoint that gates all patient-facing API calls
2. **Patient PWA** — installable Next.js mobile web app for manual BP submission and medication confirmation
3. **.ics medication reminders** — ARIA generates a downloadable calendar alarm file; patient imports once; phone fires native alarm at dose time daily — no OAuth, no VAPID keys, works on iOS and Android

After this lands: patient submits BP + confirms meds at home → adherence detector runs on real data → clinician sees pre-visit briefing with real patient-reported context.

---

## Why These Decisions

### Manual entry over BLE-only

BLE auto-submit (Fix 44) depends on vendor developer accounts and HMAC key coordination. Manual entry via PWA ships independently, covers the demo scenario in full, and is what actually happens clinically — most patients read the number off their cuff and write it down. Fix 44 (inline vendor webhook routes) is the next pickup after this lands.

### PWA over native app

PWA = "open this URL." Native = TestFlight invites, signing certificates, App Store review.
For a demo deployment on Vercel: `git push` → live in 30 seconds. PWA covers iOS + Android from one codebase. Direct BLE pairing (the only thing native wins on) is sidestepped entirely by the vendor webhook path.

### .ics over Web Push or Google Calendar

| | .ics File | Web Push | Google Calendar API |
|---|---|---|---|
| iOS support | ✓ native Calendar | iOS 16.4+ + installed PWA only | ✓ GCal app |
| Google account needed | ✗ | ✗ | ✓ |
| OAuth / token storage | ✗ | ✗ | ✓ |
| ARIA can update remotely | ✗ re-download if meds change | ✓ | ✓ |
| Build effort | ~0.5 day | ~2–3 days | ~2–3 days |
| Works when app closed | ✓ (native calendar alarm) | ✓ | ✓ |

.ics solves iOS (which Web Push cannot), requires no third-party credentials, and ships in half a day. The static nature (patient re-downloads if medications change) is acceptable for the demo cohort whose medication list is fixed. Web Push / Google Calendar are documented as future work.

---

## Out of Scope (this phase)

- Fix 44 BLE bridge / inline vendor webhook routes — deferred to next phase
- SMS / email notification microservice — out of scope per patientapp.md
- Patient view of their own readings — violates ARIA clinical boundary
- Web Push / VAPID push notifications — deferred, documented as Tier 2 future work
- Google Calendar API integration — deferred, documented as Tier 2 future work
- Photo-based medication confirmation — out of scope per CLAUDE.md

---

## Architecture

```
Patient phone
  │
  │  HTTPS
  ▼
Patient PWA (Next.js 14, Vercel)
  ├── /          Login — research ID → JWT
  ├── /submit    Manual BP reading form
  └── /confirm   Medication checklist + Download Reminders button
        │
        │  GET /api/confirmations/ics/{patient_id}
        │  → .ics file download
        │
        │  POST /api/auth/patient-token
        │  POST /api/readings        (source="manual", submitted_by="patient")
        │  GET  /api/confirmations/pending
        │  POST /api/confirmations/confirm
        ▼
ARIA Backend (FastAPI, existing)
  │
  ▼
PostgreSQL (Supabase)
  │
  ▼
Clinician dashboard — sees readings + confirmed adherence in briefing
```

---

## Repo Layout (additions only)

```
ARIA/
  backend/
    app/
      api/
        auth.py               ← NEW — POST /api/auth/patient-token
        confirmations.py      ← NEW — pending, confirm, ics endpoints
        readings.py           ← MODIFY — symptoms field, actor_type fix, symptom alerts
      utils/
        datetime_utils.py     ← MODIFY — extract _is_off_hours() from processor.py
        ics_generator.py      ← NEW — .ics file generation utility
      main.py                 ← MODIFY — register new routers, add Vercel CORS origin
      config.py               ← MODIFY — add patient_app_url setting
    scripts/
      setup_db.py             ← MODIFY — symptoms TEXT[] migration
  patient-app/                ← NEW — Next.js 14 PWA
    src/
      app/
        page.tsx              ← login (research ID entry)
        submit/page.tsx       ← BP reading submission form
        confirm/page.tsx      ← medication checklist + .ics download
      lib/
        api.ts                ← all API calls (patient JWT injected)
        auth.ts               ← JWT storage helpers, usePatientAuth() hook
    public/
      manifest.json           ← PWA manifest (display: standalone)
      icons/                  ← home screen icons (192px, 512px)
    next.config.js            ← next-pwa plugin config
    package.json
    tsconfig.json
```

---

## Part 1 — Backend Changes

### 1.1 Database Migration

**File:** `scripts/setup_db.py`

Add column to readings table (safe to re-run, follows existing pattern):

```sql
ALTER TABLE readings ADD COLUMN IF NOT EXISTS symptoms TEXT[];
```

No new tables needed. The `medication_confirmations` table already has all columns required for real tap confirmations (`confirmation_type`, `confirmed_at`, `minutes_from_schedule`).

Note: `alert_type` in the `alerts` table is a TEXT column (not a DB enum), so `symptom_urgent` is a valid value immediately — no migration needed for it.

---

### 1.2 Patient JWT Auth Endpoint

**File:** `backend/app/api/auth.py` (NEW)

```
POST /api/auth/patient-token
  body:    { "research_id": "1091" }
  returns: { "access_token": "<jwt>", "expires_in": 28800 }
```

Implementation details:
- JWT claims: `sub=research_id`, `role="patient"`, `exp=now+8h`
- Sign with `settings.patient_jwt_secret` (already in `config.py`)
- Validate research_id exists in `patients` table → HTTP 404 if not found
- Rate limit: `@limiter.limit("5/minute")` using existing `app.limiter` instance (same pattern as `readings.py`)
- New `patient_required` dependency: decodes patient JWT, injects `patient_id` — separate from the clinician JWT dependency

**Why separate secret:** blast-radius isolation. A compromised patient token cannot be used to call clinician endpoints, and vice versa.

---

### 1.3 Shared Utility — `_is_off_hours()`

**File:** `backend/app/utils/datetime_utils.py` (MODIFY)

Extract `_is_off_hours(dt: datetime) -> bool` from `backend/app/services/worker/processor.py` into `datetime_utils.py` as a public shared utility.

**Why:** The symptom alert path in `POST /api/readings` needs to tag `off_hours` on the inserted alert row. That logic currently only lives in the worker. Without this extraction, the endpoint either duplicates the logic or leaves `off_hours` unset on symptom alerts.

Definition: returns `True` if `dt` is between 18:00–08:00 UTC or is a weekend.

---

### 1.4 Readings Endpoint — Symptoms + Actor Type + Symptom Alerts

**File:** `backend/app/api/readings.py` (MODIFY)

#### Add symptoms field to ReadingIn
```python
symptoms: list[str] | None = None
```
Valid values (enforced by the PWA form): `headache`, `dizziness`, `chest_pain`, `shortness_of_breath`.
Store on the `Reading` row once the column migration runs.

#### Fix actor_type audit event
Current (line ~109): `actor_type="clinician"` hardcoded.

Fix: derive from submitted_by:
```python
actor_type = "system" if payload.submitted_by == "patient" else "clinician"
```

This ensures patient submissions write `actor_type="system"` to `audit_events`, matching the audit schema definition. A hardcoded `"clinician"` on a patient submission violates audit integrity.

#### Chest pain → immediate symptom_urgent alert
When `"chest_pain" in (payload.symptoms or [])`:
- Insert `alerts` row synchronously before response returns (bypasses 30-second worker poll loop)
- `alert_type="symptom_urgent"`
- `patient_id=payload.patient_id`
- `triggered_at=now()`
- `off_hours=_is_off_hours(payload.effective_datetime)` using extracted utility
- `systolic_avg=computed systolic avg from the reading`

#### Shortness of breath + CHF → immediate symptom_urgent alert
When `"shortness_of_breath" in (payload.symptoms or [])`:
- Query `clinical_context.problem_codes` for `patient_id`
- If `"I50"` (CHF) or `"I50.9"` in `problem_codes` → insert same `symptom_urgent` alert
- Server-side check — client sends no diagnosis information

**Cold-start suppression note:** Symptom alerts explicitly bypass the 21-day cold-start suppression gate in `processor.py`. A patient on day 2 of enrollment reporting chest pain must escalate identically to one on day 200. Add a guard comment at the suppression block to make this boundary clear.

---

### 1.5 Confirmation Endpoints

**File:** `backend/app/api/confirmations.py` (NEW)

All three endpoints require patient JWT (`patient_required` dependency).

#### `GET /api/confirmations/pending`
```
Query params: patient_id (str), session (morning|evening|ad_hoc, optional)
Returns: [{ confirmation_id, medication_name, rxnorm_code, scheduled_time }]
```
Filters: `scheduled_time::date = today AND confirmed_at IS NULL AND patient_id = ?`
Optional session filter: `AND scheduled_time falls within session UTC hour range`.

Used by the `/confirm` PWA page to show today's unconfirmed doses.

#### `POST /api/confirmations/confirm`
```
Body: { "patient_id": str, "confirmation_ids": [uuid, ...] }
Returns: { "confirmed": int }
```
- `UPDATE medication_confirmations SET confirmed_at=now(), confirmation_type='tap', minutes_from_schedule=<delta from scheduled_time to now>`
- `WHERE confirmation_id IN (...) AND patient_id = payload.patient_id`  ← patient_id guard prevents cross-patient confirmation
- Write `audit_events`: `action="reading_ingested"`, `resource_type="MedicationConfirmation"`, `actor_type="system"`, `outcome="success"`

#### `GET /api/confirmations/ics/{patient_id}`
```
Returns: text/calendar file download
Content-Disposition: attachment; filename="aria-medications.ics"
```
Calls `ics_generator.generate_ics(patient_id, session, pwa_base_url)` and returns the result as a `Response`.

---

### 1.6 .ics Generator

**File:** `backend/app/utils/ics_generator.py` (NEW)

```python
async def generate_ics(
    patient_id: str,
    session: AsyncSession,
    pwa_base_url: str,
) -> str:
```

**Logic:**

1. Query `clinical_context.current_medications` (TEXT[]) and `med_history` JSONB for the patient
2. For each active medication, derive dosing frequency using the same frequency→UTC-hours mapping already defined as constants in `confirmation_generator.py` (reuse, do not duplicate)
3. Group medications by session:
   - Morning: scheduled UTC hour 06:00–11:59
   - Evening: scheduled UTC hour 12:00–23:59
4. For each non-empty session group, generate one `VEVENT`:

```
BEGIN:VEVENT
UID:aria-morning-{patient_id}@aria.local
SUMMARY:Morning medications (ARIA)
DTSTART:{tomorrow}T080000Z
RRULE:FREQ=DAILY
DESCRIPTION:• Ramipril\n• Amlodipine\n• Bisoprolol\n\nTap to confirm:\n{pwa_base_url}/confirm?session=morning
BEGIN:VALARM
TRIGGER:-PT5M
ACTION:DISPLAY
DESCRIPTION:Time to take your morning medications
END:VALARM
END:VEVENT
```

5. Wrap in `VCALENDAR` envelope with `PRODID:-//ARIA//Medication Reminders//EN`
6. Return as string (no file I/O — caller writes to response)

**Why one event per session, not one per drug:**
A patient on 4 medications gets 4 back-to-back notifications if events are per-drug — noisy enough that patients swipe them all away. One grouped event per dosing time is one notification. Per-drug granularity is preserved in the `medication_confirmations` DB rows; it is a UX grouping only.

**Result for a typical patient:** 1–2 calendar events regardless of medication count.

---

### 1.7 Config + CORS + Router Registration

**File:** `backend/app/config.py` (MODIFY)
Add:
```python
patient_app_url: str = Field("", description="Patient PWA origin for CORS (Vercel URL)")
```

**File:** `backend/app/main.py` (MODIFY)

Import and register new routers:
```python
from app.api import auth, confirmations
...
app.include_router(auth.router, prefix="/api")
app.include_router(confirmations.router, prefix="/api")
```

Extend CORS `allow_origins`:
```python
allow_origins=[
    "http://localhost:3000",
    "http://localhost:3001",       # patient-app dev server
    settings.patient_app_url,      # Vercel deployment
]
```

---

## Part 2 — Patient PWA

### Stack
- Next.js 14 (App Router), TypeScript strict
- Tailwind CSS (consistent with clinician `frontend/`)
- `next-pwa` plugin → service worker + manifest

### Page: `/` — Login

Single input: **"Enter your Research ID"**

- Patient types their research ID (e.g. `1091`)
- On submit: `POST /api/auth/patient-token`
- Store returned JWT in `localStorage`
- Redirect to `/confirm`
- If JWT already in storage and not expired → redirect directly

---

### Page: `/submit` — BP Reading Form

Auth-protected (redirect to `/` if no valid JWT).

#### Form fields

| Field | Type | Required | Notes |
|---|---|---|---|
| Systolic (reading 1) | Number input, 60–250 | Yes | |
| Diastolic (reading 1) | Number input, 30–150 | Yes | |
| Add second reading | Toggle / expand | No | Expands systolic_2 / diastolic_2 fields below |
| Systolic (reading 2) | Number input, 60–250 | No | Only shown when toggle active |
| Diastolic (reading 2) | Number input, 30–150 | No | Only shown when toggle active |
| Heart rate | Number input, 30–200 | No | |
| Session | Radio: Morning / Evening | Yes | |
| Medication taken | Radio: Yes / No / Partial | Yes | |
| Symptoms | Checkboxes (4 items) | No | See below |

#### Symptoms checkboxes
- Headache
- Dizziness — show subtle note: *"Your doctor will be informed at your next visit."*
- Chest pain
- Shortness of breath

#### Safety banner (conditional)
Shown immediately when chest pain OR shortness of breath is checked:
> **"If you are experiencing chest pain or sudden shortness of breath, call 999 immediately."**

Red/amber background, prominent placement above submit button.

#### Timestamp behaviour
`effective_datetime` is captured as `new Date().toISOString()` at the moment the **form is opened**, not at submit time. This ensures the reading reflects when the measurement was taken, not when the patient finished filling in the form. Critical for offline queue correctness (service worker may flush readings hours after capture).

#### Submission
`POST /api/readings` with:
- `source="manual"`, `submitted_by="patient"`
- `systolic_2` / `diastolic_2` only included if second reading toggle was used
- `symptoms` array (empty array if none checked)
- `effective_datetime` from form-open timestamp

---

### Page: `/confirm` — Medication Confirmation

Auth-protected.

#### On page load
`GET /api/confirmations/pending?patient_id={id}` for today's session.

If no unconfirmed doses → show: *"No medications pending for today. Well done."*

#### Confirmation checklist
Each pending medication shown as a checkbox row:
```
☐  Ramipril        08:00 this morning
☐  Amlodipine      08:00 this morning
☐  Bisoprolol      08:00 this morning
```

**"Confirm selected"** button → `POST /api/confirmations/confirm` with checked `confirmation_id`s.
**"Confirm all"** shortcut button also available.

On success: show green confirmation banner, clear checklist.

#### Download Reminders section (below checklist)

```
┌─────────────────────────────────────────────────┐
│  Never miss a dose                              │
│  Add medication reminders to your calendar.     │
│                                                 │
│  [Download Medication Reminders]                │
│                                                 │
│  Open the downloaded file to add daily          │
│  alarms to your phone's calendar.              │
└─────────────────────────────────────────────────┘
```

- Button calls `GET /api/confirmations/ics/{patient_id}` (with patient JWT)
- Browser triggers `.ics` file download automatically
- Instruction text below button: platform-aware (detect iOS vs Android):
  - iOS: *"Tap the downloaded file — your Calendar app will open to import."*
  - Android: *"Tap the downloaded file — your Calendar or Google Calendar app will open to import."*

---

### PWA Installability

**`public/manifest.json`:**
```json
{
  "name": "ARIA — My Health",
  "short_name": "ARIA",
  "display": "standalone",
  "start_url": "/confirm",
  "background_color": "#ffffff",
  "theme_color": "#1d4ed8",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

**Service worker (next-pwa auto-generated):**
- Caches static assets
- Queues offline submissions; flushes when connectivity returns with correct `effective_datetime`

**Install flow:**
- Android Chrome: "Add to Home Screen" banner appears after first visit
- iOS Safari: Share → "Add to Home Screen"
- Result: full-screen launch, no browser chrome, looks native

---

## Part 3 — Build Order

| Priority | Item | File(s) | Why first |
|---|---|---|---|
| **P0** | Extract `_is_off_hours()` | `datetime_utils.py`, `processor.py` | Shared dep for symptom alert path |
| **P0** | DB migration: `symptoms TEXT[]` | `setup_db.py` | Foundation for readings changes |
| **P0** | Patient JWT auth + rate limit | `auth.py` (new) | Gate for all patient endpoints |
| **P0** | CORS + router registration | `main.py`, `config.py` | Required before PWA can call backend |
| **P1** | Readings: symptoms + actor_type fix + symptom alerts | `readings.py` | Core clinical safety (chest pain escalation) |
| **P1** | Confirmation endpoints | `confirmations.py` (new) | Core confirmation flow |
| **P1** | .ics generator utility | `ics_generator.py` (new) | Consumed by confirmations endpoint |
| **P2** | Patient PWA: login + submit pages | `patient-app/` | Demo-facing |
| **P2** | Patient PWA: confirm page + download button | `patient-app/src/app/confirm/` | Demo-facing |
| **P3** | Tests | `tests/` | All ACs must pass before sign-off |
| **P3** | `ruff check app/` clean | — | CLAUDE.md absolute rule |

---

## Part 4 — Effort Estimate

| Component | Effort |
|---|---|
| Extract `_is_off_hours`, DB migration | 0.25 day |
| Patient JWT auth endpoint + rate limiting | 0.5 day |
| `main.py` / `config.py` / CORS | 0.25 day |
| Readings: symptoms field + actor_type + symptom alerts | 0.75 day |
| Confirmation endpoints (pending + confirm + ics) | 0.75 day |
| .ics generator utility | 0.5 day |
| Patient PWA (login + submit + confirm pages) | 3 days |
| Tests (auth, symptoms, .ics, confirm, audit) | 0.75 day |
| **Total** | **~6.75 days** single engineer / ~3.5 days parallelised across 2 |

---

## Part 5 — Acceptance Criteria

| # | Criterion |
|---|---|
| AC-1 | `POST /api/auth/patient-token {"research_id": "1091"}` returns JWT with `role="patient"` |
| AC-2 | `POST /api/auth/patient-token` returns 429 after 5 requests in 60 seconds from the same IP |
| AC-3 | Patient submits BP reading via PWA → row in `readings` with `source="manual"`, `submitted_by="patient"` |
| AC-4 | `audit_events` row written for patient submission: `actor_type="system"`, `action="reading_ingested"`, `outcome="success"` |
| AC-5 | Patient submits `symptoms=["chest_pain"]` → `alerts` row inserted with `alert_type="symptom_urgent"` and `off_hours` correctly tagged |
| AC-6 | Patient with `I50` in `problem_codes` submits `symptoms=["shortness_of_breath"]` → `alerts` row inserted with `alert_type="symptom_urgent"` |
| AC-7 | Patient enrolled 5 days ago submits chest pain → alert fires (cold-start suppression does not apply) |
| AC-8 | `GET /api/confirmations/pending` returns today's unconfirmed doses for the patient |
| AC-9 | `POST /api/confirmations/confirm` sets `confirmed_at`, `confirmation_type="tap"`, `minutes_from_schedule` on correct rows only |
| AC-10 | Confirmation writes `audit_events` row: `action="reading_ingested"`, `resource_type="MedicationConfirmation"`, `outcome="success"` |
| AC-11 | `GET /api/confirmations/ics/1091` returns a valid `.ics` file: correct `Content-Type: text/calendar`, correct `Content-Disposition` header |
| AC-12 | .ics file contains one `VEVENT` per dosing session with `RRULE:FREQ=DAILY` and `VALARM TRIGGER:-PT5M` |
| AC-13 | .ics file description includes all medications for that session and the `/confirm?session=` deep link |
| AC-14 | Importing .ics on iOS Calendar shows recurring daily alarm events — verified manually |
| AC-15 | PWA login flow → submit form → confirm checklist works end-to-end on Android Chrome and iOS Safari |
| AC-16 | "Download Medication Reminders" button triggers `.ics` file download in the browser |
| AC-17 | PWA is installable to home screen on Android Chrome (Lighthouse PWA score ≥ 90) |
| AC-18 | Patient PWA origin can POST to backend without CORS error (browser network tab confirms no rejection) |
| AC-19 | All existing tests pass: `python -m pytest tests/ -v -m "not integration"` |
| AC-20 | `ruff check app/` passes with no errors |

---

## Part 6 — Clinical Boundary Checklist

All changes must be verified before demo dry-run:

- [ ] `symptom_urgent` alerts go to clinician dashboard only — no patient-facing display
- [ ] PWA does not show patient their own readings or risk score
- [ ] Briefing language for symptoms: "patient reported chest pain" not "patient experienced chest pain"
- [ ] Dizziness in `visit_agenda`: "consider positional BP assessment" — not "orthostatic hypotension"
- [ ] Safety notice uses 999 (UK emergency) and does not instruct clinical action
- [ ] LLM validator blocks diagnostic claims (`diagnos` pattern already in guardrails)
- [ ] No symptom data displayed back to the patient in any PWA view

---

## Future Work — Not in This Phase

### Tier 1 (high impact, low effort — pick up after this lands)
- **Fix 44 BLE bridge** — inline vendor webhook routes (`POST /api/webhooks/omron`, `/withings`) in the existing backend. HMAC validation + transform + forward to existing `ble_webhook.py` handler. ~1.5 days.
- **Medication confirmation in briefing** — `patient_reported_symptoms` field in briefing JSON, surfaced in `urgent_flags` and `visit_agenda`. Composer + LLM validator updates per `ARIA_PatientApp_Changes_Report.md` sections 1.2–1.3.
- **Real-time dashboard updates** — PostgreSQL `LISTEN/NOTIFY` → WebSocket/SSE push to clinician dashboard when patient submits.

### Tier 2 (after Tier 1)
- **Web Push (VAPID)** — proactive reminders for Android; iOS via GCal app fallback. Replaces re-download friction when medications change.
- **Google Calendar API** — auto-updates calendar events when medication list changes after FHIR ingestion. Requires OAuth + token storage.
- **Pre-visit patient questionnaire** — day before appointment, PWA prompts for symptoms/concerns → auto-populates `visit_agenda`.

---

## References

- `documentation/patientapp.md` — original patient app plan (Sahil Khalsa)
- `ARIA_PatientApp_Changes_Report.md` — system impact analysis (Kush Patel)
- `AUDIT.md` Fix 43 (patient submission), Fix 44 (BLE connector)
- `CLAUDE.md` — clinical boundary rules, three-layer AI architecture, audit requirements
- `backend/app/api/readings.py` — existing manual reading endpoint (`ReadingIn` schema)
- `backend/app/api/ble_webhook.py` — existing BLE webhook endpoint
- `backend/app/models/medication_confirmation.py` — confirmation table schema
- `backend/app/services/generator/confirmation_generator.py` — dosing schedule derivation logic (reuse for .ics grouping)
