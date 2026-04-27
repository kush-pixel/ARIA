'use client'

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import type { Reading } from '@/lib/types'

interface SparklineChartProps {
  readings: Reading[]
}

interface ChartPoint {
  date: string
  dateLabel: string
  morning: number | null
  evening: number | null
  rollingAvg: number | null
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getDate()}/${d.getMonth() + 1}`
}

function buildChartData(readings: Reading[]): ChartPoint[] {
  // Group by date + session
  const byDate = new Map<string, { morning?: number; evening?: number }>()

  for (const r of readings) {
    const date = r.effective_datetime.slice(0, 10)
    const existing = byDate.get(date) ?? {}
    if (r.session === 'morning') existing.morning = r.systolic_avg
    else if (r.session === 'evening') existing.evening = r.systolic_avg
    byDate.set(date, existing)
  }

  // Sort by date
  const sorted = Array.from(byDate.entries()).sort(([a], [b]) => a.localeCompare(b))

  // Build points with 7-day rolling average
  const points: ChartPoint[] = sorted.map(([date, vals], i) => {
    const window = sorted.slice(Math.max(0, i - 6), i + 1)
    const allVals = window.flatMap(([, v]) => [v.morning, v.evening].filter((x): x is number => x !== undefined))
    const rollingAvg = allVals.length > 0
      ? parseFloat((allVals.reduce((s, v) => s + v, 0) / allVals.length).toFixed(1))
      : null

    return {
      date,
      dateLabel: formatDate(date),
      morning: vals.morning ?? null,
      evening: vals.evening ?? null,
      rollingAvg,
    }
  })

  return points
}

interface TooltipPayloadEntry {
  name: string
  value: number | null
  color: string
}

interface CustomTooltipProps {
  active?: boolean
  payload?: TooltipPayloadEntry[]
  label?: string
}

function CustomTooltip({ active, payload, label }: CustomTooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700
                    rounded-lg px-4 py-3 shadow-lg text-[14px]">
      <p className="font-semibold text-slate-700 dark:text-slate-200 mb-2">{label}</p>
      {payload.map((entry) =>
        entry.value !== null ? (
          <p key={entry.name} style={{ color: entry.color }} className="tabular-nums">
            {entry.name}: <span className="font-semibold">{entry.value} mmHg</span>
          </p>
        ) : null
      )}
    </div>
  )
}

export default function SparklineChart({ readings }: SparklineChartProps) {
  const data = buildChartData(readings)

  if (data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[15px] text-slate-400 italic">
        No reading data available for chart.
      </div>
    )
  }

  return (
    <div className="w-full" style={{ minHeight: 220 }}>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <XAxis
            dataKey="dateLabel"
            tick={{ fontSize: 12, fill: '#94A3B8' }}
            interval={Math.floor(data.length / 7)}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[80, 200]}
            ticks={[80, 100, 120, 130, 140, 160, 180, 200]}
            tick={{ fontSize: 12, fill: '#94A3B8' }}
            axisLine={false}
            tickLine={false}
            width={36}
            unit=" "
          />
          <RechartsTooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 13, paddingTop: 8 }}
            formatter={(value) => <span style={{ color: '#64748B' }}>{value}</span>}
          />

          {/* Stage 1 threshold — 130 */}
          <ReferenceLine
            y={130}
            stroke="#94A3B8"
            strokeDasharray="4 4"
            strokeWidth={1}
            label={{ value: 'Stage 1 (130)', position: 'insideTopRight', fontSize: 11, fill: '#94A3B8' }}
          />
          {/* Stage 2 threshold — 140 */}
          <ReferenceLine
            y={140}
            stroke="#EF4444"
            strokeDasharray="4 4"
            strokeWidth={1.5}
            label={{ value: 'Stage 2 (140)', position: 'insideTopRight', fontSize: 11, fill: '#EF4444' }}
          />

          {/* Morning — teal solid */}
          <Line
            type="monotone"
            dataKey="morning"
            name="Morning"
            stroke="#0F766E"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            activeDot={{ r: 4, fill: '#0F766E' }}
          />
          {/* Evening — teal dashed */}
          <Line
            type="monotone"
            dataKey="evening"
            name="Evening"
            stroke="#0F766E"
            strokeWidth={2}
            strokeDasharray="5 4"
            dot={false}
            connectNulls={false}
            activeDot={{ r: 4, fill: '#0F766E' }}
          />
          {/* 7-day rolling average — darker, thicker */}
          <Line
            type="monotone"
            dataKey="rollingAvg"
            name="7-day avg"
            stroke="#115E59"
            strokeWidth={2.5}
            dot={false}
            connectNulls={false}
            activeDot={{ r: 4, fill: '#115E59' }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
