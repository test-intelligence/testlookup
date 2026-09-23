/**
 * Pure option builder for the heatmap renderer — no ECharts import (types are
 * erased), so it is unit-testable in jsdom and costs nothing until a heatmap
 * actually renders.
 *
 * Colours come only from resolved chart tokens; the tooltip only from
 * `domTooltipFormatter` (labels are untrusted CI text). ECharts' own aria label
 * is OFF: the wrapper in `HeatmapChart` names the chart once, and our summary
 * describes it (ADR: ECharts' generated text is not useful).
 *
 *   - no-data (`null`) cells are drawn by a second series, `no-data`: the card
 *     colour with a hatch decal in the axis colour. Every ramp step is >= 3:1
 *     against the card (asserted per theme), so an empty cell can never read as
 *     the lowest value.
 *   - `salient`: which end of the value range is the concern. The ramp's most
 *     salient step (7) goes there — for a pass rate that is the LOW end.
 *   - the active (keyboard-highlighted) cell: a 2px text-colour outline with a
 *     card-colour halo, so on any ramp step one of the two rings is >= 3:1.
 *   - a `status` matrix is coloured by status AND carries each status decal
 *     (never colour-only).
 */
import type { ComposeOption } from 'echarts/core'
import type { HeatmapSeriesOption } from 'echarts/charts'
import type {
  AriaComponentOption,
  GridComponentOption,
  TooltipComponentOption,
  VisualMapComponentOption,
} from 'echarts/components'
import type { MatrixChart, VizStatus } from '@/lib/viz/contracts'
import { decalOf, echartsDecal, STATUS_ENCODING, type ChartTokens } from '../../tokens'
import { domTooltipFormatter, type TooltipContent } from '../../tooltip'
import { NO_DATA, formatPlainValue, formatRateValue } from '../../chartText'

export type HeatmapOption = ComposeOption<
  HeatmapSeriesOption | GridComponentOption | TooltipComponentOption | VisualMapComponentOption | AriaComponentOption
>

/** A matrix whose cells are numbers (`rate` 0..1 or `count`), `null` = no data. */
export interface NumericMatrix extends Omit<MatrixChart, 'value_type' | 'cells'> {
  value_type: 'rate' | 'count'
  cells: { x: number; y: number; value: number | null; n: number }[]
}

/** A matrix whose cells are statuses, `null` = no data. */
export interface StatusMatrix extends Omit<MatrixChart, 'value_type' | 'cells'> {
  value_type: 'status'
  cells: { x: number; y: number; value: VizStatus | null; n: number }[]
}

export type HeatmapMatrix = NumericMatrix | StatusMatrix

/** Which end of the value range is the problem, and so gets the ramp's most salient colour. */
export type HeatmapSalience = 'low' | 'high'

/** Per metric: a low pass RATE is the concern; a high COUNT (failures, …) is. */
export function defaultSalient(valueType: NumericMatrix['value_type']): HeatmapSalience {
  return valueType === 'rate' ? 'low' : 'high'
}

export const NO_DATA_SERIES_ID = 'no-data'

/** The plot's insets inside the chart box, px. The y labels live in `left`, the x labels and colour bar in `bottom`. */
export const HEATMAP_GRID = { top: 8, right: 16, bottom: 56, left: 120 } as const

/**
 * Up to this many categories on an axis, EVERY one is labelled (`interval: 0`)
 * and a long label is cut to its own column instead. ECharts' default
 * (`'auto'`) thins labels by the WIDEST one: on a two-column heatmap one long
 * CI label dropped its neighbour "benign" entirely, and a reader could not
 * tell which column was which. Past the limit the columns are too narrow for
 * any text to fit, and thinning is the lesser evil; the tooltip and the table
 * still name every cell.
 */
export const HEATMAP_ALL_LABELS_MAX = 12

/** The chart width assumed when the caller's is not a number of px (`'100%'`): narrow enough to be safe. */
export const HEATMAP_ASSUMED_WIDTH = 480

/** Space kept between two neighbouring labels, px. */
const LABEL_GAP = 8

export interface HeatmapOptionInput {
  data: HeatmapMatrix
  tokens: ChartTokens
  /** Our own one-sentence description for assistive tech (read by the wrapper). */
  description: string
  animate?: boolean
  /** Defaults per metric (`defaultSalient`). Ignored for a status matrix. */
  salient?: HeatmapSalience
  /** The chart box's width in px, when known; sizes each x label's column. Default `HEATMAP_ASSUMED_WIDTH`. */
  chartWidth?: number
}

