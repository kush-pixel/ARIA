# /pattern — ARIA Pattern Engine Skill
Working on gap, inertia, adherence, deterioration, or risk scoring.

Layer 1 (runs async via worker, NEVER in HTTP path):
- Gap thresholds by tier: High flag>=1 urgent>=3, Medium flag>=3 urgent>=5, Low flag>=7 urgent>=14
- Inertia: ALL 5 conditions simultaneously
    systolic_avg >= patient_threshold (NOT hardcoded 140)
    patient_threshold = max(130, stable_baseline_mean + 1.5×SD) capped at 145
    comorbidity adjustment: -7 mmHg floor 130 when EITHER cardio+metabolic both elevated
      OR any severe-weight comorbidity (CHF/Stroke/TIA) in elevated state
      full mode uses problem_assessments; degraded mode (pre-Fix 7) uses active_problems
    med change: from med_history JSONB (activity=add|modify) NOT last_med_change column
    slope check: 7-day recent avg must still be >= threshold (do NOT fire when declining)
- Deterioration: positive slope + recent > baseline + recent >= patient_threshold (absolute gate)
    step-change sub-detector: 7d recent - 7d three-weeks-ago >= 15 mmHg → flag regardless of slope
- Adherence: < 80% threshold, Pattern A/B/C interpretation
    Pattern B suppression: slope < -0.3 AND recent < threshold AND med_change <= titration_window → suppress to none
      titration_window is drug-class-aware (TITRATION_WINDOWS):
        diuretics → 14d, beta-blockers → 14d, ACE/ARBs → 28d, amlodipine → 56d, default → 42d
      Suppression must NOT apply when no recent med change exists — that is not a succeeding treatment.
    Pattern A → write alert row with alert_type="adherence"
- Language always hedged: possible not definitive
- threshold_utils.py: shared patient-adaptive threshold + comorbidity adjustment (all 4 detectors use it)

Layer 2 (risk_scorer.py, runs AFTER all Layer 1 detectors):
- Weighted sum normalised to 0.0-100.0
- systolic_vs_baseline x 0.30 + days_since_med x 0.25 + (100-adherence) x 0.20
  + gap_normalised x 0.15 + comorbidity_severity_score x 0.10
- comorbidity_severity_score — severity-weighted, clamped 0-100 (Fix 25):
    CHF(I50) / Stroke(I63-64) / TIA(G45): 25 points each
    Diabetes(E11) / CKD(N18) / CAD(I25): 15 points each
    Any other coded problem: 5 points each
    NOT raw count / 5 — that saturates at 5 problems, useless for complex patients
- Normalisation (Fix 58):
    sig_gap = clamp(gap_days / window_days * 100.0)           — adaptive window_days, NOT / 14.0
    sig_inertia = clamp(days_since_med_change / 180.0 * 100.0) — saturates at 6 months, NOT 90 days
- Write to patients.risk_score AND patients.risk_score_computed_at = now() (Fix 61)
- Dashboard sorts by risk_tier then risk_score DESC

Adaptive detection window — all 4 detectors (Fix 28, Phase 2):
  if next_appointment is None or last_visit_date is None or interval <= 0:
    window_days = 28 (fallback default)
  else:
    window_days = min(90, max(14, (next_appointment - last_visit_date).days))
  Log window_days_source ("adaptive" vs "fallback_default").
  When available readings < window_days: log window_truncated_to_available, use available range.
  Silently benefits from longer lookback once Fix 15 full-timeline data lands.

White-coat exclusion — inertia + deterioration only (Fix 27):
  Filter readings where effective_datetime >= (next_appointment - 5 days)
  before threshold comparison (5d matches the synthetic 3-5d dip window).
  When next_appointment is None, no exclusion applied.
  Excluded rows stay in DB, visible in briefing trend.

Write to alerts table (types: gap_urgent|gap_briefing|inertia|deterioration|adherence).
Write audit_events per alert.
