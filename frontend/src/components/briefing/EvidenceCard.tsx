'use client'

import { useState } from 'react'
import type { ChatDoneEvent } from '@/lib/types'

interface EvidenceCardProps {
  evidence: string[]
  confidence: ChatDoneEvent['confidence']
  dataGaps: string[]
  toolsUsed: string[]
}

const CONFIDENCE_DOT: Record<NonNullable<ChatDoneEvent['confidence']>, string> = {
  high: 'bg-teal-500',
  medium: 'bg-amber-400',
  low: 'bg-orange-500',
  no_data: 'bg-slate-400',
  blocked: 'bg-red-500',
}

const CONFIDENCE_LABEL: Record<NonNullable<ChatDoneEvent['confidence']>, string> = {
  high: 'High confidence',
  medium: 'Medium confidence',
  low: 'Low confidence',
  no_data: 'No data',
  blocked: 'Blocked',
}

export default function EvidenceCard({
  evidence,
  confidence,
  dataGaps,
  toolsUsed,
}: EvidenceCardProps) {
  const [open, setOpen] = useState(false)

  if (evidence.length === 0 && dataGaps.length === 0) return null

  const sourceSummary = toolsUsed
    .map(t => t.replace('get_', '').replace(/_/g, ' '))
    .join(', ')

  return (
    <div className="mt-1.5 text-[12px]">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-slate-400 hover:text-slate-600
                   dark:hover:text-slate-300 transition-colors"
      >
        <span className={`h-2 w-2 rounded-full flex-shrink-0 ${CONFIDENCE_DOT[confidence]}`} />
        <span>
          {CONFIDENCE_LABEL[confidence]}
          {sourceSummary ? ` · Based on: ${sourceSummary}` : ''}
        </span>
        <span className="ml-1">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-1.5 pl-3 border-l-2 border-slate-200 dark:border-slate-600 space-y-1">
          {evidence.map((item, i) => (
            <p key={i} className="text-slate-500 dark:text-slate-400">
              {item}
            </p>
          ))}
          {dataGaps.map((gap, i) => (
            <p key={`gap-${i}`} className="text-orange-500 dark:text-orange-400">
              ⚠ {gap}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
