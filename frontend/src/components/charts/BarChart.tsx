/**
 * `BarChart` (VIZ-402) — ranked, grouped and stacked bars, and `BreakdownChart`,
 * the entry point that lets the REGISTRY pick between a donut and a ranked bar.
 *
 *   `RankedBarPlot`  horizontal bars, already sorted descending by the model,
 *                    each labelled with its full-precision count, on a value
 *                    axis that starts at zero — or, when a change chart holds
 *                    negatives, a symmetric axis with a zero baseline drawn.
 *   `StatusBarPlot`  the same bars broken into statuses, in the fixed status
 *                    order, stacked or grouped, absolute or 100%.
 *   `BarChart`       either plot inside a `ChartFrame`, with the absolute ↔
 *                    100% toggle, the page controls and the chart's own notes
 *                    (ties, pagination, the diverging axis) in the footer.
 *
 * Everything the reader is told about the data comes from the model, so the
 * plot, the notes, the tooltip and the table view cannot disagree.
 */
import { useId, useMemo, useState } from 'react'
import {
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
  useXAxisScale,
} from 'recharts'
import type { ChartSeries } from '@/lib/viz/contracts'
import { formatNumber, formatPercent } from '@/utils/formatters'
import ChartFrame, { type ChartHeadingLevel } from './ChartFrame'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip, sweepOf } from './ChartTooltip'
import { useFramePlotHeight, usePresentationScale } from './framePlotHeight'
import { OTHER_KEY } from './multiSeriesModel'
import type { TipRect } from './tipPlacement'
import ChartResponsive from './ChartResponsive'
import { sampleRow, shareRow, tipContent, type TooltipContent } from './tooltip'
import { COMPACT_CHART_WIDTH, useContainerWidth } from './chartLayout'
import { formatPercentPoints, formatPlainValue, type SeriesFormat } from './chartText'
import { hasChartData, type ChartResponse, type ChartState } from './chartState'
import { chooseChart, offersPie, type ChartRequest } from './chartCatalog'
import {
  BAR_CATEGORY_GAP,
  GROUPED_BAR_GAP,
  GROUPED_BAR_THICKNESS,
  barPlotHeight,
  breakdownCaption,
  barSeries,
  barsFromSeries,
  defaultValueAxisTitle,
  minRowHeight,
  rankedModel,
  statusBarModel,
  statusBarSeries,
  statusRowsFromSeries,
  middleTruncate,
  PERCENT_MODE_NOTE,
  type RankedBar,
  type RankedModel,
  type StackMode,
  type StatusBar,
  type StatusBarModel,
} from './BarChart.model'
import DonutChart, { handOverWhenEmpty } from './DonutChart'
import { categoryDonutModel } from './DonutChart.model'
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
import { CHART_VARS, DIV_COUNT, RECHARTS_AXIS_TICK, STATUS_ENCODING } from './tokens'

const BAR_SIZE = 16
/** Room for the middle-truncated category labels on the y axis. */
const CATEGORY_AXIS_WIDTH = 210
/** …and at phone width, where 210 px would leave no plot to draw in. */
const COMPACT_CATEGORY_AXIS_WIDTH = 92
/** The label cap on a compact axis. Still middle-truncated, still with its tail. */
const COMPACT_BAR_LABEL = 13
/** The axis titles, exported so the specs assert the words rather than retype them. */
export const CATEGORY_AXIS_TITLE_OFFSET = 8

/** The plot's margins, and the value axis's own height. */
const PLOT_MARGIN_TOP = 20
const PLOT_MARGIN_BOTTOM = 16
const VALUE_AXIS_HEIGHT = 32
/** What a ranked plot spends on anything that is not a row. */
const RANKED_CHROME = PLOT_MARGIN_TOP + PLOT_MARGIN_BOTTOM + VALUE_AXIS_HEIGHT
/** …and a status plot, which also draws its legend inside the chart: one line, measured at 28 px, plus 4 px to spare. */
const STATUS_LEGEND_HEIGHT = 32
const STATUS_CHROME = RANKED_CHROME + STATUS_LEGEND_HEIGHT
/** Recharts takes the band gap as a percentage string. */
const CATEGORY_GAP = `${BAR_CATEGORY_GAP * 100}%`

