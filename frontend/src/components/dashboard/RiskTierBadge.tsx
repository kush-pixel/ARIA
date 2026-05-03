'use client'

import * as Tooltip from '@radix-ui/react-tooltip'
import type { RiskTier } from '@/lib/types'

interface RiskTierBadgeProps {
  tier: RiskTier
  size?: 'sm' | 'md' | 'lg'
}

const TIER_CONFIG: Record<RiskTier, { label: string; className: string }> = {
  high:   { label: 'High',   className: 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/10 border border-red-100 dark:border-red-900/30' },
  medium: { label: 'Medium', className: 'text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-[#1F2937] border border-gray-200 dark:border-[#374151]' },
  low:    { label: 'Low',    className: 'text-gray-500 dark:text-gray-500 bg-gray-50 dark:bg-[#1F2937] border border-gray-100 dark:border-[#374151]' },
}

const SIZE_CLASS: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-[12px] px-2 py-0.5',
  md: 'text-[13px] px-2.5 py-1',
  lg: 'text-[13px] px-3 py-1',
}

const TIER_TOOLTIP: Record<RiskTier, string> = {
  high:   'Based on diagnosis: CHF, Stroke, or TIA automatically sets High regardless of current readings or score.',
  medium: 'Based on overall clinical profile. No high-risk diagnosis present.',
  low:    'Based on overall clinical profile. Currently lowest cardiovascular risk category.',
}

export default function RiskTierBadge({ tier, size = 'md' }: RiskTierBadgeProps) {
  const { label, className } = TIER_CONFIG[tier]
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <span
            className={`inline-flex items-center font-medium rounded whitespace-nowrap cursor-default ${className} ${SIZE_CLASS[size]}`}
            aria-label={`Risk tier: ${label}`}
          >
            {label}
          </span>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="max-w-[220px] rounded-lg bg-gray-900 dark:bg-gray-700 text-white text-[12px]
                       px-3 py-2 shadow-lg leading-snug z-50"
            sideOffset={6}
          >
            <p className="font-semibold mb-1 text-gray-300">Chronic Risk</p>
            {TIER_TOOLTIP[tier]}
            <Tooltip.Arrow className="fill-gray-900 dark:fill-gray-700" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}
