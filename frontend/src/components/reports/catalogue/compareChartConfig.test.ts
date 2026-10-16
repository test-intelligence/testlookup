import { describe, expect, it } from 'vitest'
import {
  applyCompareField,
  COMPARE_CONFIG_NOTICE,
  COMPARE_SETTINGS_MODEL,
  compareConfigWithUrl,
  compareCustomiseSpec,
  compareTitleOf,
  DEFAULT_COMPARE_CONFIG,
  isDefaultCompareConfig,
  readCompareConfig,
  resetCompareSettings,
  sameCompareChoice,
  urlWithCompareConfig,
  type CompareChartConfig,
} from './compareChartConfig'

describe('readCompareConfig (TRND-10)', () => {
  it('nothing stored is the default, with no notice', () => {
    expect(readCompareConfig(null)).toEqual({ config: DEFAULT_COMPARE_CONFIG, notice: null })
    expect(readCompareConfig(undefined).notice).toBeNull()
  })

  it.each<[string, unknown]>([
    ['another version', { version: 2, by: null, metric: 'pass_rate' }],
    ['a metric this build does not offer', { version: 1, by: null, metric: 'duration' }],
    ['a by this build does not offer', { version: 1, by: 'branch', metric: 'pass_rate' }],
    ['nine suites', { version: 1, by: null, metric: 'pass_rate', suites: Array.from({ length: 9 }, (_, i) => `s${i}`) }],
    ['a title over 80 characters', { version: 1, by: null, metric: 'pass_rate', title: 'x'.repeat(81) }],
    ['a list body', [1]],
    ['a string', 'compare'],
  ])('%s falls back to the default with the notice', (_name, raw) => {
    expect(readCompareConfig(raw)).toEqual({ config: DEFAULT_COMPARE_CONFIG, notice: COMPARE_CONFIG_NOTICE })
  })

  it('a valid config is kept, its lists cleaned (trimmed, unique, sorted)', () => {
    const raw = { version: 1, by: 'suite_release', metric: 'failures', title: ' Mine ', suites: ['b', ' a', 'a'], releases: ['r2', 'r1'] }
    expect(readCompareConfig(raw).config).toEqual({
      version: 1,
      by: 'suite_release',
      metric: 'failures',
      title: 'Mine',
      suites: ['a', 'b'],
      releases: ['r1', 'r2'],
    })
  })

  it('the settings model drops the selection (it lives in the URL)', () => {
    const { config } = COMPARE_SETTINGS_MODEL.read({ version: 1, by: 'suite', metric: 'failures', suites: ['a', 'b'] })
    expect(config).toEqual({ version: 1, by: 'suite', metric: 'failures' })
  })

  it('isDefault: no by, the pass rate, no title and no selection', () => {
    expect(isDefaultCompareConfig(DEFAULT_COMPARE_CONFIG)).toBe(true)
    expect(isDefaultCompareConfig({ ...DEFAULT_COMPARE_CONFIG, metric: 'failures' })).toBe(false)
    expect(isDefaultCompareConfig({ ...DEFAULT_COMPARE_CONFIG, suites: [] })).toBe(false)
    expect(isDefaultCompareConfig({ ...DEFAULT_COMPARE_CONFIG, title: '  ' })).toBe(true)
  })
})

