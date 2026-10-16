/**
 * TRND-10: `useChartConfig` — a config applied from a saved view beats this
 * browser's `localStorage`; an invalid one falls back to the default and says
 * so once; Reset clears both; the store mirrors what the chart shows.
 */
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { chartConfigStore, useChartConfigStore } from '@/store/chartConfigStore'
import { DEFAULT_SERIES_CONFIG, SERIES_CONFIG_MODEL, type SeriesChartConfig } from './chartCustomiseModel'
import { CONFIG_DEBOUNCE_MS, seedChartConfig, storageKeyOf, useChartConfig } from './useChartConfig'

const KEY = 'trends.suite-series'
const STORAGE = storageKeyOf(KEY)
const FAILURES: SeriesChartConfig = { ...DEFAULT_SERIES_CONFIG, metric: 'failures', topN: 5 }
const EXECUTIONS: SeriesChartConfig = { ...DEFAULT_SERIES_CONFIG, metric: 'executions' }

const stored = () => {
  const raw = window.localStorage.getItem(STORAGE)
  return raw === null ? null : (JSON.parse(raw) as unknown)
}

beforeEach(() => {
  window.localStorage.clear()
  useChartConfigStore.setState({ applied: {}, viewToken: 0 })
  vi.useRealTimers()
})

describe('useChartConfig', () => {
  it('starts from localStorage when no view was applied, and mirrors it into the store', () => {
    window.localStorage.setItem(STORAGE, JSON.stringify(FAILURES))
    const { result } = renderHook(() => useChartConfig(KEY, SERIES_CONFIG_MODEL))
    expect(result.current.config).toEqual(FAILURES)
    expect(result.current.notice).toBeNull()
    expect(chartConfigStore.get(KEY)).toEqual(FAILURES)
  })

  it('a view-applied config beats localStorage, on mount and while mounted', () => {
    window.localStorage.setItem(STORAGE, JSON.stringify(FAILURES))
    chartConfigStore.applyView({ [KEY]: EXECUTIONS }, [KEY])
    const { result } = renderHook(() => useChartConfig(KEY, SERIES_CONFIG_MODEL))
    expect(result.current.config).toEqual(EXECUTIONS)
    // A view applied while the chart is on screen: re-read at once, `applied` too (no debounce).
    act(() => chartConfigStore.applyView({ [KEY]: FAILURES }, [KEY]))
    expect(result.current.config).toEqual(FAILURES)
    expect(result.current.applied).toEqual(FAILURES)
    // A view without this chart: its default.
    act(() => chartConfigStore.applyView({}, [KEY]))
    expect(result.current.config).toEqual(DEFAULT_SERIES_CONFIG)
    expect(chartConfigStore.nonDefault([KEY])).toBeNull()
    // Applying a view never rewrites this browser's own customisation.
    expect(stored()).toEqual(FAILURES)
  })

  it('an applied config that no longer validates falls back to the default with the notice, once', () => {
    chartConfigStore.applyView({ [KEY]: { version: 1, metric: 'nope' } }, [KEY])
    const { result, rerender } = renderHook(() => useChartConfig(KEY, SERIES_CONFIG_MODEL))
    expect(result.current.config).toEqual(DEFAULT_SERIES_CONFIG)
    expect(result.current.notice).toMatch(/no longer applies/)
    // The store no longer carries the invalid config: the next saved view is clean.
    expect(chartConfigStore.nonDefault([KEY])).toBeNull()
    act(() => result.current.setConfig(FAILURES))
    rerender()
    expect(result.current.notice).toBeNull()
  })

  it('a stale stored config falls back with the notice and is rewritten as the default', () => {
    window.localStorage.setItem(STORAGE, JSON.stringify({ version: 9 }))
    const { result } = renderHook(() => useChartConfig(KEY, SERIES_CONFIG_MODEL))
    expect(result.current.notice).toMatch(/no longer applies/)
    expect(stored()).toBeNull()
  })

  it('a change writes the store and localStorage; `applied` follows after the debounce; reset clears both', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useChartConfig(KEY, SERIES_CONFIG_MODEL))
    act(() => result.current.setConfig(FAILURES))
    expect(result.current.config).toEqual(FAILURES)
    expect(result.current.applied).toEqual(DEFAULT_SERIES_CONFIG)
    expect(stored()).toEqual(FAILURES)
    expect(chartConfigStore.get(KEY)).toEqual(FAILURES)
    act(() => vi.advanceTimersByTime(CONFIG_DEBOUNCE_MS))
    expect(result.current.applied).toEqual(FAILURES)
    act(() => result.current.reset())
    expect(result.current.config).toEqual(DEFAULT_SERIES_CONFIG)
    expect(stored()).toBeNull()
    expect(chartConfigStore.get(KEY)).toBeNull()
  })

  it('mirror: the store holds what the caller adds (Compare’s URL selection)', () => {
    const mirror = (config: SeriesChartConfig) => ({ ...config, title: 'Mirrored' })
    renderHook(() => useChartConfig(KEY, SERIES_CONFIG_MODEL, { mirror }))
    expect(chartConfigStore.get(KEY)).toMatchObject({ title: 'Mirrored' })
  })

  it('seedChartConfig loads this browser’s customisation into the store before the chart mounts', () => {
    window.localStorage.setItem(STORAGE, JSON.stringify(FAILURES))
    seedChartConfig(KEY, SERIES_CONFIG_MODEL)
    expect(chartConfigStore.nonDefault([KEY])).toEqual({ [KEY]: FAILURES })
    // A key the store already knows is left alone.
    chartConfigStore.set(KEY, null)
    seedChartConfig(KEY, SERIES_CONFIG_MODEL)
    expect(chartConfigStore.get(KEY)).toBeNull()
  })
})
