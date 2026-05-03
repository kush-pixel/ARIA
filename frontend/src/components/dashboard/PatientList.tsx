'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { getPatients, getReadings, getPatientBaseline } from '@/lib/api'
import type { Patient, Reading, RiskTier } from '@/lib/types'
import RiskTierBadge from './RiskTierBadge'
import RiskScoreBar from './RiskScoreBar'
import { Clock, AlertTriangle, ChevronLeft, ChevronRight, Users } from 'lucide-react'

const PAGE_SIZE = 10

const TIER_FILTERS: Array<{ value: RiskTier | 'all'; label: string }> = [
  { value: 'all',    label: 'All' },
  { value: 'high',   label: 'High Risk' },
  { value: 'medium', label: 'Medium Risk' },
  { value: 'low',    label: 'Low Risk' },
]

// ── BP Trend ──────────────────────────────────────────────────────────────────

// Matches backend adaptive-window fallback (28 days)
const TREND_WINDOW_DAYS = 28
// Standard NICE/ESC clinic target for hypertension management
const DISPLAY_THRESHOLD_SYSTOLIC = 140

type BpTrendLabel = 'Low' | 'Stable' | 'High'

interface BpTrend {
  label: BpTrendLabel
  threshold: string
  className: string
  dot: string
}

function buildTrendLabel(avg: number, baseline: number): BpTrend {
  const a = Math.round(avg)
  const b = Math.round(baseline)
  if (a < b - 15) {
    return {
      label: 'Low',
      threshold: `${a} mmHg · baseline ${b}`,
      className: 'bg-blue-50 text-blue-600 dark:bg-blue-900/20 dark:text-blue-400 border border-blue-200 dark:border-blue-800',
      dot: 'bg-blue-500',
    }
  }
  if (a < DISPLAY_THRESHOLD_SYSTOLIC) {
    return {
      label: 'Stable',
      threshold: `${a} mmHg · baseline ${b}`,
      className: 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 border border-green-200 dark:border-green-800',
      dot: 'bg-green-500',
    }
  }
  return {
    label: 'High',
    threshold: `${a} mmHg · target <${DISPLAY_THRESHOLD_SYSTOLIC}`,
    className: 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400 border border-red-200 dark:border-red-800',
    dot: 'bg-red-500',
  }
}

function computeBpTrend(readings: Reading[], baseline: number, nextAppointment: string | null): BpTrend | null {
  const cutoff = Date.now() - TREND_WINDOW_DAYS * 24 * 60 * 60 * 1000
  // Mirror backend white-coat exclusion: drop readings in the 5-day pre-appointment dip window.
  // Only applies when the appointment is still upcoming — past appointments have no dip to exclude.
  const apptTime = nextAppointment ? new Date(nextAppointment).getTime() : null
  const whiteCoatCutoff = apptTime && apptTime > Date.now()
    ? apptTime - 5 * 24 * 60 * 60 * 1000
    : Infinity
  const home = readings
    .filter((r) => {
      if (r.source === 'clinic') return false
      const t = new Date(r.effective_datetime).getTime()
      return t >= cutoff && t < whiteCoatCutoff
    })
  if (home.length < 3) return null
  const avg = home.reduce((s, r) => s + r.systolic_avg, 0) / home.length
  return buildTrendLabel(avg, baseline)
}

// ── Chief Concern ─────────────────────────────────────────────────────────────

const CONDITION_MAP: Array<{ match: RegExp; label: string }> = [
  { match: /chf|heart failure|i50/i,      label: 'CHF' },
  { match: /stroke|i63|i64/i,             label: 'Stroke' },
  { match: /tia|g45/i,                    label: 'TIA' },
  { match: /diabet|e11/i,                 label: 'Diabetes' },
  { match: /ckd|renal|n18/i,              label: 'CKD' },
  { match: /cad|coronary|i25/i,           label: 'CAD' },
  { match: /hypertension|htn|i10/i,       label: 'Hypertension' },
  { match: /afib|atrial.?fib|i48/i,       label: 'AF' },
  { match: /asthma|j45/i,                 label: 'Asthma' },
  { match: /copd|j44/i,                   label: 'COPD' },
]

