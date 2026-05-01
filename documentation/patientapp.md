# ARIA — Patient App + BLE Bridge Plan

**Author:** Sahil Khalsa
**Status:** Planning — not yet started
**Scope:** Closes AUDIT.md Fix 43 (patient submission UI) + Fix 44 (BLE connector via vendor cloud)

---

## TL;DR

Build the patient-facing piece of ARIA. Two components:

1. **Patient PWA** — installable mobile web app where patients submit BP readings, medication confirmations, and symptoms. Works on iOS + Android + desktop from one Next.js codebase.
2. **BLE bridge microservice** — separate FastAPI service that receives BP cuff data from manufacturer cloud webhooks (Omron Connect, Withings) and forwards to the existing `POST /api/ble-webhook`.

After this lands, the data loop is complete end-to-end: patient submits at home → ARIA detects pattern → clinician sees briefing → escalation flag fires if ignored.

---

## Out of scope

- Native iOS / Android apps — PWA covers needs at ~4× lower effort
- Direct on-device BLE pairing — handled by manufacturer cloud, not the patient app
- Email / SMS notification microservice — Fix 45 escalation flag is sufficient for the thesis demo
- Patient view of their own readings — violates ARIA clinical boundary
- Photo-based medication confirmation — out of MVP per CLAUDE.md

---

## Architecture

```
                   ┌──────────────────────────────────┐
                   │   Patient phone                  │
                   │   - PWA (Next.js, installable)   │
                   │   - Manufacturer cuff app        │
                   └────┬───────────────────────┬─────┘
                        │ HTTPS                 │ Bluetooth
                        │                       ▼
                        │              ┌──────────────────┐
                        │              │  Vendor cloud    │
                        │              │  (Omron/Withings)│
                        │              └────────┬─────────┘
                        │                       │ HTTPS webhook
                        │                       ▼
                        │              ┌──────────────────┐
                        │              │  BLE bridge      │
                        │              │  (services/      │
                        │              │   ble-bridge/)   │
                        │              └────────┬─────────┘
                        │                       │
                        │ POST /api/readings    │ POST /api/ble-webhook
                        ▼                       ▼
                   ┌──────────────────────────────────┐
                   │   ARIA backend (existing)        │
                   └──────────────────┬───────────────┘
                                      │
                                      ▼
                            ┌──────────────────┐
                            │ Clinician        │
                            │ dashboard        │
                            └──────────────────┘
```

---

## Repo layout (additions)

Same monorepo. No new repo.

```
ARIA/
  backend/                    ← existing — minor additions only
  frontend/                   ← existing clinician dashboard, unchanged
  patient-app/                ← NEW — Next.js 14 PWA
    src/
      app/
        page.tsx              ← login (research ID entry)
        submit/page.tsx       ← BP reading submission form
      components/
        SymptomFlags.tsx
        ReadingForm.tsx
      lib/
        api.ts
        auth.ts
    public/
      manifest.json           ← PWA manifest
      icons/                  ← home screen icons (192px, 512px)
    next.config.js            ← next-pwa plugin config
  services/
    ble-bridge/               ← NEW — FastAPI microservice
      app/
        main.py
        webhooks/
          omron.py
          withings.py
        transformers.py
      tests/
      requirements.txt
      Dockerfile
  scripts/
    simulate_omron_webhook.py ← demo helper
```

---

## Backend changes (small surface area)

No DB migrations beyond one column. Existing endpoints reused.

### `backend/app/api/auth.py` (NEW)
```
POST /api/auth/patient-token
  body:    { "research_id": "1091" }
  returns: { "access_token": "<jwt>", "expires_in": 28800 }
```
JWT claims: `sub=research_id`, `role="patient"`, `exp=now+8h`. Validates research_id exists. New `patient_required` dependency, separate from clinician JWT.

### `backend/app/api/readings.py` (MODIFY)
Extend `ReadingIn` to accept `symptoms: list[str] | None`. On `chest_pain`, immediately insert an `Alert` row with `off_hours` tagged via existing `_is_off_hours()`.

### `backend/app/utils/datetime_utils.py` (MODIFY)
Extract `_is_off_hours` from `processor.py` into shared utils.

