/**
 * Pure option builder for the ECharts time-series renderer (VIZ-403) — no
 * ECharts import (the types are erased), so it is unit-testable in jsdom and
 * costs nothing until a chart actually exceeds `SVG_POINT_LIMIT` points.
 *
 * This path exists only for LONG series: Recharts draws one SVG node per point,
 * and past ~366 points per series that is a DOM the browser spends more time
 * laying out than the reader spends looking at it. ECharts draws on canvas and
 * `sampling: 'lttb'` keeps the SHAPE of the line while drawing a fraction of the
 * points — the extrema survive, which a naive "every nth point" would not.
 *
 * Two rules carry over from the SVG path unchanged:
 *   - a gap stays `null`. ECharts breaks a line at a null (connectNulls is off
 *     by default) exactly as Recharts does, and a zero here would be the same
 *     lie in a different engine.
 *   - the tooltip is DOM. Release and test names come from ingested CI files;
 *     an ECharts `formatter` that returns a string is assigned with innerHTML,
 *     so every formatter goes through `domTooltipFormatter` (ADR decision 4).
 */
import type { ComposeOption } from 'echarts/core'
import type { BarSeriesOption, LineSeriesOption } from 'echarts/charts'
import type {
  AriaComponentOption,
  GridComponentOption,
  LegendComponentOption,
  TooltipComponentOption,
} from 'echarts/components'
import { decalOf, type ChartTokens } from '../../tokens'
import { changeRow, domTooltipFormatter, formatRatePoints, sampleRow, tipContent, type TooltipContent } from '../../tooltip'
import { COLUMN_SIDES, columnMark, echartsTipPosition } from '../../tipPlacement'
import { NO_VALUE } from '../../chartText'
import { formatNumber } from '@/utils/formatters'
import {
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
  localDayRange,
  type TimeSeriesModel,
  type TimeSeriesPoint,
} from '../../timeSeriesModel'

export type TimeSeriesOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | AriaComponentOption
  | LegendComponentOption
>

/**
 * Largest-triangle-three-buckets: keeps the peaks and troughs a reader is
 * looking for. `'average'` would smooth away the one bad day the chart exists
 * to show.
 */
export const TIME_SERIES_SAMPLING = 'lttb'

export interface InProgressRuns {
  x: string
  names: string[]
}

export interface TimeSeriesTooltipInput {
  model: TimeSeriesModel
  index: number
  locale?: string
  timeZone?: string
  inProgressRuns?: readonly InProgressRuns[]
  tokens?: Pick<ChartTokens, 'series' | 'axis'>
}

/** A pass-rate change is in percentage POINTS (`tooltip.ts`, shared with the comparison chart). */
export { formatRatePoints }

/**
 * The bucket before `index`: the previous drawn point, or — for the first
 * drawn point of a ZOOMED model — the day just before the visible range
 * (`model.precedingPoint`, VIZ-407). `undefined` when there is none: the
 * first day of the whole series has no change to state.
 */
export function previousPoint(model: TimeSeriesModel, index: number): TimeSeriesPoint | undefined {
  if (index > 0) return model.points[index - 1]
  return model.precedingPoint ?? undefined
}

/**
 * One bucket's tooltip content, as DATA. The mouse tooltip — through
 * `domTooltipFormatter` on canvas and `ChartTooltip` on SVG — and the keyboard
 * announcement all come from here, so they cannot say different things. In the
 * one order every tooltip uses (`tipContent`): the day's dimensions (its local
 * equivalent, its release), the values, the sample size n behind the rate, the
 * change against the previous day, then the notes.
 */
