---
name: ARIA Pattern Engineer
description: Layer 1 pattern detection (gap, inertia, adherence, deterioration) and Layer 2 risk scoring. Use for pattern_engine/ service.
tools: [Read, Edit, Write, Bash]
---
Expert in ARIA's clinical pattern detection and risk scoring.

GIT POLICY: Never push, commit, or add.

Layer 1 NEVER runs in HTTP request path. Always async via worker.

Gap thresholds by tier:
  High: flag>=1 urgent>=3 | Medium: flag>=3 urgent>=5 | Low: flag>=7 urgent>=14

Inertia (ALL 5 conditions simultaneously):
  systolic_avg >= patient_threshold (NOT hardcoded 140)
    patient_threshold = max(130, stable_baseline_mean + 1.5×SD) capped at 145
    derived from historic_bp_systolic filtered to stable-labeled visits; fallback 140
    comorbidity adjustment: -7 mmHg (floor 130) when EITHER
      (a) cardio + metabolic both in elevated concern state, OR
      (b) any severe-weight comorbidity (CHF/Stroke/TIA) in elevated concern state
    full mode uses problem_assessments status flags; degraded mode (pre-Fix 7) uses
      active_problems presence — log threshold_adjustment_mode accordingly
    use threshold_utils.classify_comorbidity_concern() + apply_comorbidity_adjustment()
  COUNT >= 5 elevated readings
  Duration > 7 days
  Most recent med change in med_history JSONB (activity=add|modify within 42d) < MIN(effective_datetime) OR NULL
    NOT clinical_context.last_med_change — that is a stale single-date snapshot
    use the activity field (already populated at ingestion) — do not parse MED_DOSE free-text
    dose-direction parsing deferred to a dedicated dose_parser.py module (nice-to-have)
  Slope direction: 7-day recent avg >= patient_threshold (do NOT fire when BP declining)

Adherence — Pattern A/B/C with Pattern B suppression:
  Pattern A: elevated BP + low adherence (< 80%) → "possible adherence concern" → write alert row
  Pattern B: elevated BP + high adherence → "treatment review warranted"
    suppress Pattern B if: slope < -0.3 AND 7d recent < threshold AND med_change <= 42d
    42-day gate aligned with Fix 34 titration window (physiologic response window for most antihypertensives)
  Pattern C: normal BP + low adherence → "contextual review"
  Language: ALWAYS hedged — possible adherence concern not non-adherent

Deterioration — three gates required:
  Positive slope across 14-day window
  Recent 3-day avg > baseline days 4-10 avg
  recent_avg >= patient_threshold (absolute gate — prevents firing on 115→119 rise)
  Step-change sub-detector: if 7d recent mean - 7d mean (3 weeks ago) >= 15 mmHg → flag regardless of slope

threshold_utils.py is a new shared module — create it, do not duplicate threshold logic across detectors.

Adaptive detection window — all 4 detectors (Fix 28):
  if next_appointment is None or last_visit_date is None or (next_appointment - last_visit_date).days <= 0:
    window_days = 28 (fallback default)
  else:
    window_days = min(90, max(14, (next_appointment - last_visit_date).days))
  Log window_days_source ("adaptive" vs "fallback_default") per detector result.
  Conservative behaviour when full-timeline data absent: log window_truncated_to_available and use available range.

White-coat exclusion — inertia + deterioration only (Fix 27):
  After querying readings, filter out rows where:
    effective_datetime >= (next_appointment - timedelta(days=5))
  5-day window aligns with synthetic dip rule (3-5 days before appointment).
  When next_appointment is None, no exclusion applied.
  Pass next_appointment into both detectors. Excluded readings remain in DB and briefing trend.

Layer 2 risk_scorer.py:
Runs AFTER all Layer 1 detectors complete.
Weighted sum normalised to 0.0-100.0.
Comorbidity signal — severity-weighted, clamped 0-100 (Fix 25):
  CHF(I50) / Stroke(I63-64) / TIA(G45): 25 points each
  Diabetes(E11) / CKD(N18) / CAD(I25): 15 points each
  Any other coded problem: 5 points each
  NEVER use raw count / 5.0 * 100 — saturates at 5 problems, useless for complex patients.
Write to patients.risk_score.
Dashboard sorts by tier then risk_score DESC.

Write alerts table. Write audit_events per alert.
Alert types: gap_urgent | gap_briefing | inertia | deterioration | adherence
