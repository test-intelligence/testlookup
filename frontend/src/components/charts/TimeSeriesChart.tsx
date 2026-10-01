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
 *
 * TREND OVERLAYS (VIZ-405) are OFF unless `trendOverlays` is passed, and with
 * it absent this component renders exactly what it rendered before they
 * existed — no toolbar, no caption, no extra row key — because every existing
 * gallery item is compared against a committed screenshot
 * (`TimeSeriesChart.defaultRender.test.tsx` pins the DOM). The one deliberate
 * exception is the rate-axis title's `offset: 14`, which fixed a 3.1 px
 * overhang on every time-series item and moved their baselines. With it: two
 * toggles above the plot, the moving average and the least-squares line drawn
 * dashed in a token colour of their own on a card-coloured halo (so neither
 * dissolves into the execution bars it crosses), flagged days marked with a triangle,
 * the statistics strip under the notes, and every overlay value and anomaly
 * rule in the tooltip and the keyboard cursor's text. The numbers all come
 * from `lib/trendStats`. The canvas renderer takes no overlays: the analysis
 * itself stops at 366 days and says so.
 *
 * A RATE TARGET (VIZ-104, Wave 2.5 K2) is OFF unless `rateTarget` is passed,
 * and with it absent the render is byte-for-byte what it was (the same
 * `TimeSeriesChart.defaultRender.test.tsx` pin). With it: a dashed neutral
 * line across the plot at the target on the rate axis, a legend entry drawn
 * with the same dash, and — when the line cannot be drawn — one sentence under
 * the plot saying so, rather than a legend entry for a line that is not there.
 * The line is drawn only where the target lies on the rate axis: a zoomed
 * 92-100 axis has no room for a 90 % target, and stretching the axis to fit it
 * would undo the zoom the reader is looking at. The canvas renderer draws no
 * target, as it draws no overlays: past 366 days the sentence says so.
 */
import { useEffect, useId, useMemo, useState, type ReactElement } from 'react'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ReferenceDot,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
  useXAxisScale,
} from 'recharts'
import { PinnedTip, useColumnMark } from './ChartTooltip'
import { useTextMeasure, type TextMeasure } from './textMeasure'
import {
  MARKER_LABEL_FONT_SIZE,
  MARKER_LABEL_ROW_HEIGHT,
  layoutMarkerLabels,
  markerLabelText,
  type MarkerLabelRow,
} from './releaseMarkerLabels'
import { useFramePlotHeight, useFramePlotLayoutHeight } from './framePlotHeight'
import ChartResponsive from './ChartResponsive'
import { COLUMN_SIDES } from './tipPlacement'
import { tipContent, type TooltipContent } from './tooltip'
import {
  analyzeTrend,
  trendRowsForDay,
  type TrendAnalysis,
  type TrendDayRow,
  type TrendOverlayState,
} from '@/lib/trendStats'
import { AnomalyMarker, TrendOverlayControls, TrendStatsStrip } from './TimeSeriesChartOverlays'
import {
  TREND_OVERLAYS_OFF,
  TREND_OVERLAY_HALO_Z_INDEX,
  TREND_OVERLAY_KEYS,
  TREND_OVERLAY_STYLE,
  trendOverlayHaloWidth,
} from './TimeSeriesChartOverlayStyle'
import { formatNumber } from '@/utils/formatters'
import { CHART_VARS, RECHARTS_AXIS_TICK, useChartTokens } from './tokens'
import { NO_VALUE, utcDayLabel } from './chartText'
import { useChartAnimation } from './motion'
import { ChartLegend, type LegendEntry } from './patterns'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'
import { useEChart } from './engines/useEChart'
import {
  buildTimeSeriesOption,
  timeSeriesTooltipContent,
  type InProgressRuns,
  type TimeSeriesTooltipInput,
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
  /**
   * VIZ-405 trend overlays. Leave it out and nothing about the chart changes.
   * `{}` offers the toggles (both off, state kept here); pass `shown` +
   * `onShownChange` to control them from outside (`TimeSeriesChartFrame`
   * does, because its takeaway follows them).
   */
  trendOverlays?: TimeSeriesTrendOverlays
  /**
   * VIZ-104 K2: a target on the RATE axis (percent), drawn as a dashed line
   * with a legend entry. Leave it out and nothing about the chart changes.
   */
  rateTarget?: TimeSeriesRateTarget
}

