# ARIA Pattern Engine Context

## GIT POLICY
Never git push, commit, or add.

## Purpose
Layer 1: Detect clinical patterns via SQL queries (async, never in HTTP path)
Layer 2: Compute weighted risk score per patient after Layer 1 completes

## Layer 1 Detectors

Gap detection (gap_detector.py):
  gap_days = EXTRACT(EPOCH FROM (NOW() - MAX(effective_datetime))) / 86400
  High: flag>=1 urgent>=3 | Medium: flag>=3 urgent>=5 | Low: flag>=7 urgent>=14

Inertia (inertia_detector.py) — ALL conditions required:
  AVG(systolic_avg) >= 140 over 28 days
  COUNT(*) >= 5 elevated readings
  Duration > 7 days
  last_med_change < MIN(effective_datetime) OR NULL

Adherence correlation (adherence_analyzer.py):
  adherence_pct = COUNT(confirmed_at)/COUNT(*)*100
  < 80% = clinical flag
  Pattern A: high systolic + low adherence = "possible adherence concern"
  Pattern B: high systolic + high adherence = "possible treatment-review case"
  ALWAYS hedged language — never definitive

## Layer 2 Risk Scoring (risk_scorer.py)
Runs AFTER all Layer 1 detectors complete.
Score = weighted sum normalised to 0.0-100.0:
  systolic_vs_baseline × 0.30
  days_since_med_change × 0.25
  (100 - adherence_pct) × 0.20
  gap_days_normalised × 0.15
  comorbidity_count × 0.10
Write result to patients.risk_score.

## Output
Write to alerts table with alert_type.
Write audit_events for each alert triggered.
Briefing composer reads alerts for urgent_flags field.