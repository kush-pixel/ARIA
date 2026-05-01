'use client'

import { useState } from 'react'
import { triggerScheduler } from '@/lib/api'
import { CalendarClock, CheckCircle, AlertCircle, Settings } from 'lucide-react'

export default function AdminPage() {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ enqueued: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleTrigger() {
    setLoading(true)
    setResult(null)
    setError(null)
    try {
      const res = await triggerScheduler()
      setResult(res)
    } catch {
      setError('Failed to trigger scheduler. Check that the backend is running and DEMO_MODE=true.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-5 max-w-2xl">
      <div className="flex items-center gap-2.5 mb-1">
        <Settings size={20} strokeWidth={2} className="text-blue-600" />
        <h1 className="text-[22px] font-bold text-gray-900 dark:text-gray-100">Admin</h1>
      </div>
      <p className="text-[14px] text-gray-500 dark:text-gray-400 mb-8">
        Manual controls for demonstration and testing. Production scheduler runs automatically at 07:30 UTC.
      </p>

      <div className="card p-6">
        <div className="flex items-start gap-4">
          <div className="flex-shrink-0 h-11 w-11 rounded-xl bg-blue-50 dark:bg-blue-900/20
                          flex items-center justify-center text-blue-600 dark:text-blue-400">
            <CalendarClock size={22} strokeWidth={1.75} />
          </div>
          <div className="flex-1">
            <h2 className="text-[16px] font-bold text-gray-900 dark:text-gray-100">
              Generate Today&rsquo;s Briefings
            </h2>
            <p className="mt-1 text-[14px] text-gray-500 dark:text-gray-400 leading-relaxed">
              Runs the 7:30 AM scheduler immediately. Enqueues briefing generation jobs
              for all monitoring-active patients with appointments today.
            </p>

            <button
              onClick={handleTrigger}
              disabled={loading}
              className="btn-primary mt-5 inline-flex items-center gap-2"
            >
              {loading ? (
                <>
                  <div className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
                  Generating…
                </>
              ) : (
                'Generate Briefings'
              )}
            </button>

            {result !== null && (
              <div className="mt-4 flex items-center gap-2.5 rounded-xl
                              bg-green-50 dark:bg-green-900/10
                              border border-green-100 dark:border-green-900/30
                              px-5 py-3.5">
                <CheckCircle size={16} strokeWidth={2} className="text-green-600 flex-shrink-0" />
                <p className="text-[14px] font-semibold text-green-800 dark:text-green-300">
                  {result.enqueued} briefing{result.enqueued !== 1 ? 's' : ''} enqueued for today
                </p>
              </div>
            )}

            {error && (
              <div className="mt-4 flex items-center gap-2.5 rounded-xl
                              bg-red-50 dark:bg-red-900/10
                              border border-red-100 dark:border-red-900/30
                              px-5 py-3.5">
                <AlertCircle size={16} strokeWidth={2} className="text-red-600 flex-shrink-0" />
                <p className="text-[14px] text-red-700 dark:text-red-400">{error}</p>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="mt-6 rounded-xl bg-gray-50 dark:bg-[#111827] border border-gray-100 dark:border-[#1F2937] px-5 py-4">
        <p className="text-[12px] font-semibold uppercase tracking-widest text-gray-400 mb-2">
          Prerequisite
        </p>
        <p className="text-[13px] text-gray-500 dark:text-gray-400 leading-relaxed">
          Patient must have <code className="bg-gray-100 dark:bg-[#1F2937] px-1.5 py-0.5 rounded text-[12px]">next_appointment</code> set
          to today and <code className="bg-gray-100 dark:bg-[#1F2937] px-1.5 py-0.5 rounded text-[12px]">monitoring_active = true</code>.
          Set via Supabase SQL editor: <br />
          <code className="block mt-1.5 bg-gray-100 dark:bg-[#1F2937] px-3 py-2 rounded text-[12px] text-gray-700 dark:text-gray-300">
            UPDATE patients SET next_appointment = NOW() + interval &apos;2 hours&apos; WHERE patient_id = &apos;1091&apos;;
          </code>
        </p>
      </div>
    </div>
  )
}
