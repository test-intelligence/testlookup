/**
 * VIZ-104 K1 — the stacked-column model, table-tested. The rules that matter:
 * `null` is not measured (a gap, never 0, never summed); a status series is its
 * status's colour AND decal; any other series is a series colour, counted over
 * the non-status series only.
 */
import { describe, expect, it } from 'vitest'
import type { SeriesPoint } from '@/lib/viz/contracts'
import { NO_VALUE } from './chartText'
import { CHART_VARS, STATUS_ENCODING } from './tokens'
import { tooltipText } from './tooltip'
import {
  NOT_MEASURED_REASON,
  SERIES_DECALS,
  TOTAL_LABEL,
  TOTAL_SERIES_KEY,
  buildStackedColumnModel,
  gapNote,
  invalidNote,
  stackedColumnTipContent,
  stackedColumnToChartSeries,
  type StackedColumnBucket,
  type StackedColumnInput,
} from './stackedColumnModel'
import {
  HOURS_SERIES,
  STATUS_SERIES,
  seriesMonthlyFixture,
  singleBucketFixture,
  statusDailyBuckets,
  statusDailyFixture,
} from './__fixtures__/stackedColumn'

const input = (overrides: Partial<StackedColumnInput> = {}): StackedColumnInput => ({
  buckets: statusDailyBuckets,
  series: STATUS_SERIES,
  valueTitle: 'Executions',
  bucketTitle: 'Day (UTC)',
  ...overrides,
})

describe('buildStackedColumnModel · null is not measured', () => {
  const model = statusDailyFixture
  const gapDay = model.buckets.findIndex((bucket) => bucket.key === '2026-03-04')
  const partialDay = model.buckets.findIndex((bucket) => bucket.key === '2026-03-09')

  it('keeps a missing value as null — never 0 — and gives an all-null bucket no total', () => {
    expect(model.buckets[gapDay].values).toEqual([null, null, null, null])
    expect(model.buckets[gapDay].total).toBeNull()
    expect(model.buckets[gapDay].zero).toBe(false)
    expect(model.gaps).toBe(1)
  })

  it('sums only the measured values of a partly measured bucket', () => {
    expect(model.buckets[partialDay].values).toEqual([199, 7, 2, null])
    expect(model.buckets[partialDay].total).toBe(208)
    expect(model.partial).toBe(1)
  })

  it('tells a measured zero from a gap', () => {
    const zeroDay = model.buckets.find((bucket) => bucket.key === '2026-03-07') as StackedColumnBucket
    expect(zeroDay.values).toEqual([0, 0, 0, 0])
    expect(zeroDay.total).toBe(0)
    expect(zeroDay.zero).toBe(true)
  })

  it('treats a key the bucket does not carry as not measured', () => {
    const m = buildStackedColumnModel(input({ buckets: [{ key: 'd', label: 'D', values: { passed: 3 } }] }))
    expect(m.buckets[0].values).toEqual([3, null, null, null])
    expect(m.buckets[0].total).toBe(3)
  })

  it('turns NaN, Infinity and a negative count into not measured, and counts them', () => {
    const m = buildStackedColumnModel(
      input({ buckets: [{ key: 'd', label: 'D', values: { passed: Number.NaN, failed: Infinity, broken: -2, skipped: 4 } }] }),
    )
    expect(m.buckets[0].values).toEqual([null, null, null, 4])
    expect(m.invalid).toBe(3)
    expect(invalidNote(m)).toMatch(/^3 values were not a count or amount/)
    expect(invalidNote(statusDailyFixture)).toBeNull()
  })

  it('scales the axis from the tallest MEASURED column, from zero, ending on a tick', () => {
    expect(model.axis.domain[0]).toBe(0)
    const tallest = Math.max(...model.buckets.map((bucket) => bucket.total ?? 0))
    expect(tallest).toBe(216)
    expect(model.axis.domain[1]).toBeGreaterThanOrEqual(tallest)
    expect(model.axis.ticks[model.axis.ticks.length - 1]).toBe(model.axis.domain[1])
    expect(model.axis.ticks.every(Number.isInteger)).toBe(true)
  })

  it('states the gaps in a sentence, and nothing when there are none', () => {
    expect(gapNote(model, 'day')).toBe(`1 day has no measured value (${NO_VALUE}), drawn as a gap rather than 0.`)
    expect(gapNote(singleBucketFixture)).toBeNull()
    const twoGaps = buildStackedColumnModel(
      input({ buckets: [{ key: 'a', label: 'A', values: {} }, { key: 'b', label: 'B', values: {} }, { key: 'c', label: 'C', values: { passed: 1 } }] }),
    )
    expect(gapNote(twoGaps)).toMatch(/^2 columns have no measured value/)
  })
})

