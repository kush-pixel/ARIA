import type { AdherenceData } from '@/lib/types'

interface AdherenceSummaryProps {
  adherence: AdherenceData[]
  patternText?: string
}

function rateBadge(pct: number): string {
  if (pct >= 80) return 'bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 border border-green-200 dark:border-green-800'
  if (pct >= 60) return 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400 border border-amber-200 dark:border-amber-800'
  return 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400 border border-red-200 dark:border-red-800'
}

function patternInterpretation(adherence: AdherenceData[]): string {
  if (adherence.length === 0) return ''
  const avg = adherence.reduce((s, a) => s + a.adherence_pct, 0) / adherence.length
  if (avg >= 80) return 'Overall adherence signal is high. If BP remains elevated, a possible treatment review may be warranted rather than an adherence intervention.'
  if (avg >= 60) return 'Adherence signal is moderate. A contextual review at the visit may clarify whether dose timing or patient factors are contributing.'
  return 'Adherence signal is low across one or more medications. A possible adherence concern — contextual discussion recommended at the visit.'
}

export default function AdherenceSummary({ adherence, patternText }: AdherenceSummaryProps) {
  if (adherence.length === 0) {
    return (
      <p className="text-[13px] text-gray-400 italic">
        No adherence data available — home monitoring not active or insufficient data.
      </p>
    )
  }

  const interpretation = patternText ?? patternInterpretation(adherence)

  return (
    <div className="space-y-3">
      {/* Compact card grid — 2 columns */}
      <div className="grid grid-cols-2 gap-2">
        {adherence.map((med) => (
          <div
            key={med.medication_name}
            className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg
                       bg-gray-50 dark:bg-[#0B1220]
                       border border-gray-100 dark:border-[#1F2937]"
          >
            <span className="text-[12px] text-gray-700 dark:text-gray-300 truncate leading-tight">
              {med.medication_name}
            </span>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <span className="text-[11px] text-gray-400 dark:text-gray-600 tabular-nums whitespace-nowrap">
                {med.confirmed_doses}/{med.total_doses}
              </span>
              <span className={`text-[11px] font-bold tabular-nums px-1.5 py-0.5 rounded-full whitespace-nowrap ${rateBadge(med.adherence_pct)}`}>
                {med.adherence_pct.toFixed(0)}%
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Interpretation */}
      <p className="text-[12px] text-gray-500 dark:text-gray-400 leading-relaxed pt-1 border-t border-gray-100 dark:border-[#1F2937]">
        {interpretation}
      </p>
    </div>
  )
}