/**
 * A category axis's labels: every one shown, each cut (ECharts measures the
 * text) to `width` px when there are few enough to fit; ECharts' own thinning
 * past `HEATMAP_ALL_LABELS_MAX`. The FULL label is always in the tooltip, the
 * announcement and the table (`heatmapTooltipContent`) — the cut is display only.
 */
function categoryLabels(count: number, width: number, color: string) {
  const every = count <= HEATMAP_ALL_LABELS_MAX
  return {
    color,
    interval: every ? 0 : ('auto' as const),
    width: Math.max(1, Math.floor(width)),
    overflow: 'truncate' as const,
    ellipsis: '…',
  }
}

export function formatHeatmapValue(valueType: HeatmapMatrix['value_type'], value: number | VizStatus): string {
  if (typeof value === 'string') return STATUS_ENCODING[value]?.label ?? value
  return valueType === 'rate' ? formatRateValue(value) : formatPlainValue(value)
}

type Cell = HeatmapMatrix['cells'][number]

/**
 * A cell's tooltip content. The mouse tooltip (via the formatter) and the
 * keyboard announcement both come from here, so they cannot differ.
 */
export function heatmapTooltipContent(data: HeatmapMatrix, cell: Cell): TooltipContent {
  return {
    title: data.y_labels[cell.y] ?? '',
    rows: [
      { label: data.x_labels[cell.x] ?? '', value: cell.value === null ? NO_DATA : formatHeatmapValue(data.value_type, cell.value) },
      { label: 'Samples', value: formatPlainValue(cell.n) },
    ],
  }
}

/**
 * Where cell `index` is drawn, for ECharts' `highlight` / `showTip` actions:
 * measured cells are series 0 (same index), no-data cells are their position
 * in the `no-data` series (1).
 */
export function heatmapTarget(data: HeatmapMatrix, index: number): { seriesIndex: number; dataIndex: number } {
  if (data.cells[index]?.value !== null) return { seriesIndex: 0, dataIndex: index }
  let position = 0
  for (let i = 0; i < index; i++) if (data.cells[i].value === null) position += 1
  return { seriesIndex: 1, dataIndex: position }
}

/** The cell a tooltip is for, recovered from ECharts' params without trusting their shape. */
function cellOf(params: unknown): { x: number; y: number } | null {
  const first = Array.isArray(params) ? params[0] : params
  const value = (first as { value?: unknown } | undefined)?.value
  if (!Array.isArray(value)) return null
  const [x, y] = value
  return typeof x === 'number' && typeof y === 'number' ? { x, y } : null
}

/** A colour-ramp end as the tooltip would print that value: "100.0%" for a rate, "42" for a count. */
export function rampEndLabel(valueType: NumericMatrix['value_type'], value: number): string {
  return valueType === 'rate' ? formatRateValue(value) : formatPlainValue(value)
}

