# SUMMER_ROADMAP.md — Change Log
## Revised: May 2026
## Changes from original 13-week plan to updated 16-week plan

---

## 1. Corrections Throughout Document

These were errors or UK-specific references that needed updating for the US market.

| Original | Updated | Reason |
|---|---|---|
| BP clinic threshold: 140/90 mmHg | 130/80 mmHg | ACC/AHA 2017 is the US standard; NICE NG136 is UK-only |
| BP home threshold: 135/85 mmHg | 130/80 mmHg | ACC/AHA uses same 130/80 for both home and clinic |
| Dashboard display threshold: 140 mmHg | 130 mmHg | Matches ACC/AHA target |
| NICE over-80 exception (145 mmHg) | Removed entirely | ACC/AHA 2017 has no age-based threshold exception |
| All NICE NG136 references | ACC/AHA 2017 references | Wrong guideline for US context |
| GP / GPs | PCP / Physician / Care team | US clinical terminology |
| Surgery | Practice / Clinic | US terminology |
| UK GDPR | HIPAA | US jurisdiction |
| ICO breach notification (72hr) | HHS OCR breach notification (60 days) | US regulatory equivalent |
| DCB0160 Clinical Safety | FDA Software as a Medical Device (SaMD) | US regulatory pathway |
| EMIS / SystmOne | Epic / Cerner / athenahealth | US EMR systems |
| NHS FHIR API | SMART on FHIR | US standard; better adoption than UK NHS FHIR |
| NHS Login | Epic MyChart / Apple Health / Google Health | US equivalent patient identity systems |
| QRISK3 | PCE (Pooled Cohort Equations) | PCE is ACC/AHA standard US CVD risk calculator; free, no licence required |
| WCAG (Equality Act 2010) | Section 508 / ADA | US legal framework |
| Supabase Pro note | Added HIPAA BAA note | Supabase Pro has no HIPAA BAA; Team plan ($599/mo) required before any real US patient data |

---

## 2. Items Removed

| Removed | Reason |
|---|---|
| DPIA (Data Protection Impact Assessment) | De-identified patients fall outside HIPAA and GDPR scope entirely — not required for summer study |
| Consent re-confirmation for new data types | No real patients in summer study — not applicable |
| Team roles section | Removed per request |

---

## 3. Items Deferred to Post-Summer

These were in scope consideration but deliberately pushed out. A dedicated "Deferred to Post-Summer" section was added to the document with reasons for each.

| Deferred Item | Reason |
|---|---|
| Gaussian Process Regression (personalised threshold) | CUSUM alone is a major improvement; GP regression needs physician validation study data to validate personalised thresholds |
| FHIR R4 Validation (HAPI test server) | Not blocking the physician study |
| BP Reading PDF Export | Low clinical urgency relative to other additions |
| BOCPD (Bayesian Online Changepoint Detection) | CUSUM covers deterioration detection need; BOCPD adds complexity without enough data to tune it |
| XGBoost Adherence Prediction | Requires 200 patient-episodes minimum before clinical use |
| HMM Adherence State Detection | Requires 60-day minimum per patient |
| Prophet BP Forecasting | Not clinically urgent this summer |
| SMART on FHIR Medication Write-Back (Option B) | Requires Epic App Orchard approval and SMART write permissions — multi-month process |
| Epic MyChart / NHS Login Integration | Regulatory pathway 3–6 months |
| Multi-Condition Expansion (CHF, COPD, T2D) | Hypertension detection must be fully validated before expanding |
| Omron / Apple HealthKit / Samsung Galaxy Watch | Hardware in device lab; one device (Withings) done properly this summer |
| PCE Score | Deferred to confirm which inputs are reliably available from FHIR bundles |

---

## 4. Added to Week 1 — Critical Fixes

### 1.6 Drug Interaction Immediate Alert (New)
When FHIR re-ingestion or any medication update creates a dangerous drug combination, fire an alert immediately — do not wait for the next briefing.

