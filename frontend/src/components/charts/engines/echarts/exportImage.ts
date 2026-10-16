/**
 * The chart image for export (VIZ-606), from the ECharts engine itself.
 *
 * ECharts draws on a canvas (`core.ts` registers only the canvas renderer), so
 * there is no `<svg>` on screen to serialise; the engine's own `getDataURL` at
 * the export pixel ratio is the faithful image of the screen. Since W08
 * TRND-11 a chart that registered its export option is REDRAWN instead
 * (`exportSvg.ts`: a vector SVG, a light background); this raster is the
 * fallback for the 3D view and for a chart without one, whose SVG export
 * embeds it — stated in `ChartExportMenu`.
 *
 * Imported only with a dynamic `import()`, and only when the chart body holds
 * an ECharts instance — by which point the engine chunk is already loaded, so
 * this costs no download.
 */
import { echarts } from './core'

/** The attribute ECharts puts on every container it owns (`DOM_ATTRIBUTE_KEY` in echarts/core). */
export const ECHARTS_INSTANCE_ATTRIBUTE = '_echarts_instance_'

export interface EngineImage {
  dataUrl: string
  /** CSS px: the size the chart is drawn at on the page. */
  width: number
  height: number
}

/** The first ECharts container inside `body`, or `null`. */
export function findEChartsContainer(body: ParentNode): HTMLElement | null {
  return body.querySelector<HTMLElement>(`[${ECHARTS_INSTANCE_ATTRIBUTE}]`)
}

/**
 * TRND-11: the ECharts container inside `body` and the size its live instance
 * is drawn at (CSS px), or `null` when there is no live instance — what a
 * redrawn export needs to match the screen.
 */
export function liveEChartsChart(body: ParentNode): { container: HTMLElement; width: number; height: number } | null {
  const container = findEChartsContainer(body)
  const instance = container ? echarts.getInstanceByDom(container) : undefined
  if (!container || !instance || instance.isDisposed()) return null
  return { container, width: instance.getWidth(), height: instance.getHeight() }
}

/**
 * A PNG data URL of the ECharts chart inside `body`, at `pixelRatio`, on
 * `backgroundColor`; `null` when there is no live instance.
 */
export function echartsImage(body: ParentNode, pixelRatio: number, backgroundColor: string): EngineImage | null {
  const container = findEChartsContainer(body)
  const instance = container ? echarts.getInstanceByDom(container) : undefined
  if (!instance || instance.isDisposed()) return null
  return {
    dataUrl: instance.getDataURL({ type: 'png', pixelRatio, backgroundColor }),
    width: instance.getWidth(),
    height: instance.getHeight(),
  }
}
