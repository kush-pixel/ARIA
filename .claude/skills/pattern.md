# /pattern — ARIA Pattern Engine Skill
Working on gap, inertia, adherence, deterioration, or risk scoring.

Layer 1 (runs async via worker, NEVER in HTTP path):
- Gap thresholds by tier: High flag>=1 urgent>=3, Medium flag>=3 urgent>=5, Low flag>=7 urgent>=14
- Inertia: ALL 4 conditions simultaneously
- Adherence: < 80% threshold, Pattern A/B/C interpretation
- Language always hedged: possible not definitive

Layer 2 (risk_scorer.py, runs AFTER all Layer 1 detectors):
- Weighted sum normalised to 0.0-100.0
- systolic_vs_baseline x 0.30 + days_since_med x 0.25 + (100-adherence) x 0.20
  + gap_normalised x 0.15 + comorbidity_count x 0.10
- Write to patients.risk_score
- Dashboard sorts by risk_tier then risk_score DESC

Write to alerts table. Write audit_events per alert.
