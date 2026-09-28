/**
 * `DurationHistogram` (VIZ-406) — how long tests take, on log-spaced buckets.
 *
 * The edges are LABELLED. A log axis whose edges a reader cannot read is a
 * shape, not a measurement: "the second bar is tallest" means nothing unless
 * the reader can see that the second bar is 2–5 ms.
 *
 * The last bar is the OVERFLOW bucket, drawn hatched and labelled "≥ X". It
 * exists so one 40-minute test cannot stretch the axis until the other 5 000
 * executions share a single pixel — and so that outlier is still visibly
 * present rather than quietly dropped.
 *
 * Executions with no duration, and executions recorded as exactly zero, are
 * excluded (a log axis has no place for either) and the count is STATED in the
 * chart's own text. A histogram over an unknown fraction of the data is a
 * histogram of nothing in particular.
 *
 * Durations are formatted with `formatDuration` — the same formatter
 * `TimingCell` uses — so a bucket edge and a run's duration read identically.
 */
import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_VARS, RECHARTS_AXIS_TICK } from './tokens'
import { useChartAnimation } from './motion'
import { formatNumber } from '@/utils/formatters'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip, useColumnMark } from './ChartTooltip'
import { useFramePlotLayoutHeight } from './framePlotHeight'
import { COLUMN_SIDES } from './tipPlacement'
import ChartResponsive from './ChartResponsive'
import { sampleRow, shareRow, tipContent, type TooltipContent } from './tooltip'
import type { DurationBucket, DurationHistogramModel } from './durationBuckets'
import { useChartPatternPrefix } from './patterns'
import { useTextMeasure } from './textMeasure'

export const HISTOGRAM_EMPTY = 'No execution in this window carries a duration'
/** What the buckets are. Only ever shown under drawn buckets. */
export const HISTOGRAM_BUCKETS_CAPTION =
  'Buckets are log-spaced (1-2-5 per decade); the last bucket counts everything above the axis.'

export interface DurationHistogramProps {
  model: DurationHistogramModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title?: string
  height?: number
  animate?: boolean
}

const NOTE = 'text-xs text-[var(--color-text-secondary)]'
/** What the caption, the excluded count and the placed count under the plot keep back in full screen, px. */
const HISTOGRAM_NOTES_RESERVE = 64

/** The bucket labels' slant, degrees: every edge stays readable without a label per two buckets. */
const BUCKET_LABEL_ANGLE = 30
/** The bucket labels' size, px (`RECHARTS_AXIS_TICK`). */
const BUCKET_LABEL_SIZE = RECHARTS_AXIS_TICK.fontSize
/**
 * The room under the plot for the slanted bucket labels where they cannot be
 * measured (no layout: jsdom), and the least it ever is.
 */
export const BUCKET_AXIS_MIN_HEIGHT = 56
/** Where Recharts anchors a bottom tick label: `tickSize` (6) + `tickMargin` (2) below the axis. */
const TICK_ANCHOR = 8
/**
 * From a bottom tick's anchor to the bottom of its first line, in em: Recharts
 * drops the baseline its `capHeight` (0.71em) below the anchor, and a glyph's
 * descent reaches at most ~0.3em below the baseline in any common UI font.
 */
const TICK_LINE_DEPTH_EM = 0.71 + 0.3

/**
 * The height the x axis needs for bucket labels slanted by
 * `BUCKET_LABEL_ANGLE` whose longest is `longest` px wide.
 *
 * A label is anchored at its END, under its bucket, and slants down-left from
 * there: its start reaches `longest · sin(angle)` further down, plus its
 * line's depth turned by the angle. The axis was a fixed 56 px, which held the
 * longest label in Segoe UI and cut it by 5 px in DejaVu Sans (the Linux CI
 * runner's `system-ui`): sized from the label, it holds in every font.
 */
export function bucketAxisHeight(longest: number, fontSize: number = BUCKET_LABEL_SIZE): number {
  const angle = (BUCKET_LABEL_ANGLE * Math.PI) / 180
  const needed = TICK_ANCHOR + longest * Math.sin(angle) + TICK_LINE_DEPTH_EM * fontSize * Math.cos(angle)
  return Math.max(BUCKET_AXIS_MIN_HEIGHT, Math.ceil(needed) + 1)
}

