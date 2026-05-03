'use client'

import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
} from 'recharts'
import { getShadowModeResults } from '@/lib/api'
import type { ShadowModeResults, ShadowModeVisit } from '@/lib/types'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

function formatDateShort(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

type SparkPoint = { date: string; morning?: number; evening?: number }

function buildSparklineData(
  readings: ShadowModeVisit['synthetic_readings'],
): SparkPoint[] {
  const byDate: Record<string, SparkPoint> = {}
  for (const r of readings) {
    if (!byDate[r.date]) byDate[r.date] = { date: r.date }
    if (r.session === 'morning') byDate[r.date].morning = r.systolic_avg
    else byDate[r.date].evening = r.systolic_avg
  }
  return Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date))
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function PhysicianBadge({ label }: { label: ShadowModeVisit['physician_label'] }) {
  if (label === 'concerned')
    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
        Concerned
      </span>
    )
  if (label === 'stable')
    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
        Stable
      </span>
    )
  return (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400">
      No Flag
    </span>
  )
}

function ResultBadge({ result }: { result: ShadowModeVisit['result'] }) {
  if (result === 'agree')
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
        AGREE ✓
      </span>
    )
  if (result === 'false_negative')
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
        FALSE NEG ✗
      </span>
    )
  if (result === 'false_positive')
    return (
      <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
        FALSE POS ⚠
      </span>
    )
  return (
    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[13px] font-semibold bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400">
      NO FLAG
    </span>
  )
}

function DetectorChip({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-[12px] font-medium bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300">
      {label}
    </span>
  )
}

