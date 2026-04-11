# ARIA Frontend Context

## GIT POLICY
Never git push, commit, or add.

## Tech
Next.js 14, TypeScript strict, Tailwind CSS, recharts, axios

## Components

### Dashboard
PatientList.tsx    - sorted by risk_tier (High first) then risk_score DESC
                     shows RiskScoreBar for Layer 2 score visualization
RiskTierBadge.tsx  - High/Medium/Low color badges
RiskScoreBar.tsx   - visual bar showing 0-100 priority score
AlertInbox.tsx     - urgent unacknowledged alerts

### Briefing
BriefingCard.tsx   - full pre-visit briefing, all 8 JSON sections
SparklineChart.tsx - 28-day BP trend using recharts, morning/evening overlay
AdherenceSummary.tsx - rate per medication + pattern A/B/C interpretation
VisitAgenda.tsx    - prioritised visit items list

## Rules
TypeScript strict, no any, all props typed
All API calls through src/lib/api.ts
All types in src/lib/types.ts
Tailwind only, no inline styles
No PHI in emails or notifications
Admin: POST /api/admin/trigger-scheduler button (demo mode)

## Clinical Language
Possible, may, suggest — never definitive labels in UI text
