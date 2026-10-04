import { describe, expect, it } from 'vitest'
import { buildMultiSeriesModel, WEEKLY_CAPTION, WEEKLY_X_TITLE } from './multiSeriesModel'
import {
  DEFAULT_SERIES_CONFIG,
  impliedTitle,
  isDefault,
  readSeriesConfig,
  seriesParams,
  titleOf,
  unavailableOptions,
  type SeriesChartConfig,
} from './chartCustomiseModel'

const config = (over: Partial<SeriesChartConfig>): SeriesChartConfig => ({ ...DEFAULT_SERIES_CONFIG, ...over })

describe('chartCustomiseModel (VIZ-604)', () => {
  it('the default asks exactly what the chart always asked', () => {
    expect(seriesParams(DEFAULT_SERIES_CONFIG)).toEqual({ metric: 'pass_rate', group_by: ['day', 'suite'], top_n: 7 })
    expect(titleOf(DEFAULT_SERIES_CONFIG)).toBe('Pass rate by suite')
    expect(isDefault(DEFAULT_SERIES_CONFIG)).toBe(true)
  })

  it('every setting reaches the request, and the title follows unless the reader named it', () => {
    const custom = config({ metric: 'failures', seriesBy: 'environment', bucket: 'week', topN: 5 })
    expect(seriesParams(custom)).toEqual({ metric: 'failures', group_by: ['week', 'environment'], top_n: 5 })
    expect(impliedTitle(custom)).toBe('Failures by environment, weekly')
    expect(titleOf({ ...custom, title: '  Prod failures  ' })).toBe('Prod failures')
    expect(titleOf({ ...custom, title: '   ' })).toBe('Failures by environment, weekly')
    expect(isDefault(custom)).toBe(false)
    expect(isDefault(config({ title: 'Mine' }))).toBe(false)
  })

  it('guard rails: misleading options are listed as unavailable, with a reason fitting the metric', () => {
    const rate = unavailableOptions(config({ metric: 'pass_rate' }))
    expect(rate.map((o) => `${o.control}:${o.label}`)).toEqual(['chartType:Pie', 'chartType:Stacked area', 'scale:Log', 'topN:More than 7'])
    expect(rate.find((o) => o.label === 'Stacked area')?.reason).toMatch(/Rates cannot be stacked/)
    expect(rate.find((o) => o.label === 'Log')?.reason).toMatch(/cannot show 0%/)
    const count = unavailableOptions(config({ metric: 'executions' }))
    expect(count.find((o) => o.label === 'Log')?.reason).toMatch(/a day with 0/)
  })

  it('a stored config is validated: nothing stored is the default, a stale one falls back and says so once', () => {
    expect(readSeriesConfig(null)).toEqual({ config: DEFAULT_SERIES_CONFIG, notice: null })
    const good = { ...DEFAULT_SERIES_CONFIG, metric: 'failed', title: ' Mine ' }
    expect(readSeriesConfig(good)).toEqual({ config: { ...DEFAULT_SERIES_CONFIG, metric: 'failed', title: 'Mine' }, notice: null })
    for (const stale of [
      { ...DEFAULT_SERIES_CONFIG, version: 0 },
      { ...DEFAULT_SERIES_CONFIG, metric: 'duration_p99' },
      { ...DEFAULT_SERIES_CONFIG, topN: 50 },
      { ...DEFAULT_SERIES_CONFIG, seriesBy: 'test' },
      { ...DEFAULT_SERIES_CONFIG, title: 'x'.repeat(200) },
      'not an object',
    ]) {
      const read = readSeriesConfig(stale)
      expect(read.config).toEqual(DEFAULT_SERIES_CONFIG)
      expect(read.notice).toMatch(/no longer applies/)
    }
  })
})

describe('multiSeriesModel with a weekly bucket', () => {
  const series = [
    {
      key: 'payments',
      label: 'payments',
      points: [
        { x: '2026-09-07', y: 90, n: 10 },
        { x: '2026-09-21', y: 80, n: 10 },
      ],
    },
  ]

  it('steps the axis one week at a time: a missing week is one gap, the days between Mondays are not points', () => {
    const model = buildMultiSeriesModel({ series, metric: { kind: 'rate', title: 'Pass rate %' }, bucket: 'week' })
    expect(model.xs).toEqual(['2026-09-07', '2026-09-14', '2026-09-21'])
    expect(model.lines[0].gaps).toBe(1)
    expect(model.xTitle).toBe(WEEKLY_X_TITLE)
    expect(model.caption).toBe(WEEKLY_CAPTION)
  })

  it('by day (the default) it is every day, as before', () => {
    const model = buildMultiSeriesModel({ series, metric: { kind: 'rate', title: 'Pass rate %' } })
    expect(model.xs).toHaveLength(15)
  })
})