export default function DurationHistogram({
  model,
  title = 'Duration distribution',
  height: requestedHeight = 260,
  animate: requested,
}: DurationHistogramProps) {
  // Full screen (VIZ-608): the plot takes the frame's body, less room for the three notes under it.
  // Full screen shows the drawing scaled up (`ChartResponsive`): it is LAID OUT at page text size.
  const height = useFramePlotLayoutHeight(requestedHeight, HISTOGRAM_NOTES_RESERVE)
  const animate = useChartAnimation(requested)
  const patternId = `${useChartPatternPrefix()}-overflow`
  // `isOverflow`, not `overflow`: Recharts spreads each data row's own fields
  // onto the rendered shape, and `overflow` is a real SVG presentation
  // attribute — React then logs "Received `false` for a non-boolean attribute
  // `overflow`" on every render. A field name that is not an SVG attribute
  // keeps the row a row.
  const rows = model.buckets.map((bucket) => ({
    label: bucket.label,
    count: bucket.count,
    isOverflow: bucket.overflow,
  }))

  // The SAME content the pointer's tooltip shows (VIZ-601).
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.buckets.map((bucket) => cursorPoint(bucket.label, histogramTipContent(model, bucket))),
    [model],
  )
  const cursor = useChartCursor({ title, chartType: 'histogram', points: cursorPoints, noun: 'bucket' })
  // The x axis is as tall as its longest slanted label needs, in the font it is drawn in.
  const [plotRef, measure] = useTextMeasure<HTMLDivElement>()
  const axisHeight = useMemo(() => {
    if (!measure) return BUCKET_AXIS_MIN_HEIGHT
    let longest = 0
    for (const bucket of model.buckets) longest = Math.max(longest, measure(bucket.label, BUCKET_LABEL_SIZE))
    return bucketAxisHeight(longest)
  }, [measure, model.buckets])

  return (
    <figure data-chart="duration-histogram" className="m-0 flex flex-col gap-1">
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>
          <pattern id={patternId} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={6} height={6} fill={CHART_VARS.card} />
            <rect width={2.5} height={6} fill={CHART_VARS.series[2]} />
          </pattern>
        </defs>
      </svg>

      {rows.length === 0 ? (
        <p data-chart-empty="" style={{ minHeight: height / 4 }} className="text-sm text-[var(--color-text-secondary)]">
          {HISTOGRAM_EMPTY}
        </p>
      ) : (
        <div
          ref={plotRef}
          data-duration-histogram-plot=""
          className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          {...cursor.surfaceProps}
        >
        <ChartResponsive height={height}>
          {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} accessibilityLayer={false}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
            {/* The edges ARE the axis: every bucket shows the range it counts. */}
            <XAxis dataKey="label" axisLine={false} tickLine={false} tick={RECHARTS_AXIS_TICK} interval={0} angle={-BUCKET_LABEL_ANGLE} textAnchor="end" height={axisHeight} />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={RECHARTS_AXIS_TICK}
              allowDecimals={false}
              // `offset: 14`, as the time series' rate title: at the default 5
              // the rotated title's line box overhung the svg's left edge by
              // 4 px, and was cut — on the page and in full screen alike.
              label={{ value: 'Executions', angle: -90, position: 'insideLeft', offset: 14, fill: CHART_VARS.axis, fontSize: 11 }}
            />
            {/* Pinned beside its bucket (`PinnedTip`): a fixed origin, no slide. */}
            <Tooltip
              key={cursor.tipKey}
              cursor={false}
              content={<HistogramTooltip model={model} />}
              position={{ x: 0, y: 0 }}
              isAnimationActive={false}
              {...cursor.tipProps}
            />
            <Bar dataKey="count" name="Executions" isAnimationActive={animate}>
              {rows.map((row) => (
                <Cell
                  key={row.label}
                  data-overflow={String(row.isOverflow)}
                  // Hatched, not merely another colour: the overflow bucket
                  // counts a DIFFERENT thing (everything above the axis) and
                  // must not read as just one more equal-width bucket.
                  fill={row.isOverflow ? `url(#${patternId})` : CHART_VARS.series[2]}
                  stroke={row.isOverflow ? CHART_VARS.series[2] : undefined}
                />
              ))}
            </Bar>
          </BarChart>
        </ChartResponsive>
        {cursor.readout}
        </div>
      )}

      {/*
        Explains the BUCKETS, so it is shown only when buckets are drawn: under
        the "nothing timed" message it described an axis that is not there.
      */}
      {rows.length > 0 && <figcaption className={NOTE}>{HISTOGRAM_BUCKETS_CAPTION}</figcaption>}
      {model.excludedStatement && (
        <p data-chart-excluded="" className={NOTE}>
          {model.excludedStatement}
        </p>
      )}
      {model.counted > 0 && (
        <p data-chart-counted="" className={NOTE}>
          {formatNumber(model.counted)} of {formatNumber(model.total)} executions placed.
        </p>
      )}
    </figure>
  )
}

/** What an open-ended bucket counts, said in its tooltip. */
export const OVERFLOW_NOTE = 'Everything above the axis'
export const UNDERFLOW_NOTE = 'Everything below the axis'

/**
 * One bucket's tooltip content (VIZ-601): its range, its exact count, its
 * share of every execution the chart could place, and that placed count as
 * the sample — a histogram over an unstated fraction of the data is a
 * histogram of nothing in particular. An open-ended bucket says so.
 */
export function histogramTipContent(model: DurationHistogramModel, bucket: DurationBucket): TooltipContent {
  return tipContent(bucket.label, [
    { kind: 'value', key: 'count', label: 'Executions', value: formatNumber(bucket.count), color: CHART_VARS.series[2] },
    shareRow(bucket.count, model.counted),
    { ...sampleRow(model.counted), detail: 'executions placed' },
    bucket.overflow && { kind: 'note', key: 'overflow', label: '', value: OVERFLOW_NOTE },
    bucket.underflow && { kind: 'note', key: 'underflow', label: '', value: UNDERFLOW_NOTE },
  ])
}

/** Recharts' tooltip: the shared content model, pinned beside the bucket's bar. */
function HistogramTooltip({
  active,
  label,
  coordinate,
  model,
}: {
  active?: boolean
  label?: string | number
  coordinate?: { x?: number; y?: number }
  model: DurationHistogramModel
}) {
  const bucket = model.buckets.find((entry) => entry.label === String(label))
  // A bucket's bar fills its band, less Recharts' default 10% category gap.
  const { mark, gap, chartBox, sweep } = useColumnMark(coordinate, model.buckets.length, Number.POSITIVE_INFINITY)
  const content = active && bucket ? histogramTipContent(model, bucket) : null
  return <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" gap={gap} chartBox={chartBox} sweep={sweep} />
}
