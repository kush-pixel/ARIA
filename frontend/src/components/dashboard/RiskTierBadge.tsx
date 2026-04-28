import type { RiskTier } from '@/lib/types'

interface RiskTierBadgeProps {
  tier: RiskTier
  size?: 'sm' | 'md' | 'lg'
}

const TIER_CONFIG: Record<RiskTier, { label: string; dot: string; text: string; bg: string }> = {
  high:   { label: 'High Risk',   dot: 'bg-red-500',   text: 'text-red-700 dark:text-red-400',   bg: 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800' },
  medium: { label: 'Medium Risk', dot: 'bg-amber-500', text: 'text-amber-700 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800' },
  low:    { label: 'Low Risk',    dot: 'bg-green-500', text: 'text-green-700 dark:text-green-400', bg: 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' },
}

const SIZE_CLASS: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-[12px] px-2.5 py-1',
  md: 'text-[13px] px-3 py-1.5',
  lg: 'text-[14px] px-3.5 py-2',
}

export default function RiskTierBadge({ tier, size = 'md' }: RiskTierBadgeProps) {
  const { label, dot, text, bg } = TIER_CONFIG[tier]
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-semibold rounded-md whitespace-nowrap ${bg} ${text} ${SIZE_CLASS[size]}`}
      aria-label={`Risk tier: ${label}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} aria-hidden />
      {label}
    </span>
  )
}