- New `alert_type = "drug_interaction"` in the alerts table
- Deduplication check: only fires if no unacknowledged drug interaction alert already exists for this patient
- `off_hours = TRUE` if triggered 6PM–8AM or weekend
- SSE push delivers it to the dashboard in real time
- Alert card shows: which new drug was added, which combination is now active, severity, comorbidity amplification

**Effort:** 1 day

---

## 5. Added to Sprint 2 — Detection Engine

### 2.9 Contextual Severity Modulation (New)
Replace the flat −7 mmHg comorbidity adjustment in `threshold_utils.py` with per-condition ACC/AHA-aligned adjustments. Propagates to all four detectors automatically.

| Comorbidity | Threshold adjustment | Gap urgency |
|---|---|---|
| CHF (I50) | −10 mmHg (target <120) | ×2 |
| Stroke / TIA (I61–I64, G45) | −10 mmHg (target <120) | ×1.5 |
| CKD (N18) | −7 mmHg (target <120) | ×1.5 |
| Diabetes (E11) | −5 mmHg (target <130) | Standard |
| None | 0 | Standard |

**Effort:** 1 day

### 2.10 Circadian Pattern Analysis — Briefing Output (New)
The chatbot already has `get_circadian_pattern` as a tool. This adds it as a formal briefing detector output so physicians see it without needing to query the chatbot.

- Morning systolic consistently ≥20 mmHg above evening → "Morning surge pattern detected — discuss dosing schedule and timing." Note: do not recommend bedtime dosing — TIME trial (2022, NEJM) found no outcome difference.
- Evening BP exceeds morning (reverse dipping) → "Possible nocturnal hypertension — consider ambulatory BP monitoring referral"
- Separate morning and evening sparklines with delta annotated in briefing display

**Effort:** 2 days

---

## 6. Added to Sprint 3 — ML Algorithms

### 3.4 CausalImpact — Medication Response Assessment (New)
Objectively evaluates whether a medication change worked using Bayesian structural time series counterfactual analysis. Answers the physician's most common post-prescription question: "Did it work?"

- Minimum 21-day pre-intervention window required
- Drug-class titration window must elapse before analysis runs (reuses existing `TITRATION_WINDOWS` constants)
- Only surfaces when posterior probability of meaningful effect (≥5 mmHg) is ≥75%
- Clinical output includes posterior mean effect, 95% CI, and probability of causal effect

**Effort:** 2 days

---

## 7. Added to Sprint 4 — Chatbot & Layer 3

### 4.7 Uncertainty Communication (New)
Hard rules for what the chatbot must acknowledge before any trend statement. Overconfident AI during a physician validation study is a direct patient safety risk.

- Enrolled < 21 days → "I only have N days of data — the trend may not be reliable"
- Fewer than 10 readings in window → flag before any trend statement
- Sparse medication history → "I can only confirm what was recorded in the FHIR bundle"
- `readable_summary = null` → "I was unable to generate a validated summary — showing raw Layer 1 data instead"

**Effort:** 1 day

---

## 8. Added to Sprint 5 — Patient App

### 5.8 OCR Camera Scan Input (New)
Patient points phone camera at BP monitor screen. Google ML Kit reads the three numbers on-device, offline-capable, free. Same 60–250 mmHg validation applies. Adds `source="ocr_scan"` to readings. Enables patients with older non-BLE monitors to avoid manual transcription.

Note: distinct from "Vision AI for medication photos" out-of-scope item — this is OCR of three digits, not pill identification.

**Effort:** 2 days

### 5.9 Daily Tips — 1-Minute Bite-Sized Insights (New)
Replaces static MDX health education articles. Rotating daily tip displayed on the home hub. Pre-written content, no LLM, offline-capable. Topics: sodium reduction, potassium-rich foods, breathing techniques, reading posture, sleep and BP, medication timing. Slightly personalised by comorbidity.

**Effort:** 1 day

### 5.10 Food Suggestions (New)
Static "Foods to Watch" section in the patient app. Two columns: foods to reduce (sodium, processed meats, alcohol) and foods to increase (potassium, magnesium, oily fish). No AI, no clinical language, no BP values. Replaces chatbot concept on the patient side.

**Effort:** 4 hours

