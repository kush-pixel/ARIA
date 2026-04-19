'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { getPatient, getBriefing, getReadings, getAdherence } from '@/lib/api'
import type { Patient, Briefing, Reading, AdherenceData } from '@/lib/types'
import BriefingCard from '@/components/briefing/BriefingCard'
import { ArrowLeft } from 'lucide-react'
import Link from 'next/link'

export default function PatientPage() {
  const { id } = useParams<{ id: string }>()
  const [patient, setPatient] = useState<Patient | null>(null)
  const [briefing, setBriefing] = useState<Briefing | null>(null)
  const [readings, setReadings] = useState<Reading[]>([])
  const [adherence, setAdherence] = useState<AdherenceData[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    Promise.all([
      getPatient(id),
      getBriefing(id),
      getReadings(id),
      getAdherence(id),
    ]).then(([p, b, r, a]) => {
      setPatient(p)
      setBriefing(b)
      setReadings(r)
      setAdherence(a)
      setLoading(false)
    })
  }, [id])

  if (loading) {
    return (
      <div className="p-8 flex items-center gap-3 text-slate-500">
        <div className="h-5 w-5 rounded-full border-2 border-teal-600 border-t-transparent animate-spin" />
        <span className="text-[16px]">Loading patient briefing&hellip;</span>
      </div>
    )
  }

  if (!patient) {
    return (
      <div className="p-8 text-slate-500 text-[16px]">
        Patient not found.
      </div>
    )
  }

  return (
    <div className="p-8">
      <Link
        href="/patients"
        className="inline-flex items-center gap-2 text-[15px] text-slate-500 hover:text-teal-700
                   dark:hover:text-teal-400 transition-colors mb-6"
      >
        <ArrowLeft size={16} />
        Back to patient panel
      </Link>

      <BriefingCard
        patient={patient}
        briefing={briefing}
        readings={readings}
        adherence={adherence}
      />
    </div>
  )
}