export function timeSeriesTooltipContent({
  model,
  index,
  locale,
  timeZone,
  inProgressRuns,
  tokens,
}: TimeSeriesTooltipInput): TooltipContent {
  const point = model.points[index]
  if (!point) return { rows: [] }
  const local = localDayRange(point.x, { timeZone, locale })
  const marker = model.markers.find((entry) => entry.x === point.x)
  const before = previousPoint(model, index)
  const names = point.partial ? (inProgressRuns?.find((entry) => entry.x === point.x)?.names ?? []) : []
  return tipContent(`${point.x} (UTC)`, [
    {
      kind: 'dimension',
      key: 'local',
      label: 'Local',
      value: local.offsetChanged ? `${local.start} – ${local.end} (clocks change)` : `${local.start} – ${local.end}`,
    },
    marker && { kind: 'dimension', key: 'release', label: 'Release', value: marker.names.join(', ') },
    {
      kind: 'value',
      key: 'rate',
      label: RATE_AXIS_TITLE,
      value: point.rate === null ? NO_VALUE : `${formatNumber(point.rate, { maximumFractionDigits: 1 })}%`,
      color: tokens?.series[0],
    },
    {
      kind: 'value',
      key: 'executions',
      label: EXECUTIONS_AXIS_TITLE,
      value: point.executions === null ? NO_VALUE : formatNumber(point.executions),
      color: tokens?.series[1],
    },
    sampleRow(point.n),
    changeRow({
      current: point.rate,
      previous: before ? before.rate : undefined,
      previousLabel: before?.x,
      formatMagnitude: formatRatePoints,
    }),
    point.rate === null && point.rateReason ? { kind: 'note', key: 'why', label: 'Why', value: point.rateReason } : null,
    point.partial && {
      kind: 'note',
      key: 'partial',
      label: 'Still filling',
      value: names.length
        ? `in progress: ${names.join(', ')}`
        : model.inProgressCount > 0
          ? `${formatNumber(model.inProgressCount)} run(s) in progress`
          : 'this UTC day has not closed yet',
    },
  ])
}

export interface TimeSeriesOptionInput {
  model: TimeSeriesModel
  tokens: ChartTokens
  /** Our own one-sentence description; ECharts' generated aria text is off. */
  description: string
  animate?: boolean
  locale?: string
  timeZone?: string
  inProgressRuns?: readonly InProgressRuns[]
}

/** The bucket a tooltip is for, recovered from ECharts' params without trusting their shape. */
function indexOf(params: unknown): number | null {
  const first = Array.isArray(params) ? params[0] : params
  const index = (first as { dataIndex?: unknown } | undefined)?.dataIndex
  return typeof index === 'number' ? index : null
}

/** The plot's insets inside the canvas, px (ECharts may grow them to fit the axis labels: `containLabel`). */
export const TIME_SERIES_GRID = { left: 56, right: 56, top: 36, bottom: 32 } as const

/**
 * The column the pointer is over, as the mark the canvas tooltip keeps clear
 * of: half a day's band either side of the pointer's x, over the plot's
 * height. The canvas renderer only draws series past `SVG_POINT_LIMIT` days,
 * so a band is a pixel or two and the tooltip's `TIP_GAP` clears it easily.
 */
export function canvasColumnMark(point: readonly number[], view: { width: number; height: number }, days: number) {
  const plotWidth = Math.max(0, view.width - TIME_SERIES_GRID.left - TIME_SERIES_GRID.right)
  const band = days > 0 ? plotWidth / days : plotWidth
  return columnMark(point[0] ?? 0, band / 2, {
    top: TIME_SERIES_GRID.top,
    height: Math.max(0, view.height - TIME_SERIES_GRID.top - TIME_SERIES_GRID.bottom),
  })
}

/** The distance between two ticks of a model axis; `undefined` leaves it to ECharts. */
const tickStep = (ticks: readonly number[]): number | undefined =>
  ticks.length > 1 ? ticks[1] - ticks[0] : undefined

