'use client'

import {
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from 'recharts'
import type { Reading } from '@/lib/types'

interface SparklineChartProps {
  readings: Reading[]
}

interface ChartPoint {
  dateLabel: string
  morning: number | null
  evening: number | null
  avg: number | null
  diastolicAvg: number | null
}

interface BuiltData {
  points: ChartPoint[]
  summary: {
    avgSystolic: number
    avgDiastolic: number
    trend: 'rising' | 'stable' | 'falling'
    arrow: '↑' | '→' | '↓'
    dayCount: number
  } | null
}

function fmt(iso: string): string {
  const d = new Date(iso)
  return `${d.getDate()}/${d.getMonth() + 1}`
}

// Single pass — chart and summary header use IDENTICAL numbers
function buildData(readings: Reading[]): BuiltData {
  const home = readings
    .filter((r) => r.source !== 'clinic')
    .sort((a, b) => new Date(a.effective_datetime).getTime() - new Date(b.effective_datetime).getTime())

  if (home.length < 3) return { points: [], summary: null }

  const byDate = new Map<string, { morning?: number; evening?: number; dias: number[] }>()
  for (const r of home) {
    const date = r.effective_datetime.slice(0, 10)
    const e = byDate.get(date) ?? { dias: [] }
    if (r.session === 'morning') e.morning = r.systolic_avg
    else if (r.session === 'evening') e.evening = r.systolic_avg
    e.dias.push(r.diastolic_avg)
    byDate.set(date, e)
  }

  const sorted = Array.from(byDate.entries()).sort(([a], [b]) => a.localeCompare(b))

  const points: ChartPoint[] = sorted.map(([date, v]) => {
    const sessions = [v.morning, v.evening].filter((x): x is number => x !== undefined)
    const avg = sessions.length > 0
      ? parseFloat((sessions.reduce((s, x) => s + x, 0) / sessions.length).toFixed(1))
      : null
    const diastolicAvg = v.dias.length > 0
      ? parseFloat((v.dias.reduce((s, x) => s + x, 0) / v.dias.length).toFixed(1))
      : null
    return { dateLabel: fmt(date), morning: v.morning ?? null, evening: v.evening ?? null, avg, diastolicAvg }
  })

  // Summary = last 14 chart points — same slice the right side of the graph shows
  const recent = points.slice(-14)
  const validS = recent.filter((p) => p.avg !== null)
  const validD = recent.filter((p) => p.diastolicAvg !== null)
  if (validS.length === 0) return { points, summary: null }

  const avgSystolic = Math.round(validS.reduce((s, p) => s + p.avg!, 0) / validS.length)
  const avgDiastolic = Math.round(validD.reduce((s, p) => s + p.diastolicAvg!, 0) / Math.max(validD.length, 1))

  // Trend: first third vs last third of all days
  const third = Math.max(1, Math.floor(points.length / 3))
  const firstAvg = points.slice(0, third).filter((p) => p.avg !== null).reduce((s, p) => s + p.avg!, 0) / third
  const lastAvg  = points.slice(-third).filter((p) => p.avg !== null).reduce((s, p) => s + p.avg!, 0) / third
  const delta = lastAvg - firstAvg

  const trend  = delta > 4 ? 'rising'  : delta < -4 ? 'falling'  : 'stable'
  const arrow  = delta > 4 ? '↑'       : delta < -4 ? '↓'        : '→'

  return { points, summary: { avgSystolic, avgDiastolic, trend, arrow, dayCount: recent.length } }
}

interface TooltipEntry { name: string; value: number | null; color: string }

function CustomTooltip({ active, payload, label }: {
  active?: boolean; payload?: TooltipEntry[]; label?: string
}) {
  if (!active || !payload?.length) return null
  const entries = payload.filter((e) => e.value !== null)
  if (!entries.length) return null
  return (
    <div className="bg-white dark:bg-[#1a2235] border border-gray-100 dark:border-[#2a3548]
                    rounded-lg px-3 py-2 shadow-md text-[12px]">
      <p className="font-medium text-gray-400 mb-1">{label}</p>
      {entries.map((e) => (
        <p key={e.name} className="tabular-nums" style={{ color: e.color }}>
          {e.name}: <span className="font-semibold">{e.value} mmHg</span>
        </p>
      ))}
    </div>
  )
}

export default function SparklineChart({ readings }: SparklineChartProps) {
  const { points, summary } = buildData(readings)

  if (points.length === 0) {
    return (
      <div className="h-[180px] flex items-center justify-center text-[13px] text-gray-400 italic">
        No home reading data available.
      </div>
    )
  }

  const levelLabel = !summary ? '' :
    summary.avgSystolic > 140 ? 'elevated' :
    summary.avgSystolic > 130 ? 'borderline' : 'within target'

  const isElevated = summary && summary.avgSystolic > 140
  const trendClass = summary?.trend === 'rising' ? 'text-red-500 dark:text-red-400' : 'text-gray-500 dark:text-gray-400'

  return (
    <div className="w-full space-y-2">

      {/* Summary — numbers come from the same slice the chart draws */}
      {summary && (
        <div className="flex items-start justify-between px-1 gap-4">
          <p className="text-[13px] text-gray-600 dark:text-gray-300 leading-snug">
            BP is{' '}
            <span className={isElevated ? 'text-red-500 dark:text-red-400 font-medium' : 'font-medium'}>
              {levelLabel}
            </span>
            {' '}and{' '}
            <span className={`font-medium ${trendClass}`}>{summary.arrow} {summary.trend}</span>
            {' '}over the last {summary.dayCount} days
          </p>
        </div>
      )}

      {/* Chart */}
      <ResponsiveContainer width="100%" height={195}>
        <ComposedChart data={points} margin={{ top: 4, right: 56, bottom: 2, left: 0 }}>

          {/* Threshold zones — very subtle */}
          <ReferenceArea y1={130} y2={140} fill="#FEF3C7" fillOpacity={0.18} ifOverflow="visible" />
          <ReferenceArea y1={140} y2={200} fill="#FEE2E2" fillOpacity={0.15} ifOverflow="visible" />

          <XAxis
            dataKey="dateLabel"
            tick={{ fontSize: 11, fill: '#CBD5E1' }}
            interval={Math.max(0, Math.floor(points.length / 6) - 1)}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[90, 190]}
            ticks={[120, 130, 140, 160, 180]}
            tick={{ fontSize: 11, fill: '#CBD5E1' }}
            axisLine={false}
            tickLine={false}
            width={28}
          />
          <RechartsTooltip content={<CustomTooltip />} cursor={{ stroke: '#E2E8F0', strokeWidth: 1 }} />

          {/* Morning — light, thin */}
          <Line
            type="monotone" dataKey="morning" name="Morning"
            stroke="#94A3B8" strokeWidth={1} strokeOpacity={0.6}
            dot={false} connectNulls={false}
            activeDot={{ r: 3, fill: '#94A3B8' }}
          />

          {/* Evening — dashed, subtler */}
          <Line
            type="monotone" dataKey="evening" name="Evening"
            stroke="#94A3B8" strokeWidth={1} strokeOpacity={0.4}
            strokeDasharray="4 3"
            dot={false} connectNulls={false}
            activeDot={{ r: 3, fill: '#94A3B8' }}
          />

          {/* Average — bold, dark, primary */}
          <Line
            type="monotone" dataKey="avg" name="Avg"
            stroke="#374151" strokeWidth={2.5}
            dot={false} connectNulls={false}
            activeDot={{ r: 4, fill: '#374151' }}
          />

        </ComposedChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="flex items-center gap-5 px-1 text-[11px] text-gray-400 dark:text-gray-500">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-5 h-[2px] bg-gray-600 dark:bg-gray-400 rounded" />
          Avg
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-[1px] bg-gray-400 opacity-60" />
          Morning
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 border-t border-dashed border-gray-400 opacity-50" />
          Evening
        </span>
        <span className="flex items-center gap-1.5 ml-2">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: '#FEF3C7' }} />
          130–140
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: '#FEE2E2' }} />
          &gt;140
        </span>
      </div>
    </div>
  )
}
