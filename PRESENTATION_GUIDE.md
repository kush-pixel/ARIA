# ARIA v4.3 — Presentation Deep Reference Guide
## Leap of Faith Technologies | IIT CS 595 | Final Presentation — May 2026
### Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma

---

> This guide is code-traced — every claim links to the actual file and line that produces it.
> Edit freely. Do not present claims you cannot defend from the code.

---

## 1. EXECUTIVE SUMMARY

ARIA is a between-visit clinical intelligence platform for hypertension management. A GP managing 1,800 hypertensive patients has approximately 8 minutes per consultation and no structured view of what happened to a patient since their last appointment. ARIA solves this by (1) ingesting structured patient EHR data via FHIR R4 Bundle from an internal iEMR system, (2) generating clinically realistic synthetic home blood pressure readings and medication adherence confirmations to simulate a home monitoring device, (3) running a three-layer AI pipeline (deterministic rules → weighted risk score → LLM narrative), and (4) delivering a structured pre-visit clinical briefing at 7:30 AM on appointment days, sorted by urgency so the most at-risk patients appear first. Every output is decision support for the clinician only — ARIA never diagnoses, prescribes, or communicates directly with patients.

**What we built:**
- 12-table PostgreSQL schema (Supabase, asyncpg)
- iEMR → FHIR R4 adapter + ingestion pipeline (65 clinic readings, 14 medications, 17 conditions for demo patient 1091)
- Full-timeline synthetic BP generator (1,800+ home readings across 5 years) and medication confirmation generator (1,092 scheduled doses, ~91% adherence rate)
- **4 demo patients** set up by `scripts/setup_demo.py` (idempotent): 1091 (Patient A — Therapeutic Inertia, time-shifted to Jan–May 2026, appointment May 5 2026), DEMO_GAP (Patient B — 9-day reading gap + mild rising trend, 82 days of readings Feb 1–Apr 23), DEMO_ADH (Patient C — Adherence concern Pattern A, 28 days ~152 mmHg avg, ~58% adherence on amlodipine + lisinopril), DEMO_EHR (Patient D — EHR-only, no home monitoring, triple whammy drug interaction)
- 5 Layer 1 pattern detectors (gap, inertia, deterioration, adherence, variability)
- **Deterministic drug interaction detector** (4 rules: nsaid_antihypertensive, triple_whammy, k_sparing_ace_arb, bb_non_dhp_ccb — comorbidity-escalated severity, no LLM)
- Layer 2 risk scorer (5-signal weighted sum, 0–100)
- Layer 3 LLM briefing summarizer (claude-sonnet-4-20250514; currently gpt-4o-mini override — see Limitation 1)
- **Risk tier reclassification system** — nightly hysteresis-based tier promotion/demotion, clinician override endpoint (`PATCH /tier`), 28-day suppression window (NICE NG136), I61 haemorrhagic stroke added to system overrides
- **BP Trend single source of truth** — `trend_avg_systolic` in briefing payload; dashboard and briefing always show the same number
- **Active briefing filter** — post-visit briefings excluded; mini-briefings always shown
- **Alert calibration suppression** — approved `calibration_rules` rows now actually suppress alert inbox writes
- Next.js 14 + FastAPI dashboard with dark mode, tier filtering, interactive tour, alert inbox
- Patient PWA (CuffLink) — BP submit, medication confirm, .ics reminders (port 3001)
- Shadow mode validation framework: 78.4% agreement on 37 labelled evaluation points (target ≥80%; not yet achieved — see Limitation 2)
- 601 unit tests passing; ruff clean

---

## 2. UI METRIC DICTIONARY

### A. PATIENT LIST / DASHBOARD

---

#### A1. Risk Tier Badge

**What the user sees:**
Colored pill badge: "High Risk" (red), "Medium Risk" (amber), "Low Risk" (green) with a colored dot. Appears in the Risk Tier column.

**Where it comes from:**
- Frontend: `frontend/src/components/dashboard/RiskTierBadge.tsx`
- API: `GET /api/patients` → `backend/app/api/patients.py`
- Table: `patients.risk_tier TEXT NOT NULL`
- Computed at: `backend/app/services/fhir/ingestion.py` (at ingestion, auto-overrides applied)

**What it means:**
A clinician-facing urgency classification that controls the default sort order of the patient list. High = patients most needing pre-visit review.

**Logic:**
Three-tier system with three independent mechanisms: ingestion-time system overrides (immovable), nightly algorithmic reclassification (hysteresis), and clinician manual override (28-day suppression window).

**Ingestion-time auto-overrides (immovable floors):**
- CHF (I50.*) → `risk_tier = "high"`, `tier_override_source = "system"`
- Haemorrhagic Stroke (I61.*) → `risk_tier = "high"`, `tier_override_source = "system"` *(added v4.3)*
- Ischaemic Stroke (I63/I64) → `risk_tier = "high"`, `tier_override_source = "system"`
- TIA (G45.*) → `risk_tier = "high"`, `tier_override_source = "system"`

These cannot be demoted by the nightly job or by `PATCH /api/patients/{id}/tier`. The endpoint returns 409. Requires updating the EHR and re-ingesting.

**Nightly reclassification (hysteresis):**
Runs after every `pattern_recompute` job. Transitions: medium→high at score ≥75, high→medium at score <40 (system_score only), medium→low at score <25 with enrollment and comorbidity gates, low→medium at score ≥40. Break-glass: clinician demotion override is bypassed if score ≥85.

**Clinician manual override:**
`PATCH /api/patients/{id}/tier` with required reason string. Demotion sets `tier_override_suppressed_until = now + 28 days` (NICE NG136 §1.6.3). Nightly job will not reverse the decision for 28 days unless break-glass fires.

**Why we show it:**
High-risk patients need pre-visit review within the 8-minute consultation budget. The tier instantly signals which patients cannot be skimmed.

**Presentation talking point:**
"The risk tier has three layers: a hard clinical floor from the EHR (CHF = always High, immovable), a nightly algorithm that promotes or demotes based on score bands, and a clinician override with a 28-day protection window aligned to NICE guidance. They operate independently and never conflict."

**Professor follow-up questions:**
- Q: "Who sets the risk tier?" A: Three mechanisms. System overrides at ingestion for CHF/Stroke/TIA — immovable. Nightly algorithm for score-driven transitions with hysteresis to prevent oscillation. Clinician can override with a reason and 28-day suppression.
- Q: "Can a clinician demote a CHF patient?" A: No. `PATCH /tier` returns 409 for system-override patients. To change it, the clinician updates the EHR problem list and re-ingests. This is intentional — missing CHF on a risk tier has patient safety implications.
- Q: "What prevents the algorithm from reversing a clinician decision the next day?" A: `tier_override_suppressed_until` is set to 28 days out on demotion. The nightly job checks this field and skips reclassification until it expires.

**Weak answer to avoid:**
"The tier is computed by our AI." — It is NOT. It is three layers of deterministic rules with no AI involvement.

---

#### A2. Priority Score (Risk Score)

**What the user sees:**
A number 0–100 shown as an integer (e.g., "73") with a color-coded progress bar below it (green < 40, amber 40–70, red ≥ 70). Tooltip says "Priority score based on BP trend, medication history, and adherence signal."

**Where it comes from:**
- Frontend: `frontend/src/components/dashboard/RiskScoreBar.tsx`
- API: `GET /api/patients` → `patients.risk_score NUMERIC(5,2)`
- Backend service: `backend/app/services/pattern_engine/risk_scorer.py`, function `compute_risk_score()`
- Called by: `backend/app/services/worker/processor.py` after Layer 1 detectors complete

