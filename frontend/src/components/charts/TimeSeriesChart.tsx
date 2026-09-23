/**
 * `TimeSeriesChart` (VIZ-403) — pass rate over time with execution volume and
 * release boundaries. Generalises the orphaned `TrendChart` into the catalogue's
 * one time-series renderer.
 *
 * Everything it draws comes from `timeSeriesModel` (pure, tested on its own);
 * this file is only the drawing. What it is careful about:
 *
 *   - A day with no runs is a GAP. `connectNulls` stays off, so the line breaks
 *     rather than sliding through a day nobody ran anything, and the executions
 *     bar keeps its honest zero beside it.
 *   - The second axis has its OWN title. A bare right-hand scale next to a
 *     percentage axis is read as more percentages.
 *   - A zoomed rate axis is allowed, and then the "does not start at 0"
 *     indicator is not optional: the same 3-point dip looks like a collapse on a
 *     90–100 axis.
 *   - Isolated measured points (including a single-point series) are drawn as
 *     DOTS. A line renderer draws nothing at all for a point with no measured
 *     neighbour, so without this the chart would be blank while holding data.
 *   - The partial UTC day is hatched AND dashed — never a colour-only
 *     difference — it has its own legend entry, and its tooltip names the runs
 *     still in progress. The note under the plot fires on a DRAWN partial
 *     bucket, not on `meta.partial_day`, which names today whether or not
 *     today is on the axis.
 *   - Every bar is drawn SOLID. A washed-out fill is under the 3:1 SC 1.4.11
 *     asks of a data-carrying object, and it made the one incomplete bar the
 *     brightest thing on the chart.
 *   - The plot sits inside a named, focusable `role="group"` with a keyboard
 *     cursor (`useChartCursor`), rather than Recharts' unnamed
 *     `role="application"`.
 *   - Buckets are UTC days; the caption says so and the tooltip carries the
 *     viewer's local equivalent, including when a DST change makes the day's two
 *     ends sit at different offsets.
 *
 * Past `SVG_POINT_LIMIT` points the ECharts renderer takes over (canvas +
 * `sampling`); the engine is reached only through `useEChart`, and its tooltip
 * only through `domTooltipFormatter`.
 */
import { useId, useMemo, type ReactElement } from 'react'
import { useChartCursor, type ChartCursorPoint } from './ChartCursor'
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatNumber } from '@/utils/formatters'
import { CHART_VARS, RECHARTS_AXIS_TICK, useChartTokens } from './tokens'
import { NO_VALUE } from './chartText'
import { useChartAnimation } from './motion'
import { ChartLegend, type LegendEntry } from './patterns'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { useEChart } from './engines/useEChart'
import {
  buildTimeSeriesOption,
  timeSeriesTooltipContent,
  type InProgressRuns,
} from './engines/echarts/timeSeriesOption'
import {
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
  utcTodayNote,
  type TimeSeriesModel,
} from './timeSeriesModel'

export interface TimeSeriesChartProps {
  model: TimeSeriesModel
  /**
   * The chart's own name: it names the focusable drawing surface the keyboard
   * cursor puts around the plot (`useChartCursor`). `TimeSeriesChartFrame`
   * passes the frame's title, so the reader hears the chart they can see.
   */
  title?: string
  height?: number
  /** Forwarded to the engines' animation switch; off under `prefers-reduced-motion`. */
  animate?: boolean
  /** Runs still executing in a bucket, named in that bucket's tooltip. */
  inProgressRuns?: readonly InProgressRuns[]
  /** IANA zone for the local equivalent. Defaults to the viewer's own. */
  timeZone?: string
  locale?: string
  /** Test seam for "now" when deciding whether an empty UTC "today" needs explaining. */
  now?: Date
  /** One sentence for assistive tech, when the frame's own name is not enough. */
  description?: string
}

const NOTE = 'text-xs text-[var(--color-text-secondary)]'

/** What the hatched bar is called, in the legend and in the note under the plot. */
export const PARTIAL_LEGEND_LABEL = 'Still filling'

interface Row {
  x: string
  rate: number | null
  executions: number | null
  partial: boolean
}

const toRows = (model: TimeSeriesModel): Row[] =>
  model.points.map((point) => ({
    x: point.x,
    rate: point.rate,
    executions: point.executions,
    partial: point.partial,
  }))

// ── The tooltip ──────────────────────────────────────────────────────────────

