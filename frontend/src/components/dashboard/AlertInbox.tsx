'use client'

import { useEffect, useState } from 'react'
import { getAlerts, getAcknowledgedAlerts, acknowledgeAlert, unacknowledgeAlert } from '@/lib/api'
import type { Alert } from '@/lib/types'
import { CheckCircle, AlertTriangle, Clock, TrendingUp, ShieldAlert, RotateCcw, ChevronDown, ChevronUp } from 'lucide-react'

const UNDO_WINDOW_MS = 24 * 60 * 60 * 1000 // 24 hours

const ALERT_LABELS: Record<Alert['alert_type'], string> = {
  gap_urgent:   'Urgent reading gap — no home BP received',
  gap_briefing: 'Reading gap — review at next appointment',
  inertia:      'Possible therapeutic inertia — elevated BP with no medication change',
  deterioration:'Possible sustained BP worsening trend',
  adherence:    'Possible adherence concern flagged',
}

const ALERT_COLORS: Record<Alert['alert_type'], { border: string; icon: string; bg: string }> = {
  gap_urgent:    { border: 'border-l-red-500',   icon: 'text-red-500',   bg: 'bg-red-50 dark:bg-red-900/10' },
  gap_briefing:  { border: 'border-l-amber-400', icon: 'text-amber-500', bg: '' },
  inertia:       { border: 'border-l-amber-400', icon: 'text-amber-500', bg: '' },
  deterioration: { border: 'border-l-red-500',   icon: 'text-red-500',   bg: 'bg-red-50 dark:bg-red-900/10' },
  adherence:     { border: 'border-l-amber-400', icon: 'text-amber-500', bg: '' },
}

function timeAgo(isoTimestamp: string): string {
  const diff = Date.now() - new Date(isoTimestamp).getTime()
  const hours = Math.floor(diff / 3_600_000)
  const days = Math.floor(hours / 24)
  if (days > 0) return `${days}d ago`
  if (hours > 0) return `${hours}h ago`
  const mins = Math.floor(diff / 60_000)
  return `${mins}m ago`
}

