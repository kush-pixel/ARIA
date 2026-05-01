'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getPendingConfirmations, confirmDoses, downloadIcs } from '@/lib/api'
import type { PendingConfirmation } from '@/lib/api'
import { getPatientId, isTokenValid } from '@/lib/auth'

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function isIOS(): boolean {
  if (typeof navigator === 'undefined') return false
  return /iPad|iPhone|iPod/.test(navigator.userAgent)
}

export default function ConfirmPage() {
  const router = useRouter()
  const [patientId, setPatientId] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingConfirmation[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [icsLoading, setIcsLoading] = useState(false)
  const [icsError, setIcsError] = useState('')
  const [error, setError] = useState('')

  const loadPending = useCallback(async (pid: string) => {
    setLoading(true)
    setError('')
    try {
      const rows = await getPendingConfirmations(pid)
      setPending(rows)
      setSelected(new Set(rows.map(r => r.confirmation_id)))
    } catch {
      setError('Could not load your medications. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!isTokenValid()) { router.replace('/'); return }
    const pid = getPatientId()
    setPatientId(pid)
    if (pid) loadPending(pid)
  }, [router, loadPending])

  function toggleAll() {
    if (selected.size === pending.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(pending.map(r => r.confirmation_id)))
    }
  }

  function toggleOne(id: string) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleConfirm() {
    if (!patientId || selected.size === 0) return
    setConfirming(true)
    setError('')
    try {
      await confirmDoses(patientId, Array.from(selected))
      setConfirmed(true)
      setPending(prev => prev.filter(r => !selected.has(r.confirmation_id)))
      setSelected(new Set())
    } catch {
      setError('Confirmation failed. Please try again.')
    } finally {
      setConfirming(false)
    }
  }

  async function handleDownloadIcs() {
    if (!patientId) return
    setIcsLoading(true)
    setIcsError('')
    try {
      await downloadIcs(patientId)
    } catch {
      setIcsError('Download failed. Please try again.')
    } finally {
      setIcsLoading(false)
    }
  }

  return (
    <main className="min-h-screen px-4 py-6 max-w-md mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold">Medications</h1>
        <button onClick={() => router.push('/submit')}
          className="text-sm text-blue-700 font-medium border border-blue-700 rounded-lg px-3 py-1">
          + BP reading
        </button>
      </div>

      {/* Success banner */}
      {confirmed && (
        <div className="bg-green-50 border border-green-300 rounded-2xl p-4 mb-4 text-green-800 text-sm font-medium">
          Medications confirmed. Well done!
        </div>
      )}

      {/* Pending list */}
      <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Today&apos;s doses</h2>
          {pending.length > 1 && (
            <button onClick={toggleAll} className="text-xs text-blue-700 underline">
              {selected.size === pending.length ? 'Deselect all' : 'Select all'}
            </button>
          )}
        </div>

        {loading && <p className="text-gray-400 text-sm py-4 text-center">Loading…</p>}

        {!loading && pending.length === 0 && (
          <p className="text-gray-500 text-sm py-4 text-center">
            No medications pending for today. Well done.
          </p>
        )}

        {!loading && pending.map(row => (
          <label key={row.confirmation_id}
            className="flex items-center gap-3 py-3 border-b border-gray-50 last:border-0 cursor-pointer">
            <input type="checkbox"
              checked={selected.has(row.confirmation_id)}
              onChange={() => toggleOne(row.confirmation_id)}
              className="h-5 w-5 rounded border-gray-300 text-blue-700 focus:ring-blue-500" />
            <span className="flex-1">
              <span className="block text-sm font-medium text-gray-800">{row.medication_name}</span>
              <span className="block text-xs text-gray-400">{formatTime(row.scheduled_time)} today</span>
            </span>
          </label>
        ))}

        {error && <p className="text-red-600 text-sm mt-2">{error}</p>}

        {pending.length > 0 && (
          <button onClick={handleConfirm}
            disabled={confirming || selected.size === 0}
            className="mt-4 w-full bg-blue-700 text-white rounded-xl py-3 font-medium text-sm disabled:opacity-50">
            {confirming ? 'Confirming…' : `Confirm selected (${selected.size})`}
          </button>
        )}
      </section>

      {/* ICS download */}
      <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4">
        <h2 className="text-sm font-medium text-gray-800 mb-1">Never miss a dose</h2>
        <p className="text-xs text-gray-500 mb-4">
          Add medication reminders to your calendar.
        </p>

        <button onClick={handleDownloadIcs} disabled={icsLoading}
          className="w-full bg-gray-100 text-gray-800 rounded-xl py-3 font-medium text-sm border border-gray-200 disabled:opacity-50">
          {icsLoading ? 'Preparing…' : 'Download Medication Reminders'}
        </button>

        {icsError && <p className="text-red-600 text-xs mt-2">{icsError}</p>}

        <p className="text-xs text-gray-400 mt-3">
          {isIOS()
            ? 'Tap the downloaded file — your Calendar app will open to import.'
            : 'Tap the downloaded file — your Calendar or Google Calendar app will open to import.'}
        </p>
      </section>
    </main>
  )
}