/**
 * Same height as `ChartFrame`'s own buttons (`px-3 py-1 text-xs` → 26 px). The
 * frame's toolbar puts these side by side with them, and a 22 px control next
 * to a 26 px one is both a smaller target and a visibly ragged row.
 */
const TOOLBAR_BUTTON =
  'rounded border border-[var(--color-border-light)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] disabled:opacity-50 aria-disabled:opacity-50'

// ── The tooltip (VIZ-601) ────────────────────────────────────────────────────

/**
 * A ranked bar's tooltip content: the FULL name (however hard the axis had to
 * truncate it), the exact value under the value axis's own title, and — for a
 * count, not a change — its share of everything ranked, across every page.
 * No separate sample size: a ranked count's n IS its value (`barSeries` says
 * so in the table), and "Samples: 41" under "Failures: 41" would be the same
 * number twice.
 */
export function rankedTipContent(drawn: RankedBar, valueTitle: string, whole: number | null): TooltipContent {
  return tipContent(drawn.label, [
    { kind: 'value', key: 'value', label: valueTitle, value: drawn.valueLabel },
    whole !== null && shareRow(drawn.value, whole),
  ])
}

/**
 * A status bar's tooltip content: every segment in the fixed status order,
 * with its TRUE count and its share of the bar — in either mode, so flipping
 * the toggle never changes what a bar is said to hold — then the bar's total,
 * the sample every share is of.
 */
export function statusTipContent(bar: StatusBar): TooltipContent {
  return tipContent(bar.label, [
    ...bar.segments.map((segment) => ({
      kind: 'value' as const,
      key: segment.status,
      label: STATUS_ENCODING[segment.status].label,
      value: formatNumber(segment.value),
      detail: `${formatPercent(segment.percent)} of bar`,
      color: CHART_VARS.status[segment.status],
    })),
    sampleRow(bar.total),
  ])
}

/** The sum a ranked bar's share is of: every bar on every page — none on a chart that holds a negative (a change). */
export function rankedWhole(items: readonly { value: number }[]): number | null {
  if (items.some((item) => item.value < 0)) return null
  const sum = items.reduce((total, item) => total + item.value, 0)
  return sum > 0 ? sum : null
}

/**
 * The whole behind "Share of total", or `null` when the bars the server
 * returned do not add up to it (Wave 2.4 review F1).
 *
 * A `truncated` response is the top N of M categories with the rest DROPPED:
 * the point cap bit and nobody asked for a `top_n`, so the service rolled
 * nothing into "Other" (`chart_data_service.py`, `truncated_x`). The returned
 * bars then sum to less than the total, and every share would be inflated —
 * the top 20 of 400 would share out 100 % between them, beside a frame that
 * says "Showing top 20 of 400". Nothing in `meta` carries the missing part:
 * `truncated_total` counts categories, and `meta.totals` counts runs and
 * executions, not whatever the bars measure. So no share is stated at all —
 * the bar's own value still is. Where the response DOES carry an "Other"
 * bar (a `top_n` roll-up), the bars are the whole again, and it is used.
 */
export function rankedShareWhole(items: readonly { key: string; value: number }[], truncated: boolean): number | null {
  if (truncated && !items.some((item) => item.key === OTHER_KEY)) return null
  return rankedWhole(items)
}

interface BarTipGeometry {
  /**
   * Recharts' active coordinate: in a horizontal-bar chart, `y` is the row's
   * centre and `x` the pointer's — the line a pointer moving down the rows
   * sweeps along, which a tooltip below or above the row keeps off.
   */
  coordinate?: { x?: number; y?: number }
}

/**
 * The drawn extent of one row's bars, in chart coordinates: from the value
 * axis's zero to the furthest end, over the row's bar thickness. The tooltip
 * is placed beside THIS, so it never covers the bar it describes.
 */
