'use client'

import { useState } from 'react'
import { triggerScheduler } from '@/lib/api'
import { CalendarClock } from 'lucide-react'

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
      setError('Failed to trigger scheduler. Check that the backend is running.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-8 max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
          Admin — Demo Controls
        </h1>
        <p className="mt-1 text-[16px] text-slate-500 dark:text-slate-400">
          Manual triggers for demonstration and testing purposes only.
        </p>
      </div>

      <div className="card p-8">
        <div className="flex items-start gap-4">
          <div className="flex-shrink-0 h-12 w-12 rounded-xl bg-teal-50 dark:bg-teal-900/30
                          flex items-center justify-center text-teal-700 dark:text-teal-400">
            <CalendarClock size={24} strokeWidth={1.75} />
          </div>
          <div className="flex-1">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              Generate Today&rsquo;s Briefings
            </h2>
            <p className="mt-1 text-[15px] text-slate-500 dark:text-slate-400 leading-relaxed">
              Runs the 7:30 AM scheduler immediately. Enqueues briefing generation jobs
              for all patients with appointments today.
            </p>

            <button
              onClick={handleTrigger}
              disabled={loading}
              className="btn-primary mt-5 inline-flex items-center gap-2 min-h-[48px]"
            >
              {loading ? (
                <>
                  <div className="h-4 w-4 rounded-full border-2 border-white border-t-transparent animate-spin" />
                  Generating briefings&hellip;
                </>
              ) : (
                'Generate Today\'s Briefings'
              )}
            </button>

            {result && (
              <div className="mt-5 rounded-lg bg-teal-50 dark:bg-teal-900/20 border border-teal-200
                              dark:border-teal-800 px-5 py-4 text-[16px] text-teal-800 dark:text-teal-300
                              font-medium">
                ✓ {result.enqueued} briefing{result.enqueued !== 1 ? 's' : ''} enqueued for today
              </div>
            )}

            {error && (
              <div className="mt-5 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200
                              dark:border-red-800 px-5 py-4 text-[16px] text-red-700 dark:text-red-400">
                {error}
              </div>
            )}
          </div>
        </div>
      </div>

      <p className="mt-6 text-[14px] text-slate-400 dark:text-slate-500">
        This control is for demonstration only. In production, the scheduler runs automatically at 07:30 UTC daily.
      </p>
    </div>
  )
}
