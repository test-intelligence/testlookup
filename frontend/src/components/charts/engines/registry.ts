/**
 * Chart-type → engine module registry (VIZ-103).
 *
 * Each entry is a dynamic `import()` of a module that registers exactly one
 * chart type on the shared ECharts base, so the bundler emits one lazy chunk
 * per type plus one shared base chunk. Add a type by adding a module under
 * `echarts/` and a line here — never by importing `echarts` directly.
 */
import { lazyChartEngine } from './lazyChartEngine'

/** The slice of an ECharts instance the kit uses. */
export interface ChartInstance {
  setOption(option: object, opts?: { notMerge?: boolean; lazyUpdate?: boolean }): void
  resize(): void
  dispose(): void
  isDisposed?(): boolean
  /** `highlight` / `downplay` / `showTip` / `hideTip` — keyboard exploration (VIZ-105). */
  dispatchAction?(payload: { type: string; [key: string]: unknown }): void
}

/** The slice of the `echarts/core` namespace the kit uses. */
export interface ChartEngine {
  init(el: HTMLElement, theme?: string | object | null, opts?: { renderer?: 'canvas' | 'svg' }): ChartInstance
}

const LOADERS = {
  heatmap: () => import('./echarts/heatmap').then((m): ChartEngine => m.echarts),
} satisfies Record<string, () => Promise<ChartEngine>>

export type ChartEngineType = keyof typeof LOADERS
export const CHART_ENGINE_TYPES = Object.keys(LOADERS) as ChartEngineType[]

const loaded = new Map<ChartEngineType, Promise<ChartEngine>>()

/**
 * The engine for `type`, loaded once. A failed load is NOT cached — the next
 * call (after the user reloads, or retries) asks the network again — and a
 * missing chunk rejects with a `StaleBuildError`.
 */
export function loadChartEngine(type: ChartEngineType): Promise<ChartEngine> {
  const existing = loaded.get(type)
  if (existing) return existing
  const pending = lazyChartEngine(LOADERS[type])
  loaded.set(type, pending)
  pending.catch(() => {
    if (loaded.get(type) === pending) loaded.delete(type)
  })
  return pending
}

/** Test seam. */
export function resetChartEngineCache(): void {
  loaded.clear()
}