export function barMark(
  ends: readonly number[] | null,
  thickness: number,
  centre: number | undefined,
  xScale: ((value: number) => number | undefined) | undefined,
): TipRect | null {
  if (!ends || centre === undefined || !xScale) return null
  const xs = [0, ...ends].map((value) => xScale(value)).filter((x): x is number => typeof x === 'number' && Number.isFinite(x))
  if (xs.length === 0) return null
  const left = Math.min(...xs)
  return { left, top: centre - thickness / 2, width: Math.max(...xs) - left, height: thickness }
}

function useBarMark(ends: readonly number[] | null, thickness: number, { coordinate }: BarTipGeometry): TipRect | null {
  const xScale = useXAxisScale()
  return barMark(ends, thickness, coordinate?.y, xScale ? (value: number) => xScale(value) : undefined)
}

interface RankedTooltipProps extends BarTipGeometry {
  active?: boolean
  payload?: { payload?: RankedModel['bars'][number] }[]
  valueTitle?: string
  whole?: number | null
}

/** Recharts' ranked-bar tooltip: the shared content model, pinned beside its bar. */
export function RankedTooltip({
  active,
  payload,
  coordinate,
  valueTitle = defaultValueAxisTitle(null),
  whole = null,
}: RankedTooltipProps) {
  const drawn = active ? payload?.[0]?.payload : undefined
  const mark = useBarMark(drawn ? [drawn.value] : null, BAR_SIZE, { coordinate })
  return <PinnedTip content={drawn ? rankedTipContent(drawn, valueTitle, whole) : null} mark={mark} sweep={sweepOf(coordinate, 'y')} />
}

interface StatusRow {
  key: string
  label: string
  short: string
  total: number
  bar: StatusBarModel['bars'][number]
  [status: string]: unknown
}

interface StatusTooltipProps extends BarTipGeometry {
  active?: boolean
  payload?: { payload?: StatusRow }[]
  layout?: StatusBarModel['layout']
}

/** How thick one row's bars are drawn: one bar, or a group of them side by side. */
function rowThickness(layout: StatusBarModel['layout'], statuses: number): number {
  return layout === 'grouped' && statuses > 1
    ? statuses * GROUPED_BAR_THICKNESS + (statuses - 1) * GROUPED_BAR_GAP
    : BAR_SIZE
}

/** Recharts' status-bar tooltip: the shared content model, pinned beside the row's bars. */
export function StatusTooltip({ active, payload, coordinate, layout = 'stacked' }: StatusTooltipProps) {
  const row = active ? payload?.[0]?.payload : undefined
  const plotted = row ? row.bar.segments.map((segment) => segment.plotted) : null
  const ends = plotted ? (layout === 'stacked' ? [plotted.reduce((a, b) => a + b, 0)] : plotted) : null
  const mark = useBarMark(ends, rowThickness(layout, row?.bar.segments.length ?? 1), { coordinate })
  return <PinnedTip content={row ? statusTipContent(row.bar) : null} mark={mark} sweep={sweepOf(coordinate, 'y')} />
}

export interface RankedBarPlotProps {
  model: RankedModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title: string
  /** The category axis's title -- what the bars ARE. */
  dimension?: string
  /** The value axis's title -- what their length MEANS. Default: `defaultValueAxisTitle(model)`. */
  valueAxisLabel?: string
  height?: number
  animate?: boolean
  emptyText?: string
  /**
   * What each bar's "share of total" is a share of: the sum over EVERY bar,
   * every page (`rankedWhole`). `null` (a change chart, or unknown) states no share.
   */
  whole?: number | null
}