**What it means:**
A weighted composite priority score (0–100) that ranks patients within the same risk tier. Higher = more clinical urgency relative to that patient's own baseline and history. It is NOT a diagnosis probability. It is a prioritization tool.

**Logic — 5 signals, weights sum to 1.00:**

| Signal | Weight | Formula | Saturates |
|--------|--------|---------|-----------|
| Systolic vs baseline | 30% | `(28d_avg_systolic - baseline) / 30.0 × 100`, clamped 0–100 | +30 mmHg above baseline |
| Medication inertia | 25% | `days_since_med_change / 180.0 × 100`, clamped 0–100 | 6 months without change |
| Inverted adherence | 20% | `100 - adherence_pct`, clamped 0–100 | 0% adherence |
| Reading gap | 15% | `gap_days / window_days × 100`, clamped 0–100 | Full adaptive window with no readings |
| Comorbidity severity | 10% | CHF/Stroke/TIA = 25pts each; DM/CKD/CAD = 15pts; others = 5pts; clamped 0–100 | Heavy comorbidity load |

**Baseline selection hierarchy:**
1. `median(clinical_context.historic_bp_systolic)` — all clinic readings from EHR (preferred)
2. `clinical_context.last_clinic_systolic` — most recent clinic reading
3. `140.0 mmHg` — hardcoded fallback when no history exists

**Fallback behavior:**
- No recent BP readings: sig_systolic = 50.0 (neutral penalty, not zero)
- No medication confirmations: sig_adherence = 50.0 (neutral, not zero)
- No appointment date or last visit: sig_gap uses 28-day fallback window

**Adaptive window:**
`window_days = min(90, max(14, (next_appointment - last_visit_date).days))`, fallback 28.
sig_gap uses this window (not a hardcoded 14 days).

**Why we show it:**
Tier alone is binary. Within a cohort of High Risk patients, the score distinguishes "High Risk, score 87" (review urgently) from "High Risk, score 41" (review but less critical).

**Presentation talking point:**
"The score is not a diagnosis — it is a prioritization signal. A score of 73 does not mean 73% chance of a bad outcome. It means this patient ranks higher than a patient scoring 52 on the same five clinical dimensions."

**Professor follow-up questions:**
- Q: "Why 30% weight on systolic?" A: Systolic BP deviation from the patient's own baseline is the most direct signal of treatment insufficiency. The 30% weight reflects this primacy; the other four signals provide context.
- Q: "How did you validate these weights?" A: The weights are expert-informed, not empirically optimized. They are a starting point calibrated against clinical guidelines (JNC 8, ESH). A production system would tune them via clinician feedback over 6–12 months of use.
- Q: "What does the baseline represent?" A: The median of all clinic systolic readings in the patient's EHR history. For patient 1091, that is 65 readings across 5 years, median ~134 mmHg.
- Q: "Why not use a machine learning model for scoring?" A: We deliberately avoided ML here. Without labeled outcome data, an ML model would be fitting noise. The weighted rule-based approach is interpretable, auditable, and correctable — essential for a clinical safety context.

**Risks/limitations:**
- Weights are not empirically validated against patient outcomes — they are clinical judgment.
- Baseline requires ≥2 clinic readings; patients with sparse EHR history fall back to 140 mmHg.
- Score does not account for trending (whether things are improving or worsening week-over-week).

---

#### A3. Score Stale Indicator

**What the user sees:**
Small amber text "Score stale" with an AlertTriangle icon below the score bar. Appears when the score has not been recomputed in >26 hours.

**Where it comes from:**
- Frontend: `PatientList.tsx` → `isScoreStale(patient.risk_score_computed_at)` → `Date.now() - new Date(computedAt).getTime() > 26 * 60 * 60 * 1000`
- Table: `patients.risk_score_computed_at TIMESTAMPTZ`
- Set by: `risk_scorer.py` → `update(Patient).values(risk_score_computed_at=now)`

**What it means:**
The nightly pattern recompute (midnight UTC) should update the score daily. If more than 26 hours have elapsed since the last computation, the score shown may not reflect today's readings.

**Why 26 hours (not 24)?**
One hour of grace accommodates processing delays, time zone shifts, and brief server interruptions.

**Presentation talking point:**
"If the background worker failed overnight, we show a staleness warning so the clinician knows the score might not include today's data. Transparency about data freshness is a safety requirement."

---

#### A4. Appointment Column

**What the user sees:**
If the patient has an appointment TODAY, shows the appointment time as "HH:MM" with a blue clock icon. Otherwise shows "—".

**Where it comes from:**
- Frontend: `PatientList.tsx` → `isToday(patient.next_appointment)` → `formatApptTime()`
- Table: `patients.next_appointment TIMESTAMPTZ`
- Set at: ingestion (from `_aria_visit_dates`), or manually via `PATCH /api/patients/{id}/appointment`

**Important design decision:**
Only today's appointments are highlighted. The GP only needs to know which patients are coming in TODAY in this 8-minute workflow. Future appointments are stored but not displayed in the list view.

**Presentation talking point:**
"We deliberately only show today's appointment time. The GP's morning workflow is: who is coming in today, in what order of urgency. Showing future dates would add noise."

---

#### A5. BP Trend Sparkline (Mini)

**What the user sees:**
A small (80×28 px) SVG line chart in the BP Trend column. Shows an area fill and line in the patient's tier color (red/amber/green). A dot marks the most recent reading. Below it shows the last systolic value in mmHg.

**Where it comes from:**
- Frontend: `frontend/src/components/dashboard/MiniSparkline.tsx` (pure SVG, no chart library)
- Data: `getReadings(patient_id)` → `GET /api/readings/{patient_id}` → `backend/app/api/readings.py`
- Table: `readings.systolic_avg` (last 14 home readings, non-clinic source)
- Fetched: in parallel for all patients AFTER the patient list loads (not blocking)

**Logic:**
- Filters `source !== 'clinic'` — shows only home readings, not clinic BP
- Sorts chronologically, takes last 14 readings
- Normalizes Y-axis to the min/max range of those 14 readings
- Color determined by patient's `risk_tier`: high=red (#EF4444), medium=amber (#F59E0B), low=green (#16A34A)

**Why exclude clinic readings?**
Clinic readings appear in the briefing's long-term trajectory. The sparkline is specifically for home monitoring trend — if it shows a persistent plateau at 160 mmHg, that is more actionable than a single clinic reading.

**Limitation:**
The sparkline normalizes to the data range, not to an absolute scale. Two patients with sparklines of identical shape could have very different absolute BP levels. The numeric label below the chart provides context.

**Presentation talking point:**
"The sparkline is purely home readings — not clinic BP. Its shape over 14 readings tells the GP whether this patient's home BP has been stable, rising, or declining since the last visit."

---

#### A6. Has Briefing Indicator

**What the user sees:**
A FileText icon — blue if a briefing exists, gray if not. In the rightmost column.

**Where it comes from:**
- Frontend: `PatientList.tsx` → `patient.has_briefing` (boolean)
- API: `patients.py` → `SELECT DISTINCT patient_id FROM briefings` → set membership check
- Table: `briefings` (any row for this patient, not just today)