export interface TimeSeriesRateTarget {
  /** Percent, on the rate axis (0-100). */
  value: number
  /** Its legend entry and its name in the summary, e.g. "Target 90%". */
  label: string
}

/** The target line's dash: longer than the partial day's `4 2`, and neutral, never a series colour. */
export const RATE_TARGET_DASH = '6 4'

/** Whether the target can be drawn: the SVG renderer, and a value on the rate axis. */
export function rateTargetDrawn(model: TimeSeriesModel, target: TimeSeriesRateTarget): boolean {
  const [low, high] = model.rateAxis.domain
  return model.renderer === 'svg' && Number.isFinite(target.value) && target.value >= low && target.value <= high
}

/**
 * The one sentence about the target: where it is drawn, or why it is not.
 * The frame's summary and table view say it; the chart's own note says it
 * only when the line is NOT drawn (the legend already names a drawn one).
 */
export function rateTargetSentence(model: TimeSeriesModel, target: TimeSeriesRateTarget): string {
  const at = `${formatNumber(target.value, { maximumFractionDigits: 1 })}%`
  if (rateTargetDrawn(model, target)) return `${target.label}: a dashed line at ${at} on the rate axis.`
  if (model.renderer !== 'svg') return `${target.label} (${at}) is not drawn on a window this long.`
  const [low, high] = model.rateAxis.domain
  return `${target.label} (${at}) is outside the rate axis (${formatNumber(low)}-${formatNumber(high)}%) and is not drawn.`
}

export interface TimeSeriesTrendOverlays {
  /** Computed from `model.points` when omitted — pass it to share one analysis with the frame. */
  analysis?: TrendAnalysis
  /** Controlled state. */
  shown?: TrendOverlayState
  onShownChange?: (next: TrendOverlayState) => void
  /** Uncontrolled starting state. Both overlays start off. */
  initialShown?: Partial<TrendOverlayState>
}

/** The analysis as the renderer needs it: available, and which overlays to draw. */
interface DrawnTrend {
  analysis: Extract<TrendAnalysis, { available: true }>
  shown: TrendOverlayState
}

const NOTE = 'text-xs text-[var(--color-text-secondary)]'
/** What the notes, caption and statistics strip under the plot keep back in full screen, px. */
const TIME_SERIES_NOTES_RESERVE = 112

/** What the hatched bar is called, in the legend and in the note under the plot. */
export const PARTIAL_LEGEND_LABEL = 'Still filling'

interface Row {
  x: string
  rate: number | null
  executions: number | null
  partial: boolean
  /** Present only while trend overlays are requested — the default rows are unchanged. */
  movingAverage?: number | null
  trendLine?: number | null
}

const toRows = (model: TimeSeriesModel, trend: DrawnTrend | null = null): Row[] => {
  if (!trend) {
    return model.points.map((point) => ({
      x: point.x,
      rate: point.rate,
      executions: point.executions,
      partial: point.partial,
    }))
  }
  const average = new Map(trend.analysis.movingAverage.map((point) => [point.x, point.value]))
  const fitted = new Map(trend.analysis.fit.line.map((point) => [point.x, point.value]))
  return model.points.map((point) => ({
    x: point.x,
    rate: point.rate,
    executions: point.executions,
    partial: point.partial,
    // `null`, not a number, on a day with no average: the overlay breaks there too.
    movingAverage: average.get(point.x) ?? null,
    trendLine: fitted.get(point.x) ?? null,
  }))
}

// ── The tooltip ──────────────────────────────────────────────────────────────