### 5.11 Secure Patient Messaging (New)
Patient sends a short message (max 500 characters) to their care team. Not two-way real-time chat. Not AI-mediated. Physician sees it in the alert inbox. Mandatory non-emergency disclaimer before typing. Full audit trail in `audit_events`.

New table: `patient_messages (message_id, patient_id, message_text, sent_at, read_at, read_by)`

**Effort:** 2 days

### 5.12 Direct Google + Apple Calendar Integration (New)
Replaces `.ics` download as primary reminder mechanism. OAuth2 for Google, CalDAV for Apple. Medication reminders pushed directly as recurring calendar events. Schedule changes update existing events, not duplicate them. `.ics` download retained as fallback.

Schema addition: `patients.calendar_integration TEXT`, `patients.calendar_oauth_token TEXT` (encrypted)

**Effort:** 2 days

### 5.13 Full Engagement and Streaks System (New)
Home hub shows a streak number but the complete system was missing. Adds `patient_engagement` table with streak tracking, milestone system (first reading, 7-day streak, 30 readings, 30-day streak, 100 readings), and monthly calendar heatmap on `/progress`. Computed nightly from existing data — no new clinical data exposed to patients.

**Effort:** 2 days

### 5.14 Accessibility — Section 508 / WCAG 2.1 AA (New)
Required under the Americans with Disabilities Act. Essential for the elderly patient demographic in the physician validation study.

- System font size respected
- All touch targets ≥44×44px
- High contrast mode (`prefers-contrast: high`)
- `+` / `−` steppers on numeric inputs
- `aria-label` on all inputs
- Specific error messages
- Simplified mode toggle for patients with cognitive difficulties

**Effort:** 3 days

---

## 9. Added to Sprint 6 — Wearable Integration + Clinical Validation

### 6.2 Polypharmacy and Medication Burden Flag (New)
If patient is on ≥4 antihypertensive drug classes AND adherence <75%, surfaces as a separate visit agenda item. Without this flag, the briefing implies the patient needs more monitoring — the exact wrong clinical response when the real problem is medication overload.

Output: "High medication burden may be contributing to adherence difficulty — consider simplification review"

**Effort:** 1 day

### 6.3 `medication_safety.py` Re-Trigger (New)
Whenever `clinical_context.current_medications` is updated by any pathway (FHIR re-ingestion, wearable sync, clinician-recorded adjustment), enqueue a `pattern_recompute` job. Drug interaction detection re-runs as part of the recompute — not only at briefing generation time. This is the architectural fix that makes the Week 1.6 drug interaction alert reliable across all pathways.

**Effort:** 1 day

### 6.4 Medication Adjustment from Dashboard (New)
Clinician records intended medication change directly from the dashboard. ARIA is an intent record — clinician still prescribes in Epic. A persistent banner flags the discrepancy until FHIR re-ingestion confirms the change.

On Save:
1. `pending_med_changes` JSONB updated in `clinical_context`
2. `medication_safety.py` re-runs — drug interaction alert fires if new combination is dangerous
3. `days_since_med_change` resets — inertia detector window restarts from today
4. Titration window starts — adherence Pattern B suppression activates
5. Audit event written with old dose, new dose, reason, actor
6. Persistent banner on all screens until Epic confirms the change via FHIR

Schema addition: `clinical_context.pending_med_changes JSONB DEFAULT '[]'`

Note: Option B (writing MedicationRequest directly to Epic via SMART on FHIR) is deferred post-summer.

**Effort:** 4 days

---

## 10. New Sprint 7 — Clinician Workflow (Week 15)

Entire new sprint. None of these items existed in the original roadmap.

### 7.1 30-Second Briefing Rule
Urgent flags first in plain English. AI summary moves below the fold. Directly affects whether physicians engage with briefings during the validation study.

**Effort:** 2 days

### 7.2 Mobile-First / Tablet Responsive
The hardware budget includes an iPad ($599) for clinician tablet testing but the fixed-column layout breaks at tablet width. PCPs use tablets on rounds and home visits.

**Effort:** 2 days

