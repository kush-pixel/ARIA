# ARIA Frontend Context
## Next.js 14 | TypeScript strict | Tailwind CSS | recharts

---

## GIT POLICY
Never git push, commit, or add. Tell the user what files changed.

---

## Setup and Running

```bash
cd frontend
npm run dev
# → http://localhost:3000
```

Backend must be running at `http://localhost:8000` (or set `NEXT_PUBLIC_API_URL`).

---

## Tech Stack

```
Framework:  Next.js 14 App Router
Language:   TypeScript strict mode — no any, all props typed
Styling:    Tailwind CSS only — no inline styles, no CSS modules
Charts:     recharts (SparklineChart)
Icons:      lucide-react
API calls:  src/lib/api.ts (centralised — no direct fetch elsewhere)
Types:      src/lib/types.ts (single source of truth for all interfaces)
```

---

## File Structure

```
src/
  app/
    layout.tsx                 — root layout with Sidebar, dark mode class toggle
    page.tsx                   — redirects / → /patients
    patients/
      page.tsx                 — /patients — PatientList component
      [id]/page.tsx            — /patients/:id — patient detail + BriefingCard
    alerts/page.tsx            — /alerts — AlertInbox component
    admin/page.tsx             — /admin — demo trigger-scheduler button

  components/
    dashboard/
      PatientList.tsx          — patient list sorted risk_tier DESC, risk_score DESC
      RiskTierBadge.tsx        — High / Medium / Low badge with tier-appropriate colours
      RiskScoreBar.tsx         — visual 0-100 priority bar (Layer 2 output)
      AlertInbox.tsx           — unacknowledged alerts with acknowledge button

    briefing/
      BriefingCard.tsx         — full 9-field pre-visit briefing layout
      SparklineChart.tsx       — 28-day systolic sparkline (recharts AreaChart)
      AdherenceSummary.tsx     — per-medication adherence rate from AdherenceData[]
      VisitAgenda.tsx          — numbered list of visit_agenda items

    shared/
      Sidebar.tsx              — navigation sidebar
      ThemeToggle.tsx          — dark/light mode toggle

  lib/
    api.ts        — all API calls (MUST be used for every backend fetch)
    types.ts      — all TypeScript interfaces
    mockData.ts   — mock readings data (used by PatientList while real readings load)
```

---

## src/lib/types.ts — All Interfaces

```typescript
type RiskTier = 'high' | 'medium' | 'low'

interface Patient {
  patient_id: string
  gender: 'M' | 'F' | 'U'
  age: number
  risk_tier: RiskTier
  tier_override: string | null
  risk_score: number
  monitoring_active: boolean
  next_appointment: string | null
  enrolled_at: string
  enrolled_by: string
}

interface ClinicalContext {
  patient_id: string
  active_problems: string[]
  problem_codes: string[]
  current_medications: string[]
  med_rxnorm_codes: string[]
  last_med_change: string | null
  allergies: string[]
  last_visit_date: string | null
  last_clinic_systolic: number | null
  last_clinic_diastolic: number | null
  overdue_labs: string[]
  social_context: string | null
}

interface Reading {
  reading_id: string
  patient_id: string
  systolic_1: number
  diastolic_1: number
  heart_rate_1: number | null
  systolic_2: number | null
  diastolic_2: number | null
  heart_rate_2: number | null
  systolic_avg: number
  diastolic_avg: number
  heart_rate_avg: number | null
  effective_datetime: string
  session: 'morning' | 'evening' | 'ad_hoc'
  source: 'generated' | 'manual' | 'ble_auto' | 'clinic'
  submitted_by: string
}

interface BriefingPayload {
  trend_summary: string
  medication_status: string
  adherence_summary: string
  active_problems: string[]
  overdue_labs: string[]
  visit_agenda: string[]
  urgent_flags: string[]
  risk_score: number
  data_limitations: string
  readable_summary?: string    // optional — only present after Layer 3 runs
}

interface Briefing {
  briefing_id: string
  patient_id: string
  appointment_date: string
  llm_response: BriefingPayload
  generated_at: string
  delivered_at: string | null
  read_at: string | null
}

interface Alert {
  alert_id: string
  patient_id: string
  alert_type: 'gap_urgent' | 'gap_briefing' | 'inertia' | 'deterioration'
  gap_days: number | null
  systolic_avg: number | null
  triggered_at: string
  delivered_at: string | null
  acknowledged_at: string | null
}

interface AdherenceData {
  medication_name: string
  rxnorm_code: string | null
  adherence_pct: number
  total_doses: number
  confirmed_doses: number
}
```

