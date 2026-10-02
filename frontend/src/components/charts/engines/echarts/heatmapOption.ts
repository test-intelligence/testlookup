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
  LegendComponentOption,
  TooltipComponentOption,
  VisualMapComponentOption,
} from 'echarts/components'
import type { MatrixChart, VizStatus } from '@/lib/viz/contracts'
import { decalOf, echartsDecal, STATUS_ENCODING, type ChartTokens } from '../../tokens'
import { domTooltipFormatter, sampleRow, tipContent, type TooltipContent } from '../../tooltip'
import { echartsTipPosition, type EchartsMarkOf } from '../../tipPlacement'
import { NO_DATA, formatPlainValue, formatRateValue } from '../../chartText'

export type HeatmapOption = ComposeOption<
  | HeatmapSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | VisualMapComponentOption
  | AriaComponentOption
  | LegendComponentOption
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
 * The "No data" key's own row under the colour bar, px at text scale 1 (a
 * 14 px swatch and its 12 px label, plus a gap). A row, not a place beside the
 * bar: the bar sits centred, and beside it the two collide on a phone-width
 * chart; the bar's room is the key's when no bar is drawn.
 */
export const HEATMAP_NO_DATA_KEY_ROW = 22

/**
 * Up to this many columns every cell keeps a 1 px border in the card colour.
 * Past it (a 90-day window draws ~5 px columns) the borders were a fifth of
 * the ink: the plot read as card-coloured stripes, not as cells.
 */
export const HEATMAP_BORDERED_COLUMNS_MAX = 30

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

/**
 * A label's width per character, in em, for choosing how many columns one
 * printed label stands for. An OVER-estimate on purpose: DejaVu Sans (the CI
 * runner's font) is wider than the Windows fonts, and a step that is one too
 * many costs a label, where one too few cuts every label to a stub.
 */
const LABEL_EM_PER_CHAR = 0.65

/**
 * How many columns one printed label stands for, past `HEATMAP_ALL_LABELS_MAX`:
 * the longest label (estimated, `LABEL_EM_PER_CHAR`) plus the gap, in columns.
 */
export function heatmapColumnLabelStep(labels: readonly string[], columnWidth: number, fontPx: number): number {
  const longest = labels.reduce((most, label) => Math.max(most, label.length), 0)
  const needed = longest * fontPx * LABEL_EM_PER_CHAR + LABEL_GAP
  return columnWidth > 0 ? Math.max(1, Math.ceil(needed / columnWidth)) : labels.length || 1
}

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
  /**
   * How much bigger the canvas text is drawn: `usePresentationScale()` (full
   * screen, presentation mode). The Recharts charts get this by scaling their
   * whole SVG drawing; a canvas cannot be scaled without blurring, so its text
   * is sized here instead. Default 1: the option is exactly as before.
   */
  textScale?: number
  /**
   * Draw row 0 at the TOP (a ranked matrix: "worst first" reads downward, as
   * its table does). Default: ECharts' own category order, row 0 at the
   * bottom, which every existing heatmap was drawn with.
   */
  rowsTopDown?: boolean
  /**
   * What the column axis PRINTS, one per column (a day axis prints "Sep 5").
   * The tooltip, the announcement and the table keep `data.x_labels` (the
   * full day). Default: `data.x_labels`.
   */
  columnLabels?: readonly string[]
}

/** ECharts' own default label size, px: what the heatmap's axis and ramp text draw at unscaled. */
export const HEATMAP_FONT_SIZE = 12

/** The label size for a text scale; `undefined` at 1, so an unscaled option keeps ECharts' default. */
export function heatmapFontSize(textScale: number): number | undefined {
  if (!Number.isFinite(textScale) || textScale <= 1) return undefined
  return Math.round(HEATMAP_FONT_SIZE * textScale)
}

/**
 * The plot's insets for a text scale. The y labels and the x labels + colour
 * bar live in the left and bottom insets, so those grow with the text; at 1
 * this is `HEATMAP_GRID` itself.
 */
export function heatmapGrid(textScale: number): { top: number; right: number; bottom: number; left: number } {
  if (heatmapFontSize(textScale) === undefined) return { ...HEATMAP_GRID }
  return {
    ...HEATMAP_GRID,
    bottom: Math.round(HEATMAP_GRID.bottom * textScale),
    left: Math.round(HEATMAP_GRID.left * textScale),
  }
}

