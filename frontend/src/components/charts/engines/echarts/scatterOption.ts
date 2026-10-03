/**
 * Pure option builder for the test scatter (VIZ-506) — no ECharts import
 * (types are erased), so it is unit-testable in jsdom and costs nothing until
 * a scatter actually renders.
 *
 *   - x: the p95 duration on a LOG axis (a suite holds 3 ms unit tests and
 *     4-minute end-to-end tests; on a linear axis every fast test is one
 *     smudge at 0), extent snapped to whole decades so the ticks are 1, 10,
 *     100, ... ms. No axis `formatter` of any kind (the guard forbids it, plan
 *     F6): ECharts' default numeric tick labels, the unit in the axis NAME.
 *   - y: the failure rate on its declared unit's fixed range (0..100 for
 *     percent), so two suites' scatters read on one scale.
 *   - size: executions, by AREA (the diameter grows with the square root), so
 *     a test run four times as often looks four times as big, not sixteen.
 *   - colour AND symbol by quadrant: colour is never the only channel (a
 *     triangle is slow and flaky whatever the reader's colour vision), and the
 *     tooltip and the table name the quadrant in words.
 *   - the medians: a dashed `markLine` each, silent, with no label (a label
 *     would need a formatter or print a raw float); the key under the plot
 *     names them.
 *   - the brush: `BrushComponent` with no toolbox (spike S2), driven by
 *     `takeGlobalCursor` from the renderer. Points inside the rectangle stay
 *     opaque, the rest fade, so the selection is visible on the plot; WHICH
 *     points are selected is decided by our own filter, not by this visual.
 *   - NEVER `large` mode (spike S2: with it the size callback draws nothing
 *     and `brushSelected` is always empty). 5,000 plain points draw in ~210 ms.
 */
import type { ComposeOption } from 'echarts/core'
import type { ScatterSeriesOption } from 'echarts/charts'
import type {
  AriaComponentOption,
  BrushComponentOption,
  GridComponentOption,
  MarkLineComponentOption,
  TooltipComponentOption,
} from 'echarts/components'
import type { PointsAxis, PointsChart } from '@/lib/viz/contracts'
import type { ChartTokens } from '../../tokens'
import { domTooltipFormatter } from '../../tooltip'
import { canvasSafeText } from './canvasText'
import { echartsTipPosition, type EchartsMarkOf } from '../../tipPlacement'
import { quadrantOf, scatterTooltipContent, type Quadrant } from '../../testScatter.model'

export type ScatterOption = ComposeOption<
  | ScatterSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | AriaComponentOption
  | BrushComponentOption
  | MarkLineComponentOption
>

/** Above this many points the symbols shrink (alpha already lets overlaps read as density). */
export const SCATTER_DENSE_POINTS = 2000

/** Symbol diameters, px: the smallest and the largest test, normal and dense. */
export const SCATTER_SYMBOL = { min: 6, max: 24 } as const
export const SCATTER_SYMBOL_DENSE = { min: 4, max: 14 } as const

/** Every point is drawn translucent, so overplotted points read darker (plan 3.5: alpha 0.55). */
export const SCATTER_ALPHA = 0.55
/** Points outside the brush rectangle, while one is drawn. */
export const OUT_OF_BRUSH_ALPHA = 0.15

/** The shape that carries each quadrant beside its colour (the key under the plot draws the same shapes). */
export const QUADRANT_SYMBOLS: Record<Quadrant, 'triangle' | 'diamond' | 'rect' | 'circle'> = {
  'slow-flaky': 'triangle',
  'fast-flaky': 'diamond',
  'slow-stable': 'rect',
  'fast-stable': 'circle',
}

/** Each quadrant's colour, from resolved tokens only: the salient one is the failure colour, the calm one is muted. */
export function quadrantColor(quadrant: Quadrant, tokens: ChartTokens): string {
  switch (quadrant) {
    case 'slow-flaky':
      return tokens.status.failed
    case 'fast-flaky':
      return tokens.flaky
    case 'slow-stable':
      return tokens.series[0] ?? tokens.axis
    case 'fast-stable':
      return tokens.textMuted
  }
}