describe('buildStackedColumnModel · colour and decal', () => {
  it('draws each status series in ITS status colour and decal — Skipped and Broken are not the same', () => {
    const bySeries = Object.fromEntries(statusDailyFixture.series.map((entry) => [entry.key, entry]))
    for (const status of ['passed', 'failed', 'broken', 'skipped'] as const) {
      expect(bySeries[status].status).toBe(status)
      expect(bySeries[status].color).toBe(CHART_VARS.status[status])
      expect(bySeries[status].decal).toBe(STATUS_ENCODING[status].decal)
    }
    expect(bySeries.skipped.color).not.toBe(bySeries.broken.color)
    expect(bySeries.skipped.decal).not.toBe(bySeries.broken.decal)
    // Every status but Passed carries a pattern; a solid fill alone is colour-only.
    expect(bySeries.failed.decal).not.toBe('solid')
    expect(bySeries.broken.decal).not.toBe('solid')
    expect(bySeries.skipped.decal).not.toBe('solid')
  })

  it('gives a series with no status a SERIES colour and a category decal, never a status colour', () => {
    const series = seriesMonthlyFixture.series
    expect(series.map((entry) => entry.status)).toEqual([null, null, null])
    expect(series.map((entry) => entry.color)).toEqual(CHART_VARS.series.slice(0, 3))
    expect(series.map((entry) => entry.decal)).toEqual(SERIES_DECALS.slice(0, 3))
    for (const entry of series) expect(Object.values(CHART_VARS.status)).not.toContain(entry.color)
  })

  it('counts series colours over the non-status series only', () => {
    const m = buildStackedColumnModel(
      input({
        buckets: [{ key: 'a', label: 'A', values: { x: 1, failed: 2, y: 3 } }],
        series: [{ key: 'x', label: 'X' }, { key: 'failed', label: 'Failed', status: 'failed' }, { key: 'y', label: 'Y' }],
      }),
    )
    expect(m.series.map((entry) => entry.color)).toEqual([CHART_VARS.series[0], CHART_VARS.status.failed, CHART_VARS.series[1]])
  })

  it('draws every series from an index field, never the caller key (a dotted key is a Recharts path)', () => {
    const m = buildStackedColumnModel(
      input({ buckets: [{ key: 'a', label: 'A', values: { 'leg.one': 1 } }], series: [{ key: 'leg.one', label: 'Leg one' }] }),
    )
    expect(m.series[0].field).toBe('s0')
    expect(m.buckets[0].values).toEqual([1])
  })

  it('keeps fractional values fractional (no integer-only axis)', () => {
    expect(seriesMonthlyFixture.axis.ticks.some((tick) => !Number.isInteger(tick)) || seriesMonthlyFixture.axis.step >= 1).toBe(true)
    expect(seriesMonthlyFixture.buckets[0].total).toBe(18.75)
    expect(HOURS_SERIES).toHaveLength(3)
  })
})

