import { PolarAngleAxis, RadialBarChart, RadialBar, ResponsiveContainer } from 'recharts'
import { CHART_VARS } from './tokens'
import { useChartAnimation } from './motion'

interface Props {
  value: number
  size?: number
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps Recharts' default. */
  animate?: boolean
}

export default function PassRateGauge({ value, size = 120, animate: requestedAnimate }: Props) {
  const animate = useChartAnimation(requestedAnimate)
  const color = value >= 95 ? CHART_VARS.status.passed : value >= 80 ? CHART_VARS.status.broken : CHART_VARS.status.failed
  // ONE ring: the value arc drawn over its own background track. Two data
  // rows (a 100 "track" row plus the value) made Recharts lay out two
  // concentric rings, so the value sat beside the track instead of on it --
  // invisible while the track was near-black, obvious once it became the grid
  // colour. The fixed 0-100 angle axis makes the arc length the percentage.
  const clamped = Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0))
  const data = [{ value: clamped, fill: color }]

  return (
    <div className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          cx="50%" cy="50%"
          innerRadius="65%" outerRadius="100%"
          startAngle={210} endAngle={-30}
          data={data} barSize={10}
          accessibilityLayer
        >
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} axisLine={false} />
          <RadialBar
            dataKey="value"
            cornerRadius={5}
            background={{ fill: CHART_VARS.grid }}
            isAnimationActive={animate}
          />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-bold text-[var(--color-text)]">{value.toFixed(1)}%</span>
        <span className="text-[10px] text-[var(--color-text-muted)]">Pass Rate</span>
      </div>
    </div>
  )
}
