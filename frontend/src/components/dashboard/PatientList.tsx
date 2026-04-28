'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getPatients, getReadings } from '@/lib/api'
import type { Patient, RiskTier } from '@/lib/types'
import RiskTierBadge from './RiskTierBadge'
import RiskScoreBar from './RiskScoreBar'
import MiniSparkline from './MiniSparkline'
import { FileText, Clock, AlertTriangle, ChevronLeft, ChevronRight, Users } from 'lucide-react'

const PAGE_SIZE = 10

const TIER_FILTERS: Array<{ value: RiskTier | 'all'; label: string }> = [
  { value: 'all',    label: 'All' },
  { value: 'high',   label: 'High Risk' },
  { value: 'medium', label: 'Medium Risk' },
  { value: 'low',    label: 'Low Risk' },
]

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

export default function PatientList() {
  const router = useRouter()
  const [patients, setPatients] = useState<Patient[]>([])
  const [loading, setLoading] = useState(true)
  const [tierFilter, setTierFilter] = useState<RiskTier | 'all'>('all')
  const [page, setPage] = useState(1)
  // Map of patient_id → sorted systolic values (last 14)
  const [sparklines, setSparklines] = useState<Record<string, number[]>>({})

  useEffect(() => {
    getPatients().then((data) => {
      setPatients(data)
      setLoading(false)
      // Fetch readings for all patients in parallel after list loads
      Promise.all(
        data.map((p) =>
          getReadings(p.patient_id)
            .then((readings) => {
              const values = readings
                .filter((r) => r.source !== 'clinic')
                .sort((a, b) => new Date(a.effective_datetime).getTime() - new Date(b.effective_datetime).getTime())
                .map((r) => r.systolic_avg)
              return [p.patient_id, values] as [string, number[]]
            })
            .catch(() => [p.patient_id, []] as [string, number[]])
        )
      ).then((entries) => {
        setSparklines(Object.fromEntries(entries))
      })
    })
  }, [])

  useEffect(() => { setPage(1) }, [tierFilter])

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

  const filtered = tierFilter === 'all' ? patients : patients.filter((p) => p.risk_tier === tierFilter)
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
        {/* Header — added BP Trend column */}
        <div className="grid grid-cols-[1fr_130px_160px_100px_96px_56px] gap-4
                        px-6 py-3
                        bg-gray-50 dark:bg-[#0B1220]
                        border-b border-gray-100 dark:border-[#1F2937]
                        text-[11px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
          <span>Patient</span>
          <span>Risk Tier</span>
          <span>Priority Score</span>
          <span>Appointment</span>
          <span>BP Trend</span>
          <span className="text-center">Briefing</span>
        </div>

        {/* Rows */}
        {pageSlice.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3 text-gray-400">
            <Users size={32} strokeWidth={1.5} />
            <p className="text-[15px]">No patients in this tier.</p>
          </div>
        ) : (
          pageSlice.map((patient, idx) => {
            const apptToday = patient.next_appointment && isToday(patient.next_appointment)
            const hasBriefing = patient.has_briefing
            const sparkValues = sparklines[patient.patient_id] ?? []

            return (
              <button
                key={patient.patient_id}
                onClick={() => router.push(`/patients/${patient.patient_id}`)}
                aria-label={`View briefing for patient ${patient.patient_id}`}
                className={`w-full text-left grid grid-cols-[1fr_130px_160px_100px_96px_56px] gap-4
                            px-6 py-4 items-center
                            hover:bg-blue-50/60 dark:hover:bg-blue-900/10
                            transition-colors duration-100 cursor-pointer
                            ${idx !== pageSlice.length - 1
                              ? 'border-b border-gray-50 dark:border-[#1F2937]'
                              : ''}`}
              >
                {/* Patient */}
                <div>
                  <p className="text-[15px] font-semibold text-gray-900 dark:text-gray-100">
                    Patient {patient.patient_id}
                  </p>
                  <p className="text-[13px] text-gray-400 dark:text-gray-500 mt-0.5">
                    {patient.gender === 'M' ? 'Male' : patient.gender === 'F' ? 'Female' : 'Unknown'}, {patient.age} yrs
                    {patient.tier_override && (
                      <span className="ml-2 text-amber-600 dark:text-amber-400">
                        · {patient.tier_override}
                      </span>
                    )}
                  </p>
                </div>

                {/* Tier */}
                <div>
                  <RiskTierBadge tier={patient.risk_tier} size="sm" />
                </div>

                {/* Score */}
                <div>
                  <RiskScoreBar score={patient.risk_score} tier={patient.risk_tier} />
                  {isScoreStale(patient.risk_score_computed_at) && (
                    <div className="flex items-center gap-1 mt-1 text-[11px] text-amber-500">
                      <AlertTriangle size={10} strokeWidth={2} />
                      <span>Score stale</span>
                    </div>
                  )}
                </div>

                {/* Appointment */}
                <div>
                  {patient.next_appointment && apptToday ? (
                    <span className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-blue-600 dark:text-blue-400">
                      <Clock size={13} strokeWidth={2} />
                      {formatApptTime(patient.next_appointment)}
                    </span>
                  ) : (
                    <span className="text-[13px] text-gray-300 dark:text-gray-700">—</span>
                  )}
                </div>

                {/* BP Sparkline */}
                <div className="flex items-center">
                  {sparkValues.length >= 2 ? (
                    <div>
                      <MiniSparkline values={sparkValues} tier={patient.risk_tier} />
                      <p className="text-[10px] text-gray-400 dark:text-gray-600 mt-0.5 tabular-nums">
                        {sparkValues[sparkValues.length - 1].toFixed(0)} mmHg
                      </p>
                    </div>
                  ) : (
                    <span className="text-[11px] text-gray-300 dark:text-gray-700">No data</span>
                  )}
                </div>

                {/* Briefing icon */}
                <div className="flex justify-center">
                  <FileText
                    size={17}
                    strokeWidth={1.75}
                    className={hasBriefing
                      ? 'text-blue-600 dark:text-blue-400'
                      : 'text-gray-200 dark:text-gray-800'}
                    aria-label={hasBriefing ? 'Briefing ready' : 'No briefing'}
                  />
                </div>
              </button>
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
