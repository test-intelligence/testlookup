import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { CHART_VARS, RECHARTS_AXIS_TICK, RECHARTS_TOOLTIP_STYLE, STATUS_ENCODING } from './tokens'
import { useChartAnimation } from './motion'
import {
  ChartLegend,
  patternFill,
  renderPatterns,
  statusPatternId,
  statusPatternSpecs,
  useChartPatternPrefix,
  type LegendEntry,
} from './patterns'

interface DataPoint {
  date: string
  passed: number
  failed: number
  skipped: number
  broken?: number
  total: number
  pass_rate: number
}

interface Props {
  data: DataPoint[]
  type?: 'line' | 'area' | 'bar'
  height?: number
  /**
   * Forwarded to Recharts' `isAnimationActive`. Left `undefined` (the default)
   * Recharts keeps its own default, so existing call sites are unchanged; the
   * chart gallery passes `false` so a screenshot never catches a mid-tween frame.
   */
  animate?: boolean
}

// Every colour comes from ./tokens (VIZ-102); `npm run check:theme` rejects a literal here.
// Status is never colour-only: fills carry the status pattern, lines the status dash,
// and the legend swatches draw the same (./patterns).
const TOOLTIP_STYLE = RECHARTS_TOOLTIP_STYLE
const AXIS_TICK = RECHARTS_AXIS_TICK

const BAR_STATUSES = ['passed', 'failed', 'skipped', 'broken'] as const
const LINE_STATUSES = ['passed', 'failed', 'skipped'] as const

export default function TrendChart({ data, type = 'line', height = 280, animate: requestedAnimate }: Props) {
  // Off under prefers-reduced-motion; otherwise the caller's choice (VIZ-105).
  const animate = useChartAnimation(requestedAnimate)
  const prefix = useChartPatternPrefix()
  const fill = (status: (typeof BAR_STATUSES)[number]) => patternFill(statusPatternId(prefix, status))
  const common = {
    data,
    margin: { top: 4, right: 4, left: -16, bottom: 0 },
  }

  if (type === 'area') {
    const gradientId = `${prefix}-total`
    const legend: LegendEntry[] = [
      { key: 'total', label: 'Total Tests', fill: patternFill(gradientId) },
      { key: 'passed', label: STATUS_ENCODING.passed.label, status: 'passed', fill: fill('passed') },
    ]
    return (
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart {...common} accessibilityLayer>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={CHART_VARS.accent} stopOpacity={0.3} />
              <stop offset="95%" stopColor={CHART_VARS.accent} stopOpacity={0} />
            </linearGradient>
            {renderPatterns(statusPatternSpecs(prefix, ['passed']))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
          <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
          <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend content={() => <ChartLegend entries={legend} />} />
          <Area type="monotone" dataKey="total" stroke={CHART_VARS.accent} fill={patternFill(gradientId)} strokeWidth={2} name="Total Tests" isAnimationActive={animate} />
          <Area type="monotone" dataKey="passed" stroke={CHART_VARS.status.passed} fill={fill('passed')} fillOpacity={0.35} strokeWidth={1.5} name="Passed" isAnimationActive={animate} />
        </AreaChart>
      </ResponsiveContainer>
    )
  }

  if (type === 'bar') {
    const legend: LegendEntry[] = BAR_STATUSES.map((status) => ({
      key: status,
      label: STATUS_ENCODING[status].label,
      status,
      fill: fill(status),
    }))
    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart {...common} barSize={18} accessibilityLayer>
          <defs>{renderPatterns(statusPatternSpecs(prefix, BAR_STATUSES))}</defs>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
          <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
          <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Legend content={() => <ChartLegend entries={legend} />} />
          <Bar dataKey="passed"  stackId="a" fill={fill('passed')} name="Passed"  radius={[0, 0, 0, 0]} isAnimationActive={animate} />
          <Bar dataKey="failed"  stackId="a" fill={fill('failed')} name="Failed"  isAnimationActive={animate} />
          <Bar dataKey="skipped" stackId="a" fill={fill('skipped')} name="Skipped" isAnimationActive={animate} />
          <Bar dataKey="broken"  stackId="a" fill={fill('broken')} name="Broken"  radius={[3, 3, 0, 0]} isAnimationActive={animate} />
        </BarChart>
      </ResponsiveContainer>
    )
  }

  // Default: line chart. Each status line has its own dash (STATUS_ENCODING[s].dash).
  const legend: LegendEntry[] = LINE_STATUSES.map((status) => ({
    key: status,
    label: STATUS_ENCODING[status].label,
    status,
    stroke: CHART_VARS.status[status],
    dash: STATUS_ENCODING[status].dash,
  }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart {...common} accessibilityLayer>
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
        <XAxis dataKey="date" axisLine={false} tickLine={false} tick={AXIS_TICK} dy={8} />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend content={() => <ChartLegend entries={legend} />} />
        <Line type="monotone" dataKey="passed"  stroke={CHART_VARS.status.passed} strokeDasharray={STATUS_ENCODING.passed.dash} strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Passed" isAnimationActive={animate} />
        <Line type="monotone" dataKey="failed"  stroke={CHART_VARS.status.failed} strokeDasharray={STATUS_ENCODING.failed.dash} strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Failed" isAnimationActive={animate} />
        <Line type="monotone" dataKey="skipped" stroke={CHART_VARS.status.skipped} strokeDasharray={STATUS_ENCODING.skipped.dash} strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Skipped" isAnimationActive={animate} />
        <Line type="monotone" dataKey="pass_rate" stroke={CHART_VARS.accent} strokeWidth={2} dot={false} activeDot={{ r: 4 }} name="Pass Rate %" hide isAnimationActive={animate} />
      </LineChart>
    </ResponsiveContainer>
  )
}
