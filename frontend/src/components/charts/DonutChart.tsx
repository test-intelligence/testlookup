/**
 * `DonutChart` (VIZ-401) — the status-distribution donut, and the generic
 * donut `DefectDonut` is now built from.
 *
 *   `DonutPlot`   the renderer: fixed slice order, the total in the centre,
 *                 a count-and-percent label on every slice big enough to hold
 *                 one, the rest in the legend, and a patterned fill per slice
 *                 so status is never colour-only (VIZ-102). No exploded
 *                 slices, no 3D — an exploded slice moves an arc away from the
 *                 ring it is a share of, and 3D changes an arc's apparent size
 *                 with its position.
 *   `DonutChart`  the same plot inside a `ChartFrame`, so every empty / error
 *                 state, the table view, the announcer and the export ref come
 *                 for free, reading a `ChartState<ChartResponse>` from
 *                 `useChartData`.
 *
 * An all-zero breakdown is NOT a donut of nothing: the frame is handed the
 * `filtered-empty` state instead (`handOverWhenEmpty`), so the reader is told
 * their filters match nothing rather than shown an empty ring.
 */
import { useMemo, type ReactElement, type ReactNode } from 'react'
import { Cell, Label, Legend, Pie, PieChart, Tooltip } from 'recharts'
import type { ChartSeries } from '@/lib/viz/contracts'
import { formatNumber, formatPercent } from '@/utils/formatters'
import { formatPercentPoints, formatPlainValue, type SeriesFormat } from './chartText'
import ChartFrame, { type ChartHeadingLevel } from './ChartFrame'
import { hasChartData, type ChartResponse, type ChartState } from './chartState'
import {
  donutCentreOf,
  donutSeries,
  sliceLabel,
  statusCountsFromSeries,
  statusDonutModel,
  type DonutModel,
  type DonutSlice,
} from './DonutChart.model'
import { cursorPoint, useChartCursor, type ChartCursorPoint } from './ChartCursor'
import { PinnedTip } from './ChartTooltip'
import { useFramePlotHeight, usePresentationScale } from './framePlotHeight'
import { chartScaleOf, unscaled, type TipRect } from './tipPlacement'
import ChartResponsive from './ChartResponsive'
import { SHARE_LABEL, sampleRow, tipContent, type TooltipContent } from './tooltip'
import { useChartAnimation } from './motion'
import {
  ChartLegend,
  patternFill,
  renderPatterns,
  statusPatternId,
  useChartPatternPrefix,
  type LegendEntry,
  type PatternSpec,
} from './patterns'
import { CHART_VARS, STATUS_ENCODING, type DecalKind } from './tokens'

/** How a non-status slice is drawn. Status slices always use their own encoding. */
export interface SliceStyle {
  color: string
  decal: DecalKind
}

/** Decals for category slices, so two slices never differ by colour alone. */
const CATEGORY_DECALS: DecalKind[] = ['solid', 'diagonal', 'crosshatch', 'dashes', 'dots']

function defaultStyleOf(_slice: DonutSlice, index: number): SliceStyle {
  return {
    color: CHART_VARS.series[index % CHART_VARS.series.length],
    decal: CATEGORY_DECALS[index % CATEGORY_DECALS.length],
  }
}

export interface DonutPlotProps {
  model: DonutModel
  /**
   * What the chart is CALLED. It names the focusable drawing surface, so a
   * screen-reader user who tabs into the ring hears which chart they are in
   * (VIZ-102 / fix round A). Every caller passes the frame's own title.
   */
  title: string
  height?: number
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps its default. */
  animate?: boolean
  /** The total in the centre. Off for a donut that is not a part of one whole. */
  showCentreTotal?: boolean
  /** Under the total, e.g. "executions". */
  centreCaption?: string
  /** Count-and-percent labels on the arcs (tiny slices always go to the legend). */
  showSliceLabels?: boolean
  innerRadius?: number
  outerRadius?: number
  paddingAngle?: number
  /** Shown instead of the ring when the model is empty. */
  emptyText?: string
  /** Colour and decal for a slice with no status. */
  styleOf?: (slice: DonutSlice, index: number) => SliceStyle
  /** What a slice's value is called in its tooltip ("Executions", "Failures"). */
  valueLabel?: string
}