/** A point's diameter: the area grows with `size` (sqrt), from `min` (size 0) to `max` (the largest). */
export function symbolDiameter(size: number, maxSize: number, dense: boolean): number {
  const { min, max } = dense ? SCATTER_SYMBOL_DENSE : SCATTER_SYMBOL
  if (!(maxSize > 0) || !(size > 0)) return min
  return Math.round((min + (max - min) * Math.sqrt(Math.min(1, size / maxSize))) * 10) / 10
}

/**
 * A log axis' extent in whole decades around the data (1 .. 10,000 for 3 ms
 * .. 4 s), so its ticks are the round numbers a reader expects. One value, or
 * all values equal, still gets a decade of room.
 */
export function logExtent(values: readonly number[]): [number, number] {
  let low = Infinity
  let high = -Infinity
  for (const v of values) {
    if (v > 0 && v < low) low = v
    if (v > high) high = v
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [1, 10]
  const min = 10 ** Math.floor(Math.log10(low))
  const max = 10 ** Math.ceil(Math.log10(high))
  return [min, max > min ? max : min * 10]
}

/** A linear axis' fixed range by its unit: 0..100 for percent, 0..1 for a ratio, else from 0 to the data. */
function linearRange(axis: PointsAxis): { min: number; max?: number } {
  if (axis.unit === 'percent') return { min: 0, max: 100 }
  if (axis.unit === 'ratio') return { min: 0, max: 1 }
  return { min: 0 }
}

/**
 * How far an axis extends past an edge a point touches, as a share of its
 * span (in decades on a log axis): room for half the largest symbol (12 px)
 * on a ~200 px plot. Without it the stable tests (0% failures, often the
 * majority) sit ON the x axis, half their marks below the plot and over its
 * tick labels, and a test at the last decade runs past the plot's edge (F-07).
 */
export const SCATTER_EDGE_PAD = 0.06

export interface PaddedExtent {
  min: number
  max: number | undefined
  /** False where the edge moved: its tick would be an odd number (-6, 12,589), so the round ticks stay the only labels. */
  showMinLabel: boolean
  showMaxLabel: boolean
}

/**
 * The axis extent with room past each edge some point touches (within the
 * pad of it), and only there: a chart whose points all sit inside keeps its
 * plain range. An open top (`max` undefined, ECharts rounds it up) only pads
 * the bottom, against the data's own span. Values a log axis cannot draw
 * (zero, negative) and non-numbers touch nothing.
 */
export function padExtent(extent: { min: number; max?: number }, values: readonly number[], log: boolean): PaddedExtent {
  const plain: PaddedExtent = { min: extent.min, max: extent.max, showMinLabel: true, showMaxLabel: true }
  const toAxis = (v: number) => (log ? Math.log10(v) : v)
  const drawable = values.filter((v) => Number.isFinite(v) && (!log || v > 0)).map(toAxis)
  if (drawable.length === 0) return plain
  const low = toAxis(extent.min)
  const high = extent.max === undefined ? undefined : toAxis(extent.max)
  const span = (high ?? Math.max(...drawable)) - low
  if (!(span > 0)) return plain
  const pad = span * SCATTER_EDGE_PAD
  const touchesLow = drawable.some((v) => v < low + pad)
  const touchesHigh = high !== undefined && drawable.some((v) => v > high - pad)
  const fromAxis = (v: number) => (log ? 10 ** v : v)
  return {
    min: touchesLow ? fromAxis(low - pad) : extent.min,
    max: touchesHigh && high !== undefined ? fromAxis(high + pad) : extent.max,
    showMinLabel: !touchesLow,
    showMaxLabel: !touchesHigh,
  }
}

/** An axis' type and padded extent, and which edges moved (`padExtent`). */
function axisExtent(axis: PointsAxis, values: readonly number[]) {
  const log = axis.scale === 'log'
  const base = log ? (([min, max]) => ({ min, max }))(logExtent(values)) : linearRange(axis)
  const padded = padExtent(base, values, log)
  const range = padded.max === undefined ? { min: padded.min } : { min: padded.min, max: padded.max }
  return {
    scale: log ? { type: 'log' as const, logBase: 10, ...range } : { type: 'value' as const, ...range },
    padded,
  }
}

/**
 * An axis' tick labels, every key spelled out (the chart guard reads them; no
 * formatter of any kind): the axis colour, the presentation size (`undefined`
 * = ECharts' own), and a moved edge's label hidden (`undefined` = ECharts'
 * own rule for an unmoved one).
 */
function tickLabels(color: string, fontSize: number | undefined, edges: PaddedExtent) {
  return {
    color,
    fontSize,
    showMinLabel: edges.showMinLabel ? undefined : false,
    showMaxLabel: edges.showMaxLabel ? undefined : false,
  }
}

/** The axis name: the server's label, plus the scale when it is not linear. */
export function axisName(axis: PointsAxis): string {
  return axis.scale === 'log' ? `${axis.label}, log scale` : axis.label
}

/** ECharts' own default label size, px. */
export const SCATTER_FONT_SIZE = 12

function fontSizeFor(textScale: number): number | undefined {
  if (!Number.isFinite(textScale) || textScale <= 1) return undefined
  return Math.round(SCATTER_FONT_SIZE * textScale)
}

/**
 * The plot's insets, px. `containLabel` keeps the tick labels inside them
 * whatever the font (Linux CI renders DejaVu, wider than Windows fonts:
 * nothing is tuned to one font's width); the top holds the y axis name, the
 * bottom the x axis name, both growing with the text scale.
 */
export function scatterGrid(textScale: number): { top: number; right: number; bottom: number; left: number; containLabel: true } {
  const scale = fontSizeFor(textScale) === undefined ? 1 : textScale
  return { top: Math.round(28 * scale), right: 24, bottom: Math.round(30 * scale), left: 12, containLabel: true }
}

/** The x axis name's distance below the axis line: under the tick labels. */
export function xNameGap(textScale: number): number {
  const scale = fontSizeFor(textScale) === undefined ? 1 : textScale
  return Math.round(28 * scale)
}

/** A point's tooltip mark: a small box around the pointer (a scatter item hands no rectangle). */
export const scatterPointMark: EchartsMarkOf = (point) => ({
  left: (point[0] ?? 0) - 6,
  top: (point[1] ?? 0) - 6,
  width: 12,
  height: 12,
})

/** The index a tooltip is for, recovered from ECharts' params without trusting their shape. */
function indexOf(params: unknown): number | null {
  const first = Array.isArray(params) ? params[0] : params
  const index = (first as { dataIndex?: unknown } | undefined)?.dataIndex
  return typeof index === 'number' && Number.isInteger(index) ? index : null
}

export interface ScatterOptionInput {
  data: PointsChart
  tokens: ChartTokens
  animate?: boolean
  /** `usePresentationScale()`: full screen and presentation mode draw the canvas text larger. Default 1. */
  textScale?: number
  /**
   * The drag-to-select mode is on. The tooltip is then NOT enterable: an
   * enterable tooltip takes the pointer, and a drag that crossed it stalled
   * (the brush never ended, nothing was selected: seen on Linux, where a
   * point's tooltip lay on the drag's path). Default off: hoverable (SC 1.4.13).
   */
  brushing?: boolean
}

export function buildScatterOption({ data, tokens, animate = false, textScale = 1, brushing = false }: ScatterOptionInput): ScatterOption {
  const fontSize = fontSizeFor(textScale)
  const dense = data.points.length > SCATTER_DENSE_POINTS
  let maxSize = 0
  for (const point of data.points) if (point.size > maxSize) maxSize = point.size
  const medians = data.medians

  const textStyle = fontSize === undefined ? { color: tokens.axis } : { color: tokens.axis, fontSize }
  // Each axis line at the plot's edge, never at the other axis' zero: with
  // room below 0% the x axis would otherwise cut through the stable tests.
  const axisLine = { onZero: false, lineStyle: { color: tokens.grid } }
  const splitLine = { lineStyle: { color: tokens.grid } }
  const x = axisExtent(data.x, data.points.map((p) => p.x))
  const y = axisExtent(data.y, data.points.map((p) => p.y))

  const series: ScatterSeriesOption = {
    type: 'scatter',
    id: 'tests',
    // Spike S2: `large` stays OFF at every size.
    large: false,
    // Nothing draws outside the grid (the padded extent keeps every mark inside it).
    clip: true,
    data: data.points.map((point) => {
      const quadrant = medians ? quadrantOf(point, medians) : 'fast-stable'
      return {
        value: [point.x, point.y, point.size],
        symbol: QUADRANT_SYMBOLS[quadrant],
        symbolSize: symbolDiameter(point.size, maxSize, dense),
        itemStyle: { color: quadrantColor(quadrant, tokens), opacity: SCATTER_ALPHA },
      }
    }),
    // The keyboard's (and the pointer's) active point: opaque, ringed in the text colour.
    emphasis: { scale: 1.4, itemStyle: { opacity: 1, borderColor: tokens.text, borderWidth: 2 } },
    ...(medians
      ? {
          markLine: {
            silent: true,
            symbol: 'none',
            animation: false,
            label: { show: false },
            lineStyle: { type: 'dashed' as const, color: tokens.axis, width: 1 },
            data: [{ xAxis: medians.x }, { yAxis: medians.y }],
          },
        }
      : {}),
  }

  return {
    animation: animate,
    // Off: the wrapper names the chart (once) and the frame's summary describes it.
    aria: { enabled: false },
    grid: scatterGrid(textScale),
    tooltip: {
      trigger: 'item',
      // The same box every kit tooltip draws (`TIP_BOX_STYLE`).
      backgroundColor: tokens.card,
      borderColor: tokens.border,
      borderWidth: 1,
      borderRadius: 8,
      padding: [4, 8],
      textStyle: { color: tokens.text },
      // VIZ-601: beside the point, flipped at an edge, inside the chart, and
      // something the pointer can move onto (SC 1.4.13 Hoverable).
      confine: true,
      enterable: !brushing,
      position: echartsTipPosition(scatterPointMark),
      formatter: domTooltipFormatter((params: unknown) => {
        const index = indexOf(params)
        return index === null ? { rows: [] } : scatterTooltipContent(data, index)
      }),
    },
    xAxis: {
      ...x.scale,
      name: canvasSafeText(axisName(data.x)),
      nameLocation: 'middle',
      nameGap: xNameGap(textScale),
      nameTextStyle: textStyle,
      axisLabel: tickLabels(tokens.axis, fontSize, x.padded),
      axisLine,
      splitLine,
    },
    yAxis: {
      ...y.scale,
      name: canvasSafeText(axisName(data.y)),
      nameLocation: 'end',
      // From the axis line rightward: centred on it, a long name would run off the chart's left edge.
      nameTextStyle: { ...textStyle, align: 'left' },
      axisLabel: tickLabels(tokens.axis, fontSize, y.padded),
      axisLine,
      splitLine,
    },
    brush: {
      // No toolbox: nothing is drawn, nothing brushes until the renderer
      // takes the global cursor (spike S2).
      toolbox: [],
      xAxisIndex: 0,
      yAxisIndex: 0,
      brushType: 'rect',
      brushMode: 'single',
      throttleType: 'debounce',
      throttleDelay: 0,
      brushStyle: { borderWidth: 1, color: 'transparent', borderColor: tokens.text },
      inBrush: { opacity: 1 },
      outOfBrush: { opacity: OUT_OF_BRUSH_ALPHA },
    },
    series: [series],
  }
}