/** Ranked (and, with negatives, diverging) horizontal bars. */
export function RankedBarPlot({
  model,
  title,
  dimension = 'Category',
  valueAxisLabel,
  height: requestedHeight = 280,
  animate: requested,
  emptyText = 'No data',
  whole = null,
}: RankedBarPlotProps) {
  // Full screen (VIZ-608): the plot takes the frame's body; the footer is the frame's, not ours.
  // Full screen shows the drawing scaled up (`ChartResponsive`): it is LAID OUT at page text size.
  const scale = usePresentationScale()
  const height = Math.round(useFramePlotHeight(requestedHeight) / scale)
  const valueTitle = valueAxisLabel ?? defaultValueAxisTitle(model)
  const animate = useChartAnimation(requested)
  const prefix = useChartPatternPrefix()
  const risePattern = `${prefix}-chart-pattern-rise`
  const fallPattern = `${prefix}-chart-pattern-fall`
  const [wrapRef, width] = useContainerWidth<HTMLDivElement>()
  const compact = width > 0 && width / scale < COMPACT_CHART_WIDTH
  // A diverging axis is a CHANGE: a share of a sum of rises and falls is meaningless.
  const shareOf = model.diverging ? null : whole

  // Built from the SAME content the pointer's tooltip shows (VIZ-601).
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.bars.map((drawn) => cursorPoint(drawn.key, rankedTipContent(drawn, valueTitle, shareOf))),
    [model, valueTitle, shareOf],
  )
  const cursor = useChartCursor({ title, chartType: 'ranked bar chart', points: cursorPoints, noun: 'bar' })

  if (model.bars.length === 0) {
    return <p className="text-[var(--color-text-muted)] text-sm text-center py-8">{emptyText}</p>
  }
  // Tall enough for every bar's label: a 50-bar page is not drawn in a 5-bar plot.
  const plotHeight = barPlotHeight(model.bars.length, minRowHeight('ranked'), height, RANKED_CHROME)

  return (
    <div
      ref={wrapRef}
      data-bar-chart="ranked"
      data-bar-compact={compact ? 'true' : 'false'}
      data-bar-domain={`${model.domain[0]},${model.domain[1]}`}
      data-bar-diverging={model.diverging ? 'true' : 'false'}
      data-bar-page={model.page}
      data-bar-pages={model.pages}
      data-bar-total={model.total}
      data-bar-values={model.bars.map((drawn) => drawn.value).join(',')}
      data-bar-plot-height={plotHeight}
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
      <ChartResponsive height={plotHeight}>
        {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
        <RechartsBarChart
          layout="vertical"
          data={model.bars}
          accessibilityLayer={false}
          barCategoryGap={CATEGORY_GAP}
          margin={{ top: PLOT_MARGIN_TOP, right: compact ? 40 : 64, left: 4, bottom: PLOT_MARGIN_BOTTOM }}
        >
          <defs>
            {renderPatterns([
              // A rise and a fall differ by pattern as well as by colour.
              { id: risePattern, color: CHART_VARS.div[DIV_COUNT - 1], decal: 'solid' },
              { id: fallPattern, color: CHART_VARS.div[0], decal: 'diagonal' },
            ])}
          </defs>
          <CartesianGrid stroke={CHART_VARS.grid} strokeDasharray="3 3" horizontal={false} />
          {/*
            The value axis. `domain` AND `ticks` come from the model: it starts
            at zero and ends on a nice number, so the last interval is as wide
            as the others (Recharts, given only a domain, ends on the data max).
          */}
          <XAxis
            type="number"
            domain={model.domain}
            ticks={model.ticks}
            interval={0}
            tick={RECHARTS_AXIS_TICK}
            axisLine={false}
            tickLine={false}
            allowDataOverflow={false}
            height={VALUE_AXIS_HEIGHT}
            label={{
              value: valueTitle,
              position: 'insideBottom',
              offset: -4,
              fill: CHART_VARS.axis,
              fontSize: 11,
            }}
          />
          <YAxis
            type="category"
            dataKey="short"
            width={compact ? COMPACT_CATEGORY_AXIS_WIDTH : CATEGORY_AXIS_WIDTH}
            interval={0}
            tick={RECHARTS_AXIS_TICK}
            axisLine={false}
            tickLine={false}
            // A narrow axis truncates further. The label is already
            // middle-truncated, so this keeps its head AND its tail.
            tickFormatter={(value: string) =>
              compact ? middleTruncate(String(value), COMPACT_BAR_LABEL) : String(value)
            }
            label={{
              value: dimension,
              position: 'top',
              offset: CATEGORY_AXIS_TITLE_OFFSET,
              fill: CHART_VARS.axis,
              fontSize: 11,
            }}
          />
          {/* Pinned beside its bar (`PinnedTip`): a fixed origin, no slide. */}
          <Tooltip
            key={cursor.tipKey}
            content={<RankedTooltip valueTitle={valueTitle} whole={shareOf} />}
            cursor={false}
            position={{ x: 0, y: 0 }}
            isAnimationActive={false}
            {...cursor.tipProps}
          />
          {model.diverging && <ReferenceLine x={0} stroke={CHART_VARS.axis} strokeWidth={1.5} />}
          <Bar dataKey="value" isAnimationActive={animate} maxBarSize={BAR_SIZE} fill={patternFill(risePattern)}>
            <LabelList
              dataKey="valueLabel"
              position="right"
              fill={CHART_VARS.text}
              fontSize={compact ? 10 : 11}
            />
            {model.bars.map((drawn) => (
              <Cell key={drawn.key} fill={patternFill(drawn.value < 0 ? fallPattern : risePattern)} />
            ))}
          </Bar>
        </RechartsBarChart>
      </ChartResponsive>
      {cursor.readout}
    </div>
  )
}