const ALERT_TYPE_META: Record<string, { label: string; color: string }> = {
  GAP: { label: 'Reading Gap', color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  INE: { label: 'Therapeutic Inertia', color: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' },
  DET: { label: 'BP Deterioration', color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  ADH: { label: 'Treatment Review', color: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400' },
}

function AlertTypeBadge({ code }: { code: string }) {
  const meta = ALERT_TYPE_META[code] ?? { label: code, color: 'bg-slate-100 text-slate-600' }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold ${meta.color}`}>
      {meta.label}
    </span>
  )
}

function DetectorRow({
  name,
  fired,
  stat,
}: {
  name: string
  fired: boolean
  stat: string
}) {
  return (
    <div className="flex items-center gap-3 py-1.5 border-b border-slate-100 dark:border-slate-700 last:border-0">
      <span className="w-28 text-[14px] font-medium text-slate-700 dark:text-slate-300 flex-shrink-0">
        {name}
      </span>
      {fired ? (
        <span className="text-[12px] font-semibold px-2 py-0.5 rounded bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300 flex-shrink-0">
          FIRED
        </span>
      ) : (
        <span className="text-[12px] font-medium px-2 py-0.5 rounded bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400 flex-shrink-0">
          SILENT
        </span>
      )}
      <span className="text-[13px] text-slate-500 dark:text-slate-400 break-words">{stat}</span>
    </div>
  )
}

function ExpandedCard({ visit }: { visit: ShadowModeVisit }) {
  const sparkData = buildSparklineData(visit.synthetic_readings)
  const d = visit.with_aria.detectors

  return (
    <div className="px-4 pb-4">
      <div className="grid grid-cols-3 gap-4 mt-3">
        {/* Column 1: Without ARIA */}
        <div className="bg-slate-50 dark:bg-slate-800/40 rounded-lg p-4 min-w-0">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-3">
            Without ARIA
          </p>
          <div className="space-y-2 text-[14px]">
            <p className="text-slate-700 dark:text-slate-300">
              <span className="font-medium">Last clinic BP:</span>{' '}
              {visit.without_aria.last_clinic_systolic ?? '—'} mmHg
              <span className="text-slate-400 ml-1">
                ({visit.without_aria.days_since_last_visit} days ago)
              </span>
            </p>
            <div className="text-slate-700 dark:text-slate-300">
              <span className="font-medium">Medications:</span>{' '}
              <span className="break-words">
                {visit.without_aria.medications.length > 0
                  ? visit.without_aria.medications.join(', ')
                  : '—'}
              </span>
            </div>
            <div className="text-slate-700 dark:text-slate-300">
              <span className="font-medium block mb-1">All active problems:</span>
              {visit.without_aria.other_active_problems && visit.without_aria.other_active_problems.length > 0 ? (
                <ul className="space-y-0.5 ml-2">
                  {visit.without_aria.other_active_problems.map((prob, i) => (
                    <li key={i} className="text-[13px] text-slate-600 dark:text-slate-400 break-words">
                      <span className="font-medium">{prob.name}</span>
                      {prob.status ? `: ${prob.status}` : ''}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-slate-400">—</span>
              )}
            </div>
            {visit.without_aria.pending_followups && visit.without_aria.pending_followups.length > 0 && (
              <div className="text-slate-700 dark:text-slate-300">
                <span className="font-medium block mb-1">Pending follow-ups:</span>
                <ul className="space-y-0.5 ml-2">
                  {visit.without_aria.pending_followups.slice(0, 4).map((item, i) => (
                    <li key={i} className="text-[13px] text-slate-600 dark:text-slate-400 break-words">
                      {item}
                    </li>
                  ))}
                  {visit.without_aria.pending_followups.length > 4 && (
                    <li className="text-[13px] text-slate-400">
                      +{visit.without_aria.pending_followups.length - 4} more
                    </li>
                  )}
                </ul>
              </div>
            )}
            <p className="text-slate-700 dark:text-slate-300">
              <span className="font-medium">Days since last visit:</span>{' '}
              {visit.days_since_prior_visit}
            </p>
          </div>
          <p className="mt-4 text-[13px] italic text-slate-400 dark:text-slate-500">
            Clinician walks in with this information only.
          </p>
        </div>

        {/* Column 2: With ARIA */}
        <div className="bg-teal-50/60 dark:bg-teal-900/10 rounded-lg p-4 min-w-0">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-3">
            With ARIA
          </p>
          <p className="text-[14px] text-slate-700 dark:text-slate-300 mb-3">
            <span className="font-medium">Adherence:</span>{' '}
            {visit.with_aria.adherence_pct != null ? `${visit.with_aria.adherence_pct.toFixed(0)}%` : 'N/A'}
            {d.adherence.pattern !== 'none' && (
              <span className="ml-1 text-slate-500 dark:text-slate-400">
                (Pattern {d.adherence.pattern})
              </span>
            )}
          </p>

          <div className="mb-3">
            <DetectorRow
              name="Gap"
              fired={d.gap.fired}
              stat={`max gap ${d.gap.gap_days != null ? d.gap.gap_days.toFixed(1) : 'N/A'} days${d.gap.urgent ? ' · URGENT' : ''}`}
            />
            <DetectorRow
              name="Inertia"
              fired={d.inertia.fired}
              stat={`avg ${d.inertia.avg_systolic} mmHg${d.inertia.no_med_change ? ' · no med change' : ''}`}
            />
            <DetectorRow
              name="Adherence"
              fired={d.adherence.fired}
              stat={`${d.adherence.overall_pct != null ? d.adherence.overall_pct.toFixed(0) : 'N/A'}% · ${d.adherence.interpretation}`}
            />
            <DetectorRow
              name="Deterioration"
              fired={d.deterioration.fired}
              stat={
                d.deterioration.slope !== null
                  ? `slope ${d.deterioration.slope.toFixed(2)}`
                  : 'insufficient data'
              }
            />
          </div>

          {visit.with_aria.visit_agenda.length > 0 && (
            <div className="mb-2">
              <p className="text-[13px] font-semibold text-slate-600 dark:text-slate-400 mb-1">
                Visit agenda
              </p>
              <ol className="list-decimal list-inside space-y-1">
                {visit.with_aria.visit_agenda.map((item, i) => (
                  <li key={i} className="text-[13px] text-slate-700 dark:text-slate-300">
                    {item}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {visit.with_aria.urgent_flags.length > 0 && (
            <div className="mb-2">
              {visit.with_aria.urgent_flags.map((flag, i) => (
                <p key={i} className="text-[13px] font-medium text-red-600 dark:text-red-400 break-words">
                  ⚑ {flag}
                </p>
              ))}
            </div>
          )}

          {visit.between_visit_alerts && visit.between_visit_alerts.length > 0 && (
            <div className="mt-2 border-t border-teal-200 dark:border-teal-800 pt-2">
              <p className="text-[12px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
                First alert ARIA would have sent
              </p>
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-lg px-3 py-2.5">
                <div className="flex items-center justify-between gap-2 flex-wrap mb-1.5">
                  <p className="text-[12px] font-bold text-amber-700 dark:text-amber-400">
                    ⚑ {visit.between_visit_alerts[0].days_before_visit} days before this visit
                  </p>
                  <p className="text-[11px] text-amber-600 dark:text-amber-500">
                    {formatDate(visit.between_visit_alerts[0].alert_date)}
                  </p>
                </div>
                <div className="flex gap-1.5 flex-wrap mb-2">
                  {visit.between_visit_alerts[0].alert_type.split('|').map((code) => (
                    <AlertTypeBadge key={code} code={code} />
                  ))}
                </div>
                {visit.between_visit_alerts[0].reasons && visit.between_visit_alerts[0].reasons!.length > 0 ? (
                  <ul className="space-y-1">
                    {visit.between_visit_alerts[0].reasons!.map((r, ri) => (
                      <li key={ri} className="text-[13px] text-amber-800 dark:text-amber-300 flex gap-1.5">
                        <span className="mt-0.5 flex-shrink-0">›</span>
                        <span className="break-words">{r}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[13px] text-amber-800 dark:text-amber-300 break-words">
                    {visit.between_visit_alerts[0].message}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Column 3: Sparkline */}
        <div className="rounded-lg p-4 border border-slate-100 dark:border-slate-700">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-3">
            Home BP: 28 Days Before Visit
          </p>
          {sparkData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={sparkData}>
                <XAxis
                  dataKey="date"
                  tickFormatter={formatDateShort}
                  tick={{ fontSize: 11 }}
                  interval="preserveStartEnd"
                />
                <YAxis domain={[80, 220]} tick={{ fontSize: 11 }} width={32} />
                <Tooltip
                  formatter={(val: number) => [`${val} mmHg`]}
                  labelFormatter={formatDateShort}
                />
                <ReferenceLine
                  y={140}
                  stroke="#ef4444"
                  strokeDasharray="3 3"
                  label={{ value: '140', position: 'right', fontSize: 11, fill: '#ef4444' }}
                />
                <Line
                  type="monotone"
                  dataKey="morning"
                  stroke="#0d9488"
                  strokeWidth={1.5}
                  dot={false}
                  connectNulls={false}
                  name="Morning"
                />
                <Line
                  type="monotone"
                  dataKey="evening"
                  stroke="#0d9488"
                  strokeWidth={1.5}
                  strokeDasharray="4 2"
                  dot={false}
                  connectNulls={false}
                  name="Evening"
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-8 text-center">
              No synthetic readings generated
            </p>
          )}
        </div>
      </div>

      {/* Verdict row */}
      <div className="flex justify-between items-center px-2 py-3 mt-3 border-t border-slate-100 dark:border-slate-700">
        <p className="text-[14px] text-slate-600 dark:text-slate-400">
          Physician at visit:{' '}
          <span className="font-semibold capitalize">{visit.physician_label}</span>
        </p>
        <div className="text-center">
          {visit.result === 'agree' ? (
            <span className="text-[18px] font-bold text-green-600 dark:text-green-400">
              AGREE ✓
            </span>
          ) : (
            <span className="text-[18px] font-bold text-red-600 dark:text-red-400">
              DISAGREE ✗
            </span>
          )}
        </div>
        <p className="text-[14px] text-slate-600 dark:text-slate-400">
          ARIA pre-visit:{' '}
          <span className="font-semibold">
            {visit.with_aria.fired ? 'Would alert' : 'Silent'}
          </span>
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type FilterKey = 'all' | 'agree' | 'false_negative' | 'false_positive' | 'no_ground_truth'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'agree', label: 'Agreements' },
  { key: 'false_negative', label: 'False Negatives' },
  { key: 'false_positive', label: 'False Positives' },
  { key: 'no_ground_truth', label: 'No Flag' },
]

function applyFilter(visits: ShadowModeVisit[], filter: FilterKey): ShadowModeVisit[] {
  if (filter === 'all') return visits
  return visits.filter((v) => v.result === filter)
}

export default function ShadowModePage() {
  const [data, setData] = useState<ShadowModeResults | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<FilterKey>('all')
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null)

  useEffect(() => {
    getShadowModeResults().then((result) => {
      setData(result)
      setLoading(false)
    })
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-slate-400 text-[16px] p-8">
        <div className="h-4 w-4 rounded-full border-2 border-teal-600 border-t-transparent animate-spin" />
        Loading shadow mode results&hellip;
      </div>
    )
  }

  if (!data) {
    return (
      <div className="p-8 max-w-xl">
        <div className="card p-8 text-center space-y-4">
          <p className="text-[17px] font-semibold text-slate-800 dark:text-slate-100">
            Shadow mode has not been run yet.
          </p>
          <p className="text-[15px] text-slate-500 dark:text-slate-400">
            Run this command first:
          </p>
          <pre className="bg-slate-100 dark:bg-slate-800 rounded-lg px-5 py-3 text-left text-[14px] font-mono text-slate-700 dark:text-slate-300">
            python scripts/run_shadow_mode.py
          </pre>
          <p className="text-[14px] text-slate-400 dark:text-slate-500">
            Then refresh this page.
          </p>
        </div>
      </div>
    )
  }

  const filteredVisits = applyFilter(data.visits, filter)

  return (
    <div className="p-8 space-y-8 max-w-6xl">
      {/* Section 1: Header + stat cards */}
      <div>
        <h1 className="text-[24px] font-bold text-slate-900 dark:text-slate-100">
          Shadow Mode: Historical Validation
        </h1>
        <p className="text-[15px] text-slate-500 dark:text-slate-400 mt-1">
          Replaying patient 1091&apos;s {data.total_eval_points} evaluation points ({data.clinic_bp_points} clinic BP · {data.no_vitals_points} no-vitals HTN assessments). ARIA receives only data available before each visit.
        </p>
      </div>

      <div className="grid grid-cols-4 gap-4">
        {/* Agreement rate */}
        <div className="card p-5">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
            Agreement Rate
          </p>
          <p
            className={`text-[32px] font-bold tabular-nums ${
              data.agreement_pct >= 80
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {data.agreement_pct.toFixed(1)}%
          </p>
          <p className="text-[13px] text-slate-400 dark:text-slate-500 mt-0.5">
            Target: &ge;80%
          </p>
        </div>

        {/* Visits analysed */}
        <div className="card p-5">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
            Visits Analysed
          </p>
          <p className="text-[32px] font-bold tabular-nums text-slate-900 dark:text-slate-100">
            {data.with_ground_truth}
          </p>
          <p className="text-[13px] text-slate-400 dark:text-slate-500 mt-0.5">
            {data.skipped} skipped · {data.no_ground_truth} no flag
          </p>
        </div>

        {/* False negatives */}
        <div className="card p-5">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
            False Negatives
          </p>
          <p
            className={`text-[32px] font-bold tabular-nums ${
              data.false_negatives > 0
                ? 'text-red-600 dark:text-red-400'
                : 'text-slate-900 dark:text-slate-100'
            }`}
          >
            {data.false_negatives}
          </p>
          <p className="text-[13px] text-slate-400 dark:text-slate-500 mt-0.5">
            Physician concerned, ARIA silent
          </p>
        </div>

        {/* Passed/failed */}
        <div className="card p-5 flex flex-col justify-between">
          <p className="text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-1">
            Validation
          </p>
          {data.passed ? (
            <span className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 text-[16px] font-bold w-fit">
              ✓ PASSED
            </span>
          ) : (
            <span className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 text-[16px] font-bold w-fit">
              ✗ FAILED
            </span>
          )}
          <p className="text-[13px] text-slate-400 dark:text-slate-500 mt-1">
            {data.false_positives} false positives
          </p>
        </div>
      </div>

      {/* Section 2: Best demo window */}
      {data.best_demo_window && (
        <div className="border-l-4 border-teal-500 bg-teal-50/50 dark:bg-teal-900/10 pl-5 py-3 pr-4 rounded-r-lg">
          <p className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">
            Best Demo Window
          </p>
          <p className="text-[14px] text-slate-600 dark:text-slate-400 mt-0.5">
            {data.best_demo_window.summary}
          </p>
          <p className="text-[13px] text-slate-500 dark:text-slate-500 mt-1">
            {formatDate(data.best_demo_window.date_from)} &ndash;{' '}
            {formatDate(data.best_demo_window.date_to)}
          </p>
          <p className="text-[13px] text-slate-400 dark:text-slate-500 mt-1">
            Click these visits in the timeline below to see ARIA&apos;s full reasoning.
          </p>
        </div>
      )}

      {/* Section 3: Filter bar */}
      <div className="flex items-center gap-6 border-b border-slate-200 dark:border-slate-700">
        {FILTERS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`pb-2 text-[14px] font-medium transition-colors duration-150 ${
              filter === key
                ? 'border-b-2 border-teal-600 text-teal-700 dark:text-teal-400'
                : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Section 4: Visit timeline */}
      <div className="space-y-1">
        {filteredVisits.length === 0 && (
          <p className="text-[15px] text-slate-400 dark:text-slate-500 py-6 text-center">
            No visits match this filter.
          </p>
        )}

        {filteredVisits.map((visit) => {
          const isExpanded = expandedIndex === visit.visit_index
          const firedDetectors: string[] = []
          if (visit.with_aria.detectors.gap.fired) firedDetectors.push('Gap')
          if (visit.with_aria.detectors.inertia.fired) firedDetectors.push('Inertia')
          if (visit.with_aria.detectors.adherence.fired) firedDetectors.push('Adherence')
          if (visit.with_aria.detectors.deterioration.fired) firedDetectors.push('Deterioration')

          return (
            <div key={visit.visit_index}>
              {/* Between-visit alert strip */}
              {visit.between_visit_alerts.map((alert, ai) => (
                <div
                  key={ai}
                  className="border-l-4 border-amber-400 bg-amber-50 dark:bg-amber-900/10 px-4 py-3 mb-1 rounded-r-md"
                >
                  <div className="flex items-center gap-2 flex-wrap mb-1.5">
                    <span className="text-[13px] font-bold text-amber-800 dark:text-amber-300">
                      ⚠ ARIA alerted {alert.days_before_visit} days before this visit
                    </span>
                    <span className="text-[12px] text-amber-600 dark:text-amber-400">
                      · {formatDate(alert.alert_date)}
                    </span>
                  </div>
                  <div className="flex gap-1.5 flex-wrap mb-2">
                    {alert.alert_type.split('|').map((code) => (
                      <AlertTypeBadge key={code} code={code} />
                    ))}
                  </div>
                  {alert.reasons && alert.reasons.length > 0 ? (
                    <ul className="space-y-1">
                      {alert.reasons.map((r, ri) => (
                        <li key={ri} className="text-[13px] text-amber-800 dark:text-amber-300 flex gap-1.5">
                          <span className="mt-0.5 flex-shrink-0">›</span>
                          <span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-[13px] text-amber-800 dark:text-amber-300">{alert.message}</p>
                  )}
                </div>
              ))}

              {/* Visit card */}
              <div className="card overflow-hidden">
                {/* Collapsed row — clickable */}
                <button
                  onClick={() =>
                    setExpandedIndex(isExpanded ? null : visit.visit_index)
                  }
                  className="w-full text-left px-5 py-4 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors duration-150"
                >
                  <div className="flex items-center gap-4">
                    {/* Date + BP */}
                    <div className="flex-shrink-0 w-40">
                      <p className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">
                        {formatDate(visit.visit_date)}
                      </p>
                      <p className="text-[14px] text-slate-500 dark:text-slate-400 mt-0.5 tabular-nums">
                        {visit.systolic != null && visit.diastolic != null
                          ? `${visit.systolic.toFixed(0)}/${visit.diastolic.toFixed(0)} mmHg`
                          : "No vitals"}
                      </p>
                    </div>

                    {/* Physician label */}
                    <div className="flex-shrink-0 w-24">
                      <PhysicianBadge label={visit.physician_label} />
                    </div>

                    {/* Without ARIA */}
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] text-slate-400 dark:text-slate-500 truncate">
                        Last visit {visit.without_aria.days_since_last_visit}d ago · BP{' '}
                        {visit.without_aria.last_clinic_systolic} mmHg
                      </p>
                    </div>

                    {/* ARIA badge + detector chips */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {visit.with_aria.fired ? (
                        <span className="text-[13px] font-semibold px-2.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300">
                          ARIA FIRED
                        </span>
                      ) : (
                        <span className="text-[13px] font-medium px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                          ARIA SILENT
                        </span>
                      )}
                      {firedDetectors.map((d) => (
                        <DetectorChip key={d} label={d} />
                      ))}
                    </div>

                    {/* Result badge */}
                    <div className="flex-shrink-0">
                      <ResultBadge result={visit.result} />
                    </div>

                    {/* Chevron */}
                    <div className="flex-shrink-0 text-slate-400">
                      {isExpanded ? (
                        <ChevronUp size={16} strokeWidth={2} />
                      ) : (
                        <ChevronDown size={16} strokeWidth={2} />
                      )}
                    </div>
                  </div>
                </button>

                {/* Expanded content */}
                {isExpanded && <ExpandedCard visit={visit} />}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