### `scripts/setup_db.py` (MODIFY)
`ALTER TABLE readings ADD COLUMN IF NOT EXISTS symptoms TEXT[];`

---

## Patient PWA

### Stack
- Next.js 14 (App Router), TypeScript strict
- Tailwind CSS (matches clinician `frontend/`)
- `next-pwa` plugin → service worker + manifest auto-generated

### Pages

**`/` — Login**
Single input: "Enter your Research ID" → POSTs `/api/auth/patient-token` → stores JWT → redirects to `/submit`.

**`/submit` — BP reading form**
Auth-protected. Fields:
- Systolic (60–250)
- Diastolic (30–150)
- Heart rate (optional, 30–200)
- Session: Morning / Evening (radio)
- Medication taken: Yes / No / Partial
- Symptom checkboxes: Headache, Dizziness, Chest pain, Shortness of breath
- **Chest pain warning banner:** *"If you are experiencing severe chest pain, call 999 immediately."*

Submits to `/api/readings` with `source="manual"`, `submitted_by="patient"`.

### Installability ("download" to phone)
1. `manifest.json` declares name, icons, `display: "standalone"`
2. Service worker (auto-generated) caches assets, queues offline submissions
3. Hosted via HTTPS (Vercel free tier)

**Patient install flow:**
- Android: visit URL → "Add to Home Screen" banner → tap → app icon on home screen
- iOS Safari: visit URL → Share → "Add to Home Screen"
- Result: full-screen launch, no browser chrome, looks native

No app store, no install friction.

---

## BLE bridge microservice

### Why a separate service (not just an endpoint in `backend/`)
- Vendor webhook traffic is external and unpredictable — isolation prevents impact on clinician dashboard
- Different security boundary — vendor HMAC keys never touch main backend
- Independent scaling and deployment
- Concrete thesis talking point on service-oriented architecture

### Responsibilities
1. Expose `POST /webhooks/omron` and `POST /webhooks/withings`
2. Validate vendor HMAC signature (reject 401 on mismatch)
3. Transform vendor JSON → ARIA Reading schema
4. Forward to ARIA backend's `POST /api/ble-webhook` with `source="ble_auto"`

### Demo helper
`scripts/simulate_omron_webhook.py` sends a synthetic Omron payload — demonstrates the full integration without real hardware.

---

## Demo scenario (defense day)

1. Open ARIA PWA on phone → enter research ID → submit 165/95 morning + chest pain.
2. Clinician dashboard alert appears.
3. Run `python scripts/simulate_omron_webhook.py` from terminal — reading appears with `source="ble_auto"`.
4. Click alert → see briefing with chest pain context → acknowledge with disposition.
5. Story: *"Patient submits at home, ARIA detects, clinician sees pre-visit briefing — between-visit intelligence loop is closed."*

---

## Effort breakdown

| Component | Effort |
|---|---|
| Backend: patient JWT auth (`auth.py`) | 0.5 day |
| Backend: symptom flags + chest pain escalation on `/api/readings` | 0.5 day |
| Backend: extract `_is_off_hours` to shared utils | 0.25 day |
| Backend: `symptoms` column migration | 0.1 day |
| Backend tests | 0.5 day |
| Patient PWA (full) | 3 days |
| BLE bridge microservice | 2 days |
| Webhook simulator script | 0.25 day |
| Integration testing + demo dry-run | 0.5 day |
| **Total** | **~7 days** (single engineer; ~3–4 days parallelised across 2) |

---

## Acceptance criteria

- [ ] PWA installable on Android Chrome and iOS Safari
- [ ] Patient submits a reading end-to-end; row appears in `readings` with `source="manual"`, `submitted_by="patient"`
- [ ] Chest pain symptom triggers immediate `Alert` row insert with `off_hours` correctly tagged
- [ ] BLE bridge accepts simulated Omron webhook, validates HMAC, forwards to backend, reading appears with `source="ble_auto"`
- [ ] BLE bridge rejects invalid HMAC (returns 401)
- [ ] All existing tests pass
- [ ] New tests cover patient JWT, symptom payload, chest pain escalation, BLE bridge transform/forward
- [ ] `ruff check app/` passes for both `backend/` and `services/ble-bridge/`
- [ ] PWA Lighthouse PWA score ≥ 90
- [ ] Demo dry-run completes successfully end-to-end

