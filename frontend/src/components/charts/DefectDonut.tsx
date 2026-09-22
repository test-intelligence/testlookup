import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { CHART_VARS, RECHARTS_TOOLTIP_STYLE, STATUS_ENCODING, type DecalKind } from './tokens'
import { useChartAnimation } from './motion'
import {
  ChartLegend,
  patternFill,
  renderPatterns,
  statusPatternId,
  useChartPatternPrefix,
  type PatternSpec,
} from './patterns'

/**
 * One slice per priority. P1 / P2 borrow the failed / broken STATUS encoding
 * (colour AND pattern); P3 / P4 get their own colour and a pattern no other
 * slice uses, so the four slices never differ by colour alone (VIZ-102).
 * P4 was a literal grey; it is the theme's muted neutral now.
 */
const PRIORITIES: { label: string; color: string; decal: DecalKind; status?: 'failed' | 'broken' }[] = [
  { label: 'P1 Critical', color: CHART_VARS.status.failed, decal: STATUS_ENCODING.failed.decal, status: 'failed' },
  { label: 'P2 High', color: CHART_VARS.status.broken, decal: STATUS_ENCODING.broken.decal, status: 'broken' },
  { label: 'P3 Medium', color: CHART_VARS.accent, decal: 'dots' },
  { label: 'P4 Low', color: CHART_VARS.neutral, decal: 'solid' },
]

interface Props {
  data: number[]   // [p1, p2, p3, p4]
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps Recharts' default. */
  animate?: boolean
}

export default function DefectDonut({ data, animate: requestedAnimate }: Props) {
  const animate = useChartAnimation(requestedAnimate)
  const prefix = useChartPatternPrefix()
  const specs: PatternSpec[] = PRIORITIES.map((p, i) => ({
    id: p.status ? statusPatternId(prefix, p.status) : `${prefix}-chart-pattern-p${i + 1}`,
    color: p.color,
    decal: p.decal,
  }))
  const chartData = PRIORITIES.map((p, i) => ({ name: p.label, value: data[i] ?? 0, fill: patternFill(specs[i].id) })).filter(
    (d) => d.value > 0,
  )
  if (!chartData.length) return <p className="text-[var(--color-text-muted)] text-sm text-center py-8">No defect data</p>

  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart accessibilityLayer>
        <defs>{renderPatterns(specs)}</defs>
        <Pie data={chartData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value" isAnimationActive={animate}>
          {chartData.map((d) => <Cell key={d.name} fill={d.fill} />)}
        </Pie>
        <Tooltip
          contentStyle={RECHARTS_TOOLTIP_STYLE}
          formatter={(val, name) => [val, name]}
        />
        <Legend content={() => <ChartLegend entries={chartData.map((d) => ({ key: d.name, label: d.name, fill: d.fill }))} />} />
      </PieChart>
    </ResponsiveContainer>
  )
}