interface TooltipEntry {
  payload?: { slice?: DonutSlice }
}

/** What a donut's values are called in its tooltip when the caller does not say. */
export const DONUT_VALUE_LABEL = 'Count'

/**
 * A slice's tooltip content (VIZ-601): its name, its TRUE value (never the
 * arc padded to keep a tiny slice visible), its share of the ring — the same
 * largest-remainder percent the arc label and the legend print, so the three
 * always sum to 100.0 — and the ring's total, the sample the share is of.
 */
export function donutTipContent(slice: DonutSlice, total: number | null, valueLabel = DONUT_VALUE_LABEL): TooltipContent {
  return tipContent(slice.label, [
    { kind: 'value', key: 'value', label: valueLabel, value: formatNumber(slice.value) },
    { kind: 'share', key: 'share', label: SHARE_LABEL, value: formatPercent(slice.percent) },
    total !== null && total > 0 && sampleRow(total),
  ])
}

/**
 * The ring, as the mark every slice's tooltip keeps clear of: read from the
 * drawn pie, so the tooltip goes BESIDE the ring rather than over any slice.
 */
function ringOf(chart: HTMLElement): TipRect | null {
  const pie = chart.querySelector('.recharts-pie')
  if (!pie) return null
  const box = chart.getBoundingClientRect()
  const ring = pie.getBoundingClientRect()
  // On screen; the tooltip is placed in chart coordinates (a full-screen drawing is scaled up).
  return unscaled({ left: ring.left - box.left, top: ring.top - box.top, width: ring.width, height: ring.height }, chartScaleOf(chart))
}

/** Recharts' donut tooltip: the shared content model, pinned beside the ring. */
export function DonutTooltip({
  active,
  payload,
  total,
  valueLabel,
}: {
  active?: boolean
  payload?: TooltipEntry[]
  /** The ring's total: the sample every slice's share is of. */
  total?: number | null
  valueLabel?: string
}) {
  const slice = active ? payload?.[0]?.payload?.slice : undefined
  return <PinnedTip content={slice ? donutTipContent(slice, total ?? null, valueLabel) : null} mark={ringOf} />
}

