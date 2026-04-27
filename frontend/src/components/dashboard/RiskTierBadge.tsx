import type { RiskTier } from '@/lib/types'

interface RiskTierBadgeProps {
  tier: RiskTier
  size?: 'sm' | 'md' | 'lg'
}

const TIER_CONFIG: Record<RiskTier, { label: string; className: string }> = {
  high: {
    label: 'HIGH RISK',
    className: 'bg-red-600 text-white',
  },
  medium: {
    label: 'MEDIUM RISK',
    className: 'bg-amber-400 text-amber-900',
  },
  low: {
    label: 'LOW RISK',
    className: 'bg-green-600 text-white',
  },
}

const SIZE_CLASS: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-[11px] px-2 py-0.5 tracking-wider',
  md: 'text-[12px] px-2.5 py-1 tracking-wider',
  lg: 'text-[13px] px-3 py-1.5 tracking-widest',
}

export default function RiskTierBadge({ tier, size = 'md' }: RiskTierBadgeProps) {
  const { label, className } = TIER_CONFIG[tier]
  return (
    <span
      className={`inline-block font-bold rounded-md uppercase whitespace-nowrap ${className} ${SIZE_CLASS[size]}`}
      aria-label={`Risk tier: ${label}`}
    >
      {label}
    </span>
  )
}