function formatAcknowledgedAt(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function isUndoable(acknowledgedAt: string | null): boolean {
  if (!acknowledgedAt) return false
  return Date.now() - new Date(acknowledgedAt).getTime() < UNDO_WINDOW_MS
}

export default function AlertInbox() {
  const [active, setActive] = useState<Alert[]>([])
  const [acknowledged, setAcknowledged] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [showAcknowledged, setShowAcknowledged] = useState(true)

  useEffect(() => {
    Promise.all([getAlerts(), getAcknowledgedAlerts()]).then(([activeData, ackData]) => {
      setActive(activeData.filter((a) => !a.acknowledged_at))
      setAcknowledged(ackData)
      setLoading(false)
    })
  }, [])

  function handleAcknowledge(alert: Alert) {
    const now = new Date().toISOString()
    acknowledgeAlert(alert.alert_id).catch(() => {})
    setActive((prev) => prev.filter((a) => a.alert_id !== alert.alert_id))
    setAcknowledged((prev) => [{ ...alert, acknowledged_at: now }, ...prev])
  }

  function handleUndo(alertId: string) {
    unacknowledgeAlert(alertId).catch(() => {})
    const restored = acknowledged.find((a) => a.alert_id === alertId)
    setAcknowledged((prev) => prev.filter((a) => a.alert_id !== alertId))
    if (restored) {
      setActive((prev) => [{ ...restored, acknowledged_at: null }, ...prev])
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-gray-400 text-[15px] py-8">
        <div className="h-4 w-4 rounded-full border-2 border-blue-600 border-t-transparent animate-spin" />
        Loading alerts…
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* ── Active alerts ─────────────────────────────────────────────── */}
      {active.length === 0 ? (
        <div className="card p-10 flex flex-col items-center gap-3 text-center">
          <CheckCircle size={32} strokeWidth={1.5} className="text-green-500" />
          <div>
            <p className="text-[16px] font-semibold text-gray-900 dark:text-gray-100">All clear</p>
            <p className="text-[14px] text-gray-400 mt-0.5">No unacknowledged alerts at this time.</p>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {active.map((alert) => {
            const colors = ALERT_COLORS[alert.alert_type]
            return (
              <div
                key={alert.alert_id}
                className={`card px-6 py-5 flex items-start gap-4 border-l-4 ${colors.border} ${colors.bg}`}
              >
                <AlertTriangle size={20} strokeWidth={1.75} className={`flex-shrink-0 mt-0.5 ${colors.icon}`} aria-hidden />
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div>
                      <div className="flex items-center gap-2 flex-wrap mb-1">
                        <p className="text-[15px] font-bold text-gray-900 dark:text-gray-100">
                          {alert.patient_name ?? `Patient ${alert.patient_id}`}
                        </p>
                        {alert.escalated && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold
                                           bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300">
                            <ShieldAlert size={10} /> Escalated
                          </span>
                        )}
                        {alert.off_hours && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold
                                           bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400">
                            <Clock size={10} /> Off-hours
                          </span>
                        )}
                        <span className="text-[12px] text-gray-400">{timeAgo(alert.triggered_at)}</span>
                      </div>
                      <p className="text-[14px] text-gray-600 dark:text-gray-300 leading-snug">
                        {ALERT_LABELS[alert.alert_type]}
                      </p>
                      <div className="flex items-center gap-4 mt-2">
                        {alert.systolic_avg !== null && (
                          <span className="flex items-center gap-1 text-[13px] text-gray-500 dark:text-gray-400">
                            <TrendingUp size={13} strokeWidth={2} />
                            Avg systolic: <strong className="text-gray-800 dark:text-gray-200 ml-0.5">{alert.systolic_avg.toFixed(0)} mmHg</strong>
                          </span>
                        )}
                        {alert.gap_days !== null && (
                          <span className="text-[13px] text-gray-500 dark:text-gray-400">
                            Gap: <strong className="text-gray-800 dark:text-gray-200">{alert.gap_days}d</strong>
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={() => handleAcknowledge(alert)}
                      aria-label={`Acknowledge alert for patient ${alert.patient_id}`}
                      className="flex-shrink-0 px-4 py-2 rounded-lg text-[13px] font-semibold
                                 bg-white dark:bg-[#1F2937]
                                 border border-gray-200 dark:border-[#374151]
                                 text-gray-700 dark:text-gray-300
                                 hover:bg-gray-50 dark:hover:bg-[#374151]
                                 transition-colors duration-150"
                    >
                      Acknowledge
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Acknowledged history ───────────────────────────────────────── */}
      {acknowledged.length > 0 && (
        <div>
          <button
            onClick={() => setShowAcknowledged((v) => !v)}
            className="flex items-center gap-2 text-[13px] font-semibold text-gray-500 dark:text-gray-400
                       hover:text-gray-700 dark:hover:text-gray-200 transition-colors mb-3"
          >
            {showAcknowledged ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            Acknowledged ({acknowledged.length} in last 7 days)
          </button>

          {showAcknowledged && (
            <div className="space-y-2">
              {acknowledged.map((alert) => {
                const colors = ALERT_COLORS[alert.alert_type]
                const undoable = isUndoable(alert.acknowledged_at)
                return (
                  <div
                    key={alert.alert_id}
                    className={`card px-6 py-4 flex items-start gap-4 border-l-4 opacity-60
                                ${colors.border}`}
                  >
                    <CheckCircle size={18} strokeWidth={1.75} className="flex-shrink-0 mt-0.5 text-green-500" aria-hidden />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-4 flex-wrap">
                        <div>
                          <div className="flex items-center gap-2 flex-wrap mb-0.5">
                            <p className="text-[14px] font-semibold text-gray-700 dark:text-gray-300">
                              {alert.patient_name ?? `Patient ${alert.patient_id}`}
                            </p>
                            <span className="text-[12px] text-gray-400">
                              Acknowledged {alert.acknowledged_at ? formatAcknowledgedAt(alert.acknowledged_at) : '—'}
                            </span>
                          </div>
                          <p className="text-[13px] text-gray-500 dark:text-gray-400 leading-snug">
                            {ALERT_LABELS[alert.alert_type]}
                          </p>
                          <div className="flex items-center gap-4 mt-1.5">
                            {alert.systolic_avg !== null && (
                              <span className="text-[12px] text-gray-400">
                                Avg systolic: <strong className="text-gray-600 dark:text-gray-300">{alert.systolic_avg.toFixed(0)} mmHg</strong>
                              </span>
                            )}
                            {alert.gap_days !== null && (
                              <span className="text-[12px] text-gray-400">
                                Gap: <strong className="text-gray-600 dark:text-gray-300">{alert.gap_days}d</strong>
                              </span>
                            )}
                          </div>
                        </div>
                        {undoable && (
                          <button
                            onClick={() => handleUndo(alert.alert_id)}
                            aria-label={`Undo acknowledgement for patient ${alert.patient_id}`}
                            className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                                       text-[12px] font-semibold
                                       bg-white dark:bg-[#1F2937]
                                       border border-gray-200 dark:border-[#374151]
                                       text-gray-600 dark:text-gray-400
                                       hover:bg-gray-50 dark:hover:bg-[#374151]
                                       transition-colors duration-150"
                          >
                            <RotateCcw size={12} strokeWidth={2} />
                            Undo
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
