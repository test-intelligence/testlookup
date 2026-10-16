import { beforeEach, describe, expect, it } from 'vitest'
import { chartConfigStore, nonDefaultOf, useChartConfigStore } from './chartConfigStore'
import { resetReportScopeOnLogout } from './logoutReset'

const KEYS = ['trends.suite-series', 'trends.compare'] as const
const SERIES = { version: 1, metric: 'failures', seriesBy: 'suite', bucket: 'day', topN: 5 }

beforeEach(() => useChartConfigStore.setState({ applied: {}, viewToken: 0 }))

describe('chartConfigStore (TRND-10)', () => {
  it('nonDefault omits default configs and is null when none is customised', () => {
    expect(chartConfigStore.nonDefault(KEYS)).toBeNull()
    chartConfigStore.set('trends.suite-series', null)
    chartConfigStore.set('trends.compare', null)
    expect(chartConfigStore.nonDefault(KEYS)).toBeNull()
    chartConfigStore.set('trends.suite-series', SERIES)
    expect(chartConfigStore.nonDefault(KEYS)).toEqual({ 'trends.suite-series': SERIES })
    chartConfigStore.set('trends.suite-series', null)
    expect(chartConfigStore.nonDefault(KEYS)).toBeNull()
  })

  it('nonDefault reads only the keys asked for', () => {
    chartConfigStore.set('explore.other', { version: 1 })
    expect(chartConfigStore.nonDefault(KEYS)).toBeNull()
    expect(nonDefaultOf({ a: { version: 1 }, b: null }, ['a', 'b', 'c'])).toEqual({ a: { version: 1 } })
  })

  it('applyView replaces the configs, sets every listed key the view lacks to its default, and bumps the token', () => {
    chartConfigStore.set('trends.compare', { version: 1, by: null, metric: 'executions' })
    chartConfigStore.applyView({ 'trends.suite-series': SERIES }, KEYS)
    const state = useChartConfigStore.getState()
    expect(state.viewToken).toBe(1)
    expect(state.applied).toEqual({ 'trends.suite-series': SERIES, 'trends.compare': null })
    expect(chartConfigStore.has('trends.compare')).toBe(true)
    expect(chartConfigStore.nonDefault(KEYS)).toEqual({ 'trends.suite-series': SERIES })
  })

  it('applyView of a view with no charts (or garbage) sets the listed keys to default; prototype keys are dropped', () => {
    chartConfigStore.applyView(undefined, KEYS)
    expect(useChartConfigStore.getState().applied).toEqual({ 'trends.suite-series': null, 'trends.compare': null })
    chartConfigStore.applyView('nope', KEYS)
    expect(chartConfigStore.nonDefault(KEYS)).toBeNull()
    chartConfigStore.applyView(JSON.parse('{"__proto__": {"polluted": true}, "trends.compare": {"version": 1}}'), KEYS)
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
    expect(chartConfigStore.get('trends.compare')).toEqual({ version: 1 })
    expect(useChartConfigStore.getState().viewToken).toBe(3)
  })

  it('logout forgets the configs a view applied', () => {
    chartConfigStore.applyView({ 'trends.suite-series': SERIES }, KEYS)
    resetReportScopeOnLogout()
    expect(useChartConfigStore.getState()).toMatchObject({ applied: {}, viewToken: 0 })
  })
})
