/**
 * Pure option builder for the coverage treemap (VIZ-502) — no ECharts import
 * (types are erased), so it is unit-testable in jsdom and costs nothing until
 * a treemap renders.
 *
 *   - ONE level, flat: the response's children are the series' top-level data
 *     in the server's order (largest first), so ECharts' virtual root is
 *     `dataIndex` 0 and child k is `dataIndex` k + 1 (spike S1: the keyboard
 *     path addresses nodes by that index, never by their untrusted, possibly
 *     repeated names). `sort: false` keeps the layout in that order too.
 *   - No ECharts navigation: `nodeClick: false`, no canvas breadcrumb, no
 *     roam. A level is a request (the section's drill path), and the
 *     breadcrumb is React text.
 *   - Labels are the data NAMES (pre-shortened by `nodeDisplayName`) with the
 *     measure's value on a second line (`nodeCanvasText`), cut by
 *     ECharts to the rectangle with the real font measured (`overflow:
 *     'truncate'`), so a wider font (DejaVu on the Linux runner) cuts more,
 *     never overflows. There is no `formatter` of any kind (plan F6, the
 *     chart guard). The text is the theme's text colour with a card-colour
 *     halo, readable on every ramp step and on the hatch without computing a
 *     contrast per fill.
 *   - Colour per node from resolved tokens (no visualMap): the measure's bin
 *     on the sequential ramp, or, for a node the measure says nothing about,
 *     the card colour with that gap's pattern (never colour alone).
 *   - The keyboard ring (spike S1): ECharts draws a treemap node's emphasis
 *     `borderColor` at the BASE `borderWidth` (an emphasis width is ignored),
 *     so every node has a 2 px border in the card colour that turns into the
 *     text colour on `highlight`; beside a neighbour's card-colour border the
 *     ring always has the text-on-card contrast.
 *   - The tooltip only through `domTooltipFormatter` (labels are CI text).
 */
import type { ComposeOption } from 'echarts/core'
import type { TreemapSeriesOption } from 'echarts/charts'
import type { AriaComponentOption, TooltipComponentOption } from 'echarts/components'
import { decalOf, seriesColor, SERIES_COUNT, type ChartDecal, type ChartTokens } from '../../tokens'
import { domTooltipFormatter } from '../../tooltip'
import { canvasSafeText } from './canvasText'
import { echartsTipPosition, type EchartsMarkOf } from '../../tipPlacement'
import {
  BIN_RAMP_STEPS,
  binOf,
  GAP_DECAL,
  measureOf,
  nodeCanvasText,
  nodeTooltip,
  type CoverageChild,
  type CoverageColorBy,
  type CoverageGap,
} from '../../coverageMap.model'

export type TreemapOption = ComposeOption<TreemapSeriesOption | TooltipComponentOption | AriaComponentOption>

/** ECharts' own label size, px, at text scale 1. */
export const TREEMAP_FONT_SIZE = 12

/** Every node's border, px: the keyboard ring's width (spike S1: emphasis draws at this width). */
export const TREEMAP_BORDER_WIDTH = 2

/** The label's inset from the node's corner, px. */
export const TREEMAP_LABEL_PADDING = 4

export function treemapFontSize(textScale: number): number {
  return Number.isFinite(textScale) && textScale > 1 ? Math.round(TREEMAP_FONT_SIZE * textScale) : TREEMAP_FONT_SIZE
}

/**
 * Where child `index` is drawn, for `highlight` / `downplay` / `showTip`:
 * ECharts' virtual root is `dataIndex` 0, so child k is k + 1 (spike S1).
 */
export function treemapTarget(index: number): { seriesIndex: number; dataIndex: number } {
  return { seriesIndex: 0, dataIndex: index + 1 }
}

export interface NodeFill {
  color: string
  decal: ChartDecal | null
  /** Set when the node is drawn as a gap (hatched), not as a value. */
  gap: CoverageGap | null
  /** The bin the value fell in (0 = least salient), `null` for a gap or the Other node. */
  bin: number | null
}

/**
 * A node's fill for the measure. A value: its bin's ramp step. A gap: the card
 * colour with the gap's pattern in the axis colour. The Other node with no
 * value (a pass rate is never computed for it): the kit's "Other" grey.
 */
export function nodeFill(child: CoverageChild, colorBy: CoverageColorBy, tokens: ChartTokens): NodeFill {
  const { value, gap } = measureOf(child.node.stats, colorBy)
  if (value !== null) {
    const bin = binOf(value, colorBy)
    return { color: tokens.seq[BIN_RAMP_STEPS[bin]], decal: null, gap: null, bin }
  }
  if (child.kind === 'other') return { color: seriesColor(tokens, SERIES_COUNT - 1), decal: null, gap: null, bin: null }
  const kind = gap ?? 'unknown'
  return { color: tokens.card, decal: decalOf(GAP_DECAL[kind], tokens.axis), gap: kind, bin: null }
}