export function buildTimeSeriesOption({
  model,
  tokens,
  animate = false,
  locale,
  timeZone,
  inProgressRuns,
}: TimeSeriesOptionInput): TimeSeriesOption {
  const axisLabel = { color: tokens.axis }
  const axisLine = { lineStyle: { color: tokens.grid } }
  const splitLine = { lineStyle: { color: tokens.grid } }

  return {
    animation: animate,
    // ECharts' own generated description is not useful; the wrapper names the chart.
    aria: { enabled: false },
    /**
     * The legend the SVG path draws (`ChartLegend`), on canvas too. Crossing
     * the 366-point boundary used to delete it: the same chart, one point
     * wider, lost every name for the two marks it draws. No `formatter` here
     * by design — a legend formatter that returns a string is the same HTML
     * sink a tooltip formatter is, and `domTooltipFormatter` cannot serve one.
     */
    legend: {
      data: [RATE_AXIS_TITLE, EXECUTIONS_AXIS_TITLE],
      top: 0,
      textStyle: { color: tokens.text },
      inactiveColor: tokens.textMuted,
    },
    grid: { ...TIME_SERIES_GRID, containLabel: true },
    tooltip: {
      trigger: 'axis',
      // The same box the SVG path draws (`TIP_BOX_STYLE`): card, hairline, 8 px corners, 4/8 padding.
      backgroundColor: tokens.card,
      borderColor: tokens.border,
      borderWidth: 1,
      borderRadius: 8,
      padding: [4, 8],
      textStyle: { color: tokens.text },
      // VIZ-601: beside the day's column, never over it; flipped at an edge;
      // inside the chart (`confine`); and something the pointer can move onto
      // (`enterable`, SC 1.4.13 Hoverable).
      confine: true,
      enterable: true,
      position: echartsTipPosition((point, _rect, view) => canvasColumnMark(point, view, model.points.length), {
        sides: COLUMN_SIDES,
        align: 'start',
      }),
      formatter: domTooltipFormatter((params: unknown) => {
        const index = indexOf(params)
        return index === null
          ? { rows: [] }
          : timeSeriesTooltipContent({ model, index, locale, timeZone, inProgressRuns, tokens })
      }),
    },
    xAxis: [
      {
        type: 'category',
        data: model.points.map((point) => point.x),
        axisLabel,
        axisLine,
      },
    ],
    yAxis: [
      {
        type: 'value',
        name: RATE_AXIS_TITLE,
        nameTextStyle: { color: tokens.axis },
        min: model.rateAxis.domain[0],
        max: model.rateAxis.domain[1],
        // The model's tick step, as in SVG: the same axis on either renderer.
        interval: tickStep(model.rateAxis.ticks),
        axisLabel,
        axisLine,
        splitLine,
      },
      {
        // The second axis carries its OWN title: a bare right-hand scale beside
        // a percentage axis is read as more percentages.
        type: 'value',
        name: EXECUTIONS_AXIS_TITLE,
        nameTextStyle: { color: tokens.axis },
        min: model.executionsAxis.domain[0],
        max: model.executionsAxis.domain[1],
        interval: tickStep(model.executionsAxis.ticks),
        axisLabel,
        axisLine,
        splitLine: { show: false },
      },
    ],
    series: [
      {
        id: 'executions',
        name: EXECUTIONS_AXIS_TITLE,
        type: 'bar',
        yAxisIndex: 1,
        /**
         * One entry per bucket rather than a bare number, so the STILL-FILLING
         * day can carry the hatch it carries in SVG. Without it the canvas path
         * drew the partial day as an ordinary bar and the only thing saying it
         * was incomplete was a colour — which is to say, nothing.
         *
         * Full opacity: an execution count is a measurement, and a 45 % wash of
         * series-2 measures 2.5:1 on the darkest card and 1.6:1 on the light
         * one, under the 3:1 a data-carrying object owes SC 1.4.11.
         */
        data: model.points.map((point) => ({
          value: point.executions,
          itemStyle: point.partial
            ? { color: tokens.series[1], decal: decalOf('diagonal', tokens.card) ?? undefined }
            : undefined,
        })),
        itemStyle: { color: tokens.series[1] },
        large: true,
      },
      {
        id: 'pass_rate',
        name: RATE_AXIS_TITLE,
        type: 'line',
        yAxisIndex: 0,
        // `null` = a gap. `connectNulls` stays off: a bridged gap invents days.
        connectNulls: false,
        sampling: TIME_SERIES_SAMPLING,
        showSymbol: model.singlePoint || model.isolated.length > 0,
        symbolSize: 6,
        data: model.points.map((point) => point.rate),
        lineStyle: { color: tokens.series[0], width: 2 },
        itemStyle: { color: tokens.series[0] },
        markLine: model.markers.length
          ? {
              symbol: 'none',
              silent: true,
              lineStyle: { color: tokens.textMuted, type: 'dashed' },
              // No `formatter` here by design: ECharts' default label is the
              // mark's own `name`, and any formatter would have to go through
              // `domTooltipFormatter`, which a markLine label cannot use.
              label: { color: tokens.axis, position: 'insideEndTop' },
              data: model.markers.map((marker) => ({ xAxis: marker.x, name: marker.names.join(', ') })),
            }
          : undefined,
      },
    ],
  } as TimeSeriesOption
}
