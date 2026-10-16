/**
 * TRND-11: an ECharts chart's export, REDRAWN from its own option rather than
 * copied off the screen's canvas:
 *
 *   - SVG: the chart's registered export option through ECharts' SVG renderer
 *     (server-side mode, no DOM), so the file is a real vector — text is
 *     `<text>`, nothing is an embedded `<image>`;
 *   - Light background: the same option built on the LIGHT theme's tokens,
 *     read from a detached `div[data-theme=LIGHT_THEME]` (the tokens are
 *     defined per `[data-theme]` block), whatever the page's theme; the PNG
 *     is then drawn off-screen on the light card colour.
 *
 * Reached ONLY through a dynamic `import()` from the export path
 * (`ChartExportMenu`), so the SVG renderer is in this chunk alone and never
 * on a page that does not export (`npm run check:bundle`).
 *
 * A chart that registered no export option returns `null`: the caller keeps
 * today's raster path. The export option is held to the same rule as the
 * screen's (`assertSafeChartOption`: no string formatter, the CSP / XSS rule),
 * and the SVG text is ECharts' own output over canvas-safe option text, parsed
 * by `DOMParser` (no `innerHTML` anywhere).
 */
import { SVGRenderer } from 'echarts/renderers'
import { assertSafeChartOption } from '../../tooltip'
import { readChartTokens, type ChartTokens } from '../../tokens'
import { echarts } from './core'
import { liveEChartsChart } from './exportImage'
import { exportOptionOf } from './exportRegistry'

echarts.use([SVGRenderer])

/** The light theme's id (`lib/viz/chartImage.ts` `LIGHT_THEME`; a test holds the two equal). */
export const EXPORT_LIGHT_THEME = 'lab'

export type ExportImageResult =
  | { kind: 'svg'; svg: SVGSVGElement; width: number; height: number }
  | { kind: 'raster'; dataUrl: string; width: number; height: number }

export interface EchartsExportRequest {
  format: 'png' | 'svg'
  light: boolean
  pixelRatio: number
}

const SVG_NS = 'http://www.w3.org/2000/svg'

/** The light theme's chart tokens, read from a detached host and removed again. */
export function readLightTokens(theme = EXPORT_LIGHT_THEME): ChartTokens {
  const host = document.createElement('div')
  host.setAttribute('data-theme', theme)
  host.setAttribute('aria-hidden', 'true')
  host.style.cssText = 'position:fixed;left:-100000px;top:0;width:0;height:0;pointer-events:none'
  document.body.appendChild(host)
  try {
    // A copy: the token reader memoises on its root, and this root is gone in a moment.
    return { ...readChartTokens(host) }
  } finally {
    host.remove()
  }
}

/** The option with the background the export asks for (the light card under a light export). */
function withBackground(option: object, tokens: ChartTokens, light: boolean): object {
  return light ? { ...option, backgroundColor: tokens.card } : option
}

/** `option` drawn by the SVG renderer at `width` x `height`, as ECharts' own SVG text. */
export function renderOptionToSvg(option: object, width: number, height: number): string {
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width, height })
  try {
    chart.setOption(option as Parameters<typeof chart.setOption>[0])
    return chart.renderToSVGString()
  } finally {
    chart.dispose()
  }
}

/** ECharts' SVG text as an element, or a thrown error when it does not parse as SVG. */
export function parseSvg(text: string): SVGSVGElement {
  const root = new DOMParser().parseFromString(text, 'image/svg+xml').documentElement
  if (!root || root.namespaceURI !== SVG_NS || root.localName !== 'svg') {
    throw new Error('The chart could not be drawn as SVG.')
  }
  return root as unknown as SVGSVGElement
}

/** `option` drawn off-screen on the canvas renderer, as a PNG data URL on `background`. */
function renderOptionToPng(option: object, width: number, height: number, pixelRatio: number, background: string): string {
  const host = document.createElement('div')
  host.setAttribute('aria-hidden', 'true')
  host.style.cssText = `position:fixed;left:-100000px;top:0;width:${width}px;height:${height}px;pointer-events:none`
  document.body.appendChild(host)
  const chart = echarts.init(host, null, { renderer: 'canvas', width, height })
  try {
    chart.setOption(option as Parameters<typeof chart.setOption>[0])
    return chart.getDataURL({ type: 'png', pixelRatio, backgroundColor: background })
  } finally {
    chart.dispose()
    host.remove()
  }
}

/**
 * The export of the ECharts chart inside `body`, redrawn from its registered
 * option: an SVG element for `svg`, a PNG for `png` with `light`. `null` when
 * there is nothing to redraw from (no live instance, no registered option) or
 * nothing to redraw FOR (a PNG on the page's own theme: the screen's canvas
 * already is that image) — the caller then takes today's raster path.
 */
export async function echartsExportImage(body: ParentNode, request: EchartsExportRequest): Promise<ExportImageResult | null> {
  const live = liveEChartsChart(body)
  if (!live) return null
  const entry = exportOptionOf(live.container)
  if (!entry) return null
  if (request.format === 'png' && !request.light) return null

  const width = Math.max(1, Math.round(live.width))
  const height = Math.max(1, Math.round(live.height))
  const tokens = request.light ? readLightTokens() : readChartTokens()
  const option = entry.build(tokens)
  // The CSP / XSS rule holds for the export too: a string formatter is refused, the chart untouched.
  assertSafeChartOption(option)
  const drawn = withBackground(option, tokens, request.light)

  if (request.format === 'svg') {
    return { kind: 'svg', svg: parseSvg(renderOptionToSvg(drawn, width, height)), width, height }
  }
  return {
    kind: 'raster',
    dataUrl: renderOptionToPng(drawn, width, height, request.pixelRatio, tokens.card),
    width,
    height,
  }
}