export interface TimeSeriesTooltipProps {
  /** Recharts passes this; the tooltip renders nothing when it is not set. */
  active?: boolean
  /** The x value Recharts is hovering. */
  label?: string | number
  /** Recharts' active coordinate: `x` is the hovered day's centre. */
  coordinate?: { x?: number; y?: number }
  model: TimeSeriesModel
  timeZone?: string
  locale?: string
  inProgressRuns?: readonly InProgressRuns[]
  /**
   * VIZ-405: what the trend analysis says about the day (overlay values, and
   * the rule that flagged an anomaly), listed after the VIZ-403 rows.
   */
  overlayRows?: (x: string) => readonly TrendDayRow[]
}

/**
 * The hook the trend specs select an overlay row by. A `data-*` NAME, set as
 * an attribute by `ChartTooltip` — held in a constant because chart-guard
 * (rightly) refuses any object key spelled like "tooltip" whose value is not
 * an ECharts option object.
 */
const TREND_ROW_ATTRIBUTE = 'data-trend-tooltip-row'

/**
 * One day's tooltip content (VIZ-601): the VIZ-403 content from
 * `timeSeriesTooltipContent` — the canvas renderer's too — plus what the
 * VIZ-405 trend analysis says about the day. An overlay value is a value; the
 * rule that flagged an anomaly is a sentence, so it is a NOTE and wraps under
 * its label. The pointer's tooltip, the keyboard readout and the announcer
 * all read THIS.
 */
export function dayTipContent(
  input: Omit<TimeSeriesTooltipInput, 'tokens'>,
  overlay: readonly TrendDayRow[] = [],
): TooltipContent {
  const base = timeSeriesTooltipContent({ ...input, tokens: { series: CHART_VARS.series, axis: CHART_VARS.axis } })
  return tipContent(base.title, [
    ...base.rows,
    ...overlay.map((row) => ({
      kind: row.key === 'anomaly' ? ('note' as const) : ('value' as const),
      key: row.key,
      label: row.label,
      value: row.value,
      data: { [TREND_ROW_ATTRIBUTE]: row.key },
    })),
  ])
}

/** Half the drawn mark of a day: the 14 px execution bar (the rate's active dot is 4 px). */
const DAY_MARK_HALF = 7

/**
 * The pointer's tooltip: `dayTipContent`, drawn by the shared `ChartTooltip`
 * markup and pinned BESIDE the day's column, at the top of the plot. React
 * text, never a string formatter: release names and server reasons are
 * ingested CI text.
 */
export function TimeSeriesTooltip({
  active,
  label,
  coordinate,
  model,
  timeZone,
  locale,
  inProgressRuns,
  overlayRows,
}: TimeSeriesTooltipProps) {
  const index = model.points.findIndex((point) => point.x === String(label))
  const open = Boolean(active) && index >= 0
  const { mark, gap, chartBox, sweep } = useColumnMark(coordinate, model.points.length, DAY_MARK_HALF)
  const content = open
    ? dayTipContent({ model, index, locale, timeZone, inProgressRuns }, overlayRows ? overlayRows(model.points[index].x) : [])
    : null
  return <PinnedTip content={content} mark={mark} sides={COLUMN_SIDES} align="start" gap={gap} chartBox={chartBox} sweep={sweep} />
}

// ── The SVG (Recharts) renderer ──────────────────────────────────────────────

/** The plot's top margin: room for one row of release labels, and one more when a label is raised. */
const PLOT_MARGIN_TOP = 16
/** Recharts' own offset of a `position: 'top'` label above its line. */
const LABEL_OFFSET = 5

/**
 * Reads where the release markers fall on the x axis (inside the chart, where
 * the scale is) and reports which row each label takes (`layoutMarkerLabels`).
 * Mounted only when there is a text measure — a laid-out browser chart with at
 * least two markers — so under jsdom, or with one marker, nothing about the
 * labels changes. Draws nothing itself.
 */