### 7.3 Alert Triage Inbox Redesign
Batch acknowledge, urgency sort (`drug_interaction` and `gap_urgent` always first), snooze to next appointment, filter by alert type. With 40 patients in the validation study, without batch actions the inbox becomes a blocker.

**Effort:** 2 days

### 7.4 One-Click Dashboard Actions
Three buttons on every patient card: Send Message, Schedule Call, Flag for Review (dropdown: Dosage review / Lab test needed / Urgent callback). Reduces context switching in an 8-minute consultation.

**Effort:** 1 day

### 7.5 Practice-Level Morning Dashboard
Lead physician sees — before the first patient — urgent flag counts, drug interaction alerts, briefings ready, stale risk scores, and weekly alert summary across the practice. All data already in the database.

**Effort:** 3 days

### 7.6 Post-Appointment Feedback Loop
Structured 5-option prompt after each appointment: medication changed / investigation ordered / referral made / flag not relevant / patient declined. Without this, the physician validation study has no outcome data — no precision/recall against real clinical decisions, nothing fundable or publishable.

**Effort:** 2 days

---

## 11. New Sprint 8 — Product (Week 16)

Entire new sprint. None of these items existed in the original roadmap.

### 8.1 Multi-Tenancy + Row-Level Security
Required before the physician validation study runs across multiple practices. Without RLS, Practice A can see Practice B's patients. Staged migration: add `practice_id` as nullable → backfill → NOT NULL → RLS policy. FastAPI middleware sets `SET LOCAL app.practice_id` per request.

**Effort:** 3 days

### 8.2 Practice Admin Role
Each practice in the validation study needs to manage their own patients and clinicians without system admin access. Can enrol/discharge patients, add/remove clinicians, view practice analytics, export audit logs.

**Effort:** 2 days

### 8.3 Practice Analytics Dashboard
Panel risk distribution, briefing read rate, alert response time, inertia prevalence, engagement rate, detector accuracy, drug interaction alert counts. Makes the physician validation study results presentable to a funder.

**Effort:** 3 days

---

## 12. Timeline Change

| | Original | Updated |
|---|---|---|
| Duration | 13 weeks | 16 weeks |
| Physician validation study start | Week 6 | Week 8 |
| Physician validation study end | Week 13 | Week 16 |
| Clinician workflow sprint | Not included | Week 15 (Sprint 7) |
| Product sprint | Not included | Week 16 (Sprint 8) |

---

## 13. Summary Count

| Change Type | Count |
|---|---|
| Corrections to existing content | 16 |
| Items removed | 3 |
| Items deferred to post-summer | 12 |
| Added to Week 1 | 1 |
| Added to Sprint 2 | 2 |
| Added to Sprint 3 | 1 |
| Added to Sprint 4 | 1 |
| Added to Sprint 5 | 7 |
| Added to Sprint 6 | 3 |
| New Sprint 7 items | 6 |
| New Sprint 8 items | 3 |
| **Total changes** | **55** |

---

## 6. Logical Review Fixes (May 2026)

Thirteen issues identified in a full logical review of the document. All fixed in-place.