/** The donut itself. Pure: it draws the model it is given and fetches nothing. */
export function DonutPlot({
  model,
  title,
  height: requestedHeight = 240,
  animate: requestedAnimate,
  showCentreTotal = true,
  centreCaption,
  showSliceLabels = true,
  innerRadius = 56,
  outerRadius = 88,
  paddingAngle = 2,
  emptyText = 'No data',
  styleOf = defaultStyleOf,
  valueLabel = DONUT_VALUE_LABEL,
}: DonutPlotProps) {
  // Full screen (VIZ-608): the ring's box takes the frame's body; its legend is inside the chart.
  // Full screen shows the drawing scaled up (`ChartResponsive`): it is LAID OUT at page text size.
  const scale = usePresentationScale()
  const height = Math.round(useFramePlotHeight(requestedHeight) / scale)
  const animate = useChartAnimation(requestedAnimate)
  const prefix = useChartPatternPrefix()
  // A ring that is not one whole (no centre total) states no sample for its slices.
  const total = showCentreTotal ? model.total : null

  // Every slice, in drawn order, as the keyboard cursor walks them — in the
  // SAME content the pointer's tooltip shows (VIZ-601).
  const cursorPoints = useMemo<ChartCursorPoint[]>(
    () => model.slices.map((slice) => cursorPoint(slice.key, donutTipContent(slice, total, valueLabel))),
    [model, total, valueLabel],
  )
  const cursor = useChartCursor({ title, chartType: 'donut chart', points: cursorPoints, noun: 'slice' })

  const { specs, rows, legendEntries } = useMemo(() => {
    const patternSpecs: PatternSpec[] = []
    const entries: LegendEntry[] = []
    const data = model.slices.map((slice, index) => {
      const style = slice.status
        ? { color: CHART_VARS.status[slice.status], decal: STATUS_ENCODING[slice.status].decal }
        : styleOf(slice, index)
      const id = slice.status ? statusPatternId(prefix, slice.status) : `${prefix}-chart-pattern-slice-${index}`
      patternSpecs.push({ id, color: style.color, decal: style.decal })
      const fill = patternFill(id)
      entries.push({
        key: slice.key,
        // A slice too small to carry its own label is named here instead.
        label: slice.tiny ? sliceLabel(slice) : slice.label,
        status: slice.status,
        fill,
      })
      return { name: slice.label, arc: slice.arc, fill, slice }
    })
    return { specs: patternSpecs, rows: data, legendEntries: entries }
  }, [model, prefix, styleOf])

  if (model.empty) {
    return <p className="text-[var(--color-text-muted)] text-sm text-center py-8">{emptyText}</p>
  }

  /** A count-and-percent label, outside the arc. Tiny slices are in the legend. */
  const renderSliceLabel = (props: {
    cx?: number
    cy?: number
    midAngle?: number
    outerRadius?: number
    index?: number
  }): ReactNode => {
    const slice = model.slices[props.index ?? -1]
    if (!slice || slice.tiny || !showSliceLabels) {
      // A valid element, NOT null. Returning null makes Recharts fall through
      // to its own default pie label, which draws an empty `<text>` on the arc
      // — a node with a position, a name and nothing to say.
      return <g data-donut-slice-label-omitted={slice?.key ?? ''} />
    }
    const radians = -((props.midAngle ?? 0) * Math.PI) / 180
    const radius = (props.outerRadius ?? outerRadius) + 14
    const x = (props.cx ?? 0) + radius * Math.cos(radians)
    const y = (props.cy ?? 0) + radius * Math.sin(radians)
    return (
      <text
        data-donut-slice-label={slice.key}
        x={x}
        y={y}
        fill={CHART_VARS.text}
        fontSize={11}
        textAnchor={x > (props.cx ?? 0) ? 'start' : 'end'}
        dominantBaseline="central"
      >
        {`${formatNumber(slice.value)} (${formatPercent(slice.percent)})`}
      </text>
    )
  }

  // `unknown`, narrowed by `donutCentreOf`: Recharts' `Label` content type
  // covers every view box it has, and which one arrives is Recharts' choice.
  const renderCentre = (props: unknown): ReactElement => {
    const centre = donutCentreOf(props)
    // Nowhere to put it is no total, not a total at the origin.
    if (!centre) return <g data-donut-centre-omitted="" />
    const { x: cx, y: cy } = centre
    return (
      <text data-donut-centre="" x={cx} y={cy} textAnchor="middle" fill={CHART_VARS.text}>
        <tspan x={cx} dy="-0.1em" fontSize={20} fontWeight={600}>
          {formatNumber(model.total)}
        </tspan>
        {centreCaption && (
          <tspan x={cx} dy="1.4em" fontSize={11} fill={CHART_VARS.axis}>
            {centreCaption}
          </tspan>
        )}
      </text>
    )
  }

  return (
    <div
      data-donut=""
      data-donut-total={model.total}
      data-donut-slices={model.slices.length}
      data-donut-full-ring={model.fullRing ? 'true' : 'false'}
      data-donut-legend-only={model.legendOnly.length}
      className="w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
      {...cursor.surfaceProps}
    >
      <ChartResponsive height={height}>
        {/*
          `accessibilityLayer={false}`, EXPLICITLY: Recharts 3 defaults it to
          true, so leaving the prop off still puts `role="application"` (and a
          tabindex, and an empty <title>) on the surface. That role takes the
          screen reader's virtual cursor away and gives no accessible name
          back. `useChartCursor` names the box around it instead.
        */}
        <PieChart accessibilityLayer={false}>
          <defs>{renderPatterns(specs)}</defs>
          <Pie
            data={rows}
            cx="50%"
            cy="50%"
            innerRadius={innerRadius}
            outerRadius={outerRadius}
            paddingAngle={paddingAngle}
            // Recharts outlines every sector in white by default. On a full
            // ring the sector's two radial edges meet at 3 o'clock, and that
            // outline drew them as a seam across a ring with no neighbour: no
            // stroke at all there. Between slices, the CARD colour separates
            // them the same way in every theme (white was a bright ring round
            // each slice on a dark card, and a colour from outside the tokens).
            stroke={model.fullRing ? 'none' : CHART_VARS.card}
            // The PADDED arc is drawn so a tiny slice stays visible; every
            // number the reader sees comes from `slice.value`.
            dataKey="arc"
            nameKey="name"
            isAnimationActive={animate}
            label={showSliceLabels ? renderSliceLabel : undefined}
            labelLine={false}
          >
            {rows.map((row) => (
              <Cell key={row.slice.key} fill={row.fill} />
            ))}
            {showCentreTotal && <Label position="center" content={renderCentre} />}
          </Pie>
          {/* Pinned beside the ring (`PinnedTip`): a fixed origin, no slide. */}
          <Tooltip
            key={cursor.tipKey}
            content={<DonutTooltip total={total} valueLabel={valueLabel} />}
            position={{ x: 0, y: 0 }}
            isAnimationActive={false}
            {...cursor.tipProps}
          />
          <Legend content={() => <ChartLegend entries={legendEntries} />} />
        </PieChart>
      </ChartResponsive>
      {cursor.readout}
    </div>
  )
}

