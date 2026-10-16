/**
 * TRND-11: the export option each ECharts chart registers for its container,
 * so Export can redraw the SAME chart through ECharts' SVG renderer (a true
 * vector file) or on the light theme's tokens (Light background), instead of
 * copying the on-screen canvas.
 *
 * A chart registers through `useEChart(type, option, events, exportOption)`:
 * the builder is the same `build<X>Option` call as its on-screen option, with
 * the tokens it is GIVEN (the light theme's, or the page's) and no animation.
 *
 * This module imports nothing from `echarts` (types only), so it may sit in
 * any chunk; the export path (`exportSvg.ts`) is the only reader.
 */
import type { ChartTokens } from '../../tokens'
import type { ChartEngineType } from '../registry'

/** Builds the chart's option on `tokens` (`animate: false`): the export draws exactly the screen's chart. */
export type ExportOptionBuilder = (tokens: ChartTokens) => object

export interface ExportOptionEntry {
  type: ChartEngineType
  build: ExportOptionBuilder
}

const entries = new WeakMap<HTMLElement, ExportOptionEntry>()

export function registerExportOption(container: HTMLElement, entry: ExportOptionEntry): void {
  entries.set(container, entry)
}

export function unregisterExportOption(container: HTMLElement): void {
  entries.delete(container)
}

/** The entry registered for `container`, or `null` (the export then falls back to the raster path). */
export function exportOptionOf(container: HTMLElement): ExportOptionEntry | null {
  return entries.get(container) ?? null
}