/**
 * A category axis's labels: every one shown, each cut (ECharts measures the
 * text) to `width` px when there are few enough to fit; ECharts' own thinning
 * past `HEATMAP_ALL_LABELS_MAX`. The FULL label is always in the tooltip, the
 * announcement and the table (`heatmapTooltipContent`) — the cut is display only.
 */
function categoryLabels(count: number, width: number, color: string, fontSize: number | undefined) {
  const every = count <= HEATMAP_ALL_LABELS_MAX
  const style = {
    color,
    interval: every ? 0 : ('auto' as const),
    width: Math.max(1, Math.floor(width)),
    overflow: 'truncate' as const,
    ellipsis: '…',
  }
  // Only when scaled: an unscaled option must stay byte-identical (the
  // committed heatmap baselines were drawn at ECharts' default size).
  return fontSize === undefined ? style : { ...style, fontSize }
}

export function formatHeatmapValue(valueType: HeatmapMatrix['value_type'], value: number | VizStatus): string {
  if (typeof value === 'string') return STATUS_ENCODING[value]?.label ?? value
  return valueType === 'rate' ? formatRateValue(value) : formatPlainValue(value)
}

type Cell = HeatmapMatrix['cells'][number]

/**
 * A cell's tooltip content, in the shared model (VIZ-601): its row as the
 * title, its column and exact value, and the sample size n behind it. The
 * mouse tooltip (via the formatter) and the keyboard announcement both come
 * from here, so they cannot differ. No change against a neighbouring cell:
 * a matrix does not say its columns are ordered in time, and a "change vs
 * previous" across two failure categories would be a number about nothing.
 */