/**
 * A drawn state whose model turned out to hold nothing becomes
 * `filtered-empty`: all-zero counts are a filter that matched nothing, not a
 * chart of zeros.
 */
export function handOverWhenEmpty<T>(state: ChartState<T>, empty: boolean): ChartState<T> {
  if (!empty || !hasChartData(state)) return state
  return { status: 'filtered-empty', meta: state.meta }
}

export interface DonutChartProps {
  title: string
  state: ChartState<ChartResponse>
  headingLevel?: ChartHeadingLevel
  takeaway?: string
  height?: number
  animate?: boolean
  centreCaption?: string
  scopeLabel?: string
  onClearFilters?: () => void
  toolbar?: ReactNode
  /** The slices. The default is the status breakdown (VIZ-401). */
  modelOf?: (series: ChartSeries) => DonutModel
  /** What the slices ARE, for the table's first column and the summary. */
  dimension?: string
  /** What the values are, for the summary. */
  axisLabel?: string
  'data-testid'?: string
}

/** The status donut inside its frame, reading a `useChartData` state. */
export default function DonutChart({
  title,
  state,
  headingLevel = 3,
  takeaway,
  height = 240,
  animate,
  centreCaption = 'executions',
  scopeLabel,
  onClearFilters,
  toolbar,
  modelOf,
  dimension = 'Status',
  axisLabel = 'Executions',
  'data-testid': testId,
}: DonutChartProps) {
  const series: ChartSeries | null = hasChartData(state) ? state.data.series : null
  const model = useMemo(
    () => (series ? (modelOf ? modelOf(series) : statusDonutModel(statusCountsFromSeries(series))) : null),
    [series, modelOf],
  )
  const frameState = handOverWhenEmpty(state, model !== null && model.empty)

  // The table has a count column AND a share column, and they are different
  // units: the per-series formatter is what keeps "88.0%" out of the counts
  // and "880" out of the shares.
  const format = useMemo<SeriesFormat>(() => ({ count: formatPlainValue, percent: formatPercentPoints }), [])

  return (
    <ChartFrame
      title={title}
      takeaway={takeaway}
      state={frameState}
      headingLevel={headingLevel}
      chartType="Donut chart"
      series={model && !model.empty ? donutSeries(model, dimension) : null}
      axes={{ x: dimension, y: axisLabel }}
      format={format}
      scopeLabel={scopeLabel}
      height={height}
      onClearFilters={onClearFilters}
      toolbar={toolbar}
      data-testid={testId}
      tableExtras={
        model && !model.empty ? (
          // The number in the CENTRE of the ring. Without it the table is a
          // lesser view of the chart: the reader can add the counts up, but
          // the chart states the total and the table did not.
          <p data-donut-table-total="" className="mt-2 text-xs text-[var(--color-text-secondary)]">
            Total {formatNumber(model.total)}
            {centreCaption ? ` ${centreCaption}` : ''}
          </p>
        ) : undefined
      }
    >
      {model && !model.empty ? (
        <DonutPlot
          model={model}
          title={title}
          height={height}
          animate={animate}
          centreCaption={centreCaption}
          valueLabel={axisLabel}
        />
      ) : null}
    </ChartFrame>
  )
}