export interface StatusBarPlotProps {
  model: StatusBarModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title: string
  /** The category axis's title. */
  dimension?: string
  /** The value axis's title in ABSOLUTE mode; 100% mode names itself. */
  valueAxisLabel?: string
  height?: number
  animate?: boolean
  emptyText?: string
}

/** The value axis's title in 100% mode. The axis ticks carry the % sign too. */
export const PERCENT_AXIS_TITLE = 'Share of bar (%)'

/** Grouped or stacked bars, always in the fixed status order. */
export function StatusBarPlot({
  model,
  title,
  dimension = 'Category',
  valueAxisLabel = defaultValueAxisTitle(null),
  height: requestedHeight = 280,
  animate: requested,
  emptyText = 'No data',
}: StatusBarPlotProps) {
  // Full screen (VIZ-608): the plot takes the frame's body; its legend is inside the chart.
  // Full screen shows the drawing scaled up (`ChartResponsive`): it is LAID OUT at page text size.
  const scale = usePresentationScale()
  const height = Math.round(useFramePlotHeight(requestedHeight) / scale)
  const animate = useChartAnimation(requested)
  const prefix = useChartPatternPrefix()
  const [wrapRef, width] = useContainerWidth<HTMLDivElement>()
  const compact = width > 0 && width / scale < COMPACT_CHART_WIDTH
  const percent = model.mode === 'percent'

  const rows: StatusRow[] = useMemo(
    () =>
      model.bars.map((drawn) => {
        const row: StatusRow = { key: drawn.key, label: drawn.label, short: drawn.short, total: drawn.total, bar: drawn }
        for (const segment of drawn.segments) row[segment.status] = segment.plotted
        return row
      }),
    [model],
  )

  const legendEntries: LegendEntry[] = model.statuses.map((status) => ({
    key: status,
    label: STATUS_ENCODING[status].label,
    status,
    fill: patternFill(statusPatternId(prefix, status)),
  }))

  // One stop per BAR, reading every segment: a reader stepping through a
  // stacked chart wants the composition, not one rectangle at a time — in
  // the same words the pointer's tooltip uses (VIZ-601).
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.bars.map((drawn) => cursorPoint(drawn.key, statusTipContent(drawn))),
    [model],
  )
  const cursor = useChartCursor({
    title,
    chartType: `${model.layout === 'stacked' ? 'stacked' : 'grouped'} bar chart`,
    points: cursorPoints,
    noun: 'bar',
  })

  if (model.bars.length === 0 || model.empty) {
    return <p className="text-[var(--color-text-muted)] text-sm text-center py-8">{emptyText}</p>
  }
  // A grouped row holds one bar per status, each thick enough for its pattern.
  const plotHeight = barPlotHeight(
    model.bars.length,
    minRowHeight(model.layout, model.statuses.length),
    height,
    STATUS_CHROME,
  )

  return (
    <div
      ref={wrapRef}
      data-bar-chart={model.layout}
      data-bar-mode={model.mode}
      data-bar-compact={compact ? 'true' : 'false'}
      data-bar-domain={`${model.domain[0]},${model.domain[1]}`}
      data-bar-statuses={model.statuses.join(',')}
      data-bar-page={model.page}
      data-bar-pages={model.pages}
      data-bar-total={model.total}
      data-bar-plot-height={plotHeight}
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
      <ChartResponsive height={plotHeight}>
        {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
        <RechartsBarChart
          layout="vertical"
          data={rows}
          accessibilityLayer={false}
          barCategoryGap={CATEGORY_GAP}
          barGap={GROUPED_BAR_GAP}
          margin={{ top: PLOT_MARGIN_TOP, right: compact ? 12 : 16, left: 4, bottom: PLOT_MARGIN_BOTTOM }}
        >
          <defs>{renderPatterns(statusPatternSpecs(prefix, model.statuses))}</defs>
          <CartesianGrid stroke={CHART_VARS.grid} strokeDasharray="3 3" horizontal={false} />
          <XAxis
            type="number"
            domain={model.domain}
            ticks={model.ticks}
            interval={0}
            tick={RECHARTS_AXIS_TICK}
            axisLine={false}
            tickLine={false}
            allowDataOverflow={false}
            height={VALUE_AXIS_HEIGHT}
            // The axis carries the UNIT in 100% mode. Without it the toggle
            // exists only in the drawing: "80" on the axis is 80 runs or 80%
            // of a bar, and nothing on the chart says which.
            tickFormatter={(value: number) => (percent ? `${value}%` : formatNumber(value))}
            label={{
              value: percent ? PERCENT_AXIS_TITLE : valueAxisLabel,
              position: 'insideBottom',
              offset: -4,
              fill: CHART_VARS.axis,
              fontSize: 11,
            }}
          />
          <YAxis
            type="category"
            dataKey="short"
            width={compact ? COMPACT_CATEGORY_AXIS_WIDTH : CATEGORY_AXIS_WIDTH}
            interval={0}
            tick={RECHARTS_AXIS_TICK}
            axisLine={false}
            tickLine={false}
            tickFormatter={(value: string) =>
              compact ? middleTruncate(String(value), COMPACT_BAR_LABEL) : String(value)
            }
            label={{
              value: dimension,
              position: 'top',
              offset: CATEGORY_AXIS_TITLE_OFFSET,
              fill: CHART_VARS.axis,
              fontSize: 11,
            }}
          />
          <Tooltip
            key={cursor.tipKey}
            content={<StatusTooltip layout={model.layout} />}
            cursor={false}
            position={{ x: 0, y: 0 }}
            isAnimationActive={false}
            {...cursor.tipProps}
          />
          <Legend content={() => <ChartLegend entries={legendEntries} />} />
          {model.statuses.map((status) => (
            <Bar
              key={status}
              dataKey={status}
              name={STATUS_ENCODING[status].label}
              stackId={model.layout === 'stacked' ? 'stack' : undefined}
              fill={patternFill(statusPatternId(prefix, status))}
              maxBarSize={BAR_SIZE}
              isAnimationActive={animate}
            />
          ))}
        </RechartsBarChart>
      </ChartResponsive>
      {cursor.readout}
    </div>
  )
}

