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
import { useId, useMemo, useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ChartSeries } from '@/lib/viz/contracts'
import { formatNumber, formatPercent } from '@/utils/formatters'
import ChartFrame, { type ChartHeadingLevel } from './ChartFrame'
import { useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { COMPACT_CHART_WIDTH, useContainerWidth } from './chartLayout'
import { formatPercentPoints, formatPlainValue, type SeriesFormat } from './chartText'
import { hasChartData, type ChartResponse, type ChartState } from './chartState'
import { chooseChart, offersPie, type ChartRequest } from './chartCatalog'
import {
  barSeries,
  barsFromSeries,
  rankedModel,
  statusBarModel,
  statusBarSeries,
  statusRowsFromSeries,
  middleTruncate,
  PERCENT_MODE_NOTE,
  type RankedModel,
  type StackMode,
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
import { CHART_VARS, DIV_COUNT, RECHARTS_AXIS_TICK, RECHARTS_TOOLTIP_STYLE, STATUS_ENCODING } from './tokens'

const BAR_SIZE = 16
/** Room for the middle-truncated category labels on the y axis. */
const CATEGORY_AXIS_WIDTH = 210
/** …and at phone width, where 210 px would leave no plot to draw in. */
const COMPACT_CATEGORY_AXIS_WIDTH = 92
/** The label cap on a compact axis. Still middle-truncated, still with its tail. */
const COMPACT_BAR_LABEL = 13
/** The axis titles, exported so the specs assert the words rather than retype them. */
export const CATEGORY_AXIS_TITLE_OFFSET = 8

/**
 * Same height as `ChartFrame`'s own buttons (`px-3 py-1 text-xs` → 26 px). The
 * frame's toolbar puts these side by side with them, and a 22 px control next
 * to a 26 px one is both a smaller target and a visibly ragged row.
 */
const TOOLBAR_BUTTON =
  'rounded border border-[var(--color-border-light)] px-2 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] disabled:opacity-50 aria-disabled:opacity-50'

function TooltipShell({ children }: { children: ReactNode }) {
  return (
    <div data-chart-tooltip="" style={RECHARTS_TOOLTIP_STYLE} className="px-2 py-1">
      {children}
    </div>
  )
}

interface RankedTooltipProps {
  active?: boolean
  payload?: { payload?: RankedModel['bars'][number] }[]
}

/** Text, as React nodes: a test name from an ingested CI file is never markup. */
export function RankedTooltip({ active, payload }: RankedTooltipProps) {
  const drawn = active ? payload?.[0]?.payload : undefined
  if (!drawn) return null
  return (
    <TooltipShell>
      {/* The FULL name, however hard the axis had to truncate it. */}
      <div className="font-semibold">{drawn.label}</div>
      <div>{drawn.valueLabel}</div>
    </TooltipShell>
  )
}

interface StatusRow {
  key: string
  label: string
  short: string
  total: number
  bar: StatusBarModel['bars'][number]
  [status: string]: unknown
}

export function StatusTooltip({ active, payload }: { active?: boolean; payload?: { payload?: StatusRow }[] }) {
  const row = active ? payload?.[0]?.payload : undefined
  if (!row) return null
  return (
    <TooltipShell>
      <div className="font-semibold">{row.label}</div>
      {row.bar.segments.map((segment) => (
        <div key={segment.status} className="flex gap-3">
          <span>{STATUS_ENCODING[segment.status].label}</span>
          <span className="ml-auto font-semibold">
            {formatNumber(segment.value)} ({formatPercent(segment.percent)})
          </span>
        </div>
      ))}
    </TooltipShell>
  )
}

export interface RankedBarPlotProps {
  model: RankedModel
  /** Names the focusable drawing surface (see `useChartCursor`). */
  title: string
  /** The category axis's title -- what the bars ARE. */
  dimension?: string
  /** The value axis's title -- what their length MEANS. */
  valueAxisLabel?: string
  height?: number
  animate?: boolean
  emptyText?: string
}

/** Ranked (and, with negatives, diverging) horizontal bars. */
export function RankedBarPlot({
  model,
  title,
  dimension = 'Category',
  valueAxisLabel = 'Count',
  height = 280,
  animate: requested,
  emptyText = 'No data',
}: RankedBarPlotProps) {
  const animate = useChartAnimation(requested)
  const prefix = useChartPatternPrefix()
  const risePattern = `${prefix}-chart-pattern-rise`
  const fallPattern = `${prefix}-chart-pattern-fall`
  const [wrapRef, width] = useContainerWidth<HTMLDivElement>()
  const compact = width > 0 && width < COMPACT_CHART_WIDTH

  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.bars.map((drawn) => ({ key: drawn.key, text: `${drawn.label}: ${drawn.valueLabel}` })),
    [model],
  )
  const cursor = useChartCursor({ title, chartType: 'ranked bar chart', points: cursorPoints, noun: 'bar' })

  if (model.bars.length === 0) {
    return <p className="text-[var(--color-text-muted)] text-sm text-center py-8">{emptyText}</p>
  }

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
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
      <ResponsiveContainer width="100%" height={height}>
        {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
        <RechartsBarChart
          layout="vertical"
          data={model.bars}
          accessibilityLayer={false}
          margin={{ top: 20, right: compact ? 40 : 64, left: 4, bottom: 16 }}
        >
          <defs>
            {renderPatterns([
              // A rise and a fall differ by pattern as well as by colour.
              { id: risePattern, color: CHART_VARS.div[DIV_COUNT - 1], decal: 'solid' },
              { id: fallPattern, color: CHART_VARS.div[0], decal: 'diagonal' },
            ])}
          </defs>
          <CartesianGrid stroke={CHART_VARS.grid} strokeDasharray="3 3" horizontal={false} />
          {/* The value axis. `domain` comes from the model, and it starts at zero. */}
          <XAxis
            type="number"
            domain={model.domain}
            tick={RECHARTS_AXIS_TICK}
            axisLine={false}
            tickLine={false}
            allowDataOverflow={false}
            height={32}
            label={{
              value: valueAxisLabel,
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
          <Tooltip key={cursor.tipKey} content={<RankedTooltip />} cursor={false} {...cursor.tipProps} />
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
      </ResponsiveContainer>
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
  valueAxisLabel = 'Count',
  height = 280,
  animate: requested,
  emptyText = 'No data',
}: StatusBarPlotProps) {
  const animate = useChartAnimation(requested)
  const prefix = useChartPatternPrefix()
  const [wrapRef, width] = useContainerWidth<HTMLDivElement>()
  const compact = width > 0 && width < COMPACT_CHART_WIDTH
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
  // stacked chart wants the composition, not one rectangle at a time.
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () =>
      model.bars.map((drawn) => ({
        key: drawn.key,
        text: `${drawn.label}: ${drawn.segments
          .map((segment) =>
            model.mode === 'percent'
              ? `${STATUS_ENCODING[segment.status].label} ${formatPercent(segment.percent)}`
              : `${STATUS_ENCODING[segment.status].label} ${formatNumber(segment.value)} (${formatPercent(segment.percent)})`,
          )
          .join(', ')}`,
      })),
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
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
      <ResponsiveContainer width="100%" height={height}>
        {/* `accessibilityLayer={false}` — explicitly; see `ChartCursor`. */}
        <RechartsBarChart
          layout="vertical"
          data={rows}
          accessibilityLayer={false}
          margin={{ top: 20, right: compact ? 12 : 16, left: 4, bottom: 16 }}
        >
          <defs>{renderPatterns(statusPatternSpecs(prefix, model.statuses))}</defs>
          <CartesianGrid stroke={CHART_VARS.grid} strokeDasharray="3 3" horizontal={false} />
          <XAxis
            type="number"
            domain={model.domain}
            tick={RECHARTS_AXIS_TICK}
            axisLine={false}
            tickLine={false}
            allowDataOverflow={false}
            height={32}
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
          <Tooltip key={cursor.tipKey} content={<StatusTooltip />} cursor={false} {...cursor.tipProps} />
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
      </ResponsiveContainer>
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
  valueAxisLabel = 'Count',
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
  const tableSeries = ranked
    ? barSeries(ranked, dimension)
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
      axes={{ x: dimension, y: percentMode ? PERCENT_AXIS_TITLE : valueAxisLabel }}
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
          valueAxisLabel={valueAxisLabel}
          height={height}
          animate={animate}
        />
      ) : null}
      {stacked && !empty ? (
        <StatusBarPlot
          model={stacked}
          title={title}
          dimension={dimension}
          valueAxisLabel={valueAxisLabel}
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
          centreCaption={centreCaption}
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
