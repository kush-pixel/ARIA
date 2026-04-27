# ARIA Pattern Engine Context
## Layer 1 detectors | Layer 2 risk scorer

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Files in This Directory

```
gap_detector.py           — Layer 1: days since last home BP reading
inertia_detector.py       — Layer 1: sustained elevated BP with no med change (patient-adaptive threshold)
adherence_analyzer.py     — Layer 1: adherence rate vs BP pattern (A/B/C with Pattern B suppression)
deterioration_detector.py — Layer 1: worsening trend + absolute threshold gate + step-change sub-detector
risk_scorer.py            — Layer 2: weighted 0.0-100.0 priority score
threshold_utils.py        — shared: patient-adaptive threshold + comorbidity adjustment (used by all 4 detectors)
```

---

## Execution Order — STRICT

```
Layer 1 (all 4 detectors, in any order among themselves)
  ↓
Layer 2 (compute_risk_score) — MUST run after all Layer 1 detectors
  ↓
Layer 3 (generate_llm_summary) — lives in briefing/, NEVER in this package
```

Never call `compute_risk_score()` before all Layer 1 detectors have completed for that patient.

---

## gap_detector.py — Public API

```python
async def run_gap_detector(session: AsyncSession, patient_id: str) -> GapResult
```

**GapResult (TypedDict):**
```python
{
    "gap_days": float,                         # fractional days since last reading
    "status": Literal["none", "flag", "urgent"],
    "threshold_used": dict[str, int],          # e.g. {"flag": 1, "urgent": 3}
}
```

Tier-aware thresholds (per-tier, higher-risk patients flagged sooner):
```
risk_tier=high:    flag >= 1 day,  urgent >= 3 days
risk_tier=medium:  flag >= 3 days, urgent >= 5 days
risk_tier=low:     flag >= 7 days, urgent >= 14 days
```

If patient has no readings at all: `gap_days` is set to a large sentinel and `status="urgent"`.

---

## inertia_detector.py — Public API

```python
async def run_inertia_detector(session: AsyncSession, patient_id: str) -> InertiaResult
```

**InertiaResult (TypedDict):**
```python
{
    "inertia_detected": bool,
    "avg_systolic": float | None,
    "elevated_count": int,
    "duration_days": float,
}
```

All 5 conditions must be simultaneously true to flag inertia:
1. Average systolic >= patient_threshold over last 28 days
   patient_threshold = max(130, stable_baseline_mean + 1.5×SD) capped at 145 mmHg
   derived from historic_bp_systolic filtered to physician-labeled stable visits
   falls back to 140 if fewer than 3 stable-labeled readings
   comorbidity adjustment: threshold lowered 7 mmHg (floor 130) when both cardiovascular
     AND metabolic comorbidities are simultaneously in elevated concern state
   threshold_utils.py: classify_comorbidity_concern() + apply_comorbidity_adjustment()
2. At least 5 readings with systolic_avg >= patient_threshold
3. Elevated condition spans > 7 days (from first elevated reading to now)
4. No medication change on or after the first elevated reading
   MUST use clinical_context.med_history JSONB — NOT last_med_change (stale single-date snapshot)
   reads max({date}) across med_history entries where date <= first_elevated_reading_date
   also parses change type: dose increase = physician responding → do NOT fire
5. Slope direction check: if 7-day recent avg < patient_threshold, do NOT fire (BP is declining)

Fail-safe: sparse data, missing context, or any unmet condition → `inertia_detected=False`.

Constants:
```python
_WINDOW_DAYS = 28
_ELEVATED_THRESHOLD = 140      # FALLBACK ONLY — primary is patient-adaptive threshold
_MIN_ELEVATED_COUNT = 5
_MIN_DURATION_DAYS = 7
_RECENT_SLOPE_WINDOW = 7       # days for slope direction check
```

Patient 1091 result: `inertia_detected=True` (avg_systolic ~158, no med change since 2013-09-26).

---

## adherence_analyzer.py — Public API

```python
async def run_adherence_analyzer(session: AsyncSession, patient_id: str) -> AdherenceResult
```

**AdherenceResult (TypedDict):**
```python
{
    "adherence_pct": float | None,              # None if no scheduled doses in window
    "pattern": Literal["A", "B", "C", "none"],
    "interpretation": str,                      # plain-English clinical string
}
```

Pattern matrix (28-day window, thresholds use patient_threshold not hardcoded 140):
```
Pattern A: elevated BP (avg systolic >= patient_threshold) + low adherence  (< 80%)  → "possible adherence concern"
Pattern B: elevated BP (avg systolic >= patient_threshold) + high adherence (>= 80%) → "treatment review warranted"
Pattern C: normal BP   (avg systolic < patient_threshold)  + low adherence  (< 80%)  → "contextual review"
none:      normal BP   (avg systolic < patient_threshold)  + high adherence (>= 80%) → "no adherence concern identified"
```

Pattern B suppression — treatment is working (do NOT fire if ALL true):
  slope < -0.3 mmHg/day AND 7-day recent avg < patient_threshold AND days_since_med_change <= 14
  Suppressed pattern becomes "none" with interpretation "treatment appears effective — monitoring"
  The 14-day gate is critical — suppression MUST NOT apply when no recent med change occurred

Pattern A fires an alert row: if pattern == "A" → _upsert_alert(session, pid, "adherence")
  "adherence" is a valid alert_type in the alerts table

Clinical language boundary enforced at code level:
- NEVER use `"non-adherent"` — always `"possible adherence concern"` (Pattern A)
- NEVER use `"medication failure"` — always `"treatment review warranted"` (Pattern B)

