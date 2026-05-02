'use client'

import type { Patient, Briefing, Reading, AdherenceData } from '@/lib/types'
import RiskTierBadge from '@/components/dashboard/RiskTierBadge'
import RiskScoreBar from '@/components/dashboard/RiskScoreBar'
import SparklineChart from './SparklineChart'
import AdherenceSummary from './AdherenceSummary'
import VisitAgenda from './VisitAgenda'
import {
  AlertTriangle, Info, FlaskConical, Pill, Activity,
  ClipboardList, Flag, Sparkles, Calendar,
} from 'lucide-react'
import { isHypertensionMedication, filterMedicationStatusText } from '@/lib/hypertension-meds'

interface BriefingCardProps {
  patient: Patient
  briefing: Briefing | null
  readings: Reading[]
  adherence: AdherenceData[]
}

function Section({
  icon,
  title,
  accent = false,
  tourId,
  children,
}: {
  icon: React.ReactNode
  title: string
  accent?: boolean
  tourId?: string
  children: React.ReactNode
}) {
  return (
    <section
      className="py-4 border-b border-gray-100 dark:border-[#1F2937] last:border-0"
      {...(tourId ? { 'data-tour': tourId } : {})}
    >
      <h2 className={`flex items-center gap-2 text-[13px] font-semibold uppercase tracking-widest mb-4
                      ${accent ? 'text-blue-600 dark:text-blue-400' : 'text-gray-400 dark:text-gray-500'}`}>
        <span>{icon}</span>
        {title}
      </h2>
      <div className="text-[15px] text-gray-700 dark:text-gray-300 leading-relaxed">
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
  const hypertensionAdherence = adherence.filter((a) => isHypertensionMedication(a.medication_name))

  return (
    <div className="card overflow-hidden">
      {/* Patient header */}
      <div data-tour="briefing-header" className="px-6 py-5 border-b border-gray-100 dark:border-[#1F2937]
                      bg-gray-50 dark:bg-[#0B1220]">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 mb-1">
              Pre-Visit Clinical Briefing
            </p>
            <h1 className="text-[24px] font-bold text-gray-900 dark:text-gray-100">
              {patient.name ?? `Patient ${patient.patient_id}`}
            </h1>
            <p className="text-[14px] text-gray-500 dark:text-gray-400 mt-1">
              {patient.gender === 'M' ? 'Male' : patient.gender === 'F' ? 'Female' : 'Unknown'}, {patient.age} years
              {patient.tier_override && (
                <span className="ml-2 text-amber-600 dark:text-amber-400">
                  · {patient.tier_override}
                </span>
              )}
            </p>
            {patient.next_appointment && (
              <p className="flex items-center gap-1.5 text-[14px] text-blue-600 dark:text-blue-400 font-medium mt-2">
                <Calendar size={14} strokeWidth={2} />
                {formatApptDate(patient.next_appointment)} at {formatApptTime(patient.next_appointment)}
              </p>
            )}
            {briefing && (
              <p className="mt-2 text-[12px] text-gray-400">
                Generated {new Date(briefing.generated_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}
                {briefing.read_at && ` · Read ${new Date(briefing.read_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`}
              </p>
            )}
          </div>
          <div className="flex flex-col items-end gap-3">
            <RiskTierBadge tier={patient.risk_tier} size="lg" />
            <div className="w-48">
              <p className="text-[11px] uppercase tracking-widest text-gray-400 mb-1.5">Priority Score</p>
              <RiskScoreBar score={patient.risk_score} tier={patient.risk_tier} />
            </div>
          </div>
        </div>
      </div>

      {/* AI Summary (Layer 3) */}
      {payload?.readable_summary && (
        <div data-tour="briefing-ai-summary" className="px-6 py-4 bg-blue-50 dark:bg-blue-900/10 border-b border-blue-100 dark:border-blue-900/30">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-widest text-blue-600 dark:text-blue-400 mb-2">
            <Sparkles size={12} strokeWidth={2} />
            AI Summary
          </p>
          <p className="text-[15px] text-blue-900 dark:text-blue-100 leading-relaxed">
            {payload.readable_summary}
          </p>
        </div>
      )}

      {/* Data limitations banner */}
      {(!patient.monitoring_active || payload?.data_limitations) && (
        <div className={`px-6 py-3 flex items-start gap-3 text-[14px] border-b
          ${!patient.monitoring_active
            ? 'bg-amber-50 dark:bg-amber-900/10 text-amber-800 dark:text-amber-300 border-amber-100 dark:border-amber-900/30 border-l-4 border-l-amber-400'
            : 'bg-gray-50 dark:bg-[#0B1220] text-gray-500 dark:text-gray-400 border-gray-100 dark:border-[#1F2937]'}`}
        >
          <Info size={16} strokeWidth={1.75} className="flex-shrink-0 mt-0.5" />
          <span>
            {!patient.monitoring_active
              ? 'Home monitoring not active — this briefing is based on clinic records only.'
              : payload?.data_limitations}
          </span>
        </div>
      )}

      <div className="px-6">
        {!briefing ? (
          <div className="py-12 text-[15px] text-gray-400 italic text-center">
            No briefing available for this patient. Trigger the scheduler from the Admin page.
          </div>
        ) : (
          <>
            {/* BP Trend + chart */}
            <Section icon={<Activity size={14} strokeWidth={2} />} title="BP Trend" accent tourId="briefing-bp-trend">
              <p className="mb-5">{payload?.trend_summary}</p>
              {readings.length > 0 && (
                <div className="-mx-2">
                  <SparklineChart readings={readings} />
                </div>
              )}
            </Section>

            {/* Medication */}
            <Section icon={<Pill size={14} strokeWidth={2} />} title="Medication Status" tourId="briefing-medication">
              <p>{filterMedicationStatusText(payload?.medication_status)}</p>
            </Section>

            {/* Adherence */}
            <Section icon={<ClipboardList size={14} strokeWidth={2} />} title="Adherence Signal" tourId="briefing-adherence">
              {hypertensionAdherence.length > 0 ? (
                <AdherenceSummary adherence={hypertensionAdherence} />
              ) : adherence.length > 0 ? (
                <p className="text-gray-500 italic">No antihypertensive medications found in adherence data.</p>
              ) : (
                <p className="text-gray-500 italic">{payload?.adherence_summary}</p>
              )}
            </Section>

            {/* Active problems */}
            {payload?.active_problems && payload.active_problems.length > 0 && (
              <Section icon={<Flag size={14} strokeWidth={2} />} title="Active Problems" tourId="briefing-problems">
                <div className="flex flex-wrap gap-1.5">
                  {payload.active_problems.map((problem) => (
                    <span
                      key={problem}
                      className="inline-block text-[12px] font-medium px-2.5 py-1 rounded-md
                                 bg-gray-100 dark:bg-[#1F2937]
                                 text-gray-600 dark:text-gray-400
                                 border border-gray-200 dark:border-[#374151]"
                    >
                      {problem}
                    </span>
                  ))}
                </div>
              </Section>
            )}

            {/* Overdue labs */}
            {payload?.overdue_labs && payload.overdue_labs.length > 0 && (
              <section data-tour="briefing-overdue-labs" className="py-6 border-b border-gray-100 dark:border-[#1F2937]">
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-widest text-red-600 dark:text-red-400 mb-4">
                  <FlaskConical size={14} strokeWidth={2} />
                  Overdue Investigations
                </h2>
                <div className="rounded-xl bg-red-50 dark:bg-red-900/10 border border-red-100 dark:border-red-900/30 px-5 py-4 space-y-2.5">
                  {payload.overdue_labs.map((lab) => (
                    <p key={lab} className="text-[14px] text-red-700 dark:text-red-300 flex items-start gap-2.5">
                      <span className="mt-2 h-1.5 w-1.5 rounded-full bg-red-500 flex-shrink-0" aria-hidden />
                      {lab}
                    </p>
                  ))}
                </div>
              </section>
            )}

            {/* Medication Safety — drug interactions */}
            {payload?.drug_interactions && payload.drug_interactions.length > 0 && (
              <section className="py-6 border-b border-gray-100 dark:border-[#1F2937]">
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-widest text-amber-600 dark:text-amber-400 mb-4">
                  <AlertTriangle size={14} strokeWidth={2} />
                  Medication Safety
                </h2>
                <div className="space-y-3">
                  {payload.drug_interactions.map((ix) => (
                    <div
                      key={ix.rule}
                      className={`rounded-xl border px-5 py-4 flex items-start gap-3
                        ${ix.severity === 'critical'
                          ? 'bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-900/30'
                          : ix.severity === 'concern'
                          ? 'bg-amber-50 dark:bg-amber-900/10 border-amber-100 dark:border-amber-900/30'
                          : 'bg-yellow-50 dark:bg-yellow-900/10 border-yellow-100 dark:border-yellow-900/30'}`}
                    >
                      <AlertTriangle
                        size={16}
                        strokeWidth={1.75}
                        aria-hidden
                        className={`flex-shrink-0 mt-0.5
                          ${ix.severity === 'critical' ? 'text-red-600'
                            : ix.severity === 'concern' ? 'text-amber-500'
                            : 'text-yellow-500'}`}
                      />
                      <div>
                        <p className={`text-[13px] font-semibold mb-1
                          ${ix.severity === 'critical' ? 'text-red-700 dark:text-red-300'
                            : ix.severity === 'concern' ? 'text-amber-800 dark:text-amber-300'
                            : 'text-yellow-800 dark:text-yellow-200'}`}>
                          {ix.severity.toUpperCase()} — {ix.rule.replace(/_/g, ' ')}
                        </p>
                        <p className={`text-[14px] leading-relaxed
                          ${ix.severity === 'critical' ? 'text-red-700 dark:text-red-300'
                            : ix.severity === 'concern' ? 'text-amber-800 dark:text-amber-300'
                            : 'text-yellow-800 dark:text-yellow-200'}`}>
                          {ix.description}
                        </p>
                        <p className="text-[12px] text-gray-500 dark:text-gray-400 mt-1">
                          Drugs involved: {ix.drugs_involved.join(', ')}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Visit agenda */}
            <Section icon={<ClipboardList size={14} strokeWidth={2} />} title="Visit Agenda" accent tourId="briefing-agenda">
              <VisitAgenda items={payload?.visit_agenda ?? []} />
            </Section>

            {/* Urgent flags */}
            {payload?.urgent_flags && payload.urgent_flags.length > 0 && (
              <section className="py-6">
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-widest text-amber-600 dark:text-amber-400 mb-4">
                  <AlertTriangle size={14} strokeWidth={2} />
                  Clinical Flags
                </h2>
                <div className="space-y-3">
                  {payload.urgent_flags.map((flag) => (
                    <div
                      key={flag}
                      className="flex items-start gap-3 rounded-xl
                                 bg-amber-50 dark:bg-amber-900/10
                                 border border-amber-100 dark:border-amber-900/30
                                 px-5 py-4"
                    >
                      <AlertTriangle size={16} strokeWidth={1.75} className="flex-shrink-0 mt-0.5 text-amber-500" aria-hidden />
                      <p className="text-[14px] text-amber-800 dark:text-amber-300 leading-relaxed">{flag}</p>
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
