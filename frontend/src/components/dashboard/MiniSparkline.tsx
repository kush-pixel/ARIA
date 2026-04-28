'use client'

interface MiniSparklineProps {
  values: number[]       // systolic_avg values, chronological
  tier: string
}

export default function MiniSparkline({ values, tier }: MiniSparklineProps) {
  if (values.length < 2) {
    return <span className="text-[11px] text-gray-300 dark:text-gray-700">—</span>
  }

  const W = 80
  const H = 28
  const pts = values.slice(-14)  // last 14 readings max

  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const range = max - min || 1

  const coords = pts.map((v, i) => {
    const x = (i / (pts.length - 1)) * W
    const y = H - ((v - min) / range) * H
    return [x, y] as [number, number]
  })

  // Build smooth polyline path
  const d = coords
    .map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`))
    .join(' ')

  // Color based on tier
  const stroke =
    tier === 'high'   ? '#EF4444' :
    tier === 'medium' ? '#F59E0B' :
                        '#16A34A'

  const fillStop =
    tier === 'high'   ? '#FEE2E2' :
    tier === 'medium' ? '#FEF3C7' :
                        '#DCFCE7'

  const last = coords[coords.length - 1]
  const first = coords[0]

  // Area fill path
  const area = `${d} L${last[0]},${H} L${first[0]},${H} Z`

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      aria-label={`BP trend: ${pts[0].toFixed(0)}–${pts[pts.length - 1].toFixed(0)} mmHg`}
    >
      {/* Area fill */}
      <path d={area} fill={fillStop} fillOpacity={0.35} />
      {/* Line */}
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" />
      {/* Last point dot */}
      <circle cx={last[0]} cy={last[1]} r={2.5} fill={stroke} />
    </svg>
  )
}
