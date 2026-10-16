/**
 * TRND-10: the chart customisations a saved view carries (`filters.charts`).
 *
 * Cross-cutting client state (Zustand, not SWR: nothing here is server data
 * until a view is saved). `applied` maps a chart key (`trends.suite-series`,
 * `trends.compare`) to its config — `null` meaning "at its default" — as the
 * page shows it now:
 *
 *   - `useChartConfig` writes here on every change (and seeds the config it
 *     loaded from `localStorage`), so the page knows every customised chart;
 *   - `applyView(charts, keys)` replaces it with a view's `filters.charts`
 *     and bumps `viewToken`: every mounted chart re-reads its config (each
 *     hook's own `read` validates; this store validates nothing), and a chart
 *     that mounts later reads it from here before `localStorage`. The `keys`
 *     a page passes are set to their default when the view has none of them
 *     (a view is the whole configuration, as it is the whole scope);
 *   - `nonDefault(keys)` is what a page saves: the configs of those keys that
 *     are NOT at their default, or `null` when none is customised (the saved
 *     filters then carry no `charts` key at all, exactly as before TRND-10).
 */
import { create } from 'zustand'
import { onLogoutReset } from './logoutReset'

export interface ChartConfigStoreState {
  applied: Record<string, unknown>
  /** Bumped by every `applyView`: a mounted chart re-reads its config when it changes. */
  viewToken: number
  applyView: (charts: unknown, keys?: readonly string[]) => void
  set: (key: string, config: unknown | null) => void
  nonDefault: (keys: readonly string[]) => Record<string, unknown> | null
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** The non-default configs of `keys` in `applied`, or `null` when none is customised. */
export function nonDefaultOf(applied: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> | null {
  const out: Record<string, unknown> = {}
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(applied, key)) continue
    const value = applied[key]
    if (value !== null && value !== undefined) out[key] = value
  }
  return Object.keys(out).length > 0 ? out : null
}

export const useChartConfigStore = create<ChartConfigStoreState>()((set, get) => ({
  applied: {},
  viewToken: 0,
  applyView: (charts, keys = []) =>
    set((state) => {
      const next: Record<string, unknown> = {}
      for (const key of keys) next[key] = null
      if (isRecord(charts)) {
        for (const [key, value] of Object.entries(charts)) {
          // An own, plain key only: a view's JSON is data, never a prototype.
          if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue
          next[key] = value ?? null
        }
      }
      return { applied: next, viewToken: state.viewToken + 1 }
    }),
  set: (key, config) =>
    set((state) => {
      const current = Object.prototype.hasOwnProperty.call(state.applied, key) ? state.applied[key] : undefined
      const value = config ?? null
      if (current === value) return state
      return { applied: { ...state.applied, [key]: value } }
    }),
  nonDefault: (keys) => nonDefaultOf(get().applied, keys),
}))

// A view applied by one user is not the next user's: forget it on logout.
onLogoutReset(() => useChartConfigStore.setState({ applied: {}, viewToken: 0 }))

/** Imperative handle for non-React callers (a view's `applyExtra`). */
export const chartConfigStore = {
  applyView: (charts: unknown, keys?: readonly string[]) => useChartConfigStore.getState().applyView(charts, keys),
  set: (key: string, config: unknown | null) => useChartConfigStore.getState().set(key, config),
  nonDefault: (keys: readonly string[]) => useChartConfigStore.getState().nonDefault(keys),
  has: (key: string) => Object.prototype.hasOwnProperty.call(useChartConfigStore.getState().applied, key),
  get: (key: string) => useChartConfigStore.getState().applied[key],
}
