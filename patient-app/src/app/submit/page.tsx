'use client'

import { useState, useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { submitReading } from '@/lib/api'
import { getPatientId, isTokenValid } from '@/lib/auth'

type Session = 'morning' | 'evening'
type MedTaken = 'yes' | 'no' | 'partial'

interface Symptom {
  id: string
  label: string
  note?: string
}

const SYMPTOMS: Symptom[] = [
  { id: 'headache', label: 'Headache' },
  { id: 'dizziness', label: 'Dizziness', note: 'Your doctor will be informed at your next visit.' },
  { id: 'chest_pain', label: 'Chest pain' },
  { id: 'shortness_of_breath', label: 'Shortness of breath' },
]

const URGENT_SYMPTOMS = new Set(['chest_pain', 'shortness_of_breath'])

export default function SubmitPage() {
  const router = useRouter()
  // Timestamp captured at form open, not at submit (per spec)
  const openedAt = useRef(new Date().toISOString())

  const [patientId, setPatientId] = useState<string | null>(null)
  const [sys1, setSys1] = useState('')
  const [dia1, setDia1] = useState('')
  const [sys2, setSys2] = useState('')
  const [dia2, setDia2] = useState('')
  const [hr, setHr] = useState('')
  const [session, setSession] = useState<Session>('morning')
  const [medTaken, setMedTaken] = useState<MedTaken | ''>('')
  const [twoReadings, setTwoReadings] = useState(false)
  const [symptoms, setSymptoms] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!isTokenValid()) { router.replace('/'); return }
    setPatientId(getPatientId())
  }, [router])

  const urgentChecked = Array.from(symptoms).some(s => URGENT_SYMPTOMS.has(s))

  function toggleSymptom(id: string) {
    setSymptoms(prev => {
      const next = new Set(Array.from(prev))
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (!patientId) return

    setLoading(true)
    try {
      await submitReading({
        patient_id: patientId,
        systolic_1: parseInt(sys1),
        diastolic_1: parseInt(dia1),
        ...(hr ? { heart_rate_1: parseInt(hr) } : {}),
        ...(twoReadings && sys2 && dia2 ? { systolic_2: parseInt(sys2), diastolic_2: parseInt(dia2) } : {}),
        effective_datetime: openedAt.current,
        session,
        ...(medTaken ? { medication_taken: medTaken } : {}),
        submitted_by: 'patient',
        symptoms: symptoms.size > 0 ? Array.from(symptoms) : [],
      })
      setDone(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Submission failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  if (done) {
    return (
      <main className="min-h-screen flex items-center justify-center px-4">
        <div className="text-center space-y-4">
          <div className="text-5xl">✓</div>
          <h2 className="text-xl font-semibold text-green-700">Reading submitted</h2>
          <p className="text-gray-500 text-sm">Your doctor will see this at your next visit.</p>
          <button onClick={() => router.push('/confirm')} className="mt-4 text-blue-700 underline text-sm">
            Back to medications
          </button>
        </div>
      </main>
    )
  }

  return (
    <main className="min-h-screen px-4 py-6 max-w-md mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <button onClick={() => router.back()} className="text-gray-400 text-lg">‹</button>
        <h1 className="text-lg font-semibold">Submit Blood Pressure</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">

        {/* Reading 1 */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 space-y-3">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Reading 1</h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Systolic</label>
              <input type="number" inputMode="numeric" min={60} max={250} required
                value={sys1} onChange={e => setSys1(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Diastolic</label>
              <input type="number" inputMode="numeric" min={30} max={150} required
                value={dia1} onChange={e => setDia1(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Heart rate (optional)</label>
            <input type="number" inputMode="numeric" min={30} max={200}
              value={hr} onChange={e => setHr(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
        </section>

        {/* Second reading toggle */}
        <button type="button" onClick={() => setTwoReadings(v => !v)}
          className="text-blue-700 text-sm underline">
          {twoReadings ? 'Remove second reading' : '+ Add second reading'}
        </button>

        {twoReadings && (
          <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 space-y-3">
            <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Reading 2</h2>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Systolic</label>
                <input type="number" inputMode="numeric" min={60} max={250}
                  value={sys2} onChange={e => setSys2(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Diastolic</label>
                <input type="number" inputMode="numeric" min={30} max={150}
                  value={dia2} onChange={e => setDia2(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-lg focus:outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
            </div>
          </section>
        )}

        {/* Session */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 space-y-2">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Session</h2>
          <div className="flex gap-3">
            {(['morning', 'evening'] as const).map(s => (
              <label key={s} className={`flex-1 text-center py-2 rounded-lg border cursor-pointer text-sm font-medium transition-colors
                ${session === s ? 'bg-blue-700 text-white border-blue-700' : 'border-gray-300 text-gray-700'}`}>
                <input type="radio" name="session" value={s} checked={session === s}
                  onChange={() => setSession(s)} className="sr-only" />
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </label>
            ))}
          </div>
        </section>

        {/* Medication taken */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 space-y-2">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Medication taken?</h2>
          <div className="flex gap-3">
            {(['yes', 'no', 'partial'] as const).map(m => (
              <label key={m} className={`flex-1 text-center py-2 rounded-lg border cursor-pointer text-sm font-medium transition-colors
                ${medTaken === m ? 'bg-blue-700 text-white border-blue-700' : 'border-gray-300 text-gray-700'}`}>
                <input type="radio" name="med_taken" value={m} checked={medTaken === m}
                  onChange={() => setMedTaken(m)} className="sr-only" />
                {m.charAt(0).toUpperCase() + m.slice(1)}
              </label>
            ))}
          </div>
        </section>

        {/* Symptoms */}
        <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 space-y-3">
          <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Symptoms (optional)</h2>
          {SYMPTOMS.map(({ id, label, note }) => (
            <label key={id} className="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" checked={symptoms.has(id)} onChange={() => toggleSymptom(id)}
                className="mt-0.5 h-5 w-5 rounded border-gray-300 text-blue-700 focus:ring-blue-500" />
              <span>
                <span className="text-sm font-medium text-gray-800">{label}</span>
                {note && <span className="block text-xs text-gray-400 mt-0.5">{note}</span>}
              </span>
            </label>
          ))}
        </section>

        {/* Safety banner */}
        {urgentChecked && (
          <div className="bg-red-50 border border-red-300 rounded-2xl p-4 text-red-800 text-sm font-medium">
            If you are experiencing chest pain or sudden shortness of breath, call <strong>999</strong> immediately.
          </div>
        )}

        {error && <p className="text-red-600 text-sm">{error}</p>}

        <button type="submit" disabled={loading || !sys1 || !dia1 || !medTaken}
          className="w-full bg-blue-700 text-white rounded-xl py-3 font-medium text-base disabled:opacity-50">
          {loading ? 'Submitting…' : 'Submit reading'}
        </button>
      </form>
    </main>
  )
}
