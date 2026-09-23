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
import { domTooltipFormatter, type TooltipContent } from '../../tooltip'
import { NO_VALUE } from '../../chartText'
import { formatNumber } from '@/utils/formatters'
import {
  EXECUTIONS_AXIS_TITLE,
  RATE_AXIS_TITLE,
  localDayRange,
  type TimeSeriesModel,
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

/**
 * One bucket's tooltip content, as DATA. The mouse tooltip (through
 * `domTooltipFormatter`) and any keyboard announcement both come from here, so
 * they cannot say different things.
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
  const rows: TooltipContent['rows'] = [
    {
      label: 'Local',
      value: local.offsetChanged
        ? `${local.start} – ${local.end} (clocks change)`
        : `${local.start} – ${local.end}`,
    },
    {
      label: RATE_AXIS_TITLE,
      value: point.rate === null ? NO_VALUE : `${formatNumber(point.rate, { maximumFractionDigits: 1 })}%`,
      color: tokens?.series[0],
    },
    {
      label: EXECUTIONS_AXIS_TITLE,
      value: point.executions === null ? NO_VALUE : formatNumber(point.executions),
      color: tokens?.series[1],
    },
  ]
  if (point.rate === null && point.rateReason) rows.push({ label: 'Why', value: point.rateReason })
  if (point.partial) {
    const names = inProgressRuns?.find((entry) => entry.x === point.x)?.names ?? []
    rows.push({
      label: 'Still filling',
      value: names.length
        ? `in progress: ${names.join(', ')}`
        : model.inProgressCount > 0
          ? `${formatNumber(model.inProgressCount)} run(s) in progress`
          : 'this UTC day has not closed yet',
    })
  }
  const marker = model.markers.find((entry) => entry.x === point.x)
  if (marker) rows.push({ label: 'Release', value: marker.names.join(', ') })
  return { title: `${point.x} (UTC)`, rows }
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
    grid: { left: 56, right: 56, top: 36, bottom: 32, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: tokens.card,
      borderColor: tokens.border,
      textStyle: { color: tokens.text },
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
        min: 0,
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
