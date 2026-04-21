'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getPatients } from '@/lib/api'
import { MOCK_READINGS_1091, MOCK_READINGS_1093 } from '@/lib/mockData'
import type { Patient, Reading } from '@/lib/types'
import RiskTierBadge from './RiskTierBadge'
import RiskScoreBar from './RiskScoreBar'
import { FileText, Clock } from 'lucide-react'

const TIER_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 }

function sortPatients(patients: Patient[]): Patient[] {
  return [...patients].sort((a, b) => {
    const tierDiff = (TIER_ORDER[a.risk_tier] ?? 9) - (TIER_ORDER[b.risk_tier] ?? 9)
    if (tierDiff !== 0) return tierDiff
    return b.risk_score - a.risk_score
  })
}

function lastReading(patientId: string): Reading | null {
  const all = patientId === '1091' ? MOCK_READINGS_1091 : patientId === '1093' ? MOCK_READINGS_1093 : []
  if (all.length === 0) return null
  return all.reduce((latest, r) =>
    new Date(r.effective_datetime) > new Date(latest.effective_datetime) ? r : latest
  )
}

function daysSince(isoTimestamp: string): number {
  return Math.floor((Date.now() - new Date(isoTimestamp).getTime()) / 86_400_000)
}

function formatApptTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
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
      setPatients(sortPatients(data))
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
      <div className="grid grid-cols-[1fr_160px_180px_120px_110px_100px_60px] gap-4
                      px-6 py-3 bg-slate-50 dark:bg-slate-800/60 border-b border-slate-100 dark:border-slate-700
                      text-[13px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide">
        <span>Patient</span>
        <span>Risk Tier</span>
        <span>Priority Score</span>
        <span>Last BP</span>
        <span>Last Reading</span>
        <span>Appointment</span>
        <span>Briefing</span>
      </div>

      {/* Patient rows */}
      {patients.map((patient, idx) => {
        const reading = lastReading(patient.patient_id)
        const daysAgo = reading ? daysSince(reading.effective_datetime) : null
        const apptToday = patient.next_appointment && isToday(patient.next_appointment)

        return (
          <button
            key={patient.patient_id}
            onClick={() => router.push(`/patients/${patient.patient_id}`)}
            aria-label={`View briefing for patient ${patient.patient_id}`}
            className={`w-full text-left grid grid-cols-[1fr_160px_180px_120px_110px_100px_60px] gap-4
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
            </div>

            {/* Last BP */}
            <div>
              {reading ? (
                <span className="text-clinical font-semibold text-slate-800 dark:text-slate-100 tabular-nums">
                  {reading.systolic_avg.toFixed(0)}/{reading.diastolic_avg.toFixed(0)}
                  <span className="text-[13px] font-normal text-slate-400 ml-1">mmHg</span>
                </span>
              ) : (
                <span className="text-[15px] text-slate-400 italic">No data</span>
              )}
            </div>

            {/* Days since last reading */}
            <div>
              {daysAgo !== null ? (
                <span className={`text-[15px] font-medium ${daysAgo >= 3 ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400'}`}>
                  {daysAgo === 0 ? 'Today' : `${daysAgo}d ago`}
                </span>
              ) : (
                <span className="text-[15px] text-slate-300 dark:text-slate-600">—</span>
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
                className={apptToday ? 'text-teal-600 dark:text-teal-400' : 'text-slate-200 dark:text-slate-700'}
                aria-label={apptToday ? 'Briefing ready' : 'No briefing today'}
              />
            </div>
          </button>
        )
      })}
    </div>
  )
}
