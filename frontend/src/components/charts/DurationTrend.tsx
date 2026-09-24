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
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatDuration, formatNumber } from '@/utils/formatters'
import { CHART_VARS, RECHARTS_AXIS_TICK } from './tokens'
import { NO_VALUE } from './chartText'
import { useChartAnimation } from './motion'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip, useColumnMark } from './ChartTooltip'
import { useFramePlotHeight, usePresentationScale } from './framePlotHeight'
import { COLUMN_SIDES } from './tipPlacement'
import ChartResponsive from './ChartResponsive'
import {
  CHANGE_LABEL,
  SAMPLE_LABEL,
  changeRow,
  sampleRow,
  tipContent,
  type TooltipContent,
  type TooltipRow,
} from './tooltip'
import { ChartLegend, useChartPatternPrefix, type LegendEntry } from './patterns'
import type { DurationBandModel, DurationBandPoint } from './durationBuckets'
import { zeroBasedScale, type NiceScale } from './niceScale'
import { durationBandMax } from './zoom/zoomModel'

/** p95's dash, in the legend, on the line and in the tooltip's swatch. */
const P95_DASH = '7 3'
/** Half a day's drawn mark: the lines' points (the active dot is 4 px). */
const DAY_DOT_HALF = 4

export interface DurationTrendProps {
  model: DurationBandModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title?: string
  height?: number
  animate?: boolean
  /**
   * The largest duration the axis must reach, in ms. A ZOOMED trend is a
   * slice of a longer window (VIZ-407); passing the whole window's maximum
   * keeps the slice on the window's scale, so zooming in never redraws a 2 s
   * day as the tallest thing on the chart. Omitted: the band's own maximum.
   * Either way the axis is `durationAxis` of it — so a zoomed slice given the
   * window's maximum has EXACTLY the unzoomed axis, ticks and all.
   */
  yMax?: number
  /**
   * Leave room right of the plot for the range brush's end handle (VIZ-407).
   * The strip under the chart spans the PLOT, and each handle sits OUTSIDE its
   * edge; with the usual 8 px margin a handle at the last day ran onto the
   * frame's border. Only a zoomable trend asks for it, so every other duration
   * trend draws exactly as before.
   */
  brushRoom?: boolean
}

/** The plot's right margin, and the larger one a brush's end handle needs (a whole handle, 24 px). */
const PLOT_MARGIN_RIGHT = 8
const PLOT_MARGIN_RIGHT_WITH_BRUSH = 24

/**
 * The duration axis: from zero to a NICE top over `max` (ms), with its ticks
 * (`zeroBasedScale`), handed to Recharts rather than left to it.
 *
 * Recharts' own `auto` axis ticked a 1,373 ms band at 0, 350, 700, 1,050 and
 * 1,400 ms — printed "1.1s" for 1,050, a label naming the wrong value — and a
 * zoomed slice given the fixed domain `[0, max]` lost even that: a fixed
 * domain turns Recharts' nice rounding off, so the zoomed axis ended ON the
 * window's maximum with different ticks from the unzoomed one (Wave 2.4
 * review F6). A scale computed here from the same maximum is the same scale
 * zoomed or not, and every tick is a round number of milliseconds. `null`:
 * nothing measured, nothing to scale — Recharts' default.
 */
export function durationAxis(max: number | undefined): NiceScale | null {
  if (max === undefined || !Number.isFinite(max) || max <= 0) return null
  return zeroBasedScale(max, { intervals: DURATION_AXIS_INTERVALS })
}

/** Intervals on the duration axis — at most; the 1-2-2.5-5 step may use fewer. */
const DURATION_AXIS_INTERVALS = 4

