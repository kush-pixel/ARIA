'use client'

import * as Tooltip from '@radix-ui/react-tooltip'

interface RiskScoreBarProps {
  score: number | null
  tier: string
}

interface ScoreBand {
  label: string
  className: string
  barColor: string
}

function scoreBand(score: number): ScoreBand {
  if (score >= 60) return {
    label: 'High',
    className: 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-800',
    barColor: 'bg-red-500',
  }
  if (score >= 30) return {
    label: 'Medium',
    className: 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/10 border border-amber-200 dark:border-amber-800',
    barColor: 'bg-amber-400',
  }
  return {
    label: 'Low',
    className: 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/10 border border-green-200 dark:border-green-800',
    barColor: 'bg-green-500',
  }
}

export default function RiskScoreBar({ score, tier: _tier }: RiskScoreBarProps) {
  if (score === null) {
    return (
      <span className="text-[12px] text-gray-300 dark:text-gray-700">No data</span>
    )
  }

  const pct = Math.min(100, Math.max(0, score))
  const band = scoreBand(score)

  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <div className="flex flex-col items-center gap-1.5 cursor-default w-full">
            {/* Score number + label badge */}
            <div className="flex items-center gap-2">
              <span
                className="text-[15px] font-bold tabular-nums text-gray-900 dark:text-gray-100"
                aria-label={`Risk score ${score.toFixed(1)}`}
              >
                {score.toFixed(0)}
              </span>
              <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${band.className}`}>
                {band.label}
              </span>
            </div>

            {/* Progress bar */}
            <div
              className="w-full h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Priority score ${score.toFixed(1)} out of 100`}
            >
              <div
                className={`h-full rounded-full transition-all duration-300 ${band.barColor}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="max-w-xs rounded-lg bg-gray-900 dark:bg-gray-700 text-white text-[13px]
                       px-3.5 py-2 shadow-lg leading-snug z-50"
            sideOffset={6}
          >
            Today's urgency score (0–100): how much attention this patient needs right now. Weighted across BP vs baseline (30%), days since last med change (25%), adherence (20%), monitoring gap (15%), and comorbidities (10%). A High Risk patient can score low if currently stable and well-managed.
            <Tooltip.Arrow className="fill-gray-900 dark:fill-gray-700" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