| # | Issue | Fix Applied |
|---|---|---|
| 1 | RLS (Sprint 8) scheduled Week 16 — physician study starts Week 8 | RLS moved to Sprint 1 as S1.7 — ships before any practice is onboarded |
| 2 | Clinician workflow UX (Sprint 7) scheduled Week 15 — study starts Week 9 | 30-second briefing and alert inbox moved to Sprint 3 (items 3.5, 3.6) |
| 3 | Week 1.6 creates new alert_type before Alembic migration framework exists | Added schema note: raw constraint update in Week 1, reconciled into Sprint S1.1 baseline migration |
| 4 | 16-week project budgeted at 3-month infrastructure rates ($612 short by $200) | Infrastructure updated to 4-month rates ($812). Buffer removed. Total stays $5,000. |
| 5 | Week 1.6 and Sprint 6.3 described the same mechanism with no distinction | 1.6 now covers FHIR re-ingestion only. 6.3 explicitly adds wearable and clinician pathways. |
| 6 | CausalImpact and Medication Response Tracker produce contradictory briefing outputs | CausalImpact scoped to chatbot tool only. Deterministic tracker is the briefing output. Reconciliation rule documented. |
| 7 | Webhook system for EMR Push had no sprint assignment — orphaned in timeline | Moved into Sprint 6 as item 6.6. Timeline table updated. |
| 8 | Week 13 had 16 developer-days of work in a 5-day week | 5.11 Secure Messaging and 5.14 Accessibility moved to Week 14. |
| 9 | Apple CalDAV integration not achievable from a PWA | Section 5.12 updated to Google Calendar only. .ics retained for Apple. Effort updated to 3 days. |
| 10 | CUSUM control limit h derived from current elevated window instead of stable baseline | h now uses Phase I historic_bp_systolic stable baseline. Population SD fallback documented. |
| 11 | ARV 10 mmHg threshold from clinic data — may not apply to daily home readings | Threshold note added. Calibration against home data planned during physician study. |
| 12 | tfcausalimpact (TensorFlow) dependency not mentioned — major conflict risk | pycausalimpact (PyMC-based) recommended instead. Dependency validation step added before Sprint 3. |
| 13 | Section numbering collision — Week 1 and Sprint 1 both used ### 1.1–1.6 | Sprint 1 items renamed S1.1–S1.7. Cross-references updated. |

---

## 7. React Native Migration (May 2026)

**Decision:** Patient app migrated from Next.js 14 PWA to React Native with Expo. Full rationale in `NATIVE_APP_MIGRATION.md`.

### Impact on Timeline

| | Before (PWA) | After (React Native) |
|---|---|---|
| Sprint 5 duration | Weeks 12–13 (2 weeks) | Weeks 12–13 (2 weeks, parallel dev) |
| Sprint 6 | Week 14 | Week 14 |
| Sprint 7 | Week 15 | Week 15 |
| Sprint 8 | Week 16 | Week 16 |
| **Total** | **16 weeks** | **16 weeks** |

**How 2 weeks is maintained:** 6-person team develops React Native screens in parallel (~60 person-days capacity vs ~20 person-days of feature work). App Store submission happens at end of Week 13; Apple review (5–7 days) runs in parallel during Sprint 6 Week 14 — no dedicated submission week needed. Deferred to post-summer: secure messaging (5.11), full Section 508 accessibility audit (5.14), full streaks system (5.13 — streak number shown on home hub only).

### Impact on Budget

| Item | Change | Amount |
|---|---|---|
| Infrastructure | 4 months → 5 months | +$200 ($812 → $1,012) |
| Apple Watch Series 9 | Removed (post-summer, not needed this summer) | −$399 |
| Apple Developer Program ($99/year) | Added — required for App Store | +$99 |
| Google Play Console (one-time) | Added — required for Play Store | +$25 |
| **Net budget change** | | **−$75 (savings)** |
| **Buffer** | | **$75** |
| **Total** | | **$5,000** |

### Technology Replacements in Sprint 5

| Feature | PWA (removed) | React Native (new) | Effort change |
|---|---|---|---|
| Biometric login | WebAuthn challenge-response (3 days) | `expo-local-authentication` (1.5 days) | −1.5 days |
| Push notifications | Web Push API (2 days) | APNs + FCM via `expo-notifications` (1 day) | −1 day |
| Offline support | IndexedDB + Service Worker (2 days) | AsyncStorage + Background Fetch (1 day) | −1 day |
| OCR camera scan | Browser ML Kit (2 days) | `expo-camera` + native ML Kit (1 day) | −1 day |
| Calendar integration | Google OAuth web only, Apple impossible (3 days) | `react-native-calendar-events` for both (1 day) | −2 days |
| Accessibility | WCAG web ARIA (3 days) | Native a11y APIs (2 days) | −1 day |
| **Feature savings** | | | **−7.5 days** |
| Setup + rewrite + App Store | — | Added (Week 12 + Week 15) | +2 weeks |
| **Net timeline addition** | | | **+3 weeks** |

### New Capabilities (not possible in PWA)
- Apple HealthKit integration (post-summer, infrastructure now in place)
- Google Health Connect (post-summer)
- Background sync while app is closed
- App Store discoverability for physician validation study patients