describe('buildStackedColumnModel · sizes', () => {
  it('holds one bucket', () => {
    expect(singleBucketFixture.buckets).toHaveLength(1)
    expect(singleBucketFixture.empty).toBe(false)
    expect(singleBucketFixture.buckets[0].total).toBe(46)
  })

  it('holds no bucket as empty, on a [0, 1] axis', () => {
    const m = buildStackedColumnModel(input({ buckets: [] }))
    expect(m.buckets).toEqual([])
    expect(m.empty).toBe(true)
    expect(m.axis.domain).toEqual([0, 1])
  })

  it('calls a window of which nothing was measured empty, and a window of zeros all-zero', () => {
    const none = buildStackedColumnModel(input({ buckets: [{ key: 'a', label: 'A', values: {} }] }))
    expect(none.empty).toBe(true)
    expect(none.allZero).toBe(false)
    const zeros = buildStackedColumnModel(input({ buckets: [{ key: 'a', label: 'A', values: { passed: 0, failed: 0 } }] }))
    expect(zeros.empty).toBe(false)
    expect(zeros.allZero).toBe(true)
    expect(statusDailyFixture.allZero).toBe(false)
  })
})

describe('stackedColumnTipContent', () => {
  it('lists the stack top first, with shares, "—" for a gap, and the total last', () => {
    const index = statusDailyFixture.buckets.findIndex((bucket) => bucket.key === '2026-03-09')
    const content = stackedColumnTipContent(statusDailyFixture, index)
    expect(content.title).toBe('Mar 9')
    expect(content.rows.map((row) => [row.label, row.value, row.detail])).toEqual([
      ['Skipped', NO_VALUE, NOT_MEASURED_REASON],
      ['Broken', '2', '1.0% of column'],
      ['Failed', '7', '3.4% of column'],
      ['Passed', '199', '95.7% of column'],
      [TOTAL_LABEL, '208', undefined],
    ])
  })

  it('says a gap bucket was not measured, with no total', () => {
    const index = statusDailyFixture.buckets.findIndex((bucket) => bucket.key === '2026-03-04')
    const text = tooltipText(stackedColumnTipContent(statusDailyFixture, index))
    expect(text).toMatch(new RegExp(`Total: ${NO_VALUE} \\(${NOT_MEASURED_REASON}\\)`))
    expect(text).not.toMatch(/: 0\b/)
  })

  it('prints values in the model format', () => {
    const content = stackedColumnTipContent(seriesMonthlyFixture, 2)
    expect(content.rows.map((row) => row.value)).toEqual([NO_VALUE, '3 h', '9.5 h', '12.5 h'])
  })

  it('is empty for an index outside the model', () => {
    expect(stackedColumnTipContent(statusDailyFixture, 99).rows).toEqual([])
  })
})

describe('stackedColumnToChartSeries', () => {
  const chart = stackedColumnToChartSeries(statusDailyFixture)

  it('has one series per drawn series plus the column total, keyed by bucket with display labels', () => {
    expect(chart.kind).toBe('series')
    expect(chart.dimensions).toEqual(['Day (UTC)'])
    expect(chart.x_type).toBe('time')
    expect(chart.series.map((entry) => entry.key)).toEqual(['passed', 'failed', 'broken', 'skipped', TOTAL_SERIES_KEY])
    expect(chart.x_labels?.['2026-03-04']).toBe('Mar 4')
  })

  it('carries a gap as y null, measured false, with a reason — and a zero as 0', () => {
    const passed = chart.series[0].points
    const gap = passed.find((point) => point.x === '2026-03-04') as SeriesPoint
    expect(gap).toMatchObject({ y: null, measured: false, reason: NOT_MEASURED_REASON })
    const zero = passed.find((point) => point.x === '2026-03-07') as SeriesPoint
    expect(zero).toMatchObject({ y: 0 })
    expect(zero.measured).toBeUndefined()
    const total = chart.series[4].points.find((point) => point.x === '2026-03-04') as SeriesPoint
    expect(total.y).toBeNull()
  })
})
