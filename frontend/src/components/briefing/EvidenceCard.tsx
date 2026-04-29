'use client'

import { useState } from 'react'
import type { ChatDoneEvent } from '@/lib/types'
import { ChevronDown, ChevronUp, AlertTriangle } from 'lucide-react'

interface EvidenceCardProps {
  evidence: string[]
  confidence: ChatDoneEvent['confidence']
  dataGaps: string[]
  toolsUsed: string[]
}

const CONFIDENCE_CONFIG: Record<NonNullable<ChatDoneEvent['confidence']>, { dot: string; label: string; bar: string }> = {
  high:    { dot: 'bg-teal-500',   label: 'High confidence',   bar: 'bg-teal-500' },
  medium:  { dot: 'bg-amber-400',  label: 'Medium confidence', bar: 'bg-amber-400' },
  low:     { dot: 'bg-orange-500', label: 'Low confidence',    bar: 'bg-orange-500' },
  no_data: { dot: 'bg-gray-400',   label: 'No data',           bar: 'bg-gray-400' },
  blocked: { dot: 'bg-red-500',    label: 'Blocked',           bar: 'bg-red-500' },
}

export default function EvidenceCard({ evidence, confidence, dataGaps, toolsUsed }: EvidenceCardProps) {
  const [open, setOpen] = useState(false)
  if (evidence.length === 0 && dataGaps.length === 0) return null

  const cfg = CONFIDENCE_CONFIG[confidence]
  const sourceSummary = toolsUsed.map(t => t.replace('get_', '').replace(/_/g, ' ')).join(', ')

  return (
    <div className="mt-2 border-t border-gray-100 dark:border-[#2a3548] pt-2">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 w-full group"
      >
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${cfg.dot}`} />
        <span className="text-[11px] text-gray-400 dark:text-gray-500 group-hover:text-gray-600
                         dark:group-hover:text-gray-300 transition-colors flex-1 text-left truncate">
          {cfg.label}{sourceSummary ? ` · ${sourceSummary}` : ''}
        </span>
        {open
          ? <ChevronUp size={10} className="text-gray-400 flex-shrink-0" />
          : <ChevronDown size={10} className="text-gray-400 flex-shrink-0" />}
      </button>

      {open && (
        <div className="mt-2 space-y-1 animate-fadeSlideUp">
          {evidence.map((item, i) => (
            <p key={i} className="text-[11px] text-gray-500 dark:text-gray-400 pl-3
                                  border-l-2 border-teal-200 dark:border-teal-800">
              {item}
            </p>
          ))}
          {dataGaps.map((gap, i) => (
            <div key={`gap-${i}`} className="flex items-start gap-1 pl-3
                                             border-l-2 border-orange-200 dark:border-orange-800">
              <AlertTriangle size={10} className="text-orange-400 mt-0.5 flex-shrink-0" />
              <p className="text-[11px] text-orange-500 dark:text-orange-400">{gap}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