---

## Open questions for the team

1. **JWT secret rotation** — reuse clinician `JWT_SECRET` or use separate `PATIENT_JWT_SECRET`? Proposed: separate, for blast-radius isolation.
2. **BLE bridge hosting** — same Docker host, separate container, separate domain (`bridge.<domain>`)?
3. **PWA hosting** — Vercel free tier for the demo?
4. **Vendor accounts** — who registers the Omron Connect / Withings developer account?
5. **HMAC scheme** — confirm which vendor webhook spec to mirror in the simulator.

---

## Future work (NOT in this phase — recorded for later)

### Tier 1 — High impact, low effort (1–2 days each)
Recommended pickups *after* PWA + BLE bridge land.

- **Real-time dashboard updates (WebSocket / SSE)** — extend existing PostgreSQL `LISTEN/NOTIFY` (Phase 4 Fix 60) to push reading/alert events to the clinician dashboard. Live-updating UI when patient submits. Killer demo moment.
- **Medication confirmation flow in the PWA** — `medication_confirmations` schema already exists. Add a `/confirm` page with morning/evening tap buttons. Adherence detector starts running on real data.
- **AI explainability popup** — modal on flagged patient showing *"Flagged because: 28-day avg X mmHg vs threshold Y; no med change since DATE; adherence Z% → treatment review warranted."* Built from existing Layer 1 outputs.
- **Symptom timeline view (clinician)** — patient-submitted symptoms plotted over time on the patient detail page. Connects PWA submissions back into the clinician story.

### Tier 2 — High impact, medium effort (3–5 days each)
Pick at most one before defense.

- **Pre-visit patient questionnaire** — day before appointment, PWA prompts for symptoms/concerns. Answers auto-populate `visit_agenda` in the briefing.
- **Cohort analytics dashboard** — `/admin/cohort` route showing population-level views: median risk score by tier, adherence distribution, alert volume by detector type, escalation rate over time.
- **Adaptive medication reminders (Web Push)** — VAPID-signed push notifications. ARIA learns patient's usual confirmation time, sends reminder if late.
- **Patient education snippets** — after submitting a high reading, PWA shows non-prescriptive context (carefully worded to respect clinical boundary).

### Tier 3 — Defer to "Future Work" chapter
Mention in thesis, don't build.

- Wearable integration (Apple Watch, Fitbit, HealthKit, Google Fit)
- Photo-based medication confirmation (computer vision)
- Voice input for elderly patients
- Multi-language support
- Telehealth video integration
- Federated learning across multiple clinics
- Smart pill bottle integration
- Native iOS / Android app (post-thesis, can wrap PWA via PWABuilder if real distribution ever needed)
- Notification microservice (real email/SMS for escalated alerts)
- Patient-data architectural microservice split

---

## Native vs PWA — decision rationale

We chose PWA. Reasons:

- Effort: PWA ~3–5 days, React Native ~2–3 weeks. 4× difference.
- Demo distribution: PWA = "open this URL." Native = TestFlight invites, build pipelines, signing certificates.
- Iteration speed: PWA = `git push` → live in 30s. Native = rebuild → resubmit → reinstall.
- Maintenance: PWA URL keeps working. Native apps need cert renewals, App Store reviews, OS API breakage.
- The only feature native delivers that we need is direct on-iOS BLE pairing — sidestepped entirely by the BLE bridge microservice (vendor cloud → webhook).

Native is Tier 3 future work. Post-thesis, [PWABuilder](https://www.pwabuilder.com/) can wrap the PWA into Google Play / App Store packages in ~30 minutes.

---

## References

- AUDIT.md Fix 43 — patient-facing submission spec
- AUDIT.md Fix 44 — BLE connector spec (Option A: manufacturer cloud webhook)
- AUDIT.md Fix 45 — escalation pathway (already implemented; chest-pain symptom plugs into the same alert table)
- CLAUDE.md — clinical boundary rules
- STATUS.md — current project state (Phase 0–8 complete)