export interface TimeSeriesTooltipProps {
  /** Recharts passes this; the tooltip renders nothing when it is not set. */
  active?: boolean
  /** The x value Recharts is hovering. */
  label?: string | number
  model: TimeSeriesModel
  timeZone?: string
  locale?: string
  inProgressRuns?: readonly InProgressRuns[]
}

/**
 * A REACT tooltip, not a string formatter: the values in it (release names,
 * server reasons) are ingested CI text, and React escapes them by construction.
 */
export function TimeSeriesTooltip({
  active,
  label,
  model,
  timeZone,
  locale,
  inProgressRuns,
}: TimeSeriesTooltipProps) {
  const index = model.points.findIndex((point) => point.x === String(label))
  if (!active || index < 0) return null
  const content = timeSeriesTooltipContent({ model, index, locale, timeZone, inProgressRuns })
  return (
    <div
      data-chart-tooltip=""
      className="rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-xs text-[var(--color-text)]"
    >
      {content.title && <div className="font-semibold">{content.title}</div>}
      {content.rows.map((row) => (
        <div key={row.label} className="flex items-baseline gap-3">
          <span className="text-[var(--color-text-secondary)]">{row.label}</span>
          <span className="ml-auto font-medium tabular-nums">{row.value}</span>
        </div>
      ))}
    </div>
  )
}

// ── The SVG (Recharts) renderer ──────────────────────────────────────────────