---

## src/lib/api.ts — All API Functions

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function getPatients(): Promise<Patient[]>
async function getPatient(id: string): Promise<Patient | null>     // returns null on 404
async function getBriefing(patientId: string): Promise<Briefing | null>  // returns null on 404
async function getReadings(patientId: string): Promise<Reading[]>
async function getAlerts(): Promise<Alert[]>
async function getAdherence(patientId: string): Promise<AdherenceData[]>
async function acknowledgeAlert(alertId: string): Promise<void>
async function triggerScheduler(): Promise<{ enqueued: number }>
```

All calls go through the internal `apiFetch<T>()` helper that throws on non-OK responses.
Every API call in the codebase MUST use one of these functions — never `fetch()` directly.

---

## Pages and Their Data Fetches

| Route              | Data fetched                               | Primary component |
|--------------------|--------------------------------------------|-------------------|
| /patients          | `getPatients()`                            | PatientList       |
| /patients/[id]     | `getPatient(id)`, `getBriefing(id)`, `getReadings(id)`, `getAdherence(id)` | BriefingCard |
| /alerts            | `getAlerts()`                              | AlertInbox        |
| /admin             | `triggerScheduler()` (on button click)     | inline            |

---

## Sorting Rules — PatientList

```typescript
const TIER_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 }

function sortPatients(patients: Patient[]): Patient[] {
  return [...patients].sort((a, b) => {
    const tierDiff = (TIER_ORDER[a.risk_tier] ?? 9) - (TIER_ORDER[b.risk_tier] ?? 9)
    if (tierDiff !== 0) return tierDiff
    return b.risk_score - a.risk_score  // DESC within same tier
  })
}
```

Backend also returns patients sorted this way (via `GET /api/patients`), but the frontend re-sorts to guarantee display order regardless of API response order.

---

## Colour System (Tailwind)

```
Primary:   teal-700 (#0F766E) — section headings, links, badges
Sage:      sage (#84A98C) — secondary accents
Dark text: slate-900 (light), slate-100 (dark)
Body text: slate-700 (light), slate-300 (dark)
Muted:     slate-500 / slate-400
Border:    slate-100 (light), slate-700 (dark)
Risk High: red-500 / red-600
Risk Med:  amber-500 / amber-600
Risk Low:  green-500 / green-600
Dark mode: controlled by 'class' on <html> (Tailwind darkMode: 'class')
```

Font sizes in use:
```
text-[16px]      — body text
text-[17px]      — briefing content
text-[20px]      — section headers in BriefingCard
text-2xl         — page headings
font-semibold    — section headers
font-bold        — page titles
```

---

## Component Props

### PatientList
No props — fetches its own data.

### RiskTierBadge
```typescript
interface Props { tier: RiskTier; override?: string | null }
```

### RiskScoreBar
```typescript
interface Props { score: number }   // 0.0-100.0
```

### AlertInbox
No props — fetches `getAlerts()` internally.

### BriefingCard
```typescript
interface BriefingCardProps {
  patient: Patient
  briefing: Briefing | null
  readings: Reading[]
  adherence: AdherenceData[]
}
```

### SparklineChart
```typescript
interface Props { readings: Reading[] }
// renders systolic_avg from Reading[] as AreaChart (recharts)
```

### AdherenceSummary
```typescript
interface Props { adherence: AdherenceData[] }
```

### VisitAgenda
```typescript
interface Props { items: string[] }  // briefing.llm_response.visit_agenda
```

---

## DO NOT

- Do NOT use `any` type — TypeScript strict is enforced
- Do NOT call `fetch()` directly — all API calls go through `src/lib/api.ts`
- Do NOT add new types outside `src/lib/types.ts`
- Do NOT use inline styles — Tailwind utility classes only
- Do NOT display raw BP readings to patients — this UI is clinician-only
- Do NOT display `"non-adherent"` anywhere — use `"possible adherence concern"`
- Do NOT import from `@/lib/mockData` in new code — mock data is legacy (readings only, being phased out)
- Do NOT add API_BASE anywhere other than `src/lib/api.ts`
