'use client'

import { useEffect, useState } from 'react'
import { X, ChevronRight, ChevronLeft, Users, FileText, MessageSquare, BellRing, BarChart2, GitBranch } from 'lucide-react'

const STORAGE_KEY = 'aria_walkthrough_done'

interface Step {
  icon: React.ReactNode
  title: string
  description: string
  detail: string
}

const STEPS: Step[] = [
  {
    icon: <Users size={22} strokeWidth={1.75} className="text-blue-600 dark:text-blue-400" />,
    title: 'Patient Panel',
    description: 'Your daily patient list, sorted by urgency.',
    detail: 'Patients are ranked by Chronic Risk tier first (High → Medium → Low), then by Priority Score within each tier. Use the filter tabs to focus on a specific risk group. Click any row to open that patient\'s full briefing.',
  },
  {
    icon: <BarChart2 size={22} strokeWidth={1.75} className="text-blue-600 dark:text-blue-400" />,
    title: 'Chronic Risk vs Priority Score',
    description: 'These two columns measure different things and are not expected to match.',
    detail: 'Chronic Risk is set by diagnosis: CHF, Stroke, or TIA permanently sets a patient to High regardless of current readings. Priority Score (0–100) is today\'s urgency score: how much attention the patient needs right now based on BP trends, adherence, medication history, and monitoring gaps. A High Risk patient with a low score is stable and well-managed.',
  },
  {
    icon: <FileText size={22} strokeWidth={1.75} className="text-blue-600 dark:text-blue-400" />,
    title: 'Pre-Visit Briefing',
    description: 'A structured summary generated before each appointment.',
    detail: 'Open any patient to see their briefing: BP trend chart, medication status, adherence per drug, active problems, overdue labs, and the visit agenda. The briefing is generated at 7:30 AM on appointment days. If no briefing appears, trigger it from the Admin page.',
  },
  {
    icon: <MessageSquare size={22} strokeWidth={1.75} className="text-blue-600 dark:text-blue-400" />,
    title: 'Ask ARIA (Chatbot)',
    description: 'Ask clinical questions about the patient in plain language.',
    detail: 'Inside a patient\'s briefing, use the chat panel on the right. Ask things like "Why was this patient flagged?", "What changed in the last 14 days?", or "Which medication had the most missed doses?" ARIA answers using real patient data. It will not recommend medications or make diagnostic decisions. That is your role.',
  },
  {
    icon: <BellRing size={22} strokeWidth={1.75} className="text-blue-600 dark:text-blue-400" />,
    title: 'Alerts',
    description: 'Active clinical flags that need your attention.',
    detail: 'Click the bell icon in the top bar to see all unacknowledged alerts. Alert types include: monitoring gaps (patient stopped recording BP), therapeutic inertia (BP elevated with no recent medication change), deterioration (readings worsening), and adherence concerns. Acknowledge alerts after reviewing them.',
  },
  {
    icon: <GitBranch size={22} strokeWidth={1.75} className="text-blue-600 dark:text-blue-400" />,
    title: 'Shadow Mode',
    description: 'See how ARIA performs against historical clinic decisions.',
    detail: 'Shadow Mode replays past visits and shows what ARIA would have flagged versus what the physician recorded. Use it to understand ARIA\'s detection logic and validate its accuracy against your own clinical judgement. This is a validation tool. It does not affect live patient data.',
  },
]

export default function WalkthroughModal() {
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (typeof window !== 'undefined' && !localStorage.getItem(STORAGE_KEY)) {
      setOpen(true)
    }
  }, [])

  function dismiss() {
    localStorage.setItem(STORAGE_KEY, '1')
    setOpen(false)
  }

  if (!open) return null

  const current = STEPS[step]
  const isFirst = step === 0
  const isLast  = step === STEPS.length - 1

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="relative w-full max-w-lg mx-4 bg-white dark:bg-[#111827]
                      rounded-2xl shadow-2xl border border-gray-100 dark:border-[#1F2937]
                      overflow-hidden">

        {/* Progress bar */}
        <div className="h-1 bg-gray-100 dark:bg-[#1F2937]">
          <div
            className="h-full bg-blue-600 transition-all duration-300 ease-out"
            style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
          />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-0">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">
            Step {step + 1} of {STEPS.length}
          </p>
          <button
            onClick={dismiss}
            className="w-7 h-7 flex items-center justify-center rounded-lg
                       text-gray-400 hover:text-gray-600 dark:hover:text-gray-300
                       hover:bg-gray-100 dark:hover:bg-[#1F2937] transition-colors"
            aria-label="Close walkthrough"
          >
            <X size={15} strokeWidth={2} />
          </button>
        </div>

        {/* Content */}
        <div className="px-6 py-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-xl bg-blue-50 dark:bg-blue-900/20
                            flex items-center justify-center flex-shrink-0">
              {current.icon}
            </div>
            <div>
              <h2 className="text-[17px] font-semibold text-gray-900 dark:text-gray-100 leading-tight">
                {current.title}
              </h2>
              <p className="text-[13px] text-gray-500 dark:text-gray-400 mt-0.5">
                {current.description}
              </p>
            </div>
          </div>
          <p className="text-[14px] text-gray-600 dark:text-gray-300 leading-relaxed">
            {current.detail}
          </p>
        </div>

        {/* Step dots */}
        <div className="flex items-center justify-center gap-1.5 pb-2">
          {STEPS.map((_, i) => (
            <button
              key={i}
              onClick={() => setStep(i)}
              className={`rounded-full transition-all duration-200 ${
                i === step
                  ? 'w-4 h-1.5 bg-blue-600'
                  : 'w-1.5 h-1.5 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300'
              }`}
              aria-label={`Go to step ${i + 1}`}
            />
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4
                        border-t border-gray-100 dark:border-[#1F2937]">
          <button
            onClick={() => setStep((s) => s - 1)}
            disabled={isFirst}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium
                       text-gray-500 dark:text-gray-400
                       hover:bg-gray-100 dark:hover:bg-[#1F2937]
                       disabled:opacity-0 transition-colors"
          >
            <ChevronLeft size={15} strokeWidth={2} />
            Back
          </button>

          {isLast ? (
            <button
              onClick={dismiss}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[13px] font-semibold
                         bg-blue-600 text-white hover:bg-blue-700 transition-colors"
            >
              Get started
            </button>
          ) : (
            <button
              onClick={() => setStep((s) => s + 1)}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-[13px] font-semibold
                         bg-blue-600 text-white hover:bg-blue-700 transition-colors"
            >
              Next
              <ChevronRight size={15} strokeWidth={2} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
