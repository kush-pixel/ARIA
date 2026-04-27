'use client'

import type { Patient, Briefing, Reading, AdherenceData } from '@/lib/types'
import RiskTierBadge from '@/components/dashboard/RiskTierBadge'
import RiskScoreBar from '@/components/dashboard/RiskScoreBar'
import SparklineChart from './SparklineChart'
import AdherenceSummary from './AdherenceSummary'
import VisitAgenda from './VisitAgenda'
import { AlertTriangle, Info, FlaskConical, Pill, Activity, ClipboardList, Flag, Sparkles } from 'lucide-react'

interface BriefingCardProps {
  patient: Patient
  briefing: Briefing | null
  readings: Reading[]
  adherence: AdherenceData[]
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="py-6 border-b border-slate-100 dark:border-slate-700 last:border-0">
      <h2 className="flex items-center gap-2.5 text-[20px] font-semibold text-teal-700 dark:text-teal-500 mb-4">
        <span className="opacity-70">{icon}</span>
        {title}
      </h2>
      <div className="text-[17px] text-slate-700 dark:text-slate-300 leading-relaxed">
        {children}
      </div>
    </section>
  )
}

function formatApptDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })
}

function formatApptTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export default function BriefingCard({ patient, briefing, readings, adherence }: BriefingCardProps) {
  const payload = briefing?.llm_response

  return (
    <div className="card divide-y divide-slate-100 dark:divide-slate-700">
      {/* 1. Patient header */}
      <div className="px-8 py-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[14px] font-semibold uppercase tracking-widest text-slate-400 dark:text-slate-500 mb-1">
              Pre-Visit Briefing
            </p>
            <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              Patient {patient.patient_id}
            </h1>
            <p className="text-[15px] text-slate-400 dark:text-slate-500 mt-1">
              {patient.gender === 'M' ? 'Male' : patient.gender === 'F' ? 'Female' : 'Unknown'}, {patient.age} years
              {patient.tier_override && (
                <span className="ml-2 text-amber-600 dark:text-amber-400">
                  · {patient.tier_override}
                </span>
              )}
            </p>
            {patient.next_appointment && (
              <p className="text-[15px] text-teal-700 dark:text-teal-400 font-medium mt-2">
                Appointment: {formatApptDate(patient.next_appointment)} at {formatApptTime(patient.next_appointment)}
              </p>
            )}
          </div>
          <div className="flex flex-col items-end gap-3">
            <RiskTierBadge tier={patient.risk_tier} size="lg" />
            <div className="w-52">
              <p className="text-[13px] text-slate-400 mb-1">Priority Score</p>
              <RiskScoreBar score={patient.risk_score} tier={patient.risk_tier} />
            </div>
          </div>
        </div>

        {/* Briefing metadata */}
        {briefing && (
          <p className="mt-4 text-[13px] text-slate-400 dark:text-slate-500">
            Generated {new Date(briefing.generated_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}
            {briefing.read_at && ` · Read ${new Date(briefing.read_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`}
          </p>
        )}
      </div>

      {/* 2. AI summary (Layer 3 — only shown when generated) */}
      {payload?.readable_summary && (
        <div className="px-8 py-5 bg-teal-50 dark:bg-teal-900/20 border-b border-teal-100 dark:border-teal-800">
          <p className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-widest text-teal-600 dark:text-teal-400 mb-2">
            <Sparkles size={14} strokeWidth={2} />
            AI Summary
          </p>
          <p className="text-[16px] text-teal-900 dark:text-teal-100 leading-relaxed">
            {payload.readable_summary}
          </p>
        </div>
      )}

      {/* 3. Data limitations banner */}
      {(!patient.monitoring_active || payload?.data_limitations) && (
        <div className={`px-8 py-4 flex items-start gap-3 text-[15px]
          ${!patient.monitoring_active
            ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300 border-l-4 border-amber-400'
            : 'bg-slate-50 dark:bg-slate-800/40 text-slate-500 dark:text-slate-400'}`}
        >
          <Info size={18} strokeWidth={1.75} className="flex-shrink-0 mt-0.5" />
          <span>
            {!patient.monitoring_active
              ? 'Home monitoring not active — this briefing is based on clinic records only. No home BP trend is available.'
              : payload?.data_limitations}
          </span>
        </div>
      )}

      <div className="px-8">
        {!briefing ? (
          <div className="py-12 text-[16px] text-slate-400 italic text-center">
            No briefing available for this patient.
          </div>
        ) : (
          <>
            {/* 3. Trend summary + chart */}
            <Section icon={<Activity size={20} strokeWidth={1.75} />} title="BP Trend — Last 28 Days">
              <p className="mb-5">{payload?.trend_summary}</p>
              {readings.length > 0 && (
                <div className="mt-2 -mx-2">
                  <SparklineChart readings={readings} />
                </div>
              )}
            </Section>

            {/* 4. Medication status */}
            <Section icon={<Pill size={20} strokeWidth={1.75} />} title="Medication Status">
              <p>{payload?.medication_status}</p>
            </Section>

            {/* 5. Adherence summary */}
            <Section icon={<ClipboardList size={20} strokeWidth={1.75} />} title="Adherence Signal">
              {adherence.length > 0 ? (
                <AdherenceSummary adherence={adherence} patternText={payload?.adherence_summary} />
              ) : (
                <p className="text-slate-500 italic">{payload?.adherence_summary}</p>
              )}
            </Section>

            {/* 6. Active problems */}
            {payload?.active_problems && payload.active_problems.length > 0 && (
              <Section icon={<Flag size={20} strokeWidth={1.75} />} title="Active Problems">
                <div className="flex flex-wrap gap-2">
                  {payload.active_problems.map((problem) => (
                    <span
                      key={problem}
                      className="inline-block bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200
                                 text-[15px] font-medium px-3 py-1.5 rounded-lg"
                    >
                      {problem}
                    </span>
                  ))}
                </div>
              </Section>
            )}

            {/* 7. Overdue labs */}
            {payload?.overdue_labs && payload.overdue_labs.length > 0 && (
              <section className="py-6 border-b border-slate-100 dark:border-slate-700">
                <h2 className="flex items-center gap-2.5 text-[20px] font-semibold text-red-600 dark:text-red-400 mb-4">
                  <FlaskConical size={20} strokeWidth={1.75} className="opacity-80" />
                  Overdue Investigations
                </h2>
                <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-5 py-4 space-y-2">
                  {payload.overdue_labs.map((lab) => (
                    <p key={lab} className="text-[16px] text-red-700 dark:text-red-300 flex items-start gap-2">
                      <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-red-500 flex-shrink-0" aria-hidden />
                      {lab}
                    </p>
                  ))}
                </div>
              </section>
            )}

            {/* 8. Visit agenda */}
            <Section icon={<ClipboardList size={20} strokeWidth={1.75} />} title="Visit Agenda">
              <VisitAgenda items={payload?.visit_agenda ?? []} />
            </Section>

            {/* 9. Urgent flags */}
            {payload?.urgent_flags && payload.urgent_flags.length > 0 && (
              <section className="py-6">
                <h2 className="flex items-center gap-2.5 text-[20px] font-semibold text-amber-700 dark:text-amber-400 mb-4">
                  <AlertTriangle size={20} strokeWidth={1.75} className="opacity-80" />
                  Clinical Flags
                </h2>
                <div className="space-y-3">
                  {payload.urgent_flags.map((flag) => (
                    <div
                      key={flag}
                      className="flex items-start gap-3 rounded-lg bg-amber-50 dark:bg-amber-900/20
                                 border border-amber-200 dark:border-amber-800 px-5 py-4"
                    >
                      <AlertTriangle
                        size={18}
                        strokeWidth={1.75}
                        className="flex-shrink-0 mt-0.5 text-amber-500"
                        aria-hidden
                      />
                      <p className="text-[16px] text-amber-800 dark:text-amber-300">{flag}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}
