import { RadialBarChart, RadialBar, ResponsiveContainer } from 'recharts'
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
  // The unfilled track is the chart grid colour, so it recedes on every theme.
  const data = [{ value: 100, fill: CHART_VARS.grid }, { value, fill: color }]

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
          <RadialBar dataKey="value" cornerRadius={5} background={false} isAnimationActive={animate} />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-bold text-[var(--color-text)]">{value.toFixed(1)}%</span>
        <span className="text-[10px] text-[var(--color-text-muted)]">Pass Rate</span>
      </div>
    </div>
  )
}
