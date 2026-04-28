'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getPatients } from '@/lib/api'
import type { Patient } from '@/lib/types'
import RiskTierBadge from './RiskTierBadge'
import RiskScoreBar from './RiskScoreBar'
import { FileText, Clock, AlertTriangle } from 'lucide-react'

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

  useEffect(() => {
    getPatients().then((data) => {
      setPatients(data)
      setLoading(false)
    })
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-slate-400 text-[16px]">
        <div className="h-4 w-4 rounded-full border-2 border-teal-600 border-t-transparent animate-spin" />
        Loading patients&hellip;
      </div>
    )
  }

  return (
    <div className="card overflow-hidden">
      {/* Header row */}
      <div className="grid grid-cols-[1fr_160px_180px_100px_60px] gap-4
                      px-6 py-3 bg-slate-50 dark:bg-slate-800/60 border-b border-slate-100 dark:border-slate-700
                      text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">
        <span>Patient</span>
        <span>Risk Tier</span>
        <span>Priority Score</span>
        <span>Appointment</span>
        <span>Briefing</span>
      </div>

      {/* Patient rows */}
      {patients.map((patient, idx) => {
        const apptToday = patient.next_appointment && isToday(patient.next_appointment)
        const hasBriefing = patient.has_briefing

        return (
          <button
            key={patient.patient_id}
            onClick={() => router.push(`/patients/${patient.patient_id}`)}
            aria-label={`View briefing for patient ${patient.patient_id}`}
            className={`w-full text-left grid grid-cols-[1fr_160px_180px_100px_60px] gap-4
                        px-6 py-5 items-center
                        hover:bg-teal-50/60 dark:hover:bg-teal-900/20
                        focus-visible:bg-teal-50 dark:focus-visible:bg-teal-900/20
                        transition-colors duration-150 cursor-pointer
                        ${idx !== patients.length - 1 ? 'border-b border-slate-100 dark:border-slate-700/60' : ''}`}
          >
            {/* Patient ID + gender/age */}
            <div>
              <p className="text-[16px] font-semibold text-slate-900 dark:text-slate-100">
                Patient {patient.patient_id}
              </p>
              <p className="text-[14px] text-slate-400 dark:text-slate-500 mt-0.5">
                {patient.gender === 'M' ? 'Male' : patient.gender === 'F' ? 'Female' : 'Unknown'}, {patient.age} yrs
                {patient.tier_override && (
                  <span className="ml-2 text-amber-600 dark:text-amber-400">
                    · {patient.tier_override}
                  </span>
                )}
              </p>
            </div>

            {/* Risk tier badge */}
            <div>
              <RiskTierBadge tier={patient.risk_tier} size="sm" />
            </div>

            {/* Risk score bar */}
            <div>
              <RiskScoreBar score={patient.risk_score} tier={patient.risk_tier} />
              {isScoreStale(patient.risk_score_computed_at) && (
                <div className="flex items-center gap-1 mt-1 text-[11px] text-amber-600 dark:text-amber-400">
                  <AlertTriangle size={11} strokeWidth={2} />
                  <span>Score stale</span>
                </div>
              )}
            </div>

            {/* Appointment */}
            <div>
              {patient.next_appointment && apptToday ? (
                <span className="inline-flex items-center gap-1.5 text-[14px] font-semibold text-teal-700 dark:text-teal-400">
                  <Clock size={14} strokeWidth={2} />
                  {formatApptTime(patient.next_appointment)}
                </span>
              ) : (
                <span className="text-[14px] text-slate-300 dark:text-slate-600">—</span>
              )}
            </div>

            {/* Briefing ready indicator */}
            <div className="flex justify-center">
              <FileText
                size={18}
                strokeWidth={1.75}
                className={hasBriefing ? 'text-teal-600 dark:text-teal-400' : 'text-slate-200 dark:text-slate-700'}
                aria-label={hasBriefing ? 'Briefing ready' : 'No briefing available'}
              />
            </div>
          </button>
        )
      })}
    </div>
  )
}