export type BarVariant = 'ranked' | 'grouped' | 'stacked'

export interface BarChartProps {
  title: string
  state: ChartState<ChartResponse>
  variant: BarVariant
  /** The server's top N, so ties at ITS boundary can be admitted and stated. */
  topN?: number
  /** Which side of the absolute / 100% toggle a stacked chart opens on. */
  initialMode?: StackMode
  headingLevel?: ChartHeadingLevel
  takeaway?: string
  height?: number
  animate?: boolean
  scopeLabel?: string
  dimension?: string
  valueAxisLabel?: string
  onClearFilters?: () => void
  'data-testid'?: string
}

/** How the page state reads, in the footer and in the announcement. */
export const barPageLabel = (page: number, pages: number) => `Page ${page + 1} of ${pages}`

/**
 * Notes, and the page controls, in the frame's own footer.
 *
 * The buttons stay in the DOM and stay FOCUSABLE at both ends: `disabled` on
 * the button the reader just pressed destroys the only thing holding focus,
 * and focus drops to `<body>` — the reader is thrown back to the top of the
 * document for pressing "Next" once too often. `aria-disabled` says the same
 * thing to a screen reader, keeps the tab stop, and the handler does nothing.
 *
 * The page state is `aria-describedby` on both buttons, exactly as
 * `ChartTable`'s own pagination does it, so "Next bars" is heard as "Next
 * bars, Page 1 of 2" rather than as a button with no context.
 */