function MarkerLabelLayout({
  markers,
  measure,
  onLayout,
}: {
  markers: TimeSeriesModel['markers']
  measure: TextMeasure
  onLayout: (rows: readonly MarkerLabelRow[] | null) => void
}) {
  const xScale = useXAxisScale()
  const rows = useMemo(() => {
    if (!xScale) return null
    const items = markers.map((marker) => ({
      x: xScale(marker.x, { position: 'middle' }) ?? Number.NaN,
      width: measure(markerLabelText(marker), MARKER_LABEL_FONT_SIZE),
    }))
    return items.every((item) => Number.isFinite(item.x)) ? layoutMarkerLabels(items) : null
  }, [xScale, markers, measure])
  // Reported by CONTENT: the scale is rebuilt whenever the margin changes, and
  // the same rows must not be reported again (the margin follows the rows).
  const key = rows ? rows.map((row) => (row === null ? '-' : String(row))).join(',') : ''
  useEffect(() => {
    onLayout(key ? key.split(',').map((row) => (row === '-' ? null : (Number(row) as 0 | 1))) : null)
  }, [key, onLayout])
  return null
}

/** The label prop for one marker: as it always was on the first row, raised one row, or none. */
function markerLabel(marker: TimeSeriesModel['markers'][number], row: MarkerLabelRow) {
  if (row === null) return undefined
  const label = { value: markerLabelText(marker), position: 'top' as const, fill: CHART_VARS.axis, fontSize: MARKER_LABEL_FONT_SIZE }
  return row === 1 ? { ...label, offset: LABEL_OFFSET + MARKER_LABEL_ROW_HEIGHT } : label
}

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
  trend,
  rateTarget,
}: {
  model: TimeSeriesModel
  rows: Row[]
  /** The LAYOUT height (`useFramePlotLayoutHeight`): what Recharts draws in before full screen scales it up. */
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
  /** VIZ-405: `null` unless trend overlays were requested AND the analysis is available. */
  trend: DrawnTrend | null
  /** VIZ-104 K2: only when the target is drawn (`rateTargetDrawn`). */
  rateTarget?: TimeSeriesRateTarget
}) {
  // Isolated points (a single-point series included) MUST be dots: a line
  // renderer draws nothing for a point with no measured neighbour.
  const showDots = model.singlePoint || model.isolated.length > 0
  const legend: LegendEntry[] = [
    // A line with a dot when some days are drawn as lone dots: the legend
    // shows the marks the plot has, and a plain line matches none of them (R2 F7).
    { key: 'rate', label: RATE_AXIS_TITLE, stroke: CHART_VARS.series[0], dot: showDots },
    { key: 'executions', label: EXECUTIONS_AXIS_TITLE, fill: CHART_VARS.series[1] },
  ]
  // The hatch is a mark like any other, so it gets a legend entry like any
  // other: an undescribed texture is a second thing the reader has to guess.
  if (partialDrawn) {
    legend.push({ key: 'partial', label: `${PARTIAL_LEGEND_LABEL} (${model.partialDay})`, fill: `url(#${patternId})` })
  }
  // An overlay's legend swatch is drawn with the SAME dash and colour as the line.
  for (const key of ['movingAverage', 'trendLine'] as const) {
    if (!trend?.shown[key]) continue
    const style = TREND_OVERLAY_STYLE[key]
    legend.push({ key, label: style.label, stroke: style.stroke, dash: style.dash })
  }
  if (rateTarget) legend.push({ key: 'rateTarget', label: rateTarget.label, stroke: CHART_VARS.axis, dash: RATE_TARGET_DASH })

  /**
   * One cursor stop per bucket, worded by the SAME builder the mouse tooltip
   * uses — so the keyboard reader and the pointer reader are told the same
   * thing about the same day, down to the local equivalent. With trend
   * overlays on, the day's overlay values and any anomaly rule follow, from
   * the same `trendRowsForDay` the tooltip lists.
   */
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () =>
      model.points.map((point, index) =>
        cursorPoint(
          point.x,
          dayTipContent(
            { model, index, locale, timeZone, inProgressRuns },
            trend ? trendRowsForDay(trend.analysis, point.x, trend.shown) : [],
          ),
        ),
      ),
    [model, locale, timeZone, inProgressRuns, trend],
  )
  const cursor = useChartCursor({ title, chartType: 'line and bar chart', points: cursorPoints, noun: 'day' })

  // Release labels that would touch are raised a row, or dropped (Wave 2.6
  // R2-10). Measured in the chart's own font; with no measure (no layout) or
  // fewer than two markers, every label is drawn as it always was.
  const [plotRef, measure] = useTextMeasure<HTMLDivElement>()
  const [markerRows, setMarkerRows] = useState<readonly MarkerLabelRow[] | null>(null)
  const rowOf = (index: number): MarkerLabelRow => (markerRows && markerRows.length === model.markers.length ? markerRows[index] : 0)
  const raised = model.markers.some((_, index) => rowOf(index) === 1)
  const dropped = model.markers.filter((_, index) => rowOf(index) === null).length

  return (
    <>
    <div
      ref={plotRef}
      data-time-series-plot=""
      data-executions-max={model.executionsAxis.largest}
      data-executions-domain={model.executionsAxis.domain.join(',')}
      data-rate-domain={model.rateAxis.domain.join(',')}
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
    <ChartResponsive height={height}>
      {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
      <ComposedChart
        data={rows}
        margin={{ top: PLOT_MARGIN_TOP + (raised ? MARKER_LABEL_ROW_HEIGHT : 0), right: 8, left: 0, bottom: 0 }}
        accessibilityLayer={false}
      >
        {/*
          `yAxisId="rate"`: the grid's default axis id is 0, which this chart
          does not have, so it drew no line between the plot's top and bottom
          edges and no tick could be followed across the plot. The executions
          axis shares the rate axis's interval count, so its ticks sit on these
          same lines.
        */}
        <CartesianGrid strokeDasharray="3 3" stroke={CHART_VARS.grid} vertical={false} yAxisId="rate" />
        {/*
          The kit's one short day label ("Mar 5", `utcDayLabel`), as the stacked
          columns draw it: SuiteDetail and Trends put the two charts over the
          same days, one labelled "Aug 20" and the other "2026-08-22" (R2 F5).
          The tooltip, the table and the export keep the full UTC day.
        */}
        <XAxis dataKey="x" axisLine={false} tickLine={false} tick={RECHARTS_AXIS_TICK} dy={8} tickFormatter={utcDayLabel} />
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
          // `offset: 14`, as `MultiSeriesChart`: at the default 5 the rotated
          // title's line box overhung the svg's left edge by 3.1 px.
          label={{ value: RATE_AXIS_TITLE, angle: -90, position: 'insideLeft', offset: 14, fill: CHART_VARS.axis, fontSize: 11 }}
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
        {/* Pinned beside its day (`PinnedTip`): a fixed origin, no slide. */}
        <Tooltip key={cursor.tipKey} content={tooltip} position={{ x: 0, y: 0 }} isAnimationActive={false} {...cursor.tipProps} />
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
        {/*
          VIZ-405 halos: a wider, solid, CARD-coloured copy of each shown
          overlay, drawn over the bars and under the rate line. Without it the
          overlays ran across the execution bars at 1.48-1.75:1 (dark themes)
          and 1.02:1 (lab) — 82 % of the moving average and 70 % of the trend
          line lie over a bar — so a dashed overlay dissolved into the bar it
          crossed. On the halo the overlay is always seen against the card,
          the contrast its token was chosen for. Under the RATE line, so the
          measured series is never erased where an overlay runs beside it.
        */}
        {TREND_OVERLAY_KEYS.filter((key) => trend?.shown[key]).map((key) => (
          <Line
            key={`halo-${key}`}
            yAxisId="rate"
            type={TREND_OVERLAY_STYLE[key].curve}
            dataKey={key}
            className="trend-overlay-halo"
            zIndex={TREND_OVERLAY_HALO_Z_INDEX}
            stroke={CHART_VARS.card}
            strokeWidth={trendOverlayHaloWidth(key)}
            strokeLinecap="round"
            connectNulls={false}
            dot={false}
            activeDot={false}
            legendType="none"
            tooltipType="none"
            isAnimationActive={animate}
          />
        ))}
        <Line
          yAxisId="rate"
          type="monotone"
          dataKey="rate"
          name={RATE_AXIS_TITLE}
          stroke={CHART_VARS.series[0]}
          strokeWidth={2}
          // A gap is a gap: bridging it would invent a day that never ran.
          connectNulls={false}
          // `clipDot: false`: the rate axis sets `allowDataOverflow`, so Recharts
          // clips this line to the plot rect, and a dot ON its edge — a 100 %
          // day, the best reading there is — was cut to its lower half (R2 F6).
          // Recharts then clips the dots to the plot grown by one dot, so a dot
          // on an edge is whole while the line stays inside the plot.
          dot={showDots ? { r: 3, fill: CHART_VARS.series[0], clipDot: false } : false}
          activeDot={{ r: 4 }}
          isAnimationActive={animate}
        />
        {/*
          VIZ-405 overlays: dashed, in a token colour of their own, never a
          colour-only difference from the rate line. Neither bridges a gap.
        */}
        {trend?.shown.movingAverage && (
          <Line
            yAxisId="rate"
            type={TREND_OVERLAY_STYLE.movingAverage.curve}
            dataKey="movingAverage"
            name={TREND_OVERLAY_STYLE.movingAverage.label}
            className="trend-overlay-moving-average"
            stroke={TREND_OVERLAY_STYLE.movingAverage.stroke}
            strokeDasharray={TREND_OVERLAY_STYLE.movingAverage.dash}
            strokeWidth={TREND_OVERLAY_STYLE.movingAverage.width}
            connectNulls={false}
            dot={false}
            activeDot={false}
            isAnimationActive={animate}
          />
        )}
        {trend?.shown.trendLine && (
          <Line
            yAxisId="rate"
            type={TREND_OVERLAY_STYLE.trendLine.curve}
            dataKey="trendLine"
            name={TREND_OVERLAY_STYLE.trendLine.label}
            className="trend-overlay-trend-line"
            stroke={TREND_OVERLAY_STYLE.trendLine.stroke}
            strokeDasharray={TREND_OVERLAY_STYLE.trendLine.dash}
            strokeWidth={TREND_OVERLAY_STYLE.trendLine.width}
            connectNulls={false}
            dot={false}
            activeDot={false}
            isAnimationActive={animate}
          />
        )}
        {trend?.analysis.anomalies.map((anomaly) => (
          <ReferenceDot
            key={`anomaly-${anomaly.x}`}
            yAxisId="rate"
            x={anomaly.x}
            y={anomaly.rate}
            r={6}
            shape={(props: { cx?: number; cy?: number }) => <AnomalyMarker cx={props.cx} cy={props.cy} day={anomaly.x} />}
          />
        ))}
        {/*
          VIZ-104 K2: the target, dashed and neutral, under the release
          markers. Only ever handed a target that lies on the rate axis.
        */}
        {rateTarget && (
          <ReferenceLine
            key="rate-target"
            className="rate-target-line"
            yAxisId="rate"
            y={rateTarget.value}
            stroke={CHART_VARS.axis}
            strokeDasharray={RATE_TARGET_DASH}
            strokeWidth={1.5}
          />
        )}
        {model.markers.map((marker, index) => (
          <ReferenceLine
            key={marker.x}
            yAxisId="rate"
            x={marker.x}
            stroke={CHART_VARS.neutral}
            strokeDasharray="5 3"
            label={markerLabel(marker, rowOf(index))}
          />
        ))}
        {measure && model.markers.length > 1 && (
          <MarkerLabelLayout markers={model.markers} measure={measure} onLayout={setMarkerRows} />
        )}
      </ComposedChart>
    </ChartResponsive>
    {cursor.readout}
    </div>
    {dropped > 0 && (
      <p data-chart-marker-labels-dropped="" className={NOTE}>
        {formatNumber(dropped)} release {dropped === 1 ? 'label does' : 'labels do'} not fit over the plot; the day's tooltip
        and the release table name {dropped === 1 ? 'it' : 'them'}.
      </p>
    )}
    </>
  )
}