**Important note:**
`has_briefing` is TRUE if ANY briefing exists for this patient — not necessarily today's. Today's briefing is generated at 7:30 AM by the scheduler. If the briefing has not been generated yet (appointment not today, or scheduler hasn't run), this shows gray.

**Presentation talking point:**
"The blue icon means a full structured briefing exists and can be opened. Clicking any patient row navigates to the briefing detail page."

---

#### A7. Tier Filter Tabs + Pagination

**What the user sees:**
Four tabs: All (N), High Risk (N), Medium Risk (N), Low Risk (N). 10 patients per page with prev/next buttons and page number pills.

**Where it comes from:**
- Frontend: `PatientList.tsx` — client-side filter, no additional API call
- Sort order: determined at API level (`_TIER_ORDER = {"high": 0, "medium": 1, "low": 2}`, then risk_score DESC)

**Important:**
Sort is authoritative from the API — the frontend previously had a duplicate `sortPatients()` function that was removed (Fix 31). Do not claim the frontend sorts — it does not.

---

### B. PATIENT DETAIL / BRIEFING PAGE

---

#### B1. Pre-Visit Clinical Briefing Header

**What the user sees:**
Patient ID, gender, age, tier override reason (e.g., "CHF in problem list"), next appointment (full date + time), risk tier badge (large), priority score bar. Also shows briefing generated_at timestamp and read_at time if the clinician has viewed it.

**Where it comes from:**
- Frontend: `frontend/src/components/briefing/BriefingCard.tsx`
- Data: patient record + briefing row from `briefings` table
- `read_at` set by: `GET /api/briefings/{patient_id}` → `backend/app/api/briefings.py` → updates `briefing.read_at = now()` + writes `audit_events` row with `action="briefing_viewed"`

**Audit note:**
Every time a clinician opens a patient briefing, an audit event is written. This satisfies the GDPR and clinical audit requirements for access logging.

---

#### B2. AI Summary (Layer 3)

**What the user sees:**
A blue highlighted box labeled "AI Summary" with a sparkle icon. Contains a 3-sentence narrative summary of the briefing. Only appears if `payload.readable_summary` is non-null.

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `payload?.readable_summary`
- Table: `briefings.llm_response JSONB` → field `readable_summary`
- Generated by: `backend/app/services/briefing/summarizer.py` → `generate_llm_summary()`
- Model: **Currently gpt-4o-mini (TEMP override).** CLAUDE.md specifies claude-sonnet-4-20250514. Both summarizer.py and chat/agent.py have `# TEMP` comments for reversion.
- Validated by: `backend/app/services/briefing/llm_validator.py` → `validate_llm_output()`

**What it means:**
A 3-sentence human-readable summary of the deterministic briefing JSON. Layer 3 runs AFTER Layer 1 (compose_briefing) is complete and persisted. If validation fails twice, `readable_summary` is stored as `None` and this section is hidden.

**Guardrails enforced at code level (llm_validator.py):**
- Forbidden phrases: "non-adherent", "non-compliant", "hypertensive crisis", "medication failure", any dose-change instruction ("increase.*mg"), "prescribe", "diagnos", "emergency", direct patient instructions
- PHI check: patient ID verbatim in output
- Prompt injection check: "[INST]", "system:", "ignore previous"
- Faithfulness: exactly 3 sentences; risk_score within ±10; BP values 60–250 mmHg and within ±20 of trend; drug names must be in medication_status; urgency claims grounded in urgent_flags

**Validator hardening (demo-day fixes):**
- Short ICD-10 code false positives resolved: "tia" now matched as `\bTIA\b` to prevent substring matches in clinical words like "potential" or "initially"
- Treatment-review language: allowed when `urgent_flags` contains "inertia" and adherence Pattern A is absent
- Drug interactions injected into LLM prompt: concern/critical interactions are explicitly listed in the user message so the model cannot miss them
- **Result: ALL CHECKS PASSED on first attempt for all 4 demo patients**

**Audit:**
Every validation attempt → `audit_events` row with `action="llm_validation"`, `outcome="success"|"failure"`, `details=failed_check_name`.

**Presentation talking point:**
"The AI summary is Layer 3 — it only runs after the deterministic briefing is complete and persisted. If the AI produces anything unsafe or clinically unsound, the validator blocks it and the deterministic briefing is still shown. The AI narrative enhances, it does not replace."

**Professor follow-up questions:**
- Q: "What model is running?" A: Currently gpt-4o-mini with a TEMP override — the architecture specifies claude-sonnet-4-20250514 and reversion is a one-line change. The model swap does not affect the validation layer.
- Q: "How do you prevent the LLM from recommending medications?" A: The validator checks for forbidden phrases including any dose-change language before the output reaches the UI. The system prompt also instructs the model explicitly. Defense in depth.
- Q: "What if the LLM hallucinates a medical condition?" A: The faithfulness validator checks that all condition names in the summary are grounded in `active_problems`. A synonym map handles cases like "heart failure" mapping to "CHF".

**Weak answer to avoid:**
"The LLM generates the briefing." — The LLM only generates a 3-sentence summary. The full briefing (all 9 fields) is deterministic.

---

#### B3. Data Limitations Banner

**What the user sees:**
An amber or gray info banner near the top. Amber (with left border): "Home monitoring not active — this briefing is based on clinic records only." Gray: shows `payload.data_limitations` text, e.g., "Patient enrolled 15 days ago — minimum 21-day monitoring period required for inertia and deterioration detectors."

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `!patient.monitoring_active || payload?.data_limitations`
- Table: `patients.monitoring_active BOOLEAN` + `briefings.llm_response.data_limitations`
- Generated by: `composer.py` → `_build_data_limitations()` function

**What it means:**
Signals to the clinician that the briefing has incomplete or restricted data. Cold-start suppression (enrolled <21 days): only gap detector runs; inertia, deterioration, adherence are suppressed.

**Why 21 days for cold-start?**
≥21 days ensures at least 7 home readings exist (assuming ~1 reading/3 days), which is the minimum for meaningful slope calculation in the deterioration detector.

---

#### B4. BP Trend Section

**What the user sees:**
A text summary like "28-day home average: 158/96 mmHg (Stage 2 hypertension range) based on 42 reading sessions. Readings show an upward trend over the period. 3-month trajectory: rising from 148 in July — worsening trend." Below: a full `SparklineChart.tsx` (recharts LineChart) showing systolic and diastolic over the window.

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `SparklineChart.tsx`
- Table: `briefings.llm_response.trend_summary` (text) + `readings` (for chart)
- Generated by: `composer.py` → `_build_trend_summary()` + `_build_long_term_trajectory()`

**Logic:**
- `_build_trend_summary()`: computes mean systolic/diastolic over adaptive window. If ≥7 readings, compares first 7 vs last 7 session averages (delta ≥5 = "upward", ≤-5 = "downward", else "stable").
- `_build_long_term_trajectory()`: anchors 90-day window on the most recent clinic DATE in historic_bp_dates (not today — handles historical patients like 1091 whose last visit was 2015).
- BP categories: <120="normal range", 120–129="elevated", 130–139="Stage 1", ≥140="Stage 2"

**Presentation talking point:**
"The trend summary gives the GP two time horizons: the recent home monitoring window (14–90 days depending on inter-visit interval) and a 3-month clinic trajectory from the EHR. Both are needed because a patient can look stable on home readings but show a longer deterioration trend in clinic records."

---

#### B5. Medication Status Section

**What the user sees:**
Text describing the current antihypertensive regimen and when the last medication change occurred. Example: "Current regimen: ramipril 10mg, amlodipine 5mg, furosemide 40mg. Last medication change: 2013-09-26 — within expected titration window, full response may not yet be established." Non-antihypertensive drugs are filtered out by the frontend.

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `filterMedicationStatusText(payload?.medication_status)` → `frontend/src/lib/hypertension-meds.ts`
- Table: `briefings.llm_response.medication_status` (text)
- Generated by: `composer.py` → `_build_medication_status()`
- Filter: 60+ antihypertensive drug names across 11 drug classes (ACE inhibitors, ARBs, beta-blockers, CCBs, diuretics, etc.)

**Titration notice:**
Appended when `days_since_last_med_change ≤ titration_window`. Window is drug-class-aware:
- Diuretics/beta-blockers: 14 days
- ACE inhibitors/ARBs: 28 days
- Amlodipine: 56 days
- Unknown/default: 42 days

**Why filter to antihypertensives only?**
Patient 1091 has 14 active medications. In an 8-minute consultation, the GP needs to focus on the drugs directly relevant to BP management. NSAIDs, antidepressants, etc. are shown in the Active Problems section instead.

**Presentation talking point:**
"The medication filter is intentional — we show only the antihypertensives. The GP is reviewing this briefing for BP management, not polypharmacy review. A cardiologist or pharmacist would see a different view."

---

#### B6. Adherence Signal Section

**What the user sees:**
Per-medication adherence bars for antihypertensive drugs. Example: "Ramipril 10mg — 94% (21/22 doses confirmed)." If no antihypertensive adherence data: italic fallback text. Below the bars: the pattern interpretation ("possible adherence concern", "treatment review warranted", etc.)

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `AdherenceSummary.tsx`, filtered to `hypertensionAdherence`
- Data: `GET /api/adherence/{patient_id}` → `backend/app/api/adherence.py`
- Table: `medication_confirmations` (confirmed_at count vs scheduled count)
- Generated by: Layer 1 adherence analyzer, confirmed rates computed in briefing composer

**Logic:**
`adherence_pct = confirmed_at count / total scheduled × 100` over the adaptive window.
Low adherence threshold: <80%.

**Pattern classification:**
- A: high BP + low adherence → "possible adherence concern" (alert raised)
- B: high BP + high adherence → "treatment review warranted"
- C: normal BP + low adherence → "contextual review"
- none: normal BP + high adherence → no concern

**Important clinical language constraint:**
The code explicitly uses "possible adherence concern" — never "non-adherent". This is enforced at the string level in `adherence_analyzer.py` (`_INTERPRETATIONS` dict) AND validated by `llm_validator.py`.

**Synthetic data note:**
Adherence rates for patient 1091 are synthetic, drawn from a Beta distribution (α=6.5, β=0.65, mean≈91%). This means we cannot claim real adherence behavior — only that the system correctly handles the generated signal.

**Presentation talking point:**
"We never label a patient as non-adherent. We show 'possible adherence concern' and the supporting adherence percentage. The GP decides whether the adherence pattern is clinically significant."

---

#### B7. Active Problems Section

**What the user sees:**
Pill badges for each condition: e.g., "Congestive heart failure (CHF)", "Type 2 diabetes", "CKD". Sorted by clinical priority (CHF > HTN > DM > CAD).

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `payload.active_problems` (string array)
- Table: `clinical_context.active_problems TEXT[]` (parallel to `problem_codes TEXT[]`)
- Generated at ingestion: adapter maps FHIR Condition resources → ingestion.py stores in clinical_context

**Logic:**
`_sort_problems()` in composer.py: ICD-10 prefix priority: I50 (CHF)=0, I10 (HTN)=1, E11 (DM)=2, I25 (CAD)=3, others alphabetical.

---

#### B8. Overdue Investigations Section

**What the user sees:**
Red-highlighted section "Overdue Investigations" with bullet points. Example: "Renal function (eGFR) overdue", "HbA1c overdue (diabetic patient)".

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `payload.overdue_labs`
- Table: `clinical_context.overdue_labs TEXT[]` (populated from iEMR `ServiceRequest` resources where `PLAN_NEEDS_FOLLOWUP=YES`)
- Also: abnormal recent_labs flags from `clinical_context.recent_labs JSONB`

**Limitation:**
For patient 1091, `recent_labs` is NULL — there are no structured LOINC lab observations in the iEMR data. The infrastructure is in place but the data is absent.

---

#### B9. Visit Agenda Section

**What the user sees:**
An ordered list of 3–6 visit agenda items in clinical priority order. Example:
1. "Urgent: reading gap — no home BP received in 5 days"
2. "Possible therapeutic inertia — BP sustained above threshold, no medication change since 2013-09-26"
3. "Possible adherence concern — ramipril 89% adherence"
4. "Pending follow-up: eGFR monitoring"
5. "Review active problems: CHF, T2DM"
6. "Next appointment recommendation"

**Where it comes from:**
- Frontend: `VisitAgenda.tsx`
- Table: `briefings.llm_response.visit_agenda` (string array)
- Generated by: `composer.py` → `_build_visit_agenda()`

**Priority order (fixed):**
1. Urgent alerts (gap_urgent, deterioration)
2. Inertia (from active alert rows — Fix 18: no re-computation)
3. Adherence concern
4. Overdue labs
5. Active problems review
6. Next appointment recommendation

**Presentation talking point:**
"The visit agenda is a prioritized 8-minute consultation plan. The GP opens the briefing and immediately sees what to discuss first. This is the core value of ARIA — structured time allocation within a short consultation."

---

#### B10. Clinical Flags Section

**What the user sees:**
Amber-bordered boxes with AlertTriangle icons. Each flag is a plain-English string. Example: "Possible therapeutic inertia — 28-day average systolic 158 mmHg with no medication change detected."

**Where it comes from:**
- Table: `briefings.llm_response.urgent_flags` (string array)
- Generated by: `composer.py` — pulls from unacknowledged alerts

**Important:**
Urgent flags come from ACTIVE UNACKNOWLEDGED alerts. If an alert is acknowledged in the AlertInbox, it disappears from future briefings.

---

#### B11. Drug Interactions Section

**What the user sees:**
Severity-coded cards (critical = red border, concern = amber, warning = gray). Each card shows: the interaction rule name, drugs involved as pill badges, plain-English description, and a "Comorbidity escalated" notice when present. The section only appears when at least one interaction is detected.

**Where it comes from:**
- Frontend: `BriefingCard.tsx` → `payload?.drug_interactions`
- Table: `briefings.llm_response.drug_interactions` (JSONB array)
- Generated by: `backend/app/services/briefing/medication_safety.py` → `check_interactions(ctx)`
- Called from: `composer.py` at briefing generation time — zero additional DB queries

**What it means:**
Four deterministic interaction rules applied against the patient's `current_medications` and `problem_codes`. No LLM involvement. No AI.

| Rule | Combination | Base Severity | Escalation |
|---|---|---|---|
| `nsaid_antihypertensive` | NSAID + any antihypertensive | warning | → concern (CHF or CKD) |
| `triple_whammy` | NSAID + ACE/ARB + any diuretic | concern | → critical (both CHF AND CKD) |
| `k_sparing_ace_arb` | K-sparing diuretic + ACE/ARB | warning | → concern (CKD) |
| `bb_non_dhp_ccb` | Beta-blocker + verapamil or diltiazem | concern | No escalation |

**Deduplication:** Triple whammy is evaluated first. When it fires, the simpler NSAID + antihypertensive rule is suppressed — one finding, not two overlapping ones.

**For demo patient 1091:**
Voltaren (diclofenac, NSAID) + ramipril (ACE inhibitor) + furosemide (loop diuretic) → triple whammy → concern. With active CHF (I50.9) AND CKD: escalates to **critical**. `comorbidity_amplified: true`.

**Visit Agenda priority order (updated with interactions):**
1. Critical interactions (before all other items)
2. Urgent alerts
3. Concern-level interactions (alongside urgent alerts)
4. Therapeutic inertia
5. Adherence concern
6. Warning-level interactions
7. Variability
8. Overdue labs
9. Active problems review

**Presentation talking point:**
"The drug interaction detector is completely deterministic — four evidence-based rules checked against the medication list. Patient 1091 has a triple whammy: Voltaren with an ACE inhibitor and a diuretic. With active CHF and CKD, severity escalates to critical and appears as the first item in the visit agenda. No AI, no probability — a rule fired."

**Professor follow-up questions:**
- Q: "Couldn't the GP see this in the EHR?" A: The EHR holds the medication list. ARIA cross-references it against comorbidity codes and classifies severity at the point of the pre-visit briefing — when the GP is actively reviewing. It surfaces the interaction proactively, before the consultation.
- Q: "What if a patient's NSAID is prescribed for a valid reason?" A: ARIA flags the combination, not the prescription decision. The GP sees the interaction and its severity, then decides how to approach it in the consultation. ARIA is decision support — it does not recommend stopping medications.

**Weak answer to avoid:**
"The AI detected the drug interaction." — `medication_safety.py` is a deterministic rule engine. No LLM is involved.

---

### C. ALERT INBOX

---

#### C1. Alert Types

**What the user sees:**
Five alert types with distinct labels and colors:
- `gap_urgent` (red): "Urgent reading gap — no home BP received"
- `gap_briefing` (amber): "Reading gap — review at next appointment"
- `inertia` (amber): "Possible therapeutic inertia — elevated BP with no medication change"
- `deterioration` (red): "Possible sustained BP worsening trend"
- `adherence` (amber): "Possible adherence concern flagged"

**Where they come from:**
- Frontend: `frontend/src/components/dashboard/AlertInbox.tsx`
- Table: `alerts` table → `alert_type TEXT`, `gap_days SMALLINT`, `systolic_avg NUMERIC(5,1)`
- Created by: `processor.py` → `_upsert_alert()` after each pattern_recompute job

**Deduplication:**
One alert per (patient_id, alert_type, date). Re-running pattern_recompute on the same day does not create duplicates.

**Additional data shown:**
- "Avg systolic: 158 mmHg" — from `alerts.systolic_avg`
- "Gap: 5d" — from `alerts.gap_days`
- "Xh ago" / "Xd ago" — computed from `triggered_at` vs now

---

#### C2. Escalated Badge

**What the user sees:**
Red "Escalated" pill with ShieldAlert icon on certain active alerts.

**Where it comes from:**
- Table: `alerts.escalated BOOLEAN DEFAULT FALSE`
- Set by: `processor.py` → scans alerts WHERE `delivered_at` is non-null, `acknowledged_at` IS NULL, `triggered_at <= now - 24h` — sets `escalated = TRUE` for `gap_urgent` and `deterioration` types only.

**What it means:**
A critical alert (urgent gap or deterioration) has been unacknowledged for more than 24 hours.

**Presentation talking point:**
"If a gap_urgent or deterioration alert goes unacknowledged for 24 hours, the system escalates it. In production, this would trigger an admin notification — in the demo, it surfaces visually."

---

#### C3. Off-Hours Badge

**What the user sees:**
Gray "Off-hours" pill with Clock icon.

**Where it comes from:**
- Table: `alerts.off_hours BOOLEAN DEFAULT FALSE`
- Set by: `_is_off_hours(dt)` in `processor.py` → `True` if 6 PM–8 AM UTC or weekend (Saturday/Sunday)

**What it means:**
The alert was triggered outside normal clinic hours. In a production system, off-hours alerts might follow a different delivery protocol (lower priority, no pager notification).

---

#### C4. Acknowledge + Undo Actions

**What the user sees:**
"Acknowledge" button on active alerts. "Undo" button on acknowledged alerts within 24 hours.

**Where it comes from:**
- API: `PATCH /api/alerts/{alert_id}/acknowledge` → sets `acknowledged_at = now()`, writes `audit_events` row
- API: `PATCH /api/alerts/{alert_id}/unacknowledge` → clears `acknowledged_at`
- Frontend: optimistic update (UI changes immediately, API called asynchronously)

---

## 3. BACKEND LOGIC MAP

```
Dashboard Patient List:
  PatientList.tsx
  → GET /api/patients (patients.py)
  → SELECT * FROM patients (sorted by tier then risk_score DESC)
  → SELECT DISTINCT patient_id FROM briefings (has_briefing check)
  → getReadings(patient_id) for each patient (in parallel)
  → readings table (source != 'clinic', last 14, systolic_avg)
  → MiniSparkline.tsx (pure SVG)

Patient Detail / Briefing:
  patients/[id]/page.tsx
  → GET /api/patients/{id}
  → GET /api/briefings/{patient_id} → sets briefing.read_at + audit_event
  → GET /api/readings/{patient_id}
  → GET /api/adherence/{patient_id}
  → BriefingCard.tsx

Briefing Generation Flow:
  APScheduler 7:30 AM UTC
  → scheduler.py::enqueue_briefing_jobs()
  → processing_jobs table (status=queued)
  → pg_notify('aria_jobs', '')
  → processor.py (woken by LISTEN/NOTIFY or 30s poll)
  → _handle_briefing_generation()
  → composer.py::compose_briefing() [Layer 1]
  → summarizer.py::generate_llm_summary() [Layer 3, optional]
  → llm_validator.py::validate_llm_output() [guardrails]
  → briefings table (llm_response JSONB)

Pattern Recompute Flow:
  APScheduler midnight UTC
  → scheduler.py::enqueue_pattern_recompute_sweep()
  → processor.py::_handle_pattern_recompute()
  → gap_detector → inertia_detector → adherence_analyzer → deterioration_detector → variability_detector [Layer 1]
  → risk_scorer.py::compute_risk_score() [Layer 2]
  → alerts table (_upsert_alert for each detector that fires)
  → patients.risk_score + risk_score_computed_at updated

Risk Score Signal Chain:
  patients table → risk_score_computed_at (staleness)
  clinical_context → historic_bp_systolic (baseline), med_history (last change date), problem_codes (comorbidity)
  readings → systolic_avg AVG over window (sig_systolic)
  medication_confirmations → confirmed_at COUNT (sig_adherence)
  readings → MAX(effective_datetime) (sig_gap)
```

---

## 4. CLINICAL MEANING — WHY EACH FEATURE MATTERS

### Gap Detection
**Clinical problem:** A hypertensive patient who stops measuring has either device failure, illness, travel, depression, or declining engagement. Any of these is clinically relevant. A 3-day gap for a High Risk patient means 3 days of invisible BP.
**ARIA response:** Gap detector fires based on tier-appropriate thresholds. High Risk patients are flagged after just 1 day — they need frequent monitoring.

### Therapeutic Inertia
**Clinical problem:** Studies show GPs often continue the same drug regimen even when BP remains elevated visit after visit. The patient is adherent, taking all medications — but no dose adjustment or drug change is made. This "clinical inertia" is one of the leading causes of uncontrolled hypertension.
**ARIA response:** Inertia detector fires when: average BP over the window is above the patient's own adaptive threshold, this elevation has persisted for >7 days, and no medication change appears in the medication history. The briefing surfaces this as a "treatment review warranted" agenda item.

### Adherence-BP Correlation
**Clinical problem:** A patient with poor adherence and elevated BP needs a different clinical response than a patient with perfect adherence and elevated BP. The GP needs to distinguish these two scenarios before the appointment.
**ARIA response:** Pattern A (low adherence + high BP) → conversation about barriers to adherence. Pattern B (high adherence + high BP) → may warrant medication review. Pattern C (low adherence + normal BP) → contextual review, don't panic.

### Deterioration Detection
**Clinical problem:** A patient whose BP was previously controlled but shows a 3-week upward trend starting 10 days ago may be experiencing a life event (medication side effect, new drug interaction, dietary change, stress). Without trend data, the GP only sees the reading on appointment day.
**ARIA response:** Deterioration requires positive slope (≥0.3 mmHg/day), recent 3-day average exceeding the 4–10 day baseline, AND absolute elevation. The step-change sub-detector catches sudden BP jumps (≥15 mmHg in one week) that the linear slope model might miss.

### Risk Score + Tier Sort
**Clinical problem:** With 1,800 patients, the GP cannot review all briefings. The dashboard must surface the 5–10 patients most needing attention today.
**ARIA response:** Tier (High/Medium/Low) gives coarse ordering. Risk score ranks within tier. The GP sees High Risk patients first, then within High Risk, the most clinically urgent by composite score.

---

## 5. PRESENTATION TALKING POINTS (SLIDE-READY)

**The Problem (30 seconds):**
"A GP with 1,800 hypertensive patients gets 8 minutes per appointment and no structured view of between-visit data. They walk in with the same clinic BP from 3 months ago. ARIA fixes this."

**What ARIA Does (45 seconds):**
"ARIA ingests the patient's EHR as a FHIR bundle, generates clinically realistic home BP readings to simulate a connected device, runs deterministic pattern detection, and delivers a structured pre-visit briefing at 7:30 AM on appointment day — sorted by clinical urgency."

**Three-Layer AI (60 seconds):**
"Layer 1 is pure rules — gap detection, therapeutic inertia, adherence analysis, deterioration detection. No AI, no probability. Layer 2 is a weighted priority score from five clinical signals. Layer 3 is an LLM that converts the deterministic briefing into a readable 3-sentence summary, with hard-coded validation to block any unsafe output. Layer 1 always runs first and Layer 3 never bypasses it."

**Clinical Boundary (30 seconds):**
"ARIA does not diagnose. It does not recommend medications. It does not send alerts to patients. Every output is decision support for the clinician. We use 'possible adherence concern' not 'non-adherent' — the language is enforced at code level."

**Shadow Mode Validation (30 seconds):**
"We validated ARIA against 37 labelled historical evaluation points from patient 1091's 5-year clinic record. ARIA's detectors were replayed at each historical visit using only data that would have been available before that visit. We currently achieve 78.4% agreement against physician-labeled concern flags — below our 80% target."

**Synthetic Data (20 seconds):**
"Because we're working with a single de-identified patient, we generate realistic home BP readings and medication confirmations that follow clinical rules: day-to-day variability of 8–12 mmHg, morning readings 5–10 mmHg higher than evening, pre-appointment dip of 10–15 mmHg. These feed the detectors."

---

## 6. PROFESSOR Q&A BANK

### Q1: "This is just rule-based. Where is the AI?"
**Strong answer:** "There are three layers. Layer 1 is deterministic rules — correct, intentionally. Rules are interpretable, auditable, and safe for clinical decision support. Layer 2 is a weighted scoring model that aggregates five signals into a priority score. Layer 3 uses claude-sonnet-4-20250514 to convert the deterministic output into a readable 3-sentence narrative. The AI adds narrative fluency; it does not generate the clinical findings. This is the right architecture for a safety-critical domain — we did not use AI where rules are more trustworthy."

**Weak answer to avoid:** "We use Claude for everything." — False. Claude only runs Layer 3 and only after Layer 1 is complete.

---

### Q2: "Your shadow mode shows 78.4% — below 80% target. Why should we trust ARIA?"
**Strong answer:** "78.4% is honest. The target is ≥80%. We document the 6 false negatives in our STATUS.md and show every one can be clinically explained: 2 are cold-start (only 1–6 readings), 3 are BP actively declining under treatment, 1 is a same-day medication change. ARIA is silent in these cases for sound clinical reasons. False negatives from system bugs: zero. We would rather show you an honest 78% than an inflated 94% from an earlier ingestion bug."

**Context:** The 94.3% figure in CLAUDE.md is outdated — it was from before a corrected historic_bp_systolic ingestion that changed the patient_threshold calculation for all 37 evaluation points.

---

### Q3: "How do you know the synthetic data is clinically realistic?"
**Strong answer:** "The generator follows documented clinical rules. Day-to-day systolic SD is 8–12 mmHg — flat variance would be unrealistic. Morning readings are 5–10 mmHg higher than evening every week (diurnal pattern). There is a white-coat dip of 10–15 mmHg in the 3–5 days before appointments. Device outages produce absent rows, not null values. Two-reading sessions have readings differing by 2–6 mmHg. These rules are implemented in reading_generator.py and validated in 14 unit tests."

**What you cannot claim:** That these readings represent any real patient's home BP. They are a research prototype for demonstrating the system.

---

### Q4: "What happens if the LLM hallucinates?"
**Strong answer:** "The LLM output goes through an 11-check validation layer before it reaches the UI. If any check fails — forbidden phrase, wrong sentence count, risk score inconsistency, hallucinated drug name, or BP value outside ±20 mmHg of the trend data — the summary is retried once, then stored as null. The deterministic briefing is always shown regardless. The LLM enhances readability; it cannot corrupt the clinical data layer."

---

### Q5: "How does the adaptive threshold work and why not just use 140 mmHg?"
**Strong answer:** "The patient-adaptive threshold uses `max(130, min(145, mean + 1.5×SD))` of the patient's own clinic BP history. For a patient whose historical mean is 133 mmHg with SD 8 — a patient who has always been moderately elevated — flagging at 140 mmHg would generate constant false positives. Their inertia threshold would be ~145 mmHg. For a patient with a history of 115 mmHg mean, their threshold is 130 mmHg, so a rise to 135 is clinically meaningful. The threshold personalizes to the patient's own baseline."

---

### Q6: "What is therapeutic inertia and why does it matter?"
**Strong answer:** "Therapeutic inertia is the failure to escalate treatment when it should be escalated — the physician sees persistent elevated BP but makes no medication change. Studies estimate it contributes to 40–80% of uncontrolled hypertension cases. ARIA detects it when: average systolic exceeds the patient's adaptive threshold over the detection window; at least 5 readings confirm it's not a one-off; the elevation has persisted for at least 7 days; and no medication change appears in the medication history that could explain it — accounting for the titration window of the last changed drug."

---

### Q7: "Why is the risk score weights not ML-tuned?"
**Strong answer:** "Empirically tuning weights requires labeled outcome data — which patient's outcomes improved after clinical intervention? We don't have that dataset. The current weights are expert-informed: systolic BP deviation from baseline is the most direct signal (30%), followed by medication inertia (25%), adherence (20%), reading gap (15%), and comorbidity severity (10%). These are transparent and correctable. In a production deployment, clinician feedback over 6–12 months would re-calibrate them — that is the calibration_rules table."

---

### Q8: "How does the scheduler work? Is it robust to server restarts?"
**Strong answer:** "The scheduler uses APScheduler for cron-style triggers (7:30 AM UTC for briefings, midnight UTC for pattern recompute sweeps). Jobs are persisted to the processing_jobs table with an idempotency key before processing — so if the server restarts mid-job, the job is still queued and will be claimed by the next worker poll. The worker uses LISTEN/NOTIFY (pg_notify) to wake immediately when a job is inserted, with 30-second polling as a fallback. If the worker crashes after claiming a job (status=running), the job does not auto-recover — that is a known limitation; production would add a timeout-based recovery."

---

### Q9: "Why does pattern B (high BP + high adherence) not always fire an alert?"
**Strong answer:** "Pattern B suppression exists because Pattern B can be a false positive when treatment is actively working. If a patient recently started a new drug, their BP may still be elevated while the drug takes effect — this is the titration window. If the slope is negative (BP declining), the recent 7-day average is already below threshold, and the medication change is within the drug's titration window, ARIA suppresses Pattern B with the interpretation 'treatment appears effective — monitoring.' Suppression requires ALL three conditions; if any fails, the alert fires."

---

### Q10: "Can a clinician change the risk tier and why can't they change it for CHF patients?"
**Strong answer:** "Yes, via `PATCH /api/patients/{id}/tier` with a required reason string. A demotion — say, high to medium — sets a 28-day suppression window aligned to the NICE NG136 4-week review standard. The nightly algorithm won't reverse the clinician's decision for 28 days unless the score hits 85 or above, which triggers a break-glass promotion. But for patients whose High Risk is driven by CHF, stroke, or TIA, the API returns 409. Those are system-level overrides at ingestion. Bypassing them via API would mean ARIA could have a CHF patient in Medium Risk, which is a safety floor we won't cross. The clinician needs to update the EHR problem list and re-ingest."

---

### Q11: "How does the drug interaction detector work?"
**Strong answer:** "Completely deterministic — four evidence-based rules applied against the patient's medication list and problem codes. No LLM. No additional database queries. The four rules cover: NSAID + antihypertensive (BP attenuation risk), the triple whammy — NSAID + ACE/ARB + diuretic (acute kidney injury risk), K-sparing diuretic + ACE/ARB (hyperkalaemia risk), and beta-blocker + non-DHP CCB (bradycardia/AV block risk). Severity is warning by default and escalates to concern or critical when relevant comorbidities — CHF or CKD — are also active. For demo patient 1091, Voltaren plus ramipril plus furosemide fires the triple whammy at critical severity because both CHF and CKD are active."

---

### Q12: "What is the variability detector?"
**Strong answer:** "The variability detector computes the coefficient of variation (CV = population SD / mean × 100) of systolic readings over the adaptive window. CV ≥ 15% triggers a 'consider ABPM referral' agenda item; CV 12–14% triggers 'monitor trend.' High BP variability is an independent cardiovascular risk factor — a patient whose average is 135 mmHg but whose readings swing 115–165 mmHg is at higher risk than a patient whose average is 142 mmHg with little variability. This is not captured by the mean alone."

---

### Q13: "The dashboard and briefing BP numbers — are they consistent?"
**Strong answer:** "Yes, as of the latest fix. The briefing composer now stores `trend_avg_systolic` — the home-readings-only average — directly in the briefing JSON payload. The dashboard reads this field from the active briefing instead of recomputing independently. Before this fix, the two computation paths diverged by up to 14 mmHg in edge cases, which would have undermined clinician trust. Between visits when no active briefing exists, the dashboard falls back to a live 28-day window from the readings table."

---

## 7. WEAK CLAIMS TO AVOID

| Claim to avoid | Why it's wrong | What to say instead |
|---|---|---|
| "Our shadow mode shows 94.3% accuracy" | Outdated — current JSON shows 78.4%, below the 80% target | "We achieve 78.4% agreement on 37 labelled evaluation points; below our 80% target, with 6 false negatives all explained" |
| "ARIA uses Claude to generate the briefing" | Layer 3 (Claude) only generates a 3-sentence summary; Layer 1 generates all 9 fields deterministically | "Claude generates a readable summary of the deterministic briefing; the clinical data itself is rule-based" |
| "The chatbot uses Claude" | Both the chatbot and briefing summarizer currently use gpt-4o-mini (TEMP override) | "The chatbot and summarizer currently use gpt-4o-mini in demo mode; the architecture specifies claude-sonnet-4-20250514" |
| "Patient 1091 is a real patient" | De-identified iEMR data used under research protocol | "This is de-identified data from a research dataset; patient details are pseudonymized" |
| "The home BP readings are real" | All home BP readings are synthetically generated | "Home BP readings are synthetically generated to simulate a connected device; clinic readings from 2008–2015 are from the de-identified EHR" |
| "The risk score is AI" | It is a deterministic weighted formula | "The risk score is a deterministic weighted formula — Layer 2, not an ML model" |
| "ARIA works for any patient" | Only patient 1091 has been tested end-to-end | "ARIA is a research prototype validated on a single patient's historical data" |
| "The weights are optimized" | Weights are expert-informed, not ML-tuned | "Weights are expert-informed clinical judgment; production would recalibrate via clinician feedback" |
| "Zero false negatives" | 6 false negatives exist in current shadow mode results | "Six false negatives, all attributable to cold-start, active treatment response, or same-day medication changes — none from system bugs" |
| "We have 601 tests so the system is production-ready" | Tests verify code correctness, not clinical validity | "601 unit tests verify functional correctness; clinical validity requires prospective patient data and regulatory review" |
| "The drug interaction detector uses AI" | medication_safety.py is a deterministic rule engine — no LLM, no ML | "Four deterministic rules applied against the medication list and ICD-10 codes. Completely rule-based." |
| "A clinician can override any risk tier" | System-override patients (CHF/Stroke/TIA) return 409 — immovable | "Clinician override works for score-driven tiers. System overrides from CHF/Stroke/TIA require updating the EHR and re-ingesting." |
| "The dashboard BP average is recomputed from readings" | Since the trend_avg_systolic fix, the dashboard reads from the briefing payload | "The dashboard uses trend_avg_systolic from the active briefing — the same number the briefing page shows. Between visits it falls back to a live 28-day window." |
| "The AI summary always generates successfully" | Layer 3 can return null if validator fails — the full deterministic briefing still displays | "Layer 3 passes on first attempt for all 4 demo patients after validator hardening. If it fails, readable_summary=null and the deterministic briefing is always shown." |

---

## 8. TOP 10 THINGS THE TEAM MUST UNDERSTAND BEFORE THE PRESENTATION

1. **Shadow mode score is 78.4%, not 94.3%.** The 94.3% figure in CLAUDE.md is from an outdated ingestion. Current `data/shadow_mode_results.json` shows 78.4% (37 visits, 6 false negatives, 2 false positives, FAILED). Be honest about this.

2. **Layer 3 is currently gpt-4o-mini, not Claude.** Both `summarizer.py` and `chat/agent.py` have `# TEMP: OpenAI testing override` comments. The CLAUDE.md architecture specifies claude-sonnet-4-20250514. Know this difference.

3. **All home readings are synthetic.** `reading_generator.py` generates every home BP row. The 65 clinic readings in the EHR are from the de-identified iEMR. Do not confuse these.

4. **The risk score is a formula, not ML.** Five signals, five weights, weighted sum. If asked "how was this validated," the answer is expert judgment + unit tests, not empirical outcome validation.

5. **The adaptive detection window controls everything.** Gap thresholds, inertia duration, deterioration slope, adherence window, and risk score normalization all use `min(90, max(14, next_appt - last_visit))`. If next_appointment or last_visit_date is missing, it falls back to 28 days.

6. **White-coat exclusion has a past-appointment guard.** A stale past appointment date (like patient 1091's 2008 appointment stored as next_appointment) would have excluded all readings. The guard `if appt_aware > now` prevents this. This was a critical shadow mode bug fix.

7. **Pattern B suppression requires a recent medication change.** If there is no med change in history, suppression CANNOT apply even if BP is declining. This is an explicit code safety check.

8. **has_briefing means any briefing exists, not today's briefing.** The icon is based on the existence of any row in the briefings table. A briefing from 6 months ago would still show the blue icon.

9. **The patient_threshold is patient-adaptive, not hardcoded.** It derives from `max(130, min(145, mean + 1.5×SD))` of the patient's own clinic history. For patient 1091 with mean ~134 and SD ~16, this gives ~158 mmHg. After CHF comorbidity adjustment (-7 mmHg), the threshold is ~151 mmHg.

10. **The risk tier system now has three independent layers.** Ingestion-time system overrides (immovable — CHF/Stroke/TIA/I61), nightly hysteresis reclassification (score bands with comorbidity gates), and clinician manual override (28-day suppression window, NICE NG136). A patient can only reach Low Risk if enrolled ≥90 days, score <25, no severe/moderate comorbidity, and no active urgent alerts.

11. **Drug interactions are deterministic, not AI.** `medication_safety.py` applies 4 rules against `current_medications` and `problem_codes`. Triple whammy supersedes NSAID + antihypertensive (deduplication). Severity escalates when CHF or CKD is active. For patient 1091, the triple whammy fires at critical severity.

12. **Test count is 601 (not 521).** The risk tier reclassification system added ~50 new tests across test_ingestion.py, test_api.py, and test_pattern_engine.py.

13. **Layer 3 now passes on first attempt for all 4 demo patients.** The validator was hardened with word-boundary fixes (short ICD-10 codes like "tia" no longer match substrings in words like "potential" or "initially"), treatment-review inertia allowance (Pattern B language no longer blocked when `urgent_flags` contains "inertia"), and drug interaction injection into the LLM user message so the model cannot miss flagged interactions.

14. **`setup_demo.py` is idempotent and must be run before demo day.** It tears down and rebuilds all 4 demo patients, time-shifts patient 1091's 2010 readings to Jan–May 2026, generates DEMO_GAP/ADH/EHR patients, runs pattern recompute and briefing generation for all patients. Run: `python scripts/setup_demo.py` from repo root with `aria` conda env active.

---

## 9. TOP 5 CURRENT LIMITATIONS TO ADMIT HONESTLY

### Limitation 1: Layer 3 is using gpt-4o-mini, not claude-sonnet-4-20250514
**What:** Both the briefing summarizer and the chatbot agent use OpenAI's gpt-4o-mini (marked `# TEMP`). The architecture specification calls for claude-sonnet-4-20250514.
**Why it matters:** The validator and guardrails are designed for Claude's output characteristics. The system functions, but the model is not the specified one.
**What to say:** "We have a temporary override using gpt-4o-mini due to API key availability in this environment. The architecture specifies Claude and the reversion is a one-line change. The validation layer works identically regardless of model."

### Limitation 2: Shadow mode agreement is 78.4%, below the 80% target
**What:** Current `data/shadow_mode_results.json` shows 37 visits analysed, 78.4% agreement, 6 false negatives, 2 false positives. The target is ≥80%.
**Why the false negatives exist:** All 6 are clinically explicable: cold-start (insufficient data), BP actively declining under treatment, or same-day medication changes.
**What to say:** "We are 1.6 percentage points below the target. The false negatives are not system bugs — they represent cases where ARIA is correctly silent because treatment is working or data is insufficient. Increasing the evaluation dataset would likely improve this."

### Limitation 3: Only one demo patient (1091) has been tested end-to-end
**What:** The entire pipeline (ingestion, generation, detection, briefing, shadow mode) has been validated on a single de-identified patient from a research dataset.
**What to say:** "This is a research prototype validated at proof-of-concept scale. Multi-patient validation and real patient enrollment would be the next steps before any clinical use."

### Limitation 4: Adherence data is synthetic
**What:** Medication confirmations are generated from a Beta distribution. We cannot claim that the adherence patterns reflect any real patient's behavior.
**What to say:** "Medication adherence confirmations are simulated to test the detection pipeline. In a real deployment, these would come from a smart pill dispenser, app tap, or Bluetooth-enabled device."

### Limitation 5: No outcome validation
**What:** We cannot say that acting on ARIA's briefings improves BP control or patient outcomes. Shadow mode measures agreement with physician concern labels, not patient outcomes.
**What to say:** "Shadow mode validates internal consistency — does ARIA agree with physician concern levels? It does not validate clinical outcomes. A prospective study would be required for outcome validation."

### Limitation 6: Drug interaction ruleset is narrow (4 rules)
**What:** `medication_safety.py` implements 4 interaction rules. The full STOPP/START criteria lists 80+ relevant combinations. The current 4 rules cover the highest-clinical-impact hypertension interactions but do not approach comprehensive polypharmacy review.
**What to say:** "We cover four high-priority interactions: the triple whammy, NSAID + antihypertensive, K-sparing diuretic + ACE/ARB, and beta-blocker + non-DHP CCB. These were chosen for their prevalence and direct relevance to hypertension management. A production system would expand to the full STOPP/START criteria."

### Limitation 7: Clinician tier demotion suppression window is a fixed 28 days
**What:** `_CLINICIAN_SUPPRESSION_DAYS = 28` is a constant. NICE NG136 specifies a 4-week review standard; ARIA uses this as the suppression period. A production system would let the clinician set a custom review date.
**What to say:** "28 days is aligned to NICE NG136's 4-week review standard. It's a reasonable default — but in production we'd allow the clinician to specify a custom review date rather than a fixed window."

---

## 10. FINAL 3-MINUTE DEMO NARRATIVE

*(Use this as your live demo script. Adjust patient data values to match what's on screen.)*

---

**[Open the dashboard]**

"This is the ARIA clinical dashboard — what the GP sees at the start of the day. Patients are sorted by risk tier first, then by priority score within each tier. The High Risk patients appear at the top."

**[Point to a high-risk patient]**

"Patient 1091 is High Risk — this is an automatic override because CHF appears in the problem list. Notice the priority score of [X] and the amber 'Score stale' warning — this means the overnight pattern recompute hasn't run yet today."

**[Point to sparkline]**

"The BP trend sparkline shows the last 14 home readings. All home BP readings in this demo are synthetically generated to simulate a connected device — I'll explain that in a moment."

**[Point to briefing icon]**

"The blue document icon means a briefing was generated at 7:30 AM this morning. Let me open it."

**[Click patient → briefing page opens]**

"This is the pre-visit clinical briefing for patient 1091. At the top: a 3-sentence AI summary generated by our Layer 3 LLM — that ran after the deterministic analysis was complete."

**[Point to BP Trend]**

"Layer 1: the 28-day home average is [X]/[X] mmHg — Stage 2 hypertension range — based on [N] reading sessions. There's an upward trend. The 3-month clinic trajectory shows the same worsening pattern from the EHR."

**[Point to Drug Interactions section]**

"This is new. The drug interaction detector is entirely deterministic — no AI. Patient 1091 has Voltaren on the medication list alongside ramipril and furosemide. NSAID plus ACE inhibitor plus diuretic — that's a triple whammy, a significantly elevated acute kidney injury risk. With active CHF and CKD, severity escalates to critical. It sits at the top of the visit agenda."

**[Point to Medication Status]**

"Layer 1: the last medication change was in 2013. We're now in 2026. No antihypertensive adjustment in 12 years while BP remains elevated — that's the pattern we flag as possible therapeutic inertia."

**[Point to Adherence Signal]**

"Layer 1: adherence is 91% across the antihypertensive regimen. High adherence, high BP — that's Pattern B. This means the medications may need review, not the patient's behavior."

**[Point to Visit Agenda]**

"The visit agenda is a prioritized consultation plan. Item 1: inertia — the most urgent clinical finding. Item 2: adherence pattern. Item 3: pending lab follow-ups. This tells the GP what to spend those 8 minutes on."

**[Switch to Alert Inbox or Shadow Mode]**

"The alert inbox shows all clinical flags across the patient panel — displayed by patient name, not raw patient IDs. With 4 demo patients set up (the therapeutic inertia case, the reading gap case, the adherence concern case, and the EHR-only case), the inbox gives the GP a realistic multi-patient view. The shadow mode page — our validation view — shows that ARIA agrees with historical physician concern labels at [X]% on patient 1091's 5-year clinic record."

**[Close]**

"ARIA does not diagnose, prescribe, or communicate with patients. It organizes what the GP already knows and surfaces what they haven't seen yet — in 30 seconds, before the patient walks in."

---

*End of PRESENTATION_GUIDE.md*
*Code-traced from ARIA v4.3 codebase, May 2026.*
*Last updated: auto-generated from codebase inspection.*