Constants:
```python
_WINDOW_DAYS = 28
_LOW_ADHERENCE_THRESHOLD = 80.0      # adherence_pct below this = low adherence
_HIGH_BP_SYSTOLIC_THRESHOLD = 140    # avg systolic >= this = elevated
```

Patient 1091 result: Pattern B — `adherence_pct=89.8%`, avg systolic ~158 mmHg → "treatment review warranted".

---

## deterioration_detector.py — Public API

```python
async def run_deterioration_detector(session: AsyncSession, patient_id: str) -> DeteriorationResult
```

**DeteriorationResult (TypedDict):**
```python
{
    "deterioration": bool,
    "slope": float | None,         # mmHg/day; None if insufficient data
    "recent_avg": float | None,    # average systolic over last 3 days
    "baseline_avg": float | None,  # average systolic over days 4–10 ago
}
```

Requires all three signals to be positive (reduces false positives):
1. Positive least-squares slope across the full 14-day window
2. Recent 3-day average > baseline days 4–10 average
3. recent_avg >= patient_threshold (absolute gate — prevents firing on a patient rising 115→119)
   patient_threshold derived same way as inertia (from threshold_utils.py)
   falls back to 140 if insufficient history

Step-change sub-detector: if 7-day rolling mean of most recent week exceeds 7-day rolling mean
of 3 weeks ago by >= 15 mmHg → flag deterioration regardless of overall linear slope.
This catches acute step-changes that linear regression smooths over.

Returns `deterioration=False` if fewer than 7 readings exist (insufficient data).
Slope computed in pure Python — no numpy dependency. Missing days (device outages) are handled without interpolation.

Constants:
```python
_WINDOW_DAYS = 14          # full analysis window in days
_RECENT_DAYS = 3           # "recent" sub-window
_BASELINE_DAYS = 10        # baseline sub-window extends back this many days
_MIN_READINGS = 7          # minimum readings needed to produce a result
```

---

## risk_scorer.py — Public API

```python
async def compute_risk_score(patient_id: str, session: AsyncSession) -> float
```

- Returns: Rounded float `0.0–100.0`. Higher = higher clinical priority within same tier.
- Persists the score to `patients.risk_score` via `UPDATE patients SET risk_score = ... WHERE patient_id = ...`.
- Raises: `ValueError` if `patient_id` not found in patients table.

### Score Formula

```
score = (systolic_component    * 0.30)
      + (inertia_component     * 0.25)
      + (adherence_component   * 0.20)
      + (gap_component         * 0.15)
      + (comorbidity_component * 0.10)
```

All components are normalised to 0.0–100.0 before weighting. Final score is clamped to [0.0, 100.0].

### Component Details

| Component    | Signal                                      | Weight |
|--------------|---------------------------------------------|--------|
| systolic     | 28-day avg systolic vs personal baseline    | 30%    |
| inertia      | Days since last medication change           | 25%    |
| adherence    | 100 - adherence_pct from confirmations      | 20%    |
| gap          | Days since last reading (max 28)            | 15%    |
| comorbidity  | Count of active problem_codes               | 10%    |

Constants:
```python
_LOOKBACK_DAYS = 28
_DEFAULT_BASELINE_SYSTOLIC = 140.0    # fallback when no clinic readings exist
_SYSTOLIC_WEIGHT = 0.30
_INERTIA_WEIGHT = 0.25
_ADHERENCE_WEIGHT = 0.20
_GAP_WEIGHT = 0.15
_COMORBIDITY_WEIGHT = 0.10
```

Baseline systolic fallback order:
1. Median of `clinical_context.historic_bp_systolic[]` (preferred)
2. `clinical_context.last_clinic_systolic`
3. `_DEFAULT_BASELINE_SYSTOLIC = 140.0`

Days-since-med-change: defaults to 180 days when `last_med_change` is NULL.

Patient 1091 result: **risk_score = 69.48** (as of 2026-04-21 pipeline run).

---

## Known Patient 1091 Layer 1 Results (reference values)

```
gap_detector:            gap_days ~0, status="none" (readings current as of pipeline run)
inertia_detector:        inertia_detected=True, avg_systolic ~158 mmHg
adherence_analyzer:      adherence_pct=89.8%, pattern="B", "treatment review warranted"
deterioration_detector:  deterioration=False (white-coat dip in final days reduces slope)
risk_scorer:             risk_score=69.48
```

---

## Dependencies

- All detectors → called by `processor.py` (_handle_pattern_recompute) after each is imported
- `compute_risk_score` → called last in pattern_recompute, after all 4 Layer 1 detectors
- Results feed into `briefing/composer.py` via DB reads (not passed directly as arguments)

---

## DO NOT

- Do NOT call `compute_risk_score()` before all 4 Layer 1 detectors have run
- Do NOT call any Layer 3 function from this package — that lives in `briefing/`
- Do NOT use `session.query()` — SQLAlchemy 2.0 async uses `select()` only
- Do NOT use `"non-adherent"` anywhere — always `"possible adherence concern"`
- Do NOT use `"medication failure"` anywhere — always `"treatment review warranted"`
- Do NOT use numpy — slope computation uses pure Python arithmetic
- Do NOT interpolate missing days in deterioration detection — absent rows are device outages
- Do NOT use hardcoded 140 mmHg threshold — always call threshold_utils.py for patient-adaptive threshold
- Do NOT fire inertia when BP slope is negative (7-day recent avg < patient_threshold)
- Do NOT fire deterioration when recent_avg < patient_threshold (absolute gate required)
- Do NOT fire Pattern B when treatment is working (suppression: slope < -0.3 + recent < threshold + med change ≤ 14d)
- Do NOT use clinical_context.last_med_change for inertia — use med_history JSONB instead
