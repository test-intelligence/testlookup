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
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART_VARS, RECHARTS_AXIS_TICK } from './tokens'
import { useChartAnimation } from './motion'
import { formatNumber } from '@/utils/formatters'
import { useChartCursor, type ChartCursorPoint } from './ChartCursor'
import type { DurationHistogramModel } from './durationBuckets'
import { useChartPatternPrefix } from './patterns'

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

export default function DurationHistogram({
  model,
  title = 'Duration distribution',
  height = 260,
  animate: requested,
}: DurationHistogramProps) {
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

  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () =>
      model.buckets.map((bucket) => ({
        key: bucket.label,
        text: `${bucket.label}: ${formatNumber(bucket.count)} ${bucket.count === 1 ? 'execution' : 'executions'}${
          bucket.overflow ? ', everything above the axis' : bucket.underflow ? ', everything below the axis' : ''
        }`,
      })),
    [model],
  )
  const cursor = useChartCursor({ title, chartType: 'histogram', points: cursorPoints, noun: 'bucket' })

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
          data-duration-histogram-plot=""
          className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
          {...cursor.surfaceProps}
        >
        <ResponsiveContainer width="100%" height={height}>
          {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
          <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} accessibilityLayer={false}>
            <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} />
            {/* The edges ARE the axis: every bucket shows the range it counts. */}
            <XAxis dataKey="label" axisLine={false} tickLine={false} tick={RECHARTS_AXIS_TICK} interval={0} angle={-30} textAnchor="end" height={56} />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={RECHARTS_AXIS_TICK}
              allowDecimals={false}
              label={{ value: 'Executions', angle: -90, position: 'insideLeft', fill: CHART_VARS.axis, fontSize: 11 }}
            />
            <Tooltip key={cursor.tipKey} cursor={false} content={<HistogramTooltip />} {...cursor.tipProps} />
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
        </ResponsiveContainer>
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

/** A React tooltip: no string formatter, so a bucket label cannot become markup. */
function HistogramTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: { payload?: { label?: string; count?: number; isOverflow?: boolean } }[]
}) {
  const row = payload?.[0]?.payload
  if (!active || !row) return null
  return (
    <div
      data-chart-tooltip=""
      className="rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)]"
    >
      <div className="font-semibold">{row.label}</div>
      <div>{formatNumber(row.count ?? 0)} executions</div>
      {row.isOverflow && <div className="text-[var(--color-text-secondary)]">Everything above the axis</div>}
    </div>
  )
}
