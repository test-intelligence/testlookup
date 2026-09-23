/**
 * `DurationTrend` (VIZ-406) — p50 and p95 per day as two lines with the band
 * between them shaded.
 *
 * The band is the point of the chart: p50 alone hides the tail, p95 alone hides
 * the typical case, and the DISTANCE between them is what says whether a suite
 * is getting slower for everyone or only in its tail.
 *
 * When the data says p95 is BELOW p50 — two percentiles can come from separate
 * cached queries over slightly different samples — nothing is swapped and
 * nothing is clamped. Both lines keep their reported values, the band is drawn
 * between the min and the max so it still reads as a band, and the chart SAYS
 * the percentiles disagree. Reordering them silently would destroy the only
 * evidence that the numbers cannot both be right.
 *
 * A bucket with no timed execution is a GAP on both lines and in the band —
 * `duration_p50` is `zero_fill=False` on the server for exactly this reason, and
 * a 0 ms median would read as an instant suite rather than an unmeasured one.
 */
import { useMemo } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatDuration } from '@/utils/formatters'
import { CHART_VARS, RECHARTS_AXIS_TICK } from './tokens'
import { NO_VALUE } from './chartText'
import { useChartAnimation } from './motion'
import { useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { ChartLegend, useChartPatternPrefix, type LegendEntry } from './patterns'
import type { DurationBandModel } from './durationBuckets'

export interface DurationTrendProps {
  model: DurationBandModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title?: string
  height?: number
  animate?: boolean
}

const NOTE = 'text-xs text-[var(--color-text-secondary)]'

/** The x axis title: the buckets are UTC days, as everywhere else in the kit. */
export const DURATION_TREND_X_AXIS = 'Day (UTC)'
/** The y axis title. A bare duration scale beside a date axis names neither. */
export const DURATION_TREND_Y_AXIS = 'Duration'
/** What the shaded area between the two lines is called, in the legend. */
export const BAND_LEGEND_LABEL = 'p50–p95 band'
/** The figcaption: what the reader is looking at, in one line. */
export const DURATION_TREND_CAPTION =
  'p50 and p95 per UTC day; the hatched band between them is the spread. A day with no timed execution is a gap, never 0 ms.'

/**
 * ONE interpolation for the band's edges and both lines: straight segments.
 *
 * The band is `[min, max]` of the two percentiles per day. Between two days a
 * straight line is at every point the same mix of its two ends as the band's
 * straight edges are of theirs, so it can never leave the band — not even
 * across an inverted day, where p95 dips under p50 and the lines cross. A
 * curve cannot promise that: `monotone` lines over straight band edges bulged
 * out of the band on both sides of the inverted day, and `monotone` edges
 * would not help, because the edges follow min / max, which switch from one
 * line to the other at an inversion and so curve differently from either.
 * Straight also draws exactly what was measured: one value per day, nothing
 * implied in between.
 */
export const DURATION_TREND_CURVE = 'linear' as const

/**
 * A tick rendered as a React element rather than through `tickFormatter`.
 * `chart-guard` rejects every `*formatter` key that is not a
 * `domTooltipFormatter` — a blanket rule worth keeping — and a tick element
 * gets the same formatted duration with no formatter key at all.
 */
function DurationTick({ x, y, payload }: { x?: number; y?: number; payload?: { value?: number } }) {
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fill={CHART_VARS.axis} fontSize={11}>
      {formatDuration(payload?.value ?? 0)}
    </text>
  )
}

