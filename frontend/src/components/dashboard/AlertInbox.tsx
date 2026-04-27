'use client'

import { useEffect, useState } from 'react'
import { getAlerts, acknowledgeAlert } from '@/lib/api'
import type { Alert } from '@/lib/types'
import { CheckCircle, AlertTriangle, Clock, TrendingUp } from 'lucide-react'

const ALERT_LABELS: Record<Alert['alert_type'], string> = {
  gap_urgent: 'Urgent reading gap',
  gap_briefing: 'Reading gap — review at next briefing',
  inertia: 'Possible therapeutic inertia — no medication change despite sustained elevated readings',
  deterioration: 'Possible sustained worsening trend',
  adherence: 'Possible adherence concern',
}

function timeAgo(isoTimestamp: string): string {
  const diff = Date.now() - new Date(isoTimestamp).getTime()
  const hours = Math.floor(diff / 3_600_000)
  const days = Math.floor(hours / 24)
  if (days > 0) return `${days} day${days !== 1 ? 's' : ''} ago`
  if (hours > 0) return `${hours} hour${hours !== 1 ? 's' : ''} ago`
  const mins = Math.floor(diff / 60_000)
  return `${mins} minute${mins !== 1 ? 's' : ''} ago`
}

export default function AlertInbox() {
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAlerts().then((data) => {
      setAlerts(data.filter((a) => !a.acknowledged_at))
      setLoading(false)
    })
  }, [])

  function handleAcknowledge(alertId: string) {
    acknowledgeAlert(alertId).catch(() => {})
    setAlerts((prev) => prev.filter((a) => a.alert_id !== alertId))
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-slate-400 text-[16px]">
        <div className="h-4 w-4 rounded-full border-2 border-teal-600 border-t-transparent animate-spin" />
        Loading alerts&hellip;
      </div>
    )
  }

  if (alerts.length === 0) {
    return (
      <div className="card p-8 flex items-center gap-4 text-green-700 dark:text-green-400">
        <CheckCircle size={24} strokeWidth={1.75} />
        <span className="text-[17px] font-medium">
          No urgent alerts — all patients stable.
        </span>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {alerts.map((alert) => (
        <div
          key={alert.alert_id}
          className={`card px-6 py-5 flex items-start gap-5 ${
            alert.escalated ? 'border-l-4 border-red-500' : ''
          }`}
        >
          <AlertTriangle
            size={22}
            strokeWidth={1.75}
            className={`flex-shrink-0 mt-0.5 ${alert.escalated ? 'text-red-500' : 'text-amber-500'}`}
            aria-hidden
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-[17px] font-semibold text-slate-900 dark:text-slate-100">
                    Patient {alert.patient_id}
                  </p>
                  {alert.escalated && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[12px] font-medium bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300">
                      <TrendingUp size={11} /> Escalated
                    </span>
                  )}
                  {alert.off_hours && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[12px] font-medium bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300">
                      <Clock size={11} /> Off-hours
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[16px] text-slate-600 dark:text-slate-300 leading-snug">
                  {ALERT_LABELS[alert.alert_type]}
                </p>
                {alert.systolic_avg !== null && (
                  <p className="mt-1 text-[15px] text-slate-400 dark:text-slate-500">
                    Avg systolic: {alert.systolic_avg.toFixed(1)} mmHg
                  </p>
                )}
                {alert.gap_days !== null && (
                  <p className="mt-1 text-[15px] text-slate-400 dark:text-slate-500">
                    Gap: {alert.gap_days} day{alert.gap_days !== 1 ? 's' : ''}
                  </p>
                )}
                <p className="mt-1 text-[14px] text-slate-400 dark:text-slate-500">
                  {timeAgo(alert.triggered_at)}
                </p>
              </div>
              <button
                onClick={() => handleAcknowledge(alert.alert_id)}
                aria-label={`Acknowledge alert for patient ${alert.patient_id}`}
                className="flex-shrink-0 min-h-[48px] min-w-[120px] px-5
                           bg-slate-100 hover:bg-slate-200 dark:bg-slate-700 dark:hover:bg-slate-600
                           text-slate-700 dark:text-slate-200 text-[15px] font-medium
                           rounded-lg transition-colors duration-150"
              >
                Acknowledge
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
