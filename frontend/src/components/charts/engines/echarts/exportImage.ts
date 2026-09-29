/**
 * The chart image for export (VIZ-606), from the ECharts engine itself.
 *
 * ECharts draws on a canvas (`core.ts` registers only the canvas renderer), so
 * there is no `<svg>` to serialise and `renderToSVGString` is not available;
 * the engine's own `getDataURL` at the export pixel ratio is the faithful
 * image. The SVG export embeds that raster — an honest limitation of a canvas
 * engine, stated in `ChartExportMenu`.
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