export function heatmapTooltipContent(data: HeatmapMatrix, cell: Cell): TooltipContent {
  return tipContent(data.y_labels[cell.y] ?? '', [
    {
      kind: 'value',
      key: 'value',
      label: data.x_labels[cell.x] ?? '',
      value: cell.value === null ? NO_DATA : formatHeatmapValue(data.value_type, cell.value),
    },
    { ...sampleRow(cell.n), value: formatPlainValue(cell.n) },
  ])
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

/** A heatmap tooltip's mark: the cell ECharts hands the `position` callback, else the pointer. */
export const heatmapCellMark: EchartsMarkOf = (point, rect) =>
  rect
    ? { left: rect.x, top: rect.y, width: rect.width, height: rect.height }
    : { left: point[0] ?? 0, top: point[1] ?? 0, width: 0, height: 0 }

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
  textScale = 1,
  rowsTopDown = false,
  columnLabels,
}: HeatmapOptionInput): HeatmapOption {
  const byCell = new Map<string, Cell>(data.cells.map((cell) => [`${cell.x}:${cell.y}`, cell]))
  const empty = data.cells.filter((cell) => cell.value === null)
  const fontSize = heatmapFontSize(textScale)
  // The colour bar: a numeric matrix with at least one measured cell.
  const rampShown = data.value_type !== 'status' && empty.length < data.cells.length
  // R2-18: hatched cells get a "No data" key, in its own row under the bar
  // (or in the bar's place when there is none). Scaled with the text, as the
  // insets are; 0 when nothing is hatched, so that layout is exactly as before.
  const keyRow =
    empty.length > 0 && rampShown
      ? fontSize === undefined
        ? HEATMAP_NO_DATA_KEY_ROW
        : Math.round(HEATMAP_NO_DATA_KEY_ROW * textScale)
      : 0
  const insets = heatmapGrid(textScale)
  const grid = keyRow === 0 ? insets : { ...insets, bottom: insets.bottom + keyRow }
  const cellBorder =
    data.x_labels.length > HEATMAP_BORDERED_COLUMNS_MAX ? { borderWidth: 0 } : { borderColor: tokens.card, borderWidth: 1 }
  const noDataDecal = decalOf('diagonal', tokens.axis) ?? undefined

  const plotWidth = chartWidth - grid.left - grid.right
  const columnWidth = plotWidth / Math.max(1, data.x_labels.length)
  const printed = columnLabels && columnLabels.length === data.x_labels.length ? columnLabels : data.x_labels
  // Past the limit (a 14- to 90-day axis), label every k-th column and give
  // each printed label the k columns it stands for. Cutting each to ONE
  // column's width, whatever ECharts then thinned, printed "S…" under every
  // other day of a 14-day axis and nothing at all under 90.
  const step =
    data.x_labels.length <= HEATMAP_ALL_LABELS_MAX
      ? 0
      : heatmapColumnLabelStep(printed, columnWidth, fontSize ?? HEATMAP_FONT_SIZE)
  const xLabel =
    step === 0
      ? categoryLabels(data.x_labels.length, columnWidth - LABEL_GAP, tokens.axis, fontSize)
      : { ...categoryLabels(data.x_labels.length, step * columnWidth - LABEL_GAP, tokens.axis, fontSize), interval: step - 1 }
  // The y labels share the left inset, whatever the row count.
  const yLabel = categoryLabels(data.y_labels.length, grid.left - LABEL_GAP, tokens.axis, fontSize)
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
          itemStyle: cellBorder,
          emphasis,
        }
      : {
          type: 'heatmap',
          id: 'cells',
          // `null` stays "no data" — ECharts leaves a '-' cell undrawn; the no-data series draws it.
          data: data.cells.map((cell) => [cell.x, cell.y, cell.value ?? '-']),
          itemStyle: cellBorder,
          emphasis,
        }

  const series: HeatmapSeriesOption[] = [measuredSeries]
  if (empty.length > 0) {
    series.push({
      type: 'heatmap',
      id: NO_DATA_SERIES_ID,
      // The key's entry (`legend` below) names this series.
      name: NO_DATA,
      data: empty.map((cell) => [cell.x, cell.y, 0]),
      itemStyle: {
        color: tokens.card,
        borderColor: tokens.grid,
        borderWidth: 1,
        decal: noDataDecal,
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
      show: rampShown,
      seriesIndex: 0,
      min: 0,
      max,
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      // Above the "No data" key's row when there is one.
      bottom: keyRow,
      itemHeight: 120,
      // Step 7 is the salient end: put it where the problem is.
      inRange: { color: direction === 'high' ? [...tokens.seq] : [...tokens.seq].reverse() },
      // The ramp's two ENDS, in words: `[max, min]` (ECharts' order). A colour
      // bar with no values on it cannot be read at all, and because the
      // salient end flips with the metric (a rate is reversed, a count is
      // not), the same end colour meant "lowest" on one heatmap and "highest" on
      // the next — the first Linux baselines showed both, unlabelled.
      text: [rampEndLabel(data.value_type, max), rampEndLabel(data.value_type, 0)],
      textStyle: fontSize === undefined ? { color: tokens.axis } : { color: tokens.axis, fontSize },
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
    grid,
    tooltip: {
      trigger: 'item',
      // The same box the Recharts tooltips draw (`TIP_BOX_STYLE`).
      backgroundColor: tokens.card,
      borderColor: tokens.border,
      borderWidth: 1,
      borderRadius: 8,
      padding: [4, 8],
      textStyle: { color: tokens.text },
      // VIZ-601: beside the cell (ECharts hands the callback the cell's own
      // rect), never over it; flipped at an edge; inside the chart
      // (`confine`); and something the pointer can move onto (`enterable`,
      // SC 1.4.13 Hoverable). The keyboard's `showTip` lands here too.
      confine: true,
      enterable: true,
      position: echartsTipPosition(heatmapCellMark),
      formatter: domTooltipFormatter((params: unknown) => {
        const at = cellOf(params)
        const cell = at ? byCell.get(`${at.x}:${at.y}`) : undefined
        return cell ? heatmapTooltipContent(data, cell) : { rows: [] }
      }),
    },
    xAxis: {
      type: 'category',
      data: columnLabels && columnLabels.length === data.x_labels.length ? [...columnLabels] : data.x_labels,
      axisLabel: xLabel,
      axisLine,
      splitArea: { show: false },
    },
    yAxis: rowsTopDown
      ? { type: 'category', data: data.y_labels, axisLabel: yLabel, axisLine, splitArea: { show: false }, inverse: true }
      : { type: 'category', data: data.y_labels, axisLabel: yLabel, axisLine, splitArea: { show: false } },
    visualMap,
    series,
    // R2-18: the hatch, keyed. The swatch is drawn as the cell is (card fill,
    // the same decal), outlined like every other legend swatch in the kit;
    // not a toggle — hiding the hatched cells would leave holes that read as
    // the card. Only when something is hatched: otherwise the option is
    // exactly as before.
    ...(empty.length > 0
      ? {
          legend: {
            data: [NO_DATA],
            selectedMode: false,
            left: 'center',
            bottom: 0,
            itemWidth: 14,
            itemHeight: 14,
            itemStyle: { color: tokens.card, borderColor: tokens.border, borderWidth: 1, decal: noDataDecal },
            textStyle: fontSize === undefined ? { color: tokens.axis } : { color: tokens.axis, fontSize },
          },
        }
      : {}),
  }
}