export default function DurationTrend({
  model,
  title = 'Duration trend',
  height = 260,
  animate: requested,
}: DurationTrendProps) {
  const animate = useChartAnimation(requested)
  const bandPattern = `${useChartPatternPrefix()}-band`
  const rows = model.points.map((point) => ({
    x: point.x,
    p50: point.p50,
    p95: point.p95,
    // A Recharts range area: `[low, high]`, or `null` for a gap.
    band: point.low === null || point.high === null ? null : [point.low, point.high],
    inverted: point.inverted,
    reason: point.reason,
  }))
  const legend: LegendEntry[] = [
    { key: 'p50', label: 'p50', stroke: CHART_VARS.series[3] },
    { key: 'p95', label: 'p95', stroke: CHART_VARS.series[4], dash: '7 3' },
    // The band is the POINT of the chart and it had no legend entry at all —
    // the one mark on the chart that nothing named.
    { key: 'band', label: BAND_LEGEND_LABEL, fill: `url(#${bandPattern})` },
  ]

  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () =>
      model.points.map((point) => ({
        key: point.x,
        text: [
          point.x,
          `p50 ${point.p50 === null ? NO_VALUE : formatDuration(point.p50)}`,
          `p95 ${point.p95 === null ? NO_VALUE : formatDuration(point.p95)}`,
          point.inverted ? 'p95 is below p50 here, as reported' : null,
          point.p50 === null && point.reason ? point.reason : null,
        ]
          .filter(Boolean)
          .join(', '),
      })),
    [model],
  )
  const cursor = useChartCursor({
    title,
    chartType: 'line chart with a shaded band',
    points: cursorPoints,
    noun: 'day',
  })

  return (
    <figure data-chart="duration-trend" data-inverted={model.inverted || undefined} className="m-0 flex flex-col gap-1">
      {/*
        The band's hatch. A flat 18 % wash of series-5 measures 1.19:1 against
        the card — invisible to anyone who is not looking for it, and far under
        the 3:1 SC 1.4.11 asks of a data-carrying object. The stripes are drawn
        at FULL series-5 (3.4–5.2:1 depending on the theme) and the area keeps
        a boundary stroke in the same colour, so the band reads as a shape with
        an edge rather than as a tint.
      */}
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>
          <pattern id={bandPattern} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={6} height={6} fill={CHART_VARS.card} />
            <rect width={2} height={6} fill={CHART_VARS.series[4]} />
          </pattern>
        </defs>
      </svg>

      <div
        data-duration-trend-plot=""
        className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
        {...cursor.surfaceProps}
      >
      <ResponsiveContainer width="100%" height={height}>
        {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
        <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 8, bottom: 8 }} accessibilityLayer={false}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
          <XAxis
            dataKey="x"
            axisLine={false}
            tickLine={false}
            tick={RECHARTS_AXIS_TICK}
            dy={8}
            height={48}
            label={{ value: DURATION_TREND_X_AXIS, position: 'insideBottom', fill: CHART_VARS.axis, fontSize: 11 }}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={<DurationTick />}
            width={78}
            label={{ value: DURATION_TREND_Y_AXIS, angle: -90, position: 'insideLeft', fill: CHART_VARS.axis, fontSize: 11 }}
          />
          <Tooltip key={cursor.tipKey} content={<DurationTrendTooltip />} {...cursor.tipProps} />
          <Legend content={() => <ChartLegend entries={legend} />} />
          <Area
            dataKey="band"
            name={BAND_LEGEND_LABEL}
            type={DURATION_TREND_CURVE}
            stroke={CHART_VARS.series[4]}
            strokeWidth={1}
            fill={`url(#${bandPattern})`}
            fillOpacity={1}
            connectNulls={false}
            isAnimationActive={animate}
          />
          <Line
            dataKey="p50"
            name="p50"
            type={DURATION_TREND_CURVE}
            stroke={CHART_VARS.series[3]}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={animate}
          />
          <Line
            dataKey="p95"
            name="p95"
            type={DURATION_TREND_CURVE}
            stroke={CHART_VARS.series[4]}
            strokeDasharray="7 3"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={animate}
          />
        </ComposedChart>
      </ResponsiveContainer>
      {cursor.readout}
      </div>

      <figcaption data-chart-axis-caption="" className={NOTE}>
        {DURATION_TREND_CAPTION}
      </figcaption>

      {model.notice && (
        <p data-chart-band-notice="" className={NOTE}>
          {model.notice}
        </p>
      )}
    </figure>
  )
}

function DurationTrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  label?: string | number
  payload?: { payload?: { p50: number | null; p95: number | null; inverted?: boolean; reason?: string | null } }[]
}) {
  const row = payload?.[0]?.payload
  if (!active || !row) return null
  return (
    <div
      data-chart-tooltip=""
      className="rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)]"
    >
      <div className="font-semibold">{String(label)}</div>
      <div>p50 {row.p50 === null ? NO_VALUE : formatDuration(row.p50)}</div>
      <div>p95 {row.p95 === null ? NO_VALUE : formatDuration(row.p95)}</div>
      {row.inverted && <div className="text-[var(--color-text-secondary)]">p95 is below p50 here, as reported</div>}
      {row.p50 === null && row.reason && <div className="text-[var(--color-text-secondary)]">{row.reason}</div>}
    </div>
  )
}
