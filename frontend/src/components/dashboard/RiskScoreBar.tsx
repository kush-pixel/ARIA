'use client'

import * as Tooltip from '@radix-ui/react-tooltip'

interface RiskScoreBarProps {
  score: number | null
  tier: string
}

function barColor(score: number): string {
  if (score >= 70) return 'bg-red-500'
  if (score >= 40) return 'bg-amber-400'
  return 'bg-green-500'
}

export default function RiskScoreBar({ score, tier: _tier }: RiskScoreBarProps) {
  const safeScore = score ?? 0
  const pct = Math.min(100, Math.max(0, safeScore))
  const color = barColor(safeScore)

  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <div className="flex items-center gap-3 cursor-default min-w-0">
            <span
              className="text-clinical font-semibold tabular-nums text-slate-800 dark:text-slate-100 w-10 flex-shrink-0"
              aria-label={`Risk score ${safeScore.toFixed(1)}`}
            >
              {safeScore.toFixed(1)}
            </span>
            <div
              className="flex-1 h-3 bg-slate-100 dark:bg-slate-700 rounded-full overflow-hidden min-w-[60px]"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Priority score ${safeScore.toFixed(1)} out of 100`}
            >
              <div
                className={`h-full rounded-full transition-all duration-300 ${color}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="max-w-xs rounded-lg bg-slate-800 dark:bg-slate-700 text-white text-[14px]
                       px-4 py-2.5 shadow-lg leading-snug z-50"
            sideOffset={6}
          >
            Priority score based on BP trend, medication history, and adherence signal.
            <Tooltip.Arrow className="fill-slate-800 dark:fill-slate-700" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