function BarFooter({
  notes,
  page,
  pages,
  onPage,
}: {
  notes: string[]
  page: number
  pages: number
  onPage: (next: number) => void
}) {
  const statusId = useId()
  const first = page === 0
  const last = page >= pages - 1
  return (
    <>
      {notes.map((note) => (
        <span key={note} data-chart-note="">
          {note}
        </span>
      ))}
      {pages > 1 && (
        <span className="flex items-center gap-2">
          <button
            type="button"
            className={TOOLBAR_BUTTON}
            aria-disabled={first}
            aria-describedby={statusId}
            data-bar-page-button="previous"
            onClick={() => {
              if (!first) onPage(page - 1)
            }}
          >
            Previous bars
          </button>
          <span id={statusId} data-bar-page-status="">
            {barPageLabel(page, pages)}
          </span>
          <button
            type="button"
            className={TOOLBAR_BUTTON}
            aria-disabled={last}
            aria-describedby={statusId}
            data-bar-page-button="next"
            onClick={() => {
              if (!last) onPage(page + 1)
            }}
          >
            Next bars
          </button>
        </span>
      )}
    </>
  )
}

/** Ranked, grouped or stacked bars inside a `ChartFrame`. */
export default function BarChart({
  title,
  state,
  variant,
  topN,
  initialMode = 'absolute',
  headingLevel = 3,
  takeaway,
  height = 280,
  animate,
  scopeLabel,
  dimension = 'category',
  valueAxisLabel,
  onClearFilters,
  'data-testid': testId,
}: BarChartProps) {
  const [page, setPage] = useState(0)
  const [mode, setMode] = useState<StackMode>(initialMode)
  const series: ChartSeries | null = hasChartData(state) ? state.data.series : null

  const ranked = useMemo(
    () => (series && variant === 'ranked' ? rankedModel(barsFromSeries(series), { topN, page }) : null),
    [series, variant, topN, page],
  )
  // Every bar the server returned, not just this page's: a bar's share is of
  // the whole ranking — and none when the server returned only part of it.
  const truncated = state.status === 'truncated'
  const whole = useMemo(
    () => (series && variant === 'ranked' ? rankedShareWhole(barsFromSeries(series), truncated) : null),
    [series, variant, truncated],
  )
  const stacked = useMemo(
    () =>
      series && variant !== 'ranked'
        ? statusBarModel(statusRowsFromSeries(series), {
            layout: variant === 'stacked' ? 'stacked' : 'grouped',
            mode,
            page,
          })
        : null,
    [series, variant, mode, page],
  )

  // All-zero is not a chart of zeros. The donut hands an all-zero breakdown to
  // the frame as `filtered-empty`; the ranked bars drew three zero-length bars
  // on a [0, 1] axis instead, which is the same data told two different ways.
  const empty = ranked
    ? ranked.total === 0 || ranked.allZero
    : stacked
      ? stacked.total === 0 || stacked.empty
      : false
  const frameState = handOverWhenEmpty(state, empty)
  // What the bars' length means: the caller's words, else the chart's own
  // kind (a diverging ranking is a change). The axis, table and summary share it.
  const valueTitle = valueAxisLabel ?? defaultValueAxisTitle(ranked)
  const tableSeries = ranked
    ? barSeries(ranked, dimension, valueTitle)
    : stacked
      ? statusBarSeries(stacked, dimension)
      : null
  const model = ranked ?? stacked
  // 100% mode is a different UNIT, so the table and the summary read it as
  // one: the series carry percents there (`statusBarSeries`), and this is what
  // prints them as "88.0%" rather than as "88".
  const percentMode = stacked !== null && stacked.mode === 'percent'
  const format = useMemo<SeriesFormat>(
    () => (percentMode ? formatPercentPoints : formatPlainValue),
    [percentMode],
  )

  const toolbar =
    variant === 'stacked' && stacked && !empty ? (
      <button
        type="button"
        className={TOOLBAR_BUTTON}
        aria-pressed={mode === 'percent'}
        data-bar-mode-toggle={mode}
        onClick={() => setMode(mode === 'percent' ? 'absolute' : 'percent')}
      >
        {mode === 'percent' ? 'Show counts' : 'Show 100%'}
      </button>
    ) : undefined

  return (
    <ChartFrame
      title={title}
      takeaway={takeaway}
      state={frameState}
      headingLevel={headingLevel}
      chartType={variant === 'ranked' ? 'Ranked bar chart' : `${variant === 'stacked' ? 'Stacked' : 'Grouped'} bar chart`}
      series={empty ? null : tableSeries}
      axes={{ x: dimension, y: percentMode ? PERCENT_AXIS_TITLE : valueTitle }}
      format={format}
      scopeLabel={scopeLabel}
      height={height}
      onClearFilters={onClearFilters}
      toolbar={toolbar}
      data-testid={testId}
      // A page turn is a PAGE TURN, not an "update": the reader pressed the
      // button and knows the data did not change.
      changeLabel={model && model.pages > 1 ? barPageLabel(model.page, model.pages).toLowerCase() : undefined}
      footer={
        model && !empty ? (
          <BarFooter notes={model.notes} page={model.page} pages={model.pages} onPage={setPage} />
        ) : undefined
      }
    >
      {ranked && !empty ? (
        <RankedBarPlot
          model={ranked}
          title={title}
          dimension={dimension}
          valueAxisLabel={valueTitle}
          height={height}
          animate={animate}
          whole={whole}
        />
      ) : null}
      {stacked && !empty ? (
        <StatusBarPlot
          model={stacked}
          title={title}
          dimension={dimension}
          valueAxisLabel={valueTitle}
          height={height}
          animate={animate}
        />
      ) : null}
    </ChartFrame>
  )
}

