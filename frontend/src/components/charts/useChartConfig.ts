/**
 * TRND-10 — one chart's customisation, for any chart model (VIZ-604 made it
 * for the suite series; Compare uses it too).
 *
 * `config` follows every change at once (the panel's controls); `applied` is
 * the same config 300 ms after the last change, which is what the chart
 * requests with, so a burst of edits is one request.
 *
 * Where a config comes from, in order:
 *   1. a saved view applied on this page (`chartConfigStore`, `applyView`) —
 *      a teammate who opens the view sees the same chart;
 *   2. this browser's `localStorage` (the per-browser default for an unsaved
 *      customisation);
 *   3. the model's default.
 * Every source goes through the model's `read`: one that no longer validates
 * falls back to the default and the reader is told once (`notice`, consumed by
 * the caller). What the chart shows is mirrored into the store (`null` at the
 * default), so the page's next saved view carries it; a reader's own change is
 * also saved to `localStorage`. Applying a view does not touch `localStorage`.
 */
import { useEffect, useState } from 'react'
import { chartConfigStore, useChartConfigStore } from '@/store/chartConfigStore'

export const CONFIG_DEBOUNCE_MS = 300

export interface ChartConfigModel<T> {
  /** A stored or applied config, validated: the default with a notice when it no longer fits. */
  read: (raw: unknown) => { config: T; notice: string | null }
  defaults: T
  isDefault: (config: T) => boolean
}

export interface ChartConfigState<T> {
  config: T
  applied: T
  notice: string | null
  setConfig: (next: T) => void
  reset: () => void
}

export const storageKeyOf = (chartKey: string) => `testlookup.chartConfig.${chartKey}`

/** The config `localStorage` holds for `chartKey`, read through the model. */
export function loadStoredConfig<T>(chartKey: string, model: ChartConfigModel<T>): { config: T; notice: string | null } {
  try {
    const raw = window.localStorage.getItem(storageKeyOf(chartKey))
    return model.read(raw === null ? null : (JSON.parse(raw) as unknown))
  } catch {
    // Storage blocked, or a value that is not JSON: the default, said once.
    return { config: model.defaults, notice: null }
  }
}

export function saveStoredConfig(chartKey: string, config: unknown | null): void {
  try {
    if (config === null) window.localStorage.removeItem(storageKeyOf(chartKey))
    else window.localStorage.setItem(storageKeyOf(chartKey), JSON.stringify(config))
  } catch {
    // Storage blocked: the customisation lasts for this page only.
  }
}

/** A view's config for `chartKey` (`null` = the default), through the model. */
function readApplied<T>(chartKey: string, model: ChartConfigModel<T>): { config: T; notice: string | null } {
  const raw = chartConfigStore.get(chartKey)
  return raw === null || raw === undefined ? { config: model.defaults, notice: null } : model.read(raw)
}

/** The config this chart starts from: an applied view's first, then `localStorage`. */
function initialConfig<T>(chartKey: string, model: ChartConfigModel<T>): { config: T; notice: string | null; fromView: boolean } {
  if (chartConfigStore.has(chartKey)) return { ...readApplied(chartKey, model), fromView: true }
  return { ...loadStoredConfig(chartKey, model), fromView: false }
}

/**
 * Seed the store with the customisation this browser keeps for `chartKey`,
 * for a page that saves views before its charts have mounted (Trends' default
 * tab draws neither customisable chart). A key the store already knows is left
 * alone; a stored config that does not validate is skipped (the chart says so
 * itself when it mounts).
 */
export function seedChartConfig<T>(chartKey: string, model: ChartConfigModel<T>): void {
  if (chartConfigStore.has(chartKey)) return
  const { config, notice } = loadStoredConfig(chartKey, model)
  if (notice === null && !model.isDefault(config)) chartConfigStore.set(chartKey, config)
}

export interface UseChartConfigOptions<T> {
  /**
   * What the STORE (and so a saved view) holds for this chart, from its
   * config: Compare adds the reader's suites and releases, which live in the
   * URL rather than in the chart's own settings. Identity-stable (memoised by
   * the caller): a new function re-mirrors.
   */
  mirror?: (config: T) => T
}

export function useChartConfig<T>(
  chartKey: string,
  model: ChartConfigModel<T>,
  { mirror }: UseChartConfigOptions<T> = {},
): ChartConfigState<T> {
  const [initial] = useState(() => initialConfig(chartKey, model))
  const [config, setConfigState] = useState<T>(initial.config)
  const [applied, setApplied] = useState<T>(initial.config)
  const [notice, setNotice] = useState<string | null>(initial.notice)

  // A view applied while this chart is on screen: re-read it at once (no
  // debounce). Adjusted during render (the previous-value pattern), not in an
  // effect: this repo forbids a synchronous setState inside useEffect.
  const viewToken = useChartConfigStore((s) => s.viewToken)
  const [seenToken, setSeenToken] = useState(viewToken)
  if (viewToken !== seenToken) {
    setSeenToken(viewToken)
    const read = readApplied(chartKey, model)
    setConfigState(read.config)
    setApplied(read.config)
    setNotice(read.notice)
  }

  // Debounce: the chart asks with `applied`.
  useEffect(() => {
    if (config === applied) return
    const timer = setTimeout(() => setApplied(config), CONFIG_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [config, applied])

  // A stored config that fell back is rewritten as the default, so the notice is said once.
  useEffect(() => {
    if (initial.notice && !initial.fromView) saveStoredConfig(chartKey, null)
  }, [chartKey, initial])

  // The store mirrors what this chart shows (`null` at the default): the page's next view saves it.
  const mirrored = mirror ? mirror(config) : config
  const isDefault = model.isDefault(mirrored)
  const mirroredKey = isDefault ? 'default' : JSON.stringify(mirrored)
  useEffect(() => {
    chartConfigStore.set(chartKey, mirroredKey === 'default' ? null : (JSON.parse(mirroredKey) as unknown))
  }, [chartKey, mirroredKey])

  return {
    config,
    applied,
    notice,
    setConfig: (next) => {
      setNotice(null)
      setConfigState(next)
      saveStoredConfig(chartKey, next)
    },
    reset: () => {
      setNotice(null)
      setConfigState(model.defaults)
      setApplied(model.defaults)
      saveStoredConfig(chartKey, null)
    },
  }
}
