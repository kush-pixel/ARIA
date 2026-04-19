import type { AdherenceData } from '@/lib/types'

interface AdherenceSummaryProps {
  adherence: AdherenceData[]
  patternText?: string
}

function barColor(pct: number): string {
  if (pct >= 80) return 'bg-green-500'
  if (pct >= 60) return 'bg-amber-400'
  return 'bg-red-500'
}

function barLabel(pct: number): string {
  if (pct >= 80) return 'text-green-700 dark:text-green-400'
  if (pct >= 60) return 'text-amber-700 dark:text-amber-400'
  return 'text-red-700 dark:text-red-400'
}

function patternInterpretation(adherence: AdherenceData[]): string {
  if (adherence.length === 0) return ''
  const avg = adherence.reduce((s, a) => s + a.adherence_pct, 0) / adherence.length
  if (avg >= 80) {
    return 'Overall adherence signal is high. If BP remains elevated, this pattern may suggest a possible treatment review is warranted rather than an adherence intervention.'
  }
  if (avg >= 60) {
    return 'Adherence signal is moderate. A contextual review at the visit may clarify whether dose timing or patient factors are contributing to the pattern.'
  }
  return 'Adherence signal is low across one or more medications. A possible adherence concern — contextual discussion recommended at the visit.'
}

export default function AdherenceSummary({ adherence, patternText }: AdherenceSummaryProps) {
  if (adherence.length === 0) {
    return (
      <p className="text-[16px] text-slate-400 italic">
        No adherence data available — home monitoring not active or insufficient data.
      </p>
    )
  }

  const interpretation = patternText ?? patternInterpretation(adherence)

  return (
    <div className="space-y-4">
      {adherence.map((med) => (
        <div key={med.medication_name}>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[16px] text-slate-700 dark:text-slate-200 font-medium">
              {med.medication_name}
            </span>
            <span className={`text-clinical font-semibold tabular-nums ${barLabel(med.adherence_pct)}`}>
              {med.adherence_pct.toFixed(0)}%
            </span>
          </div>
          <div
            className="h-3 w-full bg-slate-100 dark:bg-slate-700 rounded-full overflow-hidden"
            role="progressbar"
            aria-valuenow={med.adherence_pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${med.medication_name} adherence: ${med.adherence_pct.toFixed(0)}%`}
          >
            <div
              className={`h-full rounded-full ${barColor(med.adherence_pct)}`}
              style={{ width: `${med.adherence_pct}%` }}
            />
          </div>
          <p className="text-[13px] text-slate-400 dark:text-slate-500 mt-1">
            {med.confirmed_doses} of {med.total_doses} doses confirmed
          </p>
        </div>
      ))}

      <p className="text-[16px] text-slate-600 dark:text-slate-300 leading-relaxed pt-2 border-t border-slate-100 dark:border-slate-700">
        {interpretation}
      </p>
    </div>
  )
}