function SvgTimeSeries({
  model,
  rows,
  height,
  animate,
  patternId,
  tooltip,
  title,
  locale,
  timeZone,
  inProgressRuns,
  partialDrawn,
}: {
  model: TimeSeriesModel
  rows: Row[]
  height: number
  animate: boolean | undefined
  patternId: string
  tooltip: ReactElement
  title: string
  locale?: string
  timeZone?: string
  inProgressRuns?: readonly InProgressRuns[]
  /** True only when a DRAWN bucket is the partial one — see the figure below. */
  partialDrawn: boolean
}) {
  // Isolated points (a single-point series included) MUST be dots: a line
  // renderer draws nothing for a point with no measured neighbour.
  const showDots = model.singlePoint || model.isolated.length > 0
  const legend: LegendEntry[] = [
    { key: 'rate', label: RATE_AXIS_TITLE, stroke: CHART_VARS.series[0] },
    { key: 'executions', label: EXECUTIONS_AXIS_TITLE, fill: CHART_VARS.series[1] },
  ]
  // The hatch is a mark like any other, so it gets a legend entry like any
  // other: an undescribed texture is a second thing the reader has to guess.
  if (partialDrawn) {
    legend.push({ key: 'partial', label: `${PARTIAL_LEGEND_LABEL} (${model.partialDay})`, fill: `url(#${patternId})` })
  }

  /**
   * One cursor stop per bucket, worded by the SAME builder the mouse tooltip
   * uses — so the keyboard reader and the pointer reader are told the same
   * thing about the same day, down to the local equivalent.
   */
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () =>
      model.points.map((point, index) => {
        const content = timeSeriesTooltipContent({ model, index, locale, timeZone, inProgressRuns })
        return {
          key: point.x,
          text: [content.title, ...content.rows.map((row) => `${row.label} ${row.value}`)].join(', '),
        }
      }),
    [model, locale, timeZone, inProgressRuns],
  )
  const cursor = useChartCursor({ title, chartType: 'line and bar chart', points: cursorPoints, noun: 'day' })

  return (
    <div
      data-time-series-plot=""
      data-executions-max={model.executionsAxis.largest}
      data-executions-domain={model.executionsAxis.domain.join(',')}
      data-rate-domain={model.rateAxis.domain.join(',')}
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
    <ResponsiveContainer width="100%" height={height}>
      {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
      <ComposedChart data={rows} margin={{ top: 16, right: 8, left: 0, bottom: 0 }} accessibilityLayer={false}>
        {/*
          `yAxisId="rate"`: the grid's default axis id is 0, which this chart
          does not have, so it drew no line between the plot's top and bottom
          edges and no tick could be followed across the plot. The executions
          axis shares the rate axis's interval count, so its ticks sit on these
          same lines.
        */}
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} yAxisId="rate" />
        <XAxis dataKey="x" axisLine={false} tickLine={false} tick={RECHARTS_AXIS_TICK} dy={8} />
        {/*
          Both y axes take their TICKS from the model, not only a domain:
          left to itself Recharts ticked a 90-100 axis at 90, 93, 96, 100.
        */}
        <YAxis
          yAxisId="rate"
          domain={model.rateAxis.domain}
          ticks={model.rateAxis.ticks}
          interval={0}
          allowDataOverflow
          axisLine={false}
          tickLine={false}
          tick={RECHARTS_AXIS_TICK}
          label={{ value: RATE_AXIS_TITLE, angle: -90, position: 'insideLeft', fill: CHART_VARS.axis, fontSize: 11 }}
        />
        <YAxis
          yAxisId="executions"
          orientation="right"
          // Reaches the tallest bar (never clipped) and shares the rate axis's
          // interval count, so its ticks sit on the same grid lines.
          domain={model.executionsAxis.domain}
          ticks={model.executionsAxis.ticks}
          interval={0}
          allowDecimals={false}
          axisLine={false}
          tickLine={false}
          tick={RECHARTS_AXIS_TICK}
          label={{ value: EXECUTIONS_AXIS_TITLE, angle: 90, position: 'insideRight', fill: CHART_VARS.axis, fontSize: 11 }}
        />
        <Tooltip key={cursor.tipKey} content={tooltip} {...cursor.tipProps} />
        <Legend content={() => <ChartLegend entries={legend} />} />
        <Bar yAxisId="executions" dataKey="executions" name={EXECUTIONS_AXIS_TITLE} isAnimationActive={animate} barSize={14}>
          {rows.map((row) => (
            <Cell
              key={row.x}
              data-partial={row.partial}
              // The still-filling day is hatched AND dashed: a difference that
              // survives a monochrome print and a colour-blind reader.
              //
              // An ordinary day is drawn SOLID. It used to be a 45 % wash,
              // which measures 2.5:1 against the darkest card and 1.6:1
              // against the light one — under the 3:1 SC 1.4.11 asks of an
              // object that carries data — and which had the side effect of
              // making the one INCOMPLETE bar the brightest thing on the
              // chart. Solid days and a hatched partial day put the emphasis
              // back where the data is: the hatch tile is mostly card colour,
              // so the still-filling bar carries visibly less ink than a
              // finished one while staying distinct by TEXTURE, not by hue.
              fill={row.partial ? `url(#${patternId})` : CHART_VARS.series[1]}
              fillOpacity={1}
              stroke={row.partial ? CHART_VARS.series[1] : undefined}
              strokeDasharray={row.partial ? '4 2' : undefined}
            />
          ))}
        </Bar>
        <Line
          yAxisId="rate"
          type="monotone"
          dataKey="rate"
          name={RATE_AXIS_TITLE}
          stroke={CHART_VARS.series[0]}
          strokeWidth={2}
          // A gap is a gap: bridging it would invent a day that never ran.
          connectNulls={false}
          dot={showDots ? { r: 3, fill: CHART_VARS.series[0] } : false}
          activeDot={{ r: 4 }}
          isAnimationActive={animate}
        />
        {model.markers.map((marker) => (
          <ReferenceLine
            key={marker.x}
            yAxisId="rate"
            x={marker.x}
            stroke={CHART_VARS.neutral}
            strokeDasharray="5 3"
            label={{ value: marker.names.join(', '), position: 'top', fill: CHART_VARS.axis, fontSize: 10 }}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
    {cursor.readout}
    </div>
  )
}

// ── The canvas (ECharts) renderer, past SVG_POINT_LIMIT points ───────────────

const BUTTON = 'rounded border border-[var(--color-border-light)] px-3 py-1 text-[var(--color-text)]'

function CanvasTimeSeries({
  model,
  height,
  animate,
  description,
  locale,
  timeZone,
  inProgressRuns,
}: {
  model: TimeSeriesModel
  height: number
  animate: boolean | undefined
  description: string
  locale?: string
  timeZone?: string
  inProgressRuns?: readonly InProgressRuns[]
}) {
  const tokens = useChartTokens()
  const option = useMemo(
    () => buildTimeSeriesOption({ model, tokens, description, animate: animate === true, locale, timeZone, inProgressRuns }),
    [model, tokens, description, animate, locale, timeZone, inProgressRuns],
  )
  const { containerRef, status, retry } = useEChart('timeSeries', option)

  if (status === 'stale-build' || status === 'error') {
    return (
      <div
        data-chart-draw-error={status}
        style={{ height }}
        className="flex flex-col items-center justify-center gap-2 text-sm text-[var(--color-text-secondary)]"
      >
        <span>{status === 'stale-build' ? STALE_BUILD_MESSAGE : CHART_DRAW_ERROR}</span>
        <button type="button" onClick={status === 'stale-build' ? () => window.location.reload() : retry} className={BUTTON}>
          {status === 'stale-build' ? STALE_BUILD_ACTION : 'Try again'}
        </button>
      </div>
    )
  }
  return (
    <div
      ref={containerRef}
      data-testid="time-series-canvas"
      data-chart-engine="echarts"
      data-chart-type="timeSeries"
      data-chart-status={status}
      role="img"
      aria-label={description}
      style={{ width: '100%', height }}
    />
  )
}

// ── The chart ────────────────────────────────────────────────────────────────

export default function TimeSeriesChart({
  model,
  title = 'Pass rate over time',
  height = 280,
  animate: requestedAnimate,
  inProgressRuns,
  timeZone,
  locale,
  now,
  description = 'Pass rate and execution volume over time',
}: TimeSeriesChartProps) {
  const animate = useChartAnimation(requestedAnimate)
  const patternId = `${useId().replace(/[^A-Za-z0-9_-]/g, '')}-partial-day`
  const rows = useMemo(() => toRows(model), [model])
  const latest = model.points[model.points.length - 1] ?? null
  const todayNote = utcTodayNote({ latest, now: now ?? new Date(), timeZone })
  /**
   * Whether a bucket the chart actually DRAWS is the partial one.
   *
   * `meta.partial_day` names today unconditionally, and a metric the server
   * does not zero-fill (`duration_p50` / `duration_p95` are `zero_fill=False`)
   * simply omits a day it has nothing to report for — including today. The
   * flag would then name a day that is nowhere on the axis, and the note said
   * "2026-09-22 is still filling" beside a chart whose newest bar is the 19th.
   * The note and the marker attribute both belong to a DRAWN bar, so they are
   * gated on one existing.
   */
  const partialDrawn = model.points.some((point) => point.partial)

  return (
    <figure data-chart="time-series" data-renderer={model.renderer} data-single-point={model.singlePoint ? 'true' : undefined} data-partial-day={(partialDrawn && model.partialDay) || undefined} className="m-0 flex flex-col gap-1">
      {/*
        Document-scoped defs: an SVG `url(#…)` fill resolves against the whole
        document, so the hatch lives here rather than inside a chart the canvas
        renderer does not draw.
      */}
      <svg width={0} height={0} aria-hidden="true" focusable="false" className="absolute">
        <defs>
          <pattern id={patternId} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={6} height={6} fill={CHART_VARS.card} />
            <rect width={2.5} height={6} fill={CHART_VARS.series[1]} />
          </pattern>
        </defs>
      </svg>

      {model.renderer === 'echarts' ? (
        <CanvasTimeSeries
          model={model}
          height={height}
          animate={animate}
          description={description}
          locale={locale}
          timeZone={timeZone}
          inProgressRuns={inProgressRuns}
        />
      ) : (
        <SvgTimeSeries
          model={model}
          rows={rows}
          height={height}
          animate={animate}
          patternId={patternId}
          title={title}
          locale={locale}
          timeZone={timeZone}
          inProgressRuns={inProgressRuns}
          partialDrawn={partialDrawn}
          tooltip={
            <TimeSeriesTooltip model={model} timeZone={timeZone} locale={locale} inProgressRuns={inProgressRuns} />
          }
        />
      )}

      <figcaption data-chart-axis-caption="" className={NOTE}>
        {model.caption}
      </figcaption>

      {model.rateAxis.label && (
        <p data-chart-axis-zero-indicator="" className={NOTE}>
          {model.rateAxis.label}
        </p>
      )}
      {partialDrawn && model.partialDay && (
        <p data-chart-partial-note="" className={NOTE}>
          {model.partialDay} is still filling
          {model.inProgressCount > 0 ? ` (${formatNumber(model.inProgressCount)} in progress)` : ''}.
        </p>
      )}
      {todayNote && (
        <p data-chart-utc-note="" className={NOTE}>
          {todayNote}
        </p>
      )}
      {model.markersOutsideWindow > 0 && (
        <p data-chart-markers-outside="" className={NOTE}>
          {formatNumber(model.markersOutsideWindow)}{' '}
          {model.markersOutsideWindow === 1 ? 'release is' : 'releases are'} not shown: dated outside this window, or
          carrying a date that could not be read.
        </p>
      )}
      {model.gaps > 0 && (
        <p data-chart-gap-note="" className={NOTE}>
          {formatNumber(model.gaps)} {model.gaps === 1 ? 'day has' : 'days have'} no pass rate ({NO_VALUE}), drawn as a
          gap rather than 0%.
        </p>
      )}
    </figure>
  )
}