export function buildHeatmapOption({
  data,
  tokens,
  animate = false,
  salient,
  chartWidth = HEATMAP_ASSUMED_WIDTH,
}: HeatmapOptionInput): HeatmapOption {
  const byCell = new Map<string, Cell>(data.cells.map((cell) => [`${cell.x}:${cell.y}`, cell]))
  const empty = data.cells.filter((cell) => cell.value === null)

  const plotWidth = chartWidth - HEATMAP_GRID.left - HEATMAP_GRID.right
  const xLabel = categoryLabels(data.x_labels.length, plotWidth / Math.max(1, data.x_labels.length) - LABEL_GAP, tokens.axis)
  // The y labels share the left inset, whatever the row count.
  const yLabel = categoryLabels(data.y_labels.length, HEATMAP_GRID.left - LABEL_GAP, tokens.axis)
  const axisLine = { lineStyle: { color: tokens.grid } }
  // Active cell: 2px outline in the text colour + a halo in the card colour.
  const emphasis = {
    itemStyle: { borderColor: tokens.text, borderWidth: 2, shadowColor: tokens.card, shadowBlur: 6 },
  }

  const measuredSeries: HeatmapSeriesOption =
    data.value_type === 'status'
      ? {
          type: 'heatmap',
          id: 'cells',
          data: data.cells.map((cell) => {
            // Undrawn here (the no-data series draws it); kept so dataIndex === cell index.
            if (cell.value === null) return [cell.x, cell.y, '-']
            const decal = echartsDecal(cell.value, tokens)
            return {
              value: [cell.x, cell.y, 0],
              itemStyle: { color: tokens.status[cell.value], ...(decal ? { decal } : {}) },
            }
          }),
          itemStyle: { borderColor: tokens.card, borderWidth: 1 },
          emphasis,
        }
      : {
          type: 'heatmap',
          id: 'cells',
          // `null` stays "no data" — ECharts leaves a '-' cell undrawn; the no-data series draws it.
          data: data.cells.map((cell) => [cell.x, cell.y, cell.value ?? '-']),
          itemStyle: { borderColor: tokens.card, borderWidth: 1 },
          emphasis,
        }

  const series: HeatmapSeriesOption[] = [measuredSeries]
  if (empty.length > 0) {
    series.push({
      type: 'heatmap',
      id: NO_DATA_SERIES_ID,
      data: empty.map((cell) => [cell.x, cell.y, 0]),
      itemStyle: {
        color: tokens.card,
        borderColor: tokens.grid,
        borderWidth: 1,
        decal: decalOf('diagonal', tokens.axis) ?? undefined,
      },
      emphasis,
    })
  }

  // ECharts requires EVERY heatmap series to be targeted by a visualMap. The
  // colour ramp targets the measured numeric series only; the series that are
  // coloured per item (status cells, no-data cells) get a hidden visualMap
  // that maps nothing but opacity 1, so their own colours and decals stand.
  const visualMap: VisualMapComponentOption[] = []
  const selfColoured: number[] = []
  if (data.value_type === 'status') selfColoured.push(0)
  else {
    let max = 1
    if (data.value_type === 'count') {
      // A loop, not Math.max(...spread): a spread past ~100k arguments overflows the stack.
      for (const cell of data.cells) if (cell.value !== null && cell.value > max) max = cell.value
    }
    const direction = salient ?? defaultSalient(data.value_type)
    visualMap.push({
      type: 'continuous',
      id: 'ramp',
      // Nothing measured, nothing to read against a scale: a "0.0% - 100.0%"
      // bar under a grid of hatched cells reads as if some of them were data.
      // The ramp stays (ECharts needs every heatmap series under a visualMap),
      // only its bar is hidden.
      show: empty.length < data.cells.length,
      seriesIndex: 0,
      min: 0,
      max,
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      itemHeight: 120,
      // Step 7 is the salient end: put it where the problem is.
      inRange: { color: direction === 'high' ? [...tokens.seq] : [...tokens.seq].reverse() },
      // The ramp's two ENDS, in words: `[max, min]` (ECharts' order). A colour
      // bar with no values on it cannot be read at all, and because the
      // salient end flips with the metric (a rate is reversed, a count is
      // not), the same yellow meant "lowest" on one heatmap and "highest" on
      // the next — the first Linux baselines showed both, unlabelled.
      text: [rampEndLabel(data.value_type, max), rampEndLabel(data.value_type, 0)],
      textStyle: { color: tokens.axis },
    })
  }
  if (empty.length > 0) selfColoured.push(1)
  if (selfColoured.length > 0) {
    visualMap.push({
      type: 'continuous',
      id: 'self-coloured',
      show: false,
      seriesIndex: selfColoured,
      min: 0,
      max: 1,
      inRange: { opacity: 1 },
      outOfRange: { opacity: 1 },
    })
  }

  return {
    animation: animate,
    // Off: the wrapper names the chart (once) and the frame's summary describes it.
    aria: { enabled: false },
    grid: { ...HEATMAP_GRID },
    tooltip: {
      trigger: 'item',
      backgroundColor: tokens.card,
      borderColor: tokens.border,
      textStyle: { color: tokens.text },
      formatter: domTooltipFormatter((params: unknown) => {
        const at = cellOf(params)
        const cell = at ? byCell.get(`${at.x}:${at.y}`) : undefined
        return cell ? heatmapTooltipContent(data, cell) : { rows: [] }
      }),
    },
    xAxis: { type: 'category', data: data.x_labels, axisLabel: xLabel, axisLine, splitArea: { show: false } },
    yAxis: { type: 'category', data: data.y_labels, axisLabel: yLabel, axisLine, splitArea: { show: false } },
    visualMap,
    series,
  }
}