// ── The canvas (ECharts) renderer, past SVG_POINT_LIMIT points ───────────────

const BUTTON = 'rounded border border-[var(--color-border-light)] px-3 py-1 text-[var(--color-text)]'

/**
 * No trend overlays and no rate target here (VIZ-104 K2): the canvas renderer
 * serves windows past `SVG_POINT_LIMIT`, and the chart says under the plot
 * that a requested target is not drawn (`rateTargetSentence`).
 */
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
  height: requestedHeight = 280,
  animate: requestedAnimate,
  inProgressRuns,
  timeZone,
  locale,
  now,
  description = 'Pass rate and execution volume over time',
  trendOverlays,
  rateTarget,
}: TimeSeriesChartProps) {
  // Full screen (VIZ-608): the plot takes the frame's body, less room for the notes under it.
  const height = useFramePlotHeight(requestedHeight, TIME_SERIES_NOTES_RESERVE)
  // Full screen shows the SVG drawing scaled up (`ChartResponsive`): it is LAID OUT at page text size.
  const layoutHeight = useFramePlotLayoutHeight(requestedHeight, TIME_SERIES_NOTES_RESERVE)
  const animate = useChartAnimation(requestedAnimate)
  const patternId = `${useId().replace(/[^A-Za-z0-9_-]/g, '')}-partial-day`

  // ── VIZ-405: nothing below changes the render unless `trendOverlays` is given ──
  const trendRequested = trendOverlays !== undefined
  const givenAnalysis = trendOverlays?.analysis
  const analysis = useMemo(
    () => (trendRequested ? (givenAnalysis ?? analyzeTrend(model.points)) : null),
    [trendRequested, givenAnalysis, model],
  )
  const [ownShown, setOwnShown] = useState<TrendOverlayState>(() => ({
    ...TREND_OVERLAYS_OFF,
    ...trendOverlays?.initialShown,
  }))
  const controlled = trendOverlays?.shown !== undefined
  const shown = trendOverlays?.shown ?? ownShown
  const onShownChange = trendOverlays?.onShownChange
  const setShown = (next: TrendOverlayState) => {
    if (!controlled) setOwnShown(next)
    onShownChange?.(next)
  }
  // Drawn only when the analysis is available: below the minimum sample a
  // requested overlay is simply not drawn, and the toggle says why.
  // Keyed on the two VALUES: a controlling parent may hand a fresh object each render.
  const showAverage = shown.movingAverage
  const showLine = shown.trendLine
  const trend = useMemo<DrawnTrend | null>(
    () =>
      analysis?.available && model.renderer === 'svg'
        ? { analysis, shown: { movingAverage: showAverage, trendLine: showLine } }
        : null,
    [analysis, model.renderer, showAverage, showLine],
  )
  const overlayRows = useMemo(
    () => (trend ? (x: string) => trendRowsForDay(trend.analysis, x, trend.shown) : undefined),
    [trend],
  )

  const rows = useMemo(() => toRows(model, trend), [model, trend])
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
  // VIZ-104 K2: drawn only on the rate axis of the SVG renderer; otherwise a sentence says why not.
  const targetDrawn = rateTarget !== undefined && rateTargetDrawn(model, rateTarget)

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

      {analysis && <TrendOverlayControls analysis={analysis} shown={trend ? shown : TREND_OVERLAYS_OFF} onChange={setShown} />}

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
          height={layoutHeight}
          animate={animate}
          patternId={patternId}
          title={title}
          locale={locale}
          timeZone={timeZone}
          inProgressRuns={inProgressRuns}
          partialDrawn={partialDrawn}
          trend={trend}
          rateTarget={targetDrawn ? rateTarget : undefined}
          tooltip={
            <TimeSeriesTooltip
              model={model}
              timeZone={timeZone}
              locale={locale}
              inProgressRuns={inProgressRuns}
              overlayRows={overlayRows}
            />
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
      {rateTarget && !targetDrawn && (
        <p data-chart-target-note="" className={NOTE}>
          {rateTargetSentence(model, rateTarget)}
        </p>
      )}
      {analysis && <TrendStatsStrip analysis={analysis} />}
    </figure>
  )
}