const NOTE = 'text-xs text-[var(--color-text-secondary)]'
/** What the caption and the inverted-day notice under the plot keep back in full screen, px. */
const DURATION_NOTES_RESERVE = 56

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
  height: requestedHeight = 260,
  animate: requested,
  yMax,
  brushRoom = false,
}: DurationTrendProps) {
  // Full screen (VIZ-608): the plot takes the frame's body, less room for the caption and notice under it.
  // Full screen shows the drawing scaled up (`ChartResponsive`): it is LAID OUT at page text size.
  const scale = usePresentationScale()
  const height = Math.round(useFramePlotHeight(requestedHeight, DURATION_NOTES_RESERVE) / scale)
  const animate = useChartAnimation(requested)
  const bandPattern = `${useChartPatternPrefix()}-band`
  // The larger of `yMax` (a zoomed slice's WHOLE window) and what is drawn:
  // the axis reaches every point either way.
  const axis = useMemo(() => {
    const own = durationBandMax(model)
    const top = Math.max(yMax !== undefined && Number.isFinite(yMax) ? yMax : 0, own ?? 0)
    return durationAxis(top > 0 ? top : undefined)
  }, [model, yMax])
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
    { key: 'p95', label: 'p95', stroke: CHART_VARS.series[4], dash: P95_DASH },
    // The band is the POINT of the chart and it had no legend entry at all —
    // the one mark on the chart that nothing named.
    { key: 'band', label: BAND_LEGEND_LABEL, fill: `url(#${bandPattern})` },
  ]

  // The SAME content the pointer's tooltip shows (VIZ-601).
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.points.map((point, index) => cursorPoint(point.x, durationTrendTipContent(model, index))),
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
      <ChartResponsive height={height}>
        {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
        <ComposedChart
          data={rows}
          margin={{ top: 8, right: brushRoom ? PLOT_MARGIN_RIGHT_WITH_BRUSH : PLOT_MARGIN_RIGHT, left: 8, bottom: 8 }}
          accessibilityLayer={false}
        >
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
            // From zero to a nice top over the window's maximum — a zoomed
            // slice's `yMax` is the whole window's — with its ticks (`durationAxis`).
            domain={axis?.domain}
            ticks={axis?.ticks}
            interval={0}
            axisLine={false}
            tickLine={false}
            tick={<DurationTick />}
            width={78}
            label={{ value: DURATION_TREND_Y_AXIS, angle: -90, position: 'insideLeft', fill: CHART_VARS.axis, fontSize: 11 }}
          />
          {/* Pinned beside its day (`PinnedTip`): a fixed origin, no slide. */}
          <Tooltip
            key={cursor.tipKey}
            content={<DurationTrendTooltip model={model} />}
            position={{ x: 0, y: 0 }}
            isAnimationActive={false}
            {...cursor.tipProps}
          />
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
            strokeDasharray={P95_DASH}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={animate}
          />
        </ComposedChart>
      </ChartResponsive>
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

/** What the chart says on a day p95 came out below p50. */
export const INVERTED_NOTE = 'p95 is below p50 here, as reported'

/** A duration difference, whole milliseconds under a second (`formatDuration` prints raw ms). */
const formatDurationChange = (ms: number) => formatDuration(ms < 1000 ? Math.round(ms) : ms)

/**
 * The sample behind a day's percentiles. Usually one number; two when the
 * two percentiles came from different samples, which is exactly when a reader
 * needs to see both (it is how p95 can end up below p50).
 */
function durationSamples(point: DurationBandPoint): TooltipRow | null {
  const { n50, n95 } = point
  if (n50 === null && n95 === null) return null
  if (n50 === null || n95 === null || n50 === n95) return sampleRow((n50 ?? n95) as number)
  return { kind: 'sample', key: 'samples', label: SAMPLE_LABEL, value: `${formatNumber(n50)} (p50), ${formatNumber(n95)} (p95)` }
}

/**
 * One day's tooltip content (VIZ-601): both percentiles as reported (a gap is
 * "—", never 0 ms), the sample behind them, each percentile's change against
 * the previous day, and the notes — an inverted day, the server's reason for
 * a gap. The pointer's tooltip, the keyboard readout and the announcer all
 * read THIS.
 */
export function durationTrendTipContent(model: DurationBandModel, index: number): TooltipContent {
  const point = model.points[index]
  if (!point) return { rows: [] }
  // The first day of a ZOOMED band still has a previous day: the one just
  // before the view (`precedingPoint`, Wave 2.4 review F4).
  const before = index > 0 ? model.points[index - 1] : (model.precedingPoint ?? undefined)
  return tipContent(point.x, [
    {
      kind: 'value',
      key: 'p50',
      label: 'p50',
      value: point.p50 === null ? NO_VALUE : formatDuration(point.p50),
      color: CHART_VARS.series[3],
      mark: 'line',
    },
    {
      kind: 'value',
      key: 'p95',
      label: 'p95',
      value: point.p95 === null ? NO_VALUE : formatDuration(point.p95),
      color: CHART_VARS.series[4],
      mark: 'line',
      dash: P95_DASH,
    },
    durationSamples(point),
    changeRow({
      current: point.p50,
      previous: before ? before.p50 : undefined,
      previousLabel: before?.x,
      formatMagnitude: formatDurationChange,
      label: `p50 ${CHANGE_LABEL.toLowerCase()}`,
    }),
    changeRow({
      current: point.p95,
      previous: before ? before.p95 : undefined,
      previousLabel: before?.x,
      formatMagnitude: formatDurationChange,
      label: `p95 ${CHANGE_LABEL.toLowerCase()}`,
    }),
    point.inverted && { kind: 'note', key: 'inverted', label: '', value: INVERTED_NOTE },
    point.p50 === null && point.reason ? { kind: 'note', key: 'reason', label: '', value: point.reason } : null,
  ])
}

/** Recharts' tooltip: the shared content model, pinned beside the day's column. */
function DurationTrendTooltip({
  active,
  label,
  coordinate,
  model,
}: {
  active?: boolean
  label?: string | number
  coordinate?: { x?: number; y?: number }
  model: DurationBandModel
}) {
  const index = model.points.findIndex((point) => point.x === String(label))
  const { mark, gap, chartBox, sweep } = useColumnMark(coordinate, model.points.length, DAY_DOT_HALF)
  const content = active && index >= 0 ? durationTrendTipContent(model, index) : null
  return <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" gap={gap} chartBox={chartBox} sweep={sweep} />
}