describe('compareCustomiseSpec', () => {
  it('offers title, metric and only the compare-by options the selection supports, with the guard rails', () => {
    const spec = compareCustomiseSpec(DEFAULT_COMPARE_CONFIG, { byOptions: ['suite', 'suite_release'], effectiveBy: 'suite_release' })
    expect(spec.fields.map((f) => f.id)).toEqual(['title', 'metric', 'by', 'chartType', 'scale'])
    const by = spec.fields.find((f) => f.id === 'by')
    expect(by?.value).toBe('suite_release')
    expect(by?.options?.map((o) => o.value)).toEqual(['suite', 'suite_release'])
    expect(spec.unavailable.map((u) => u.label)).toEqual(['Pie', 'Stacked area', 'Log', 'More than 8 lines'])
    expect(spec.unavailable[1].reason).toMatch(/Rates cannot be stacked/)
    expect(spec.isDefault).toBe(true)
  })

  it('a count metric gives the count reasons; no supported comparison is said in the select', () => {
    const spec = compareCustomiseSpec({ ...DEFAULT_COMPARE_CONFIG, metric: 'executions' }, { byOptions: [], effectiveBy: null })
    expect(spec.unavailable[1].reason).toMatch(/Stacking hides/)
    expect(spec.unavailable[2].reason).toMatch(/cannot show a day with 0/)
    const by = spec.fields.find((f) => f.id === 'by')
    expect(by?.options).toEqual([expect.objectContaining({ disabled: true, label: 'Pick two or more suites or releases first' })])
    expect(spec.isDefault).toBe(false)
  })

  it('applyCompareField sets what Compare offers and ignores the rest; reset keeps the selection', () => {
    let config: CompareChartConfig = { ...DEFAULT_COMPARE_CONFIG, suites: ['a', 'b'] }
    config = applyCompareField(config, 'metric', 'failures')
    config = applyCompareField(config, 'by', 'release')
    config = applyCompareField(config, 'title', 'Payments vs cart')
    config = applyCompareField(config, 'metric', 'nope')
    config = applyCompareField(config, 'chartType', 'pie')
    expect(config).toEqual({ version: 1, by: 'release', metric: 'failures', title: 'Payments vs cart', suites: ['a', 'b'] })
    expect(compareTitleOf(config)).toBe('Payments vs cart')
    expect(applyCompareField(config, 'title', '')).not.toHaveProperty('title')
    expect(resetCompareSettings(config)).toEqual({ ...DEFAULT_COMPARE_CONFIG, suites: ['a', 'b'] })
    expect(compareTitleOf(DEFAULT_COMPARE_CONFIG)).toBe('Compare')
  })
})

describe('URL mirroring (cmp_suite / cmp_release)', () => {
  it('reads the URL’s selection into the config (an absent key is not chosen)', () => {
    const params = new URLSearchParams('tab=by-suite&cmp_suite=search&cmp_suite=billing&cmp_release=')
    expect(compareConfigWithUrl({ ...DEFAULT_COMPARE_CONFIG, metric: 'failures', suites: ['old'] }, params)).toEqual({
      version: 1,
      by: null,
      metric: 'failures',
      suites: ['billing', 'search'],
      releases: [],
    })
    expect(compareConfigWithUrl(DEFAULT_COMPARE_CONFIG, new URLSearchParams('tab=by-suite'))).toEqual(DEFAULT_COMPARE_CONFIG)
  })

  it('writes a config’s selection into the URL, keeping every other key; none removes the keys', () => {
    const params = new URLSearchParams('tab=by-suite&cmp_suite=x')
    const next = urlWithCompareConfig(params, { ...DEFAULT_COMPARE_CONFIG, suites: ['b', 'a'], releases: ['r1'] })
    expect(next.get('tab')).toBe('by-suite')
    expect(next.getAll('cmp_suite')).toEqual(['a', 'b'])
    expect(next.getAll('cmp_release')).toEqual(['r1'])
    const cleared = urlWithCompareConfig(next, DEFAULT_COMPARE_CONFIG)
    expect(cleared.has('cmp_suite')).toBe(false)
    expect(cleared.has('cmp_release')).toBe(false)
    expect(cleared.get('tab')).toBe('by-suite')
    // An empty choice is kept as a choice (`cmp_suite=`): clearing the picker never brings the default back.
    expect(urlWithCompareConfig(params, { ...DEFAULT_COMPARE_CONFIG, suites: [] }).toString()).toBe('tab=by-suite&cmp_suite=')
  })

  it('round-trips: config → URL → config is the same selection', () => {
    const config: CompareChartConfig = { ...DEFAULT_COMPARE_CONFIG, suites: ['checkout', 'payments'], releases: ['r2'] }
    const url = urlWithCompareConfig(new URLSearchParams(), config)
    expect(sameCompareChoice(config, url)).toBe(true)
    expect(compareConfigWithUrl(DEFAULT_COMPARE_CONFIG, url)).toEqual(config)
    expect(sameCompareChoice({ ...config, suites: ['checkout'] }, url)).toBe(false)
  })
})