/** The child a tooltip is for, recovered from ECharts' params without trusting their shape. */
function childIndexOf(params: unknown): number | null {
  const first = Array.isArray(params) ? params[0] : params
  const dataIndex = (first as { dataIndex?: unknown } | undefined)?.dataIndex
  return typeof dataIndex === 'number' && Number.isInteger(dataIndex) && dataIndex > 0 ? dataIndex - 1 : null
}

/** A treemap tooltip's mark: the node's own rectangle when ECharts hands it over, else the pointer. */
export const treemapNodeMark: EchartsMarkOf = (point, rect) =>
  rect
    ? { left: rect.x, top: rect.y, width: rect.width, height: rect.height }
    : { left: point[0] ?? 0, top: point[1] ?? 0, width: 0, height: 0 }

export interface TreemapOptionInput {
  children: readonly CoverageChild[]
  colorBy: CoverageColorBy
  tokens: ChartTokens
  animate?: boolean
  /** `usePresentationScale()`: the canvas text grows in full screen and presentation mode. */
  textScale?: number
}

export function buildTreemapOption({ children, colorBy, tokens, animate = false, textScale = 1 }: TreemapOptionInput): TreemapOption {
  const data = children.map((child) => {
    const fill = nodeFill(child, colorBy, tokens)
    return {
      // The drawn text, never a formatter: the shortened label and, under it, the
      // measure's value (F-03: the bin is never colour alone; `nodeCanvasText`).
      // Canvas-safe (R1B-1): zrender measures every PREFIX of a line it cuts, so a
      // line that merely STARTS WITH `__proto__` / `constructor` must not reach
      // its text cache as such (`canvasSafeText` guards each line).
      name: canvasSafeText(nodeCanvasText(child, colorBy)),
      // A size is a count; a negative one would be drawn as nothing anyway.
      value: Math.max(0, child.node.value),
      itemStyle: fill.decal ? { color: fill.color, decal: fill.decal } : { color: fill.color },
    }
  })

  const series: TreemapSeriesOption = {
    type: 'treemap',
    id: 'coverage',
    left: 0,
    top: 0,
    right: 0,
    bottom: 0,
    roam: false,
    nodeClick: false,
    breadcrumb: { show: false },
    sort: false,
    drillDownIcon: '',
    // Draw every node, however small: a node the keyboard can reach must be on the canvas.
    visibleMin: 0,
    label: {
      show: true,
      position: 'insideTopLeft',
      padding: TREEMAP_LABEL_PADDING,
      color: tokens.text,
      textBorderColor: tokens.card,
      textBorderWidth: 3,
      fontSize: treemapFontSize(textScale),
      overflow: 'truncate',
      ellipsis: '…',
    },
    upperLabel: { show: false },
    itemStyle: { borderColor: tokens.card, borderWidth: TREEMAP_BORDER_WIDTH, gapWidth: 0 },
    emphasis: {
      itemStyle: { borderColor: tokens.text },
      upperLabel: { show: false },
    },
    // The ring appears at once, not as a 300 ms blend (spike S1 note 3): a
    // focus indicator that fades in is late, and a blend driven by the clock
    // never finishes where `Date` is pinned (the e2e and visual harnesses).
    stateAnimation: { duration: 0 },
    // The virtual root: no border of its own, so the children reach the edges.
    levels: [{ itemStyle: { borderWidth: 0, gapWidth: 0, borderColor: tokens.card } }],
    data,
  }

  return {
    animation: animate,
    // Off: the wrapper names the chart (once) and the frame's summary describes it.
    aria: { enabled: false },
    tooltip: {
      trigger: 'item',
      // The same box the Recharts tooltips draw (`TIP_BOX_STYLE`).
      backgroundColor: tokens.card,
      borderColor: tokens.border,
      borderWidth: 1,
      borderRadius: 8,
      padding: [4, 8],
      textStyle: { color: tokens.text },
      // Beside the node, inside the chart, and hoverable (SC 1.4.13); the
      // keyboard's `showTip` lands here too.
      confine: true,
      enterable: true,
      position: echartsTipPosition(treemapNodeMark),
      formatter: domTooltipFormatter((params: unknown) => {
        const index = childIndexOf(params)
        const child = index === null ? undefined : children[index]
        return child ? nodeTooltip(child) : { rows: [] }
      }),
    },
    series: [series],
  }
}