function deriveChiefConcern(patient: Patient): string {
  const source = patient.tier_override ?? ''
  const found: string[] = []
  for (const { match, label } of CONDITION_MAP) {
    if (match.test(source) && !found.includes(label)) found.push(label)
  }
  if (!found.includes('Hypertension')) found.push('Hypertension')
  return found.slice(0, 3).join(' + ')
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatApptTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

function isScoreStale(computedAt: string | null): boolean {
  if (!computedAt) return false
  return Date.now() - new Date(computedAt).getTime() > 26 * 60 * 60 * 1000
}

function isToday(iso: string): boolean {
  const d = new Date(iso)
  const now = new Date()
  return d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
}

// ── Component ─────────────────────────────────────────────────────────────────

// Column layout: Patient | Priority Score | Chronic Risk | Chief Concern | BP Trend | Appointment
const COLS = 'grid-cols-[1fr_160px_110px_160px_96px_140px]'

export default function PatientList() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const searchQuery = (searchParams.get('q') ?? '').toLowerCase().trim()
  const [patients, setPatients] = useState<Patient[]>([])
  const [loading, setLoading] = useState(true)
  const [tierFilter, setTierFilter] = useState<RiskTier | 'all'>('all')
  const [page, setPage] = useState(1)
  const [bpTrends, setBpTrends] = useState<Record<string, BpTrend | null>>({})

  useEffect(() => {
    getPatients().then((data) => {
      setPatients(data)
      setLoading(false)
      Promise.all(
        data.map((p) =>
          getPatientBaseline(p.patient_id).then(({ baseline_systolic }) => {
            // Prefer the briefing's authoritative average — same number the clinician sees
            if (p.trend_avg_systolic != null) {
              return [p.patient_id, buildTrendLabel(p.trend_avg_systolic, baseline_systolic)] as [string, BpTrend | null]
            }
            // No briefing yet — fall back to live computation from readings
            return getReadings(p.patient_id)
              .catch(() => [] as Reading[])
              .then((readings) => [
                p.patient_id,
                computeBpTrend(readings, baseline_systolic, p.next_appointment),
              ] as [string, BpTrend | null])
          })
        )
      ).then((entries) => setBpTrends(Object.fromEntries(entries)))
    })
  }, [])

  useEffect(() => { setPage(1) }, [tierFilter, searchQuery])

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-gray-400 text-[15px] py-8">
        <div className="h-4 w-4 rounded-full border-2 border-blue-600 border-t-transparent animate-spin" />
        Loading patients…
      </div>
    )
  }

  const counts: Record<RiskTier | 'all', number> = {
    all:    patients.length,
    high:   patients.filter((p) => p.risk_tier === 'high').length,
    medium: patients.filter((p) => p.risk_tier === 'medium').length,
    low:    patients.filter((p) => p.risk_tier === 'low').length,
  }

  const tierFiltered = tierFilter === 'all' ? patients : patients.filter((p) => p.risk_tier === tierFilter)
  const filtered = searchQuery
    ? tierFiltered.filter((p) =>
        p.patient_id.toLowerCase().includes(searchQuery) ||
        (p.name ?? '').toLowerCase().includes(searchQuery) ||
        p.active_problems.some((prob) => prob.toLowerCase().includes(searchQuery))
      )
    : tierFiltered
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const pageSlice = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  return (
    <div className="space-y-4">
      {/* Tier filter tabs */}
      <div className="flex items-center gap-1 p-1 bg-gray-100 dark:bg-[#1F2937] rounded-xl w-fit">
        {TIER_FILTERS.map(({ value, label }) => {
          const active = tierFilter === value
          let activeStyle = 'bg-white dark:bg-[#111827] text-gray-900 dark:text-gray-100 shadow-sm'
          if (active && value === 'high')   activeStyle = 'bg-red-600 text-white shadow-sm'
          if (active && value === 'medium') activeStyle = 'bg-amber-500 text-white shadow-sm'
          if (active && value === 'low')    activeStyle = 'bg-green-600 text-white shadow-sm'

          return (
            <button
              key={value}
              onClick={() => setTierFilter(value)}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold
                          transition-all duration-150
                          ${active ? activeStyle : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}`}
            >
              {label}
              <span className={`text-[11px] font-bold px-1.5 py-0.5 rounded-full
                               ${active ? 'bg-white/20' : 'bg-gray-200 dark:bg-[#374151] text-gray-500 dark:text-gray-400'}`}>
                {counts[value]}
              </span>
            </button>
          )
        })}
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {/* Header */}
        <div className={`grid ${COLS} gap-4 px-6 py-3
                        bg-gray-50 dark:bg-[#0B1220]
                        border-b border-gray-100 dark:border-[#1F2937]
                        text-[11px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500`}>
          <span>Patient</span>
          <span className="text-center" data-tour="patient-priority-score">Priority Score</span>
          <span className="text-center" data-tour="patient-chronic-risk">Chronic Risk</span>
          <span className="text-center" data-tour="patient-chief-concern">Chief Concern</span>
          <span className="text-center" data-tour="patient-bp-trend">BP Trend</span>
          <span className="text-center" data-tour="patient-appointment">Appointment</span>
        </div>

        {/* Rows */}
        {pageSlice.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
            <Users size={32} strokeWidth={1.5} />
            <p className="text-[15px]">
              {searchQuery ? `No patients match "${searchQuery}".` : 'No patients in this tier.'}
            </p>
          </div>
        ) : (
          pageSlice.map((patient, idx) => {
            const apptToday = patient.next_appointment && isToday(patient.next_appointment)
            const trend = bpTrends[patient.patient_id]
            const chiefConcern = deriveChiefConcern(patient)
            const isLast = idx === pageSlice.length - 1

            return (
              <div
                key={patient.patient_id}
                className={`grid ${COLS} gap-4 px-6 py-4 items-center
                            ${!isLast ? 'border-b border-gray-50 dark:border-[#1F2937]' : ''}
                            hover:bg-blue-50/60 dark:hover:bg-blue-900/10 transition-colors duration-100`}
              >
                {/* Patient — clickable */}
                <button
                  onClick={() => router.push(`/patients/${patient.patient_id}`)}
                  className="text-left"
                >
                  <p className="text-[15px] font-semibold text-gray-900 dark:text-gray-100">
                    {patient.name ?? `Patient ${patient.patient_id}`}
                  </p>
                  <p className="text-[13px] text-gray-400 dark:text-gray-500 mt-0.5">
                    {patient.gender === 'M' ? 'Male' : patient.gender === 'F' ? 'Female' : 'Unknown'}, {patient.age} yrs
                  </p>
                </button>

                {/* Priority Score */}
                <button onClick={() => router.push(`/patients/${patient.patient_id}`)} className="flex flex-col items-center gap-1">
                  <RiskScoreBar score={patient.risk_score} tier={patient.risk_tier} />
                  {isScoreStale(patient.risk_score_computed_at) && (
                    <div className="flex items-center gap-1 text-[11px] text-amber-500">
                      <AlertTriangle size={10} strokeWidth={2} />
                      <span>Score outdated (&gt;26h)</span>
                    </div>
                  )}
                </button>

                {/* Chronic Risk */}
                <button onClick={() => router.push(`/patients/${patient.patient_id}`)} className="flex justify-center">
                  <RiskTierBadge tier={patient.risk_tier} size="sm" />
                </button>

                {/* Chief Concern */}
                <button onClick={() => router.push(`/patients/${patient.patient_id}`)} className="flex justify-center">
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg
                                   bg-indigo-50 dark:bg-indigo-900/20
                                   border border-indigo-200 dark:border-indigo-800
                                   text-[12px] font-semibold
                                   text-indigo-700 dark:text-indigo-300 whitespace-nowrap">
                    {chiefConcern}
                  </span>
                </button>

                {/* BP Trend + threshold */}
                <button onClick={() => router.push(`/patients/${patient.patient_id}`)} className="flex flex-col items-center">
                  {trend ? (
                    <div className="flex flex-col items-center">
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px] font-semibold ${trend.className}`}>
                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${trend.dot}`} />
                        {trend.label}
                      </span>
                      <p className="text-[10px] text-gray-400 dark:text-gray-600 mt-1">
                        {trend.threshold}
                      </p>
                    </div>
                  ) : (
                    <span className="text-[12px] text-gray-300 dark:text-gray-700">No data</span>
                  )}
                </button>

                {/* Appointment */}
                <button onClick={() => router.push(`/patients/${patient.patient_id}`)} className="flex justify-center">
                  {patient.next_appointment && apptToday ? (
                    <span className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-blue-600 dark:text-blue-400">
                      <Clock size={13} strokeWidth={2} />
                      {formatApptTime(patient.next_appointment)}
                    </span>
                  ) : (
                    <span className="text-[13px] text-gray-300 dark:text-gray-700">—</span>
                  )}
                </button>

              </div>
            )
          })
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-1">
          <p className="text-[13px] text-gray-400">
            Showing {(safePage - 1) * PAGE_SIZE + 1}–{Math.min(safePage * PAGE_SIZE, filtered.length)} of {filtered.length} patients
          </p>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={safePage === 1}
              className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-[#1F2937]
                         disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              aria-label="Previous page"
            >
              <ChevronLeft size={15} />
            </button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((n) => (
              <button
                key={n}
                onClick={() => setPage(n)}
                className={`w-8 h-8 rounded-lg text-[13px] font-semibold transition-colors
                            ${n === safePage
                              ? 'bg-blue-600 text-white'
                              : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-[#1F2937]'}`}
              >
                {n}
              </button>
            ))}
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={safePage === totalPages}
              className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-[#1F2937]
                         disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              aria-label="Next page"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
