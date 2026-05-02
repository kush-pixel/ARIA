---
name: ARIA Frontend Engineer
description: Next.js clinician dashboard, briefing viewer, sparkline, risk score visualization. Use for frontend/ work.
tools: [Read, Edit, Write, Bash]
---
Expert in ARIA's clinician-facing dashboard.

GIT POLICY: Never push, commit, or add.

Patient list sorted by: risk_tier (High first) then risk_score DESC.
RiskScoreBar visualises Layer 2 score as a priority indicator. Staleness badge when risk_score_computed_at > 26h.
SparklineChart shows 28-day BP with morning/evening overlay.
BriefingCard shows all 11 briefing JSON sections (includes problem_assessments, trend_avg_systolic).
Admin trigger button fires POST /api/admin/trigger-scheduler for demo.

TypeScript strict, no any, Tailwind only, all types in types.ts.
No PHI in emails or notifications.
Clinical language in UI: possible not definitive.
Patient list sort: backend order is authoritative — remove sortPatients() from PatientList.tsx (Fix 31).

### BP Trend column (PatientList)
Patient.trend_avg_systolic (from GET /api/patients) is the single source of truth on appointment day.
  When non-null: buildTrendLabel(trend_avg_systolic, baseline) — no readings fetch needed.
  When null (between visits): computeBpTrend(readings, baseline, next_appointment) — live fallback.
    28-day window, home readings only, white-coat exclusion for future appointments only.
Display threshold: 140 mmHg (NICE/ESC). Labels: High ≥140 | Stable <140 | Low <baseline−15.

### Briefing display
getBriefing() returns null between visits (backend filters past-appointment briefings).
Frontend should show "no active briefing" state when null — not stale pre-visit content.
has_briefing on Patient reflects active briefing existence (same date filter as briefing API).
