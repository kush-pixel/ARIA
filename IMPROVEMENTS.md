# ARIA v4.3 — Product Improvement Roadmap

**Leap of Faith Technologies | IIT CS 595 | Spring 2026**  
**Team: Krishna Patel, Kush Patel, Sahil Khalsa, Nesh Rochwani, Prakriti Sharma, Yash Sharma**

---

## Table of Contents

1. [Infrastructure & Production Readiness](#1-infrastructure--production-readiness)
2. [Detection Engine — Layer 1 Improvements](#2-detection-engine--layer-1-improvements)
3. [Machine Learning & AI Algorithms — Layer 2 Enhancements](#3-machine-learning--ai-algorithms--layer-2-enhancements)
4. [Chatbot & LLM — Layer 3 Improvements](#4-chatbot--llm--layer-3-improvements)
5. [New Clinical Features](#5-new-clinical-features)
6. [Wearable Device Integration](#6-wearable-device-integration)
7. [Patient App Redesign](#7-patient-app-redesign)
8. [Clinician Workflow Integration](#8-clinician-workflow-integration)
9. [Product Expansion](#9-product-expansion)
10. [Clinical Governance & Compliance](#10-clinical-governance--compliance)
11. [Master Build Roadmap](#11-master-build-roadmap)

---

## 1. Infrastructure & Production Readiness

### 1.1 Security — Critical (Fix Before Anything Else)

The `.env` file containing live credentials (Supabase password, Anthropic API key, Groq API key, OpenAI API key) may be committed to the repository. This is a critical vulnerability.

**GDPR breach assessment (required immediately):** If the `.env` file has been committed to a repository with any history of remote pushes, and the database contains patient health records, this must be assessed as a potential notifiable breach under UK GDPR Article 33. The ICO notification deadline is 72 hours from discovery. Do not treat credential rotation alone as sufficient — assess whether patient data was accessible and document the assessment regardless of outcome.

**Required actions:**
- Rotate every credential in `.env` immediately upon discovery
- Migrate secrets to **AWS Secrets Manager** or **Doppler** — inject at runtime, never stored on disk
- Add `.env` to `.gitignore` and add a `git-secrets` pre-commit hook to prevent regression
- Add `SECRET_KEY_ROTATION_DATE` tracking so key staleness is visible
- Separate `APP_SECRET_KEY` rotation schedule from `PATIENT_JWT_SECRET` rotation schedule
- Run `trivy fs .` in CI to scan for secrets on every push

### 1.2 Observability

Currently the only observability is a custom logger wrapper. If Layer 3 silently degrades or the pattern engine throws a partial exception, no one knows until a clinician complains.

**Error tracking — Sentry**
- Wire up `sentry_sdk.init()` in `main.py` with `traces_sample_rate=0.1`
- Catches all unhandled exceptions in FastAPI, the worker, and Layer 3 LLM calls with full stack traces
- Estimated integration time: 2 hours

**Structured metrics — Prometheus + Grafana**

| Metric | Description |
|---|---|
| `aria_layer3_validation_failures_total` | LLM guardrail breach rate |
| `aria_pattern_engine_duration_seconds` | Per-detector latency histogram |
| `aria_briefing_generation_duration_seconds` | End-to-end briefing latency |
| `aria_worker_queue_depth` | Jobs waiting in `processing_jobs` |
| `aria_risk_score_staleness_hours` | How old risk scores are across the fleet |
| `aria_layer3_retry_total` | How often Layer 3 needs a retry |

**Distributed tracing — OpenTelemetry**
- Trace every request from `GET /api/briefings/{id}` through Layer 1 → Layer 2 → Layer 3 → response
- Without tracing, debugging a slow briefing generation is guesswork

**Clinical alerting (PagerDuty / OpsGenie)**
- Alert if `briefing_generation` fails on appointment morning (7:30–9:00 AM window)
- Alert if Layer 3 failure rate exceeds 10% in a 15-minute window
- Alert if pattern engine has not completed for any patient in more than 36 hours

### 1.3 Database Migrations — Alembic

Alembic is in `requirements.txt` but not configured. All migrations are currently `ALTER TABLE ADD COLUMN IF NOT EXISTS` statements in `setup_db.py`. This fails when two instances start simultaneously and race on migration, and provides no rollback capability.

**Required actions:**
- `alembic init migrations/`
- Set `target_metadata = Base.metadata` in `env.py`
- Generate baseline from current schema: `alembic revision --autogenerate -m "initial"` — this must be reconciled against the existing `ADD COLUMN IF NOT EXISTS` statements in `setup_db.py` to ensure both paths produce identical schema state on a fresh database
- All future schema changes go through `alembic revision` — never raw `ALTER TABLE`
- Run `alembic upgrade head` as a pre-deploy step in CI/CD
- Allow 1–2 days for the baseline migration, not 4 hours — the existing 17 `ADD COLUMN IF NOT EXISTS` statements across 6 tables and 37 index definitions require careful reconciliation and testing against a real Supabase instance before the baseline can be trusted

### 1.4 CI/CD Pipeline — GitHub Actions

No pipeline currently exists. Every deploy is manual. For clinical software this is unacceptable.

```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  lint-test:
    steps:
      - ruff check app/
      - ruff format app/ --check
      - alembic upgrade head        # against test DB
      - pytest tests/ -v -m "not integration"
      - pytest tests/ -v -m integration

  security:
    steps:
      - pip-audit                   # Python dependency CVE scan
      - bandit -r app/ -c .bandit   # SAST — requires .bandit config to suppress false positives
      - trivy fs .                  # secrets scan

  deploy:
    needs: [lint-test, security]
    if: branch == main
```

**Environments:** `dev → staging → production`. Layer 3 LLM calls mocked in CI, real in staging.

**CI secrets management:** The pipeline requires `DATABASE_URL` for the integration test Supabase project, `ANTHROPIC_API_KEY` for staging Layer 3 tests, and other secrets. Store these as GitHub Actions encrypted secrets (`Settings → Secrets and variables → Actions`). Never echo or print secret values in CI steps. The integration test database must be a dedicated test project, never the production Supabase instance.

**Bandit configuration:** `bandit -r app/` will generate false positives on standard FastAPI patterns. Create a `.bandit` config file to suppress known-safe findings and establish a severity baseline. All `HIGH` severity findings must be triaged before the security gate passes.

### 1.5 Real Health Check Endpoint

Current `/health` returns `{"status": "ok"}` statically. A load balancer cannot detect a database outage.

```python
from sqlalchemy.exc import OperationalError, DatabaseError

@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except (OperationalError, DatabaseError):
        db_ok = False
    status = "ok" if db_ok else "degraded"
    return {"status": status, "db": db_ok, "env": settings.app_env}
```

Add a `/ready` endpoint returning 503 until the worker is initialised — used by Kubernetes readiness probes.

### 1.6 Celery + Redis Worker

The current worker is a 30-second polling loop. This means:
- Jobs sit for up to 30 seconds before pickup
- No priority queuing (7:30 AM briefing competes equally with background recomputes)
- Horizontal scaling requires careful coordination

**Proposed architecture:**
- Redis as Celery broker and result backend — configure Redis with AOF persistence (`appendonly yes`) so in-flight jobs survive a Redis restart
- Three queues: `critical` (briefing on appointment day), `high` (pattern recompute on new reading), `default` (bulk recomputes)
- `processing_jobs` table retained as the audit and status log — Celery tasks write status updates back to it. The existing `idempotency_key` UNIQUE constraint must be respected: Celery retries must check whether the job is already in `succeeded` state before re-executing
- Replace the existing APScheduler with Celery Beat for the 7:30 AM briefing scheduler and midnight recompute jobs
- Unlocks: horizontal worker scaling, retry with exponential backoff, ETA scheduling for 7:30 AM jobs, dead-letter queue

### 1.7 Real-Time Dashboard — Server-Sent Events

The frontend polls every 60 seconds. Replace `setInterval` in `PatientList.tsx` with an SSE `EventSource` client connected to a new `GET /api/stream/patients` endpoint. The SSE endpoint must require the same clinician JWT as other authenticated endpoints — include the token as a query parameter since `EventSource` does not support custom headers, and validate it server-side on connection. Implement server-side connection cleanup (via `asyncio` cancellation on disconnect) to avoid accumulating idle SSE connections.

### 1.8 End-to-End Clinical Accuracy Tests

Current tests mock the database. Clinical accuracy regressions are only caught by the manual shadow mode script.

**Required additions:**
- A dedicated test Supabase project seeded with the 4 demo patients
- Integration test suite (`@pytest.mark.integration`) asserting:
  - Inertia fires for Patient A (1091)
  - Gap alert fires at day 9 for Patient C (DEMO_GAP)
  - Layer 3 passes all 15 validation checks
  - No guardrail strings appear in `readable_summary`
- Playwright E2E tests: login → dashboard → patient detail → briefing renders → alert acknowledge → audit event created

---

## 2. Detection Engine — Layer 1 Improvements

### 2.1 Diastolic BP — Currently Completely Ignored

Every detector only examines `systolic_avg`. Diastolic hypertension is an independent cardiovascular risk factor, especially in patients under 50. A patient presenting with 128/95 mmHg will not trigger a single alert in the current system.

**Changes required:**

| Detector | Change |
|---|---|
| Inertia | Fire if `diastolic_avg >= 90` sustained, even if systolic is within range |
| Deterioration | Add step-change sub-detector on diastolic (≥10 mmHg jump over 3 weeks) |
| Variability | Separate ARV calculation for diastolic — elevated diastolic variability indicates arterial stiffness |
| Risk scorer | See §2.4 for the unified weight redistribution that incorporates a diastolic component |

### 2.2 `medication_taken` Field — Currently Never Used

When a patient submits a reading they report whether they took their medication (`yes|no|partial|null`). The adherence analyser ignores this entirely and only reads `medication_confirmations`. This is a missed signal.

**Changes required:**

| Detector | Change |
|---|---|
| Adherence | If `medication_taken=no` or `partial` correlates with elevated readings on those days, weight it more strongly than missed confirmation |
| Inertia | If `medication_taken=yes` on every elevated reading, adherence is ruled out as a confound — inertia conclusion is strengthened. Note: this applies only when `medication_taken=yes` is consistently reported; sparse or missing values do not warrant the same inference |
| Deterioration | If `medication_taken=no` precedes a BP spike → adherence-driven deterioration (different clinical action from treatment failure) |

### 2.3 Symptom Burden Detector — New

The `symptoms` array (`headache|dizziness|chest_pain|shortness_of_breath`) is collected at reading time and never analysed by any detector.

**New detector rules (deterministic, no LLM):**
- Flag `chest_pain` or `shortness_of_breath` with systolic ≥ 160 → urgent flag in visit agenda regardless of tier
- Flag `dizziness` clustering within 2 days of a BP dip → orthostatic hypotension signal (especially in elderly patients on diuretics)
- Track symptom frequency trend: "patient reported headache on 6 of last 14 readings" → surface in `adherence_summary` as possible medication side effect
- Suppress inertia "sustained elevated" language if patient has reported dizziness (they may already be symptomatic)

### 2.4 Variability Score — Never Fed Into Risk Scorer

`variability_detector.py` computes a full variability score. `risk_scorer.py` never imports or uses it. High BP variability (independent of mean) is an established predictor of stroke (Rothwell et al., Lancet 2010).

**Unified weight redistribution (combines §2.1 diastolic addition and this section):**

The two proposed weight changes — adding a diastolic component (§2.1) and adding variability (this section) — must be implemented as a single coherent redistribution. The combined final weights are:

| Component | Current Weight | Final Weight |
|---|---|---|
| Systolic vs baseline | 30% | 20% |
| **Diastolic vs threshold** | **0%** | **5%** |
| Days since med change | 25% | 20% |
| Adherence rate | 20% | 20% |
| Gap duration | 15% | 15% |
| Comorbidity severity | 10% | 10% |
| **Variability score** | **0%** | **10%** |
| **Total** | **100%** | **100%** |

Update `risk_scorer.py` constants `_SYSTOLIC_WEIGHT`, `_INERTIA_WEIGHT` etc. to match, and add `_DIASTOLIC_WEIGHT = 0.05` and `_VARIABILITY_WEIGHT = 0.10`. Import `variability_detector` in `risk_scorer.py` to obtain the variability score.

### 2.5 Heart Rate Analysis — Currently Stored, Never Analysed

`heart_rate_avg` exists in the readings table. No detector reads it.

**New heart rate signals:**

| Signal | Logic | Visit Agenda Output |
|---|---|---|
| Beta-blocker monitoring | Patient on beta-blocker (`atenolol\|bisoprolol\|carvedilol\|metoprolol`) AND resting HR consistently > 85 bpm | Supporting evidence for inertia — possible underdose |
| Tachycardia flag | Resting HR > 100 on 3 or more readings in 7 days | "Resting tachycardia noted — consider ECG" |
| AF screening | `irregular_pulse = TRUE` on 2 or more sessions (device-reported) | "Possible AF — 12-lead ECG recommended" |

**Note:** Between-reading HR differences (`|heart_rate_1 - heart_rate_2|`) must NOT be used as an AF criterion. Session-to-session HR variation reflects breathing, position, and normal physiological variation, not arrhythmia. The only valid AF signal is device-reported `irregular_pulse = TRUE` from the monitor's validated firmware algorithm (NICE NG196 pathway).

AF screening via home BP monitor is a validated clinical pathway (NICE NG196). Add `irregular_pulse BOOLEAN` to the `readings` schema. This same schema change also required by §5.6 — implement once, reference from both sections.

The patient app's guided reading flow (§7.5) must add a post-measurement checkbox: "Did your device show an irregular heartbeat indicator?" to capture `irregular_pulse` from devices that display it visually but do not transmit it via API.

### 2.6 Orthostatic Hypotension Detector — New

The `bp_position` field (`seated|standing`) exists but no detector compares across positions. Orthostatic hypotension — a drop ≥ 20 mmHg systolic or ≥ 10 mmHg diastolic on standing — affects approximately 20% of elderly hypertensive patients and is a leading cause of falls.

**Logic:** When the patient submits both seated and standing readings in the same session, compute the delta. If sustained over 3 or more sessions: "Possible orthostatic hypotension — review antihypertensive dosing and timing."

**Dependency on patient app:** The current guided reading flow (§7.5) presents one reading position. The flow must offer a "Take standing reading" option to capture the position pair. Without this app change, the detector will fire for very few patients.

**Comorbidity amplification:** Escalate concern level when diabetes (E11), alpha-blocker, diuretic, or ACE inhibitor is present in medication history.

### 2.7 White-Coat vs. Masked Hypertension Classification — New Detector

ARIA currently uses white-coat detection only to exclude pre-appointment readings from inertia. The comparison between clinic readings and home readings should drive a formal classification.

| | Home High | Home Normal |
|---|---|---|
| **Clinic High** | Sustained hypertension | White-coat hypertension (possibly overtreated) |
| **Clinic Normal** | **Masked hypertension** (high stroke risk — undertreated) | Controlled hypertension |

Masked hypertension is the most dangerous scenario — clinic BP looks fine so the clinician does not act, but home BP is elevated. ARIA has all the data to detect this. No other system in the standard GP workflow does.

Store the classification as a new column `bp_classification TEXT` on `clinical_context` (values: `sustained|white_coat|masked|controlled|insufficient_data`). Surface prominently in the briefing trend summary.

**Home BP threshold:** The classifier must use `HOME_BP_HIGH_THRESHOLD_SYSTOLIC = 135` and `HOME_BP_HIGH_THRESHOLD_DIASTOLIC = 85` (NICE NG136) — not the clinic threshold of 140/90. Define these as named constants in `threshold_utils.py`. Using 140/90 as the home threshold will misclassify patients in the masked hypertension quadrant by NICE definition.

### 2.8 Adaptive Threshold Learning — Replace Hardcoded Cap

**Current problem:** `patient_threshold = max(130, mean + 1.5×SD)` capped at 145 mmHg. A patient who has always run at 165 mmHg gets a threshold of 145 — below their actual stable baseline — causing constant false-positive flags.

**Required change:** See §3.1 for the full ML implementation using Gaussian Process regression. The threshold becomes `posterior_mean + 2×posterior_SD`. Updated monthly.

**Clinical tension to resolve:** Removing the 145 mmHg cap must be paired with a guideline floor. NICE NG136 Section 1.4 targets < 140 mmHg for most adults under 80. A purely data-driven threshold that places a long-term 165 mmHg patient's threshold at ~163 mmHg would suppress alerts that are clinically warranted by guidelines. Resolution: the adaptive threshold governs *deterioration detection* (has their personal trajectory worsened?) while NICE guideline targets govern *inertia detection* (is their BP above the standard target?). These are distinct clinical questions requiring distinct thresholds. Codify this explicitly in `threshold_utils.py`.

### 2.9 Multi-Signal Fusion Layer — New Layer 1.5

Currently Layer 1 detectors run independently. Clinical specificity improves when signals are interpreted together.

| Signal Combination | Fused Interpretation |
|---|---|
| Gap + high adherence before gap | Device issue more likely than disengagement |
| Deterioration + low adherence + no med change | Adherence-driven worsening |
| Inertia + good adherence + high nocturnal variability | Possible OSA — output: "consider Epworth questionnaire" (OSA affects 30–40% of hypertensive patients and is the most common secondary cause of resistant hypertension) |
| Inertia + good adherence + high intra-session variability + social context flag | Possible situational component — output: "review social context and stressors with patient" |
| Inertia + poor adherence | Adherence must be addressed before inertia conclusion is valid |
| Good BP + low adherence | Masked response — undertreated but "looks controlled" |

This adds a Layer 1.5 to the three-layer architecture. The architecture diagram and system documentation must be updated to reflect a four-layer design (Rules → Fusion → Risk Score → Explanation) before this is shipped.

### 2.10 Contextual Severity Modulation

Every detector threshold should be modulated by the patient's comorbidity profile with per-condition adjustments beyond the current flat −7 mmHg value.

| Comorbidity | Inertia threshold | Deterioration sensitivity | Gap urgency |
|---|---|---|---|
| CHF (I50) | −10 mmHg (target < 130) | +50% more sensitive | × 2 (tier up) |
| Stroke / TIA (I61–I64, G45) | −10 mmHg (target < 130) | +50% | × 1.5 |
| CKD (N18) | −7 mmHg (target < 130) | +25% | × 1.5 |
| Diabetes (E11) | −5 mmHg (target < 135) | +25% | Standard |
| None | 0 | Standard | Standard |

Thresholds derived from NICE NG136, ACC/AHA 2018, and ESH 2023 guidelines. Applied centrally in `threshold_utils.py`, replacing the existing flat `_COMORBIDITY_ADJUSTMENT = -7.0` constant. All four detectors must be updated consistently when this change lands.

**Age-specific threshold (NICE NG136 §1.4.4):** Add a further conditional in `threshold_utils.py` — patients aged ≥ 80 use `NICE_OVER_80_HOME_THRESHOLD_SYSTOLIC = 145 mmHg` and `NICE_OVER_80_HOME_THRESHOLD_DIASTOLIC = 85 mmHg` instead of the standard 135/85. This is a single guard applied after comorbidity adjustment and propagates automatically to all four detectors. An 82-year-old with a home average of 148 mmHg is within NICE target and must not trigger inertia.

### 2.11 Detector Confidence Scores

Each detector currently returns binary `True/False`. Each should return a `DetectionResult` with:

```python
@dataclass
class DetectionResult:
    fired: bool
    confidence: float       # 0.0 (noise) to 1.0 (high certainty)
    evidence: list[str]     # specific readings/dates driving the flag
    algorithm_used: str     # "cusum" | "bocpd" | "slope" | "arv"
    data_quality: str       # "good" | "limited" | "insufficient"
```

The risk scorer weights flags by `confidence × detector_weight` instead of `binary_fired × weight`.

### 2.12 Detector Audit Trail

Every detector run must log not just the result but the evidence used.

```json
{
  "algorithm": "cusum",
  "cusum_value": 23.4,
  "control_limit": 20.0,
  "patient_threshold": 151.2,
  "readings_evaluated": 34,
  "window_days": 42,
  "white_coat_excluded": 3,
  "comorbidity_adjustment": -7,
  "baseline_source": "historic_mean_cap_145"
}
```

`baseline_source` must reflect the actual algorithm in use at the time: `"historic_mean_cap_145"` before GP regression is deployed (Sprint 1–3), `"gp_posterior_mean"` after (Sprint 4+). This field must not be hardcoded — derive it from the threshold computation path.

Stored in `audit_events.details`. Required for CQC inspection and clinical governance review.

### 2.13 ARV — Replace Coefficient of Variation in Variability Detector

**Current problem:** CV is statistically flawed for BP. It conflates magnitude with sequential variability and differs mathematically across different mean BP levels. The current implementation in `variability_detector.py` uses `pstdev(systolics) / mean_sys * 100.0`.

**Replace with Average Real Variability (ARV):**
```
ARV = mean(|SBP[i] - SBP[i-1]|)   for consecutive readings
```

ARV > 10 mmHg is the validated clinical threshold (Rothwell et al., Lancet 2010 — the paper establishing BP variability as a stroke predictor independent of mean BP).

**Gap handling:** ARV must only be computed over reading pairs where the time gap between consecutive readings is ≤ 2 days. Pairs separated by a device outage (3+ days apart) must be excluded from the ARV calculation to avoid inflating variability scores due to normal between-outage BP drift. Log the number of pairs excluded.

**Secondary metric — VIM (Variability Independent of Mean):**
```
VIM = SD / (mean^β)   where β is sourced from a published population regression
```

VIM requires a population-level β coefficient. ARIA has no internal population cohort — use the value published in Rothwell et al. (2010), Parati et al. (2013), or equivalent. Store the β constant and its citation as a named constant in `threshold_utils.py`, not inline in the detector.

---

## 3. Machine Learning & AI Algorithms — Layer 2 Enhancements

### 3.1 Gaussian Process Regression — Personalised Threshold

**Replaces:** Hardcoded `max(130, mean + 1.5×SD)` cap at 145 mmHg (see §2.8 for the clinical rationale and threshold scope)  
**Library:** `GPyTorch` (GPU-accelerated) or `scikit-learn GaussianProcessRegressor`

Fits a GP prior over each patient's BP trajectory using `historic_bp_systolic` as training data. The posterior mean becomes the patient's true personalised baseline. The posterior variance defines expected variability. Threshold = `posterior_mean + 2×posterior_SD`. Updated monthly with each new reading (online learning).

**Cold-start handling:** Patients with fewer than 14 clinic readings use the existing `max(130, mean + 1.5×SD)` fallback. The `baseline_source` audit field (see §2.12) distinguishes the two paths.

### 3.2 CUSUM — Replaces Linear Slope in Inertia and Deterioration

**Replaces:** Simple linear regression slope  
**Library:** `ruptures` Python package, or 10-line direct implementation

CUSUM (Cumulative Sum Control Chart) detects when a process has shifted from its target level. Originally from industrial quality control; it is more sensitive than rolling averages and naturally handles variable reading frequency and gaps.

```
S_high[t] = max(0, S_high[t-1] + (reading - (baseline + k)))
Alert when S_high[t] >= h
```

Where `k` = half the minimum shift to detect (5 mmHg for systolic, 3 mmHg for diastolic), `h` = decision interval (4×SD). The SD used for `h` is the patient's adaptive window SD, not the global historic SD — derive it from the same reading window used by the detector.

Apply CUSUM to both systolic and diastolic, consistent with §2.1. Use separate parameter tuning for each.

**Sprint 4 dependency:** CUSUM performance depends on the accuracy of the personalised baseline threshold from §3.1. Sprint 1 CUSUM will use the existing `threshold_utils.py` adaptive threshold. When GP regression is deployed in Sprint 4, the CUSUM `k` parameter must be re-evaluated against the updated baseline source. Plan for a parameter review cycle after Sprint 4.

### 3.3 Bayesian Online Changepoint Detection (BOCPD)

**Augments:** Deterioration detector  
**Library:** `bocpd` Python package or implement from Adams & MacKay (2007)

BOCPD maintains a probability distribution over the time since the last changepoint. Outputs `P(changepoint at t)` in real time as each reading arrives. Detects structural breaks — moments where the data-generating process changes — including medication response onset, illness-driven spikes, and seasonal regime changes.

**White-coat exclusion:** BOCPD inputs must apply the same 5-day pre-appointment exclusion window as inertia and deterioration detectors. Without this, the normal pre-appointment dip will register as a structural change and produce false positives before every appointment.

**Clinical output:** "A structural change in BP pattern was detected around April 12 (probability 94%). This predates the medication confirmation gap by 3 days."

### 3.4 XGBoost — Adherence Prediction (7–14 Day Early Warning)

**New capability:** Predicts adherence decline before it happens  
**Library:** `xgboost` or `lightgbm`

**Features (all computable from existing data):**
- Confirmation rate 7-day vs. 28-day (trend direction)
- Confirmation rate weekend vs. weekday (lifestyle pattern)
- Average minutes late to confirm (drifting later each week?)
- Days since last symptom report (engagement proxy — note: sparse in current data)
- Days since medication change (new regimen adjustment period)
- Reading submission frequency trend
- Morning vs. evening confirmation consistency

**Target:** `adherence_rate_next_14d < 75%` (binary)

**Class imbalance handling:** Use time-series-aware class balancing (`scale_pos_weight` in XGBoost or class-weight proportional resampling preserving temporal order). Do not use standard SMOTE — it creates synthetic interpolated samples that violate temporal ordering and introduce data leakage from future measurement periods into past training windows.

**Validation gate before deployment:** Model must achieve ≥ 70% precision and ≥ 65% recall on a held-out time-ordered test set (walk-forward validation, not k-fold). Do not deploy if below this threshold regardless of training accuracy. Retrain weekly. Minimum viable training set: **200 patient-episodes** before any deployment. 50 episodes is insufficient to learn reliable signal from the feature set — with a binary target and 7+ features, the model will overfit to the specific behavioral patterns of a small cohort and degrade on unseen patients.

**Model status flag:** Set `MODEL_STATUS = "research_prototype"` until the 200-episode threshold is met. While in prototype status, predictions are computed and logged internally but must not surface in the briefing or influence the risk score. This prevents an unvalidated model from affecting clinical output during the data collection period.

**Model storage:** Serialise trained model to a persistent `models/` directory (or S3) with version tracking. The prediction endpoint must load from storage; do not retrain on request.

**Output added to risk scorer:** `predicted_adherence_decline = True` with `confidence = 0.84`

**SHAP values** provide per-patient explanation: "Late weekend confirmations are the strongest predictor for this patient." Requires storing the trained model artefact alongside the prediction (SHAP runs against the model, not just its output).

### 3.5 CausalImpact — Medication Response Assessment

**New capability:** Objectively evaluates whether a medication change worked  
**Library:** `causalimpact` Python package (port of Google's R library)

Models the counterfactual BP trajectory: what would BP have been if the medication had not changed? Pre-intervention period trains a Bayesian structural time series model. Post-intervention actuals vs. predicted counterfactual yield a causal effect estimate with a 95% confidence interval.

**Minimum data requirements:** A pre-intervention window of at least 21 days of readings is required to fit the BSTS model reliably. Medication changes with fewer than 21 prior days of readings must be excluded from CausalImpact analysis and labelled `"insufficient_data"` in the medication response output. Do not attempt to fit the model with fewer readings — the confidence intervals will span the entire plausible range and the output will be meaningless.

**Post-intervention window — drug-class-aware:** The analysis must not fire until `days_since_med_change >= titration_window_for_drug_class`. Reuse the existing `TITRATION_WINDOWS` constants already defined in the adherence analyser:

| Drug class | Minimum post-intervention window |
|---|---|
| Diuretics / beta-blockers | 14 days |
| ACE inhibitors / ARBs | 28 days |
| Amlodipine | 56 days |
| Default | 42 days |

Running CausalImpact on amlodipine at day 14 post-change produces a meaningless result — the drug has not reached steady state. Label the output `"titration_in_progress"` until the window is met.

**Clinical surfacing threshold:** Only surface causal effect language to the clinician when the posterior probability of a meaningful effect (≥ 5 mmHg) is ≥ 75%. Below this threshold, display: "Insufficient evidence to assess medication response — more readings needed." The 61% example in earlier drafts of this document represents a coin-flip and must not generate clinical language.

**Clinical output (when threshold met):**
```
Medication: Amlodipine 5mg → 10mg (2026-03-15)
Posterior mean effect: -8.4 mmHg systolic (95% CI: -13.1 to -3.7)
Probability of causal effect: 92%
Assessment: BP responded to medication change — within expected titration window
```

### 3.6 Isolation Forest — Anomalous Reading Detection

**New capability:** Per-patient outlier flagging  
**Library:** `scikit-learn IsolationForest` (3 lines to implement)

Fits per-patient on reading history. Each new reading receives an `anomaly_score` (0–1). Score above the configured threshold flags the reading as anomalous. Anomalous readings are **flagged in the briefing but never silently excluded from trend calculations**. A reading of 198 mmHg scoring high on Isolation Forest may be a genuine hypertensive urgency — the exact signal ARIA exists to surface. The briefing composer surfaces flagged readings explicitly: "One high-anomaly reading (198 mmHg, April 23) was included in the trend — review for measurement error or clinical event." The clinician, not the algorithm, decides whether to discount it.

**Cold-start handling:** Requires minimum 30 readings to fit a meaningful model. Patients with fewer than 30 readings use a simple statistical outlier rule (|reading − patient_mean| > 3×SD) until sufficient data accumulates.

**Configuration:** The anomaly score threshold must be stored as a named constant in configuration (e.g., `ISOLATION_FOREST_ANOMALY_THRESHOLD = 0.7`), not hardcoded inline. This allows clinical tuning without code changes.

**Schema addition:** `readings.anomaly_score NUMERIC(3,2)` stored at ingestion time.

### 3.7 Hidden Markov Model — Adherence State Detection

**New capability:** Detects adherence regime changes before the rate statistic drops  
**Library:** `hmmlearn`

Adherence is a hidden state: patients are either in an **engaged** state or a **disengaged** state. Confirmation data is noisy evidence of the true state. A 2-state Gaussian HMM models transitions between these states.

**Initialisation:** Initialise from clinical priors rather than random values to avoid Baum-Welch converging to degenerate local minima. Engaged state: μ ≈ patient 28-day confirmation rate, σ ≈ 5 percentage points. Disengaged state: μ ≈ 65%, σ ≈ 15 percentage points. Transition probability initial values: 0.9 self-transition (states are sticky).

**Minimum training data:** 60 days of confirmation records required before HMM is fitted. Below this, fall back to the threshold-based adherence detector.

**Output:** `adherence_state = "disengaged"` with `transition_probability = 0.73` — even if this week's confirmation rate looks acceptable. Provides 7–14 days earlier warning than threshold-based detection.

### 3.8 Prophet — BP Trajectory Forecasting

**New capability:** Forward-looking 14-day BP forecast  
**Library:** `prophet` Python package

Decomposes BP time series into trend + weekly seasonality + day-of-week effects + medication change events (as external regressors). Handles gaps (device outages) gracefully. Produces probabilistic 14-day forecasts with confidence intervals.

**External regressors in the forecast horizon:** During the 14-day forecast period, future medication changes are unknown. Set the medication change regressor to zero for the forecast horizon — the forecast explicitly represents "BP trajectory if current regimen continues unchanged." Document this assumption in the briefing output.

**Minimum data requirement:** 28 days of readings required before Prophet is fitted. For patients with fewer than 28 days of home readings, omit the forecast and display "Insufficient data for forecasting — available after [enrolment_date + 28 days]."

**Clinical output in briefing:**
```
Forecast: If current regimen continues, projected 14-day mean is 169 mmHg
          (95% CI: 161–177 mmHg). Appointment in 12 days.
          Note: forecast assumes no medication changes.
```

---

## 4. Chatbot & LLM — Layer 3 Improvements

### 4.1 Critical Bugs — Fix Immediately

| Bug | Location | Impact |
|---|---|---|
| `summarizer.py` calls OpenAI `gpt-4o-mini` instead of `claude-sonnet-4-20250514` | `backend/app/services/briefing/summarizer.py` line 34, `_MODEL_VERSION = "gpt-4o-mini"` | Every Layer 3 summary uses the wrong model with different guardrail behaviour; `briefings.model_version` is audited as `gpt-4o-mini` in production |
| `POST /api/chat/summary` has no guardrails | `backend/app/api/chat.py` lines 118–149 | Generates clinical notes using `claude-sonnet-4-20250514` directly with no call to `validate_llm_output()` — same guardrail violations possible as in unvalidated Layer 3 |

**Audit action required:** Query `audit_events` for all records where `action = "briefing_viewed"` or any chat-related actions since deployment to determine how many summaries have been generated via `POST /api/chat/summary` without guardrail validation. Review the generated content for any guardrail violations. Document the review outcome.

**Bug fix for in-memory conversation history:** `chat.py` stores conversation history in a process-level dict (`session_store`). This is lost on server restart or when running multiple workers. This is a data integrity issue, not a 30-minute fix — it requires the `chat_sessions` database table in §4.2. Schedule it for Sprint 5.

### 4.2 Persistent, Audited Conversation History

**New table required:**
```sql
CREATE TABLE chat_sessions (
    session_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    clinician_id TEXT NOT NULL,
    messages JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT now(),
    last_message_at TIMESTAMPTZ DEFAULT now(),
    turn_count SMALLINT DEFAULT 0
);
```

Benefits: conversation survives navigation, page refresh, and server restarts. Every message is audited. A clinician can return to a conversation started during the consultation.

### 4.3 Evidence Cards — Citation-Grounded Responses

Every factual claim the chatbot makes must show its source data. The agent returns a structured `citations` array alongside text. The frontend renders each citation as a collapsible card.

```
"The 28-day average systolic is 163 mmHg."
  → [Source: 47 readings, April 9 – May 7, 2026]

"No medication change since October 2013."
  → [Source: medication history — last entry: Amlodipine 5mg, 2013-10-03]
```

This is the difference between a chatbot that provides comfort and one that provides clinical confidence.

### 4.4 Expanded Tool Set

**Current:** 6 read-only tools  
**Proposed additions:**

| Tool | Function | Clinical Use Case |
|---|---|---|
| `get_symptom_timeline` | Returns symptom reports with corresponding BP readings | "Did headaches correlate with the April spike?" |
| `get_medication_response` | BP trajectory before and after each medication change | "Did the amlodipine dose change in March actually work?" |
| `compare_periods` | Statistical comparison of two date ranges | "Was February better controlled than March?" |
| `get_risk_score_breakdown` | SHAP-style decomposition of risk score components | "Why is his risk score 78?" |
| `get_reading_detail` | Raw detail for a specific session | "What exactly was recorded on April 12?" |
| `get_circadian_pattern` | Morning vs. evening delta trend | "Is there a morning surge pattern?" |

### 4.5 Proactive Hypothesis Surfacing

After the briefing loads, the chatbot initiates rather than waiting:

> "I noticed the BP elevation on April 12 coincides with a 3-day medication confirmation gap the previous week. Would you like me to show the correlation?"

> "The medication response model suggests amlodipine had no measurable effect after the March dose change — should I pull the comparative data?"

These are generated from Layer 1 signals — the chatbot surfaces what the detectors already found, packaged as a question rather than an assertion. This respects clinical autonomy while making the intelligence proactive.

### 4.6 Clinical Note Generation

The `POST /api/chat/summary` endpoint exists but bypasses all guardrails. Transform it into a validated clinical note generator.

**Output format:**
```
ARIA Between-Visit Summary — David Patel — Generated 07/05/2026 09:12

Objective: 47 home BP readings (09/04 – 07/05). 28-day mean 163/94 mmHg.
           Adherence 91% (41/45 confirmations).

Assessment: Sustained BP above target despite current regimen (no medication
            change since October 2013 — possible treatment review warranted).
            Possible adherence concern noted week of 28/04 (4 missed confirmations).

Actions discussed: [clinician completes]

Data limitations: Home readings only — no clinic attendance since last appointment.
ARIA version: 4.3 | Generated: ARIA Layer 3 | Validated: passed (15/15 checks)
```

Designed for direct paste into EMIS or SystmOne. Full guardrail validation (all 15 checks) before return. The footer must reference an abstracted version identifier (`ARIA Layer 3`) rather than the raw model name and version — surfacing `claude-sonnet-4-20250514` in the clinical record creates medico-legal exposure if the model changes without the footer being updated.

### 4.7 Uncertainty Communication

Hard rules for what the chatbot must acknowledge:

- Enrolled less than 21 days ago → "I only have N days of data — the trend may not be reliable."
- Fewer than 10 readings in window → flag before any trend statement
- Sparse medication history → "I can only confirm what was recorded in the FHIR bundle."
- `readable_summary = null` (Layer 3 validation failed) → "I was unable to generate a validated summary — showing raw Layer 1 data instead."

Overconfident AI in a clinical setting is a safety risk. Explicit uncertainty is a feature.

### 4.8 Multi-Patient Practice Queries

Beyond single-patient Q&A, enable practice-level queries:

> "Which of my high-risk patients haven't submitted readings in the last 5 days?"  
> "Show me all patients with both a CHF comorbidity and an inertia flag."

Requires a `practice_query` tool scoped by `practice_id` via RLS. Returns counts and risk tiers — not patient names unless the clinician scopes to a named patient.

### 4.9 Prompt Caching

As documented in `chatbot.md` but not implemented in `chat.py`. System prompt and patient context snapshot should be cached with `cache_control: {"type": "ephemeral"}`. Conversation history appended per turn without caching.

Token cost reduction depends on system prompt size and conversation length — expect 40–70% reduction for consultations with 3 or more turns. Single-turn queries will see minimal benefit since the cache is cold.

---

## 5. New Clinical Features

### 5.1 Medication Response Tracker

When a medication change is recorded in `med_history`, ARIA knows the date but never evaluates whether it worked.

**For every medication change, track:**
- Pre-change 14-day average vs. post-change 14-day average (respecting drug-class titration window)
- Response classification: `good_response` (> 10 mmHg drop), `partial_response` (5–10 mmHg), `no_response` (< 5 mmHg), `worsening`

**Briefing output:** "BP did not respond to the medication change from March 15. Consider dose review."

Over time this builds a per-patient medication response history — clinically unique intelligence unavailable elsewhere.

### 5.2 QRISK3 Score Integration

QRISK3 is the UK standard 10-year cardiovascular risk calculator used in NICE guidelines and embedded in every EMIS and SystmOne installation. GPs already think in QRISK3 terms.

**Licence requirement:** QRISK3-2018 is copyright ClinRisk Ltd. Academic use may be covered under the free academic licence, but commercial deployment requires a paid licence agreement with ClinRisk. Confirm licence status with the team lead before integrating QRISK3 into any deployment outside the academic research context.

**Data completeness:** QRISK3 requires approximately 25 inputs. ARIA can source approximately 8 from existing data. The table below shows what is available and what must be defaulted:

| QRISK3 Input | ARIA Source | Status |
|---|---|---|
| Age, sex | `patients.age`, `patients.gender` | Available |
| Systolic BP + variability | `readings` | Available |
| Diabetes (Type 1/2) | `problem_codes` (E10, E11) | Available |
| Atrial fibrillation | `problem_codes` (I48) | Available |
| CKD | `problem_codes` (N18) | Available |
| Systolic BP SD (5-year) | Computable from `historic_bp_systolic` | Available for patients with 5+ years of clinic data |
| Total cholesterol / HDL | `recent_labs` | Available if recorded |
| Smoking status | Not captured | **Must default to population mean** |
| Ethnicity | Not captured | **Must default to population mean** |
| Townsend deprivation score | Not captured | **Must default to population mean** |
| BMI | Not directly captured | **Must default to population mean** |
| Family history CVD | Not captured | **Must default to population mean** |
| Rheumatoid arthritis, SLE, other inputs | Not captured | **Must default to population mean** |

Defaulted inputs must use population means as specified in the QRISK3-2018 algorithm documentation, not zero. The briefing must display the number of defaulted inputs: "QRISK3 estimated with 9 defaulted inputs — treat as indicative only." Do not display QRISK3 without this caveat.

Compute at briefing generation time. Track longitudinal trend: "QRISK3 score has increased from 18% to 22% over the past 6 months."

### 5.3 Circadian Pattern Analysis

Morning BP surge is an independent cardiovascular risk factor. ARIA collects `session` (morning/evening) but never analyses the circadian pattern.

**New detector outputs:**
- "Morning systolic consistently ≥ 20 mmHg above evening" → morning surge pattern → output: "discuss dosing schedule and timing with patient." **Do not recommend bedtime dosing.** The TIME trial (2022, *NEJM*, n=21,104) — the largest RCT on antihypertensive dosing timing — found no significant difference in cardiovascular outcomes between morning and bedtime dosing, superseding the earlier HYGIA trial. No directional dosing recommendation is warranted from morning surge detection alone.
- "Evening BP exceeds morning" (reverse dipping) → associated with autonomic dysfunction or nocturnal hypertension → consider ABPM referral

**Briefing display:** Morning readings and evening readings rendered as separate sparklines with delta annotated.

### 5.4 Lab Result Clinical Rules

`recent_labs` is stored as JSONB but no detector reads it. The following rules are deterministic and require no LLM.

**LOINC code mapping:** `recent_labs` keys values by LOINC code. The detector must use a LOINC-to-analyte mapping stored as constants (not inline strings), building on the LOINC constants already defined in `fhir/adapter.py`. Add the following to the constants file:

| Analyte | LOINC Code |
|---|---|
| Potassium (K+) | 2823-3 |
| Creatinine | 2160-0 |
| HbA1c (IFCC) | 4548-4 |
| eGFR (CKD-EPI) | 33914-3 |
| Sodium | 2951-2 |

| Lab finding | Clinical rule |
|---|---|
| K+ < 3.5 mEq/L on diuretic | "Hypokalaemia confirmed — review diuretic dose" |
| Creatinine rising > 20% in 3 months on ACE/ARB | "Possible ACEi-induced AKI — consider dose review" |
| HbA1c > 75 mmol/mol in diabetic patient | Elevate risk score, add to visit agenda |
| eGFR < 30 | Flag all nephrotoxic medications, elevate risk tier |
| Sodium < 130 mEq/L on thiazide | "Possible hyponatraemia — urgent review" |

Note: K+ < 3.5 is the definitional threshold for hypokalaemia (not "possible") — the direct clinical language is appropriate here and consistent with the lab definition.

### 5.5 Polypharmacy and Medication Burden Flag

If a patient is on 4 or more antihypertensive drug classes and adherence is declining, this is a textbook polypharmacy problem, not a treatment failure.

**Logic:**
- Count distinct antihypertensive drug classes in active medication history
- If ≥ 4 classes AND adherence < 75%: "High medication burden may be contributing to adherence difficulty — consider simplification review"
- Surface as a distinct visit agenda item, separate from the adherence alert

### 5.6 AF Screening via Home BP Monitor

Most validated home BP monitors (Omron, Withings) report irregular heartbeat detection. The `irregular_pulse BOOLEAN` schema addition and the AF screening rule (2+ sessions flagging irregular pulse within 14 days → "Possible atrial fibrillation — 12-lead ECG recommended") are fully described in §2.5. Implement from that section. This is a validated clinical pathway (NICE NG196).

---

## 6. Wearable Device Integration

### 6.1 Device Tier Strategy

**Tier 1 — Clinical-grade home BP monitors (highest priority)**

| Device | Clinical standard | API |
|---|---|---|
| Omron Connect | Most common NHS-prescribed home monitor | Omron Health Partner API |
| Withings BPM Connect | Medical-grade, widely used | Withings Health API |
| iHealth | NHS Digital certified | REST API |

These eliminate manual transcription entirely. Patient authenticates once; readings flow in automatically via webhook or polling job.

**Tier 2 — Consumer wearables (continuous signals)**

| Device | Relevant signals | API |
|---|---|---|
| Apple Watch | HR continuous, SpO2, ECG (AF detection, FDA-cleared) | Apple HealthKit |
| Samsung Galaxy Watch | BP measurement (CE-marked), HR, SpO2 | Samsung Health Connect |
| Fitbit / Google | HR, SpO2, sleep quality | Google Health Connect |

**Tier 3 — Specialist devices**

| Device | Use case |
|---|---|
| ABPM (24-hour Holter) | White-coat vs. masked HTN confirmation; NICE-recommended pathway |
| CGM (Libre / Dexcom) | Glucose–BP correlation for diabetic-hypertension patients |
| Smart scale (Withings) | Weight trending for CHF patients (fluid overload early warning) |

### 6.2 Schema Changes

```sql
-- New table: wearable device enrollments
CREATE TABLE wearable_enrollments (
    enrollment_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    device_type TEXT NOT NULL,
    device_id TEXT,
    -- OAuth tokens require application-level encryption (e.g., pgcrypto or KMS-backed key)
    -- Declaring as TEXT does not encrypt; implement encrypt/decrypt in the service layer
    oauth_access_token TEXT,
    oauth_refresh_token TEXT,
    oauth_expires_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ,
    sync_status TEXT DEFAULT 'active',
    enrolled_at TIMESTAMPTZ DEFAULT now(),
    enrolled_by TEXT,
    UNIQUE(patient_id, device_type, device_id)  -- prevents duplicate enrollments
);

-- Extensions to readings table
ALTER TABLE readings ADD COLUMN device_type TEXT;
ALTER TABLE readings ADD COLUMN device_id TEXT;
ALTER TABLE readings ADD COLUMN irregular_pulse BOOLEAN;  -- also required by §2.5 and §5.6
ALTER TABLE readings ADD COLUMN activity_context TEXT;  -- rest|post_exercise|post_meal

-- New table: continuous signals (HR, SpO2, activity)
CREATE TABLE activity_readings (
    activity_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    device_type TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    heart_rate SMALLINT,
    spo2 NUMERIC(4,1),
    steps_last_hour SMALLINT,
    sleep_quality TEXT,               -- good|fair|poor (device-reported)
    source TEXT NOT NULL,
    UNIQUE(patient_id, device_type, recorded_at)  -- idempotency for wearable sync
);
```

**OAuth token encryption:** The access and refresh tokens are long-lived credentials for third-party health accounts. Store them encrypted at the application layer using a key stored in AWS Secrets Manager (or equivalent), not in the database alongside the data. A pgcrypto-based approach encrypts/decrypts in the service layer; a KMS-backed approach delegates key management. Choose one and implement it — the `TEXT` column type alone does not provide any protection.

### 6.3 Wearable Sync Architecture

```
Patient authorises device (OAuth2 flow in patient PWA)
        ↓
wearable_enrollments row created
        ↓
New job_type: "wearable_sync" in processing_jobs
        ↓
Worker polls device API every 15 minutes (or receives webhook from Withings/Omron)
        ↓
New readings inserted: source="wearable_api", device_type set
        ↓
Existing UNIQUE idx on (patient_id, effective_datetime, source) prevents duplication
        ↓
If irregular_pulse=TRUE on 2+ sessions → AF flag in visit agenda
```

**Source value:** Wearable cloud API sync uses `source="wearable_api"`, not `source="ble_auto"`. The existing `ble_auto` source is reserved for the direct BLE webhook pathway (`/api/ble_webhook`). Mixing them would make it impossible to distinguish direct device reads from cloud-synced reads in audit trails and idempotency checks.

All wearable readings flow through the exact same ingestion pipeline as manual readings — same idempotency, same audit trail, same detectors. The only difference is `source` and `device_type`.

### 6.4 Activity-Aware BP Interpretation

Once `activity_readings` is populated:
- High BP reading within 30 minutes of `steps_last_hour > 500` → label as "post-exercise reading — exclude from baseline"
- Resting HR consistently > 90 from wearable + beta-blocker in regimen → stronger inertia supporting signal
- SpO2 < 92% at night → possible sleep apnoea flag (present in approximately 40% of hypertensive patients, frequently missed)

---

## 7. Patient App Redesign

### 7.1 Fundamental Problem

The current app is a data collection instrument. A patient submits a reading and receives: *"Your doctor will see this at your next visit."* The app gives nothing back — no acknowledgement that the reading was done correctly, no sense of progress, no connection to their care.

Real patients need a health companion that makes them feel supported between visits, not just observed.

### 7.2 Information Architecture Redesign

**Current:** 2 pages (Login → Confirm/Submit)

**Proposed:**

| Route | Purpose |
|---|---|
| `/login` | First-time entry only |
| `/home` | Daily hub — today's tasks, streak, appointment |
| `/reading` | Guided multi-step BP measurement |
| `/medications` | Medication management with history |
| `/progress` | Streak, weekly summary, milestones |
| `/learn` | Health education |
| `/settings` | PIN, notifications, accessibility, carer access |

### 7.3 Home Hub

Replaces `/confirm` as the post-login landing page.

```
┌──────────────────────────────────────────┐
│  Good morning, David                     │
│  Wednesday, 7 May                        │
├──────────────────────────────────────────┤
│  TODAY'S TASKS                           │
│  ✓  Morning medications confirmed        │
│  ○  Morning BP reading         [Take →]  │
├──────────────────────────────────────────┤
│  YOUR STREAK                             │
│  14 days                                 │
│  Keep going — you're building a          │
│  great picture for your care team.       │
├──────────────────────────────────────────┤
│  THIS WEEK                               │
│  Readings:      5 / 7  ████████░░        │
│  Medications:   6 / 7  █████████░        │
├──────────────────────────────────────────┤
│  NEXT APPOINTMENT                        │
│  Wednesday, 14 May — 7 days away         │
│  Your care team will review your         │
│  readings at this appointment.           │
└──────────────────────────────────────────┘
```

Answers the three questions every patient has: What do I need to do today? Am I doing well? When does my doctor see this?

### 7.4 Login and Session Management

**Current problems:**
- JWT expires after 8 hours — requires re-login multiple times per day (morning and evening readings)
- No biometric authentication
- Research ID must be re-typed every login
- No logout button anywhere in the app

**Redesign:**
- Research ID entered once on first login
- Patient immediately prompted to create a 6-digit PIN
- Option to enable Face ID / Touch ID via Web Authentication API (supported on iOS Safari 16.4+ as installed PWA and Android Chrome)
- Implementing WebAuthn requires backend changes: store per-patient public keys, implement challenge-response protocol in `auth.py`. This is a 2–3 day backend task alongside the frontend changes
- Refresh token issued alongside access token (30-day expiry, silently auto-refreshed)
- JWT access token moved from `localStorage` to `sessionStorage`; refresh token stored in encrypted IndexedDB
- Auto-lock after 10 minutes of inactivity
- Sign Out button in Settings

### 7.5 Guided BP Measurement — Most Clinically Important Change

The current form presents all fields simultaneously with no measurement guidance. NICE NG136 requires: 5 minutes of quiet sitting, two readings 1 minute apart, same arm, seated position, before morning medications. A patient who takes a reading while walking to the kitchen submits data that influences clinical management.

**New 5-step guided flow:**

**Step 1 — Preparation:** Illustrated instructions. 5-minute waiting guidance. `medication_taken` (yes/not yet/partial) captured here — before the reading, when clinically relevant.

**Step 2 — First reading:** Systolic, diastolic, heart rate fields. Optional: irregular heartbeat indicator (if device displays it).

**Step 3 — 1-minute countdown:** Large visible timer. "Stay seated and relaxed. Your second reading will be more accurate after resting." Skip option de-emphasised.

**Step 4 — Second reading:** Same fields.

**Step 5 — Symptoms:** Checkboxes. Chest pain / SOB safety banner appears immediately when checked. Does not disable submit.

The 1-minute countdown is the most important step. It makes the two readings clinically valid and ensures the data driving clinical decisions is accurate.

### 7.6 Medication Management Overhaul

**Current problems:**
- Past doses silently disappear at midnight — no way to mark a missed dose
- `.ics` download requires technical knowledge to import
- No late confirmation window (patient who forgot but took the medication has no recourse)
- No weekly adherence summary

**Redesigned `/medications` page:**
- Missed doses visible (greyed, with "I took this late" option up to 4 hours after scheduled time)
- Late confirmation records accurate `minutes_from_schedule`
- Weekly adherence bar ("14 of 16 doses confirmed this week") — no clinical judgment language, just the number
- Push notifications as primary reminder; `.ics` download retained as secondary

**Schema change:**
```sql
ALTER TABLE medication_confirmations ADD COLUMN missed_at TIMESTAMPTZ;
```

### 7.7 Push Notifications

The `.ics` download requires: download file → locate file → tap file → import to calendar. Many elderly patients will fail at step 3. Web Push API is the correct primary reminder mechanism.

Supported on iOS 16.4+ (installed as PWA) and Android Chrome (any version).

**Consent:** Obtain explicit push notification consent before subscribing patients. Under UK GDPR, push notification subscriptions require affirmative consent separate from the initial monitoring consent. Store the push subscription endpoint and the consent timestamp together. Never silently subscribe.

**Notification types:**
- Medication time: "Time for your morning medications — tap to confirm"
- BP reminder: "Don't forget your morning reading" (sent at 9:30 AM if no reading submitted)
- Appointment reminder: "Your appointment is in 3 days — keep up your readings"
- Milestone: "You've hit a 30-day streak!"

Never send: clinical values, risk scores, alert content, BP readings.

### 7.8 Engagement and Streaks

**New `patient_engagement` table:**
```sql
CREATE TABLE patient_engagement (
    patient_id TEXT PRIMARY KEY REFERENCES patients(patient_id),
    reading_streak_current INTEGER DEFAULT 0,
    reading_streak_best INTEGER DEFAULT 0,
    total_readings_submitted INTEGER DEFAULT 0,
    total_doses_confirmed INTEGER DEFAULT 0,
    last_reading_date DATE,
    milestones_achieved TEXT[] DEFAULT '{}'
);
```

Computed nightly from existing `readings` and `medication_confirmations` data — no new clinical data exposed.

**Milestones:** First reading, 7-day streak, 30 readings, 30-day streak, 100 readings. Displayed in `/progress` with a monthly calendar heatmap.

Streak mechanics and progress visibility have demonstrated efficacy in improving medication adherence in mHealth research. The patient sees engagement counts — never clinical values.

### 7.9 Appointment Awareness

Show the patient their next appointment date from `patients.next_appointment`.

- Standard: "Your appointment is in 7 days — your care team will review your readings then."
- 3 days before: "Your appointment is almost here."
- Day of: "Your appointment is today. We hope it goes well."
- No appointment scheduled: "Your next appointment will appear here when it's scheduled."

The most motivated period for any chronic disease patient is the week before their GP appointment. This feature costs one DB read.

### 7.10 Meaningful Submission Feedback

Current: *"Reading submitted. Your doctor will see this at your next visit."*

**New personalised acknowledgment (one of, based on context):**
- "That's your 47th reading. Your care team will have a detailed picture of your health at your next appointment."
- "You're on a 14-day streak — this consistency really helps your care team give you the best support."
- "Your appointment is in 7 days. Consistent readings help your doctor prepare for your visit."
- If symptoms checked: "Your care team will be aware of your symptoms at your next appointment."

Never mention: BP values, whether the reading was high or low, risk scores.

### 7.11 Offline Support

The service worker is configured but offline support for dynamic data is incomplete.

**What must work offline:**
- `/home` page loads from cached state (last known data)
- `/reading` form works entirely offline — reading saved to IndexedDB queue (namespace: `aria_reading_queue` to avoid conflict with the refresh token also stored in IndexedDB)
- `/medications` shows today's meds from cached data
- Offline indicator banner: "You're offline — your data will sync when you reconnect."
- On reconnect: IndexedDB queue flushed to backend via Background Sync API

### 7.12 Carer and Family Access

An estimated 40% of hypertension patients over 75 have a family member or carer managing their medications. The current app has no concept of this.

**Invitation token:** Patient generates an invitation token in Settings. The token must be cryptographically random with at least 128-bit entropy (e.g., 32-character URL-safe base64 string generated via `secrets.token_urlsafe(32)`). Display to the patient as a QR code or copy-paste link. Do not use a 6-digit numeric code — the 10^6 keyspace is trivially brute-forceable without rate limiting.

**Token storage:** Store as a bcrypt hash in the database. A plain SHA hash of a short token is vulnerable to rainbow table attack. Use bcrypt with a cost factor of at least 12.

**Carer permissions:**
- Can: confirm medications, submit BP readings (tagged `submitted_by: "carer"`)
- Cannot: see clinical data, change settings, access other patients

**Schema:**
```sql
CREATE TABLE carer_access (
    access_id UUID PRIMARY KEY,
    patient_id TEXT REFERENCES patients(patient_id),
    carer_token_hash TEXT NOT NULL,       -- bcrypt hash of 128-bit random token
    granted_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '90 days'),  -- auto-expiry required
    revoked_at TIMESTAMPTZ,
    granted_by TEXT NOT NULL
);
```

Carer access automatically expires after 90 days unless renewed. Indefinite access grants are non-compliant with UK GDPR data minimisation principles (Article 5(1)(e)).

### 7.13 Accessibility

Primary user demographic (65+) frequently has reduced vision, arthritic fingers, and cognitive difficulties. UK health apps must meet WCAG 2.1 AA (Equality Act 2010).

**Required changes:**
- System font size respected (Tailwind currently overrides system preferences)
- All interactive elements ≥ 44×44px touch target (Apple HIG and WCAG requirement)
- High contrast mode respecting `prefers-contrast: high` media query
- `+` / `−` stepper buttons alongside all numeric inputs
- `aria-label` on all inputs, descriptive text on all icon buttons
- Specific error messages ("Systolic must be between 60 and 250") not generic ("Invalid input")
- Colour never used as the sole means of conveying information

**Simplified mode (Settings toggle):** Two large buttons on the home screen — "Take BP Reading" and "Confirm Medications." Same functionality, extreme simplicity for patients with cognitive difficulties.

### 7.14 Health Education

Patients who understand why they monitor are significantly more adherent. Currently the app provides no context.

**Static MDX pages, loaded offline, 500 words max, 8th-grade reading level:**
- What is blood pressure?
- Why does it vary throughout the day?
- Why do I take two readings?
- Why is it important to take medications at the same time every day?
- What happens to my data?
- Who sees my readings?
- When should I call the surgery?

### 7.15 Privacy and Trust

```
Settings → Data and Privacy

Your data is shared only with your clinical team at [Practice Name].
It is stored securely and is only accessible to authorised members of your care team.
It is never sold or shared with third parties.

[Request a copy of my data]
[Ask to be removed from monitoring]
```

Note: Do not claim NHS Data Security Standards certification in patient-facing text until the DSPT assessment described in §10 is formally completed. Patients have a legal right to access their data (UK GDPR Article 15) and to withdraw consent. Surface this clearly rather than making it difficult to find.

### 7.16 Security Improvements

- JWT access token: move from `localStorage` to `sessionStorage` (see §7.4)
- Refresh token: store in encrypted IndexedDB under namespace `aria_auth`, separate from the `aria_reading_queue` namespace used for offline reading queuing (see §7.11)
- Sign Out button added to Settings (currently absent from the entire app)
- Auto-lock after 10 minutes of inactivity (shared tablet protection)

---

## 8. Clinician Workflow Integration

### 8.1 EMR Deep Link — Highest Adoption Impact

The number one adoption barrier is context switching. A GP currently must leave EMIS, open a browser, navigate to ARIA, and find the patient — all in an 8-minute consultation.

**Target:** GP opens patient record in EMIS → ARIA briefing panel appears inline. One click.

EMIS supports third-party web panels embedded in the patient record (EMIS Web Partner ecosystem). ARIA registers as a partner app. SystmOne supports equivalent integration via the FHIR STU3/R4 API (NHS Digital).

**Patient identifier in link:** The NHS number must not appear in the URL path or query string — it appears in server access logs, browser history, and HTTP referer headers. Use `POST /api/deeplink` with the NHS number in the request body, or issue an opaque short-lived token via `POST /api/deeplink/token` and pass `GET /api/deeplink?token=<opaque>` to EMIS. The opaque token expires after 30 seconds and can only be resolved once.

### 8.2 30-Second Briefing Rule

A GP has 8 minutes per consultation. The briefing must be readable in 30 seconds.

**Redesign priority hierarchy:**
```
┌─────────────────────────────────────────────┐
│  DAVID PATEL  ·  High Risk  ·  Score: 78   │
│  Next appointment: TODAY 10:30 AM           │
├─────────────────────────────────────────────┤
│  ⚠  TREATMENT REVIEW WARRANTED              │
│  BP avg 163/94 mmHg · no med change 2013   │
│  ⚠  MONITORING GAP: 12 days (since Apr 24) │
├─────────────────────────────────────────────┤
│  [90-day sparkline]       Adherence: 91%   │
├─────────────────────────────────────────────┤
│  "Why was treatment review flagged?"        │
│  "What happened during the gap?"           │
└─────────────────────────────────────────────┘
```

Most urgent flags at the top in plain English. The 3-sentence AI summary moves below the fold as supporting detail, not the primary display.

### 8.3 Mobile-First for Tablets and Home Visits

The current patient detail page has a fixed 380px chat column. GPs use tablets for home visits and ward rounds.

**Required changes:**
- Responsive breakpoints: briefing and chat panel stack vertically on tablet and phone
- Service worker pre-caches today's appointment briefings at 7:00 AM
- One-tap alert acknowledgment from the patient list (no need to open full detail page)
- Voice input for chatbot (hands-free during physical examination)

### 8.4 Alert Triage Inbox Redesign

**New features for `AlertInbox` component:**

| Feature | Description |
|---|---|
| Snooze | "Remind me at next appointment" — removes from inbox until then |
| Delegate | Assign alert to practice nurse or pharmacist |
| Batch acknowledge | "Mark all inertia alerts as reviewed" |
| Urgency sort | `gap_urgent` always first regardless of when it fired |
| Filter by type | View only adherence alerts across all patients |

### 8.5 Practice-Level Morning Dashboard

Before the first patient of the day, the clinical lead sees:

```
Morning Summary — Wednesday, 7 May 2026

Today's appointments: 12 patients with ARIA monitoring
  ├── 3 with urgent flags (action required today)
  ├── 5 with briefings ready (review before appointment)
  ├── 2 with stale risk scores (> 26h — recomputing now)
  └── 2 monitoring_active=FALSE (EHR only)

Practice alerts this week:
  ├── 7 gap alerts (3 new)
  ├── 4 inertia flags (1 new)
  └── 2 adherence concerns (2 new)
```

All data is already in the database (`audit_events`, `alert_feedback`, `briefings`). Requires a new API endpoint and a dashboard UI component.

### 8.6 Post-Appointment Feedback Loop

Currently a GP acknowledges an alert and ARIA never learns what happened. This is the most important missing loop for clinical improvement.

**Post-appointment structured prompt (30 seconds):**
```
After appointment with David Patel:
□ Medication was changed
□ Investigation ordered
□ Referral made
□ No action — ARIA flag was not clinically relevant
□ Patient declined intervention
```

This data feeds:
- Calibration engine — improve per-patient thresholds
- ML model retraining — real outcome labels for XGBoost adherence predictor
- Alert quality metrics — what % of inertia alerts led to medication change?
- QRISK3 trajectory validation — was the risk score directionally correct?

### 8.7 NHS Login and NHS App Integration

Replace the current custom patient JWT with NHS Login. Patients already have NHS Login credentials. Eliminates new account creation. NHS number verified at login links directly to the ARIA patient record.

**Regulatory pathway:** NHS Login integration requires NHS Digital certification (Information Standards Notice ISN), completion of the Digital Technology Assessment Criteria (DTAC), a Data Security and Protection Toolkit (DSPT) submission, and formal NHS England approval. This is a multi-month regulatory process, not a sprint-scoped technical task. The 3-day estimate in the roadmap covers the technical implementation only. Include 3–6 months of procurement and certification lead time in the project plan.

### 8.8 EMR Bidirectional Write-Back

Currently ARIA reads from EMR (FHIR ingestion) but never writes back. Bidirectional integration closes the loop.

- Write ARIA briefing findings as a `ClinicalImpression` or `DocumentReference` FHIR resource in the patient EMR
- Create a `ServiceRequest` (ABPM referral) when masked hypertension is detected
- Write medication response classification back as a structured `Observation`
- EMIS and SystmOne both support FHIR R4 write operations via NHS Digital FHIR API

**Clinical governance requirement:** ARIA-generated content written to the EMR becomes part of the legal patient record and creates a medico-legal paper trail. Before enabling automated write-back, establish: (1) which clinician is the named responsible author of the written resource, (2) a mandatory clinician review-and-approve step before any write executes (a "pending write" queue, not a background job), and (3) a documented governance policy reviewed by the practice clinical lead. Automated writes without clinician sign-off are not appropriate for this class of clinical decision support.

---

## 9. Product Expansion

### 9.1 Multi-Tenancy — Practice Isolation

**Enable Supabase Row-Level Security:**

The `practice_id NOT NULL` column must be added via a staged migration to avoid failing on existing rows:

```sql
-- Stage 1: add as nullable
ALTER TABLE patients ADD COLUMN practice_id TEXT;

-- Stage 2: backfill all existing rows before adding the constraint
UPDATE patients SET practice_id = 'default_practice' WHERE practice_id IS NULL;

-- Stage 3: add the NOT NULL constraint (safe now that all rows are populated)
ALTER TABLE patients ALTER COLUMN practice_id SET NOT NULL;

-- Stage 4: RLS policy
CREATE POLICY patient_isolation ON patients
    USING (practice_id = current_setting('app.practice_id'));
```

Every JWT includes `practice_id` as a claim. FastAPI middleware sets `SET LOCAL app.practice_id = ?` at the start of each request via `await session.execute(text("SET LOCAL app.practice_id = :pid"), {"pid": practice_id})`. No query changes required — RLS enforces isolation at the database level.

### 9.2 Practice Admin Role

New role: `practice_admin` (above clinician, below system admin).

**Capabilities:**
- Enrol and discharge patients
- Add and remove clinician accounts
- Configure practice-level thresholds (e.g., "our practice targets < 135 mmHg for all T2D patients")
- View practice-level analytics dashboard
- Export audit logs for CQC inspection

### 9.3 Practice Analytics Dashboard

| Metric | Description |
|---|---|
| Panel risk distribution | % of monitored patients in High / Medium / Low tier |
| Briefing read rate | % of appointment briefings read before the appointment |
| Alert response time | Median time from alert delivery to acknowledgment |
| Inertia prevalence | Patients with no medication change in > 180 days |
| Engagement rate | % of patients submitting ≥ 4 readings per week |
| Detector accuracy | Alert disposition breakdown from `alert_feedback` |

### 9.4 Condition Expansion

The three-layer architecture is designed to be condition-extensible. After hypertension detection is at the highest level of accuracy, expand to additional conditions. Note: the current Layer 1 detectors are built specifically around BP thresholds, antihypertensive medication patterns, and BP variability — expansion requires new detector modules with condition-specific logic, not just configuration changes.

| Condition | Monitoring signals | Detector targets |
|---|---|---|
| Heart failure | Weight gain (fluid overload), SpO2 decline, breathlessness | Early decompensation, fluid retention clusters |
| Type 2 diabetes | HbA1c trends, CGM glucose monitoring | Hypoglycaemia patterns, HbA1c trajectory |
| COPD | Peak flow monitoring, SpO2 decline | Exacerbation prediction, oxygen saturation decline |

Each condition gets its own detector module feeding into the same Layer 1 → Layer 2 → Layer 3 pipeline.

---

## 10. Clinical Governance & Compliance

This section describes non-negotiable requirements before any clinical deployment. These are regulatory and ethical obligations, not optional enhancements.

### 10.1 Clinical Safety Case (DCB0160)

NHS Digital requires a Clinical Safety Case for software that supports clinical decision-making. This applies to ARIA.

**Required artefacts:**
- Hazard log: for each detector and AI output, document what happens if it fires incorrectly (false positive) or fails to fire (false negative), and the clinical consequence
- Safety officer: a named clinician who has reviewed and approved the hazard log
- Hazard mitigation: documented controls for each identified hazard (e.g., "inertia false positive mitigated by requiring 5+ readings over 7+ day window before alerting")
- Change assessment: every new detector or ML model added in later sprints requires a hazard log update before deployment

**Rollback strategy:** If a new detector ships and generates systematic false positives, define the rollback path *before* deploying. For each new detector, provide a feature flag that can be set to disable it without a code deployment. A clinical software rollback cannot be treated as a simple git revert — alerting clinicians with false urgent flags creates its own liability.

### 10.2 Data Protection Impact Assessment (DPIA)

UK GDPR Article 35 requires a DPIA before processing special category data (health records) at scale, and before introducing new high-risk processing activities. ARIA requires a DPIA covering:

- Current processing: home BP readings, medication confirmations, FHIR EHR ingestion, AI-generated briefings
- New processing introduced by this roadmap: wearable continuous monitoring (HR, SpO2, activity), ML prediction models (adherence prediction, BP forecasting), carer access to health monitoring data, cross-patient practice-level queries

A single DPIA covering the full roadmap scope should be completed and reviewed by the Data Protection Officer (or equivalent) before any deployment. Update the DPIA whenever new processing activities are introduced.

### 10.3 Data Retention Policy

Define and enforce data retention periods for each data category:

| Data category | Retention period | Basis |
|---|---|---|
| Clinical readings and briefings | 8 years (NHS standard for GP records) | NHS Records Management Code of Practice |
| `audit_events` | 8 years | Clinical governance requirement |
| `chat_sessions` | 3 years | Operational, no direct patient care record |
| `patient_engagement` streaks | 2 years | Operational analytics only |
| Wearable OAuth tokens | Delete on revocation or expiry | GDPR data minimisation |
| Carer access tokens | Delete on expiry or revocation | GDPR data minimisation |

Implement automated deletion jobs or Supabase row-level TTL policies where supported. Document the retention schedule and include it in the DPIA.

### 10.4 Consent Re-Confirmation for New Data Types

Patients consented to home BP monitoring when enrolling. Adding wearable continuous monitoring (HR, SpO2, sleep quality, activity data), carer access to health monitoring, and AI prediction models represents materially different processing under UK GDPR. Fresh consent is required for each new processing purpose.

**Required actions:**
- Before enabling wearable sync for an existing patient: present a new consent screen in the patient app describing what data will be collected and why
- Before enabling carer access: explicit patient consent at the point of granting access
- Before ML prediction models use a patient's data for training: add this purpose to the initial consent form for new patients; obtain fresh consent from existing patients

The `consent_version` field on readings must be extended to a consent management workflow: a `patient_consents` table recording which consent versions each patient has accepted and when.

### 10.5 ML Model Validation Methodology

All ML models (XGBoost adherence prediction, GP regression, BOCPD, Prophet, HMM) must pass a clinical validation gate before deployment. The following minimum standards apply:

| Model | Validation method | Minimum performance gate |
|---|---|---|
| XGBoost adherence predictor | Walk-forward time-series validation on held-out patients | ≥ 70% precision, ≥ 65% recall at 75% confidence threshold |
| GP regression threshold | Compare predicted thresholds against clinician-assessed baselines for 10+ patients | Predicted threshold within ±5 mmHg of clinician estimate in ≥ 80% of cases |
| BOCPD changepoint detection | Validate against known medication change dates in historic data | ≥ 80% of known changes detected within 7-day window |
| Prophet forecast | Backtesting over last 28 days for patients with 90+ day history | MAPE ≤ 15% for 14-day forecast |
| HMM adherence state | Compare detected state transitions against confirmed adherence drops | ≥ 75% sensitivity for detecting confirmed disengagement |

Models that do not meet these gates must not be deployed, regardless of training accuracy. Document validation results in the Clinical Safety Case.

### 10.6 `medication_safety.py` Interaction With New Pathways

`medication_safety.py` (the drug interaction detector called from the briefing composer) is only triggered when a briefing is generated. Two new pathways introduced in this roadmap add or change medications without triggering a briefing:

- **Wearable pharmacy sync:** If future wearable integrations sync medication data from pharmacy apps, new medications may be added to `clinical_context` outside the FHIR ingestion pathway
- **EMR bidirectional write-back (§8.8):** If ARIA writes a `MedicationRequest` back to the EMR and this triggers a re-ingestion, `medication_safety.py` must run against the updated medication list

**Required:** Whenever `clinical_context.current_medications` is updated by any pathway, trigger a `pattern_recompute` job for that patient. The worker's `pattern_recompute` handler must re-evaluate drug interactions as part of the recompute, not only at briefing generation time.

### 10.7 NHS Digital Technology Assessment Criteria (DTAC)

Any NHS deployment requires completion of the DTAC, which assesses:
- Clinical safety (DCB0160 — covered in §10.1)
- Data protection (DSPT — covered by §10.2)
- Technical security (Cyber Essentials Plus)
- Interoperability (FHIR conformance)
- Usability (accessibility, WCAG 2.1 AA)

The DTAC process is managed by NHS England and typically takes 3–6 months from submission to approval. Begin the DTAC process in parallel with Sprint 1, not after the product is feature-complete.

---

## 11. Master Build Roadmap

### Sprint 0 — Compliance Prerequisites (Before Any Clinical Data)

These items must be completed before any patient data is processed in a deployment environment.

| Item | Effort |
|---|---|
| DPIA completion and DPO review | 1 week |
| Clinical Safety Case — initial hazard log | 1 week |
| Data retention policy defined and documented | 2 days |
| DTAC submission initiated | 1 day (submission) |
| Consent management workflow designed | 2 days |

### Immediate (Before Any Clinical Deployment)

| Item | Effort | Reason |
|---|---|---|
| Rotate all credentials, fix `.env`, assess GDPR breach notification | 2 hours + legal review | Live breach risk; ICO notification may be required |
| Fix `summarizer.py` — revert to `claude-sonnet-4-20250514` | 30 minutes | Wrong model in production |
| Add guardrails to `POST /api/chat/summary` — all 15 checks | 2 hours | Patient safety — unvalidated clinical notes |
| Audit past `POST /api/chat/summary` outputs for guardrail violations | 4 hours | Identify any violations already in the wild |
| Real health check endpoint with DB probe | 1 hour | Load balancer reliability |
| Sentry error tracking integration | 2 hours | Immediate error visibility |

### Sprint 1 — Detection Accuracy

| Item | Effort |
|---|---|
| Diastolic BP integration across all detectors | 2 days |
| Heart rate analysis (tachycardia, beta-blocker, AF flag) + `irregular_pulse` schema | 2 days |
| `medication_taken` field used in adherence and inertia | 1 day |
| Symptom burden detector | 1 day |
| Unified weight redistribution (§2.4 — diastolic + variability) | 4 hours |
| ARV and VIM replace CV in variability detector | 1 day |
| CUSUM in inertia and deterioration detectors (using existing adaptive threshold baseline) | 2 days |
| Orthostatic hypotension detector (deterministic rule, no ML) | 1 day |
| White-coat vs. masked hypertension classification (deterministic comparison) | 1 day |
| Isolation Forest anomaly scoring on readings | 1 day |
| Detector audit trail (§2.12) | 1 day |

### Sprint 2 — Infrastructure

| Item | Effort |
|---|---|
| Alembic migrations setup (allow 1–2 days; 17 existing `ADD COLUMN` statements must be reconciled) | 2 days |
| GitHub Actions CI/CD pipeline (including secrets management for test DB) | 1 day |
| Prometheus metrics instrumentation | 2 days |
| Celery + Redis worker (replaces APScheduler; Redis AOF persistence required) | 2 days |
| SSE real-time dashboard (with JWT auth on SSE endpoint) | 1 day |
| Integration test suite against test Supabase | 3 days |

### Sprint 3 — Patient App Core

| Item | Effort |
|---|---|
| Guided BP measurement flow (multi-step, 1-minute countdown, irregular pulse checkbox) | 2 days |
| Home hub with today's tasks | 2 days |
| PIN / biometric login with refresh tokens (includes backend WebAuthn changes) | 3 days |
| Push notifications (Web Push API + GDPR consent flow) | 2 days |
| Meaningful submission feedback | 1 day |
| Missed dose and late confirmation | 1 day |
| Appointment awareness | 4 hours |

### Sprint 4 — ML Algorithms

| Item | Effort |
|---|---|
| Bayesian personalised threshold (GP regression) + CUSUM parameter re-evaluation | 3 days |
| BOCPD deterioration detection (with white-coat exclusion) | 2 days |
| Multi-signal fusion layer (Layer 1.5) + architecture documentation update | 3 days |
| CausalImpact medication response assessment (minimum 21-day pre-period gate) | 2 days |
| Contextual severity modulation (§2.10) | 1 day |

### Sprint 5 — Chatbot and LLM

| Item | Effort |
|---|---|
| Persist conversation history to `chat_sessions` table | 1 day |
| Evidence cards with citation rendering | 2 days |
| Expanded tool set (6 new tools) | 3 days |
| Proactive hypothesis surfacing | 2 days |
| Clinical note generation with all 15 guardrail checks + abstracted footer | 2 days |
| Prompt caching implementation | 4 hours |
| Multi-patient practice queries | 2 days |

### Sprint 6 — Patient App Extended

| Item | Effort |
|---|---|
| Offline queuing via IndexedDB (`aria_reading_queue` namespace) + Background Sync | 2 days |
| Engagement and streak system | 2 days |
| Carer and family access (128-bit token, bcrypt hash, 90-day expiry) | 3 days |
| Accessibility (touch targets, text size, ARIA labels) | 2 days |
| Health education section (MDX static pages) | 1 day |
| Privacy and data page (without unsupported NHS certification claim) | 4 hours |

### Sprint 7 — Clinical Features and New Detectors

| Item | Effort |
|---|---|
| QRISK3 score computation (with missing-input defaulting, data completeness caveat, licence confirmed) | 2 days |
| Medication response tracker | 2 days |
| Lab result clinical rules (with LOINC constant mapping) | 2 days |
| Circadian pattern analysis | 2 days |
| Polypharmacy and medication burden flag | 1 day |
| XGBoost adherence prediction model (temporal validation gate, model storage, SHAP) | 4 days |
| Prophet BP forecasting (minimum 28-day data requirement) | 2 days |
| HMM adherence state detection (clinical prior initialisation, 60-day minimum) | 2 days |
| Post-appointment feedback collection | 2 days |

### Sprint 8 — Wearables

| Item | Effort |
|---|---|
| Withings BPM Connect integration | 3 days |
| Omron Connect integration | 3 days |
| Apple HealthKit integration | 3 days |
| `wearable_enrollments` and `activity_readings` schema (with UNIQUE constraints and encryption) | 1 day |
| Activity-aware BP interpretation | 2 days |
| OAuth2 device authorisation flow in patient PWA | 2 days |
| `medication_safety.py` re-trigger on medication update from any pathway (§10.6) | 1 day |
| Patient consent re-confirmation flow for wearable data | 1 day |

### Sprint 9 — Product and Platform

| Item | Effort |
|---|---|
| Multi-tenancy with RLS (staged migration — see §9.1) | 3 days |
| Practice admin role | 2 days |
| Practice analytics dashboard | 3 days |
| EMR deep link with opaque token (NHS number never in URL) | 3 days |
| NHS Login integration — technical: 3 days; regulatory pathway: 3–6 months (initiate in Sprint 0) | 3 days technical |
| EMR bidirectional write-back (clinician review gate required) — technical: 5 days; regulatory: parallel to Sprint 0 | 5 days technical |
| Alert triage inbox redesign | 2 days |
| Practice-level morning dashboard | 2 days |

---

---

## 12. Project Costs

**Total available budget: $5,000**

---

### 12.1 Phase 1 — Development and Demo Infrastructure

Duration: 6 months. All costs are recurring monthly unless noted.

| Service | Tier | Monthly | 6-month total |
|---|---|---|---|
| Supabase | Pro (required — free tier pauses after 1 week of inactivity, no PITR, 500MB cap) | $25 | $150 |
| Backend hosting | Fly.io hobby / Railway free tier | $0–20 | $0–120 |
| Anthropic API | Pay-per-use — Layer 3 briefings + chatbot at demo scale | $5–15 | $30–90 |
| Domain + SSL | One-time | — | $15 |
| **Phase 1 total** | | | **~$195–375** |

**Supabase tier note:** Supabase Pro ($25/month) is the minimum viable tier for this project. The free tier has three hard blockers: projects pause after one week of inactivity (incompatible with the 30-second polling worker), no point-in-time recovery (required for clinical data integrity), and a 500MB database cap that will be exceeded in the first year of readings data. Supabase Team ($599/month) — which includes a HIPAA Business Associate Agreement — is required before any deployment handling real patient data, and is outside this budget.

---

### 12.2 Technical R&D Spend — $4,000

The remaining budget is allocated entirely to technical validation costs that cannot be substituted with developer time. Each item requires either physical hardware, real behavioral data, cloud compute, or a live external service.

| Item | Cost | Technical justification |
|---|---|---|
| Real BP devices (6× Withings BPM Connect + 2× Omron Connect) | $1,400 | Two device types required to validate the wearable ingestion pipeline, OAuth2 authorisation flow, and `source="wearable_api"` idempotency against different hardware implementations. The Withings and Omron APIs differ in webhook format and token refresh behaviour — cross-device testing cannot be mocked. |
| ML training dataset acquisition (real patient readings, 30 days) | $750 | The XGBoost adherence predictor (§3.4) requires 200 patient-episodes of real behavioral data before `MODEL_STATUS` exits `"research_prototype"`. Synthetic confirmations cannot replicate actual timing drift, weekend/weekday patterns, or late-dose behavior the model trains on. Participant incentives are dataset acquisition costs. |
| Infrastructure — 12-month full sprint plan | $900 | Supabase Pro ($25/month) + backend hosting ($20/month) + Redis for Celery ($30/month) across the 9-sprint build period. |
| ML compute — cloud GPU instances | $450 | Training runs for GP regression, XGBoost, Prophet, BOCPD, and HMM (§3.1–3.8) on AWS SageMaker or EC2. One-time training cost per model. |
| Anthropic API — Layer 3 development | $300 | Guardrail validation runs, 15-check test suite iteration, and chatbot tool development during Sprints 4–5. Scales to ~$50/month in production at 1800-patient panel size. |
| Dedicated CI test database | $200 | Separate Supabase Pro project for the GitHub Actions integration test pipeline (§1.8). Isolates test runs from development data — required so CI can run destructive migrations without affecting the development environment. |
| **Total R&D spend** | **$4,000** | |

---

### 12.3 Budget Summary

| Category | Amount |
|---|---|
| Phase 1 infrastructure (6 months) | ~$375 |
| Technical R&D spend | $4,000 |
| **Total** | **~$4,375** |
| **Remaining buffer** | **~$625** |

The ~$625 buffer covers unexpected API cost increases if usage grows beyond estimates, additional storage if the Supabase Pro 8GB cap is approached, or additional device procurement for Sprint 8 testing.

---

*Document revised: 7 May 2026*
*Covers: infrastructure, detection engine, ML algorithms, chatbot, clinical features, wearables, patient app, clinician workflow, product expansion, clinical governance, project costs*
*Total improvements catalogued: 80+ | Clinical corrections applied: 8 | Budget: $5,000*