export interface BreakdownChartProps extends Omit<BarChartProps, 'variant'> {
  /** What the reader asked for. The registry still decides the chart type. */
  intent?: ChartRequest['intent']
  /** A preference only: the registry overrules it when a rule says otherwise. */
  preferred?: ChartRequest['preferred']
  centreCaption?: string
}

/** The donut's slices for a CATEGORY breakdown (not the status vocabulary). */
const categoryModelOf = (series: ChartSeries) => categoryDonutModel(barsFromSeries(series))

/**
 * A categorical breakdown, drawn as whatever the REGISTRY says: a donut at or
 * under `MAX_PIE_CATEGORIES`, a ranked horizontal bar past it — and past it no
 * pie is offered at all, whatever the caller preferred.
 */
export function BreakdownChart({
  intent = 'part-to-whole',
  preferred,
  centreCaption,
  ...props
}: BreakdownChartProps) {
  const series: ChartSeries | null = hasChartData(props.state) ? props.state.data.series : null
  const items = useMemo(() => (series ? barsFromSeries(series) : []), [series])
  const request: ChartRequest = {
    intent,
    categories: items.length,
    hasNegatives: items.some((item) => item.value < 0),
    preferred,
  }
  const choice = chooseChart(request)

  return (
    <div data-chart-choice={choice.type} data-chart-offers-pie={offersPie(request) ? 'true' : 'false'}>
      {choice.type === 'donut' ? (
        <DonutChart
          title={props.title}
          state={props.state}
          headingLevel={props.headingLevel}
          takeaway={props.takeaway}
          height={props.height}
          animate={props.animate}
          scopeLabel={props.scopeLabel}
          onClearFilters={props.onClearFilters}
          data-testid={props['data-testid']}
          centreCaption={centreCaption ?? breakdownCaption(series)}
          modelOf={categoryModelOf}
          dimension={props.dimension}
          axisLabel={props.valueAxisLabel}
        />
      ) : (
        <BarChart {...props} variant="ranked" />
      )}
    </div>
  )
}
