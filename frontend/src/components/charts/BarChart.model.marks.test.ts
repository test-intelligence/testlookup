/**
 * Wave 3 (VIZ-602 / 603): the marks a bar chart hands its host. A mark carries
 * the chart's KEY for the bar (never its label), the dimension the server
 * grouped by, and the figures the chart drew, so a drill sends back exactly
 * the bucket and the rows panel can compare its total with what was drawn.
 */
import { describe, expect, it } from 'vitest'
import type { ChartSeries, SeriesChart } from '@/lib/viz/contracts'
import {
  barMarkDimensions,
  barSampleSizes,
  rankedBarMark,
  rankedModel,
  statusBarMark,
  statusBarModel,
  statusRowsFromSeries,
  statusSegmentMark,
  barsFromSeries,
} from './BarChart.model'
import { OTHER_KEY } from './multiSeriesModel'

/** The one item `test` names: a missing fixture item fails here, by name. */
function only<T>(items: readonly T[], test: (item: T) => boolean): T {
  const found = items.find(test)
  if (found === undefined) throw new Error('fixture item missing')
  return found
}

/** chart-data `metric=executions&group_by=suite&group_by=status` (the ladder's L0). */
const SUITE_BY_STATUS: SeriesChart = {
  kind: 'series',
  dimensions: ['suite', 'status'],
  x_type: 'category',
  x_labels: { payments: 'Payments', '<img src=x onerror=1>': '<img src=x onerror=1>' },
  series: [
    { key: 'passed', label: 'passed', points: [{ x: 'payments', y: 40, n: 40 }, { x: 'cart', y: 9, n: 9 }] },
    { key: 'failed', label: 'failed', points: [{ x: 'payments', y: 3, n: 3 }, { x: 'cart', y: null, n: 0 }] },
  ],
}

/** chart-data `metric=failed&group_by=test&top_n=20`: `y` the failures, `n` every execution of the test. */
const TESTS: SeriesChart = {
  kind: 'series',
  dimensions: ['test'],
  x_type: 'category',
  x_labels: { 'fp-1': 'test_pay', 'fp-2': 'test_refund' },
  series: [{ key: 'value', label: 'Failed', points: [{ x: 'fp-1', y: 4, n: 30 }, { x: 'fp-2', y: 2, n: 12 }, { x: OTHER_KEY, y: 1, n: 5 }] }],
}

describe('barMarkDimensions', () => {
  it('reads the axis and the series dimension from the series, when they are C5 dimensions', () => {
    expect(barMarkDimensions(SUITE_BY_STATUS)).toEqual({ axis: 'suite', series: 'status' })
    expect(barMarkDimensions(TESTS)).toEqual({ axis: 'test', series: null })
  })

  it('a dimension the contract does not know (a display word, an object key) is no dimension', () => {
    const odd: ChartSeries = { ...TESTS, dimensions: ['category', 'constructor'] }
    expect(barMarkDimensions(odd)).toEqual({ axis: null, series: null })
    expect(barMarkDimensions({ ...TESTS, dimensions: ['__proto__'] })).toEqual({ axis: null, series: null })
    expect(barMarkDimensions(null)).toEqual({ axis: null, series: null })
    expect(barMarkDimensions({ kind: 'tree', nodes: [] } as unknown as ChartSeries)).toEqual({ axis: null, series: null })
  })
})

describe('barSampleSizes', () => {
  it('sums n per bar across the series, and reads one segment', () => {
    const sizes = barSampleSizes(SUITE_BY_STATUS)
    expect(sizes.bar('payments')).toBe(43)
    expect(sizes.bar('cart')).toBe(9)
    expect(sizes.segment('failed', 'payments')).toBe(3)
    expect(sizes.segment('failed', 'cart')).toBe(0)
  })

  it('a bar or segment the series never drew has no sample: null, never 0', () => {
    const sizes = barSampleSizes(SUITE_BY_STATUS)
    expect(sizes.bar('nowhere')).toBeNull()
    expect(sizes.segment('broken', 'payments')).toBeNull()
    expect(barSampleSizes({ kind: 'tree', nodes: [] } as unknown as ChartSeries).bar('payments')).toBeNull()
  })
})

describe('rankedBarMark', () => {
  const dims = barMarkDimensions(TESTS)
  const sizes = barSampleSizes(TESTS)
  const bars = rankedModel(barsFromSeries(TESTS)).bars

  it('the KEY is the value, the label is the display text, y is what was drawn and n its sample', () => {
    const first = only(bars, (bar) => bar.key === 'fp-1')
    expect(rankedBarMark(first, dims, sizes)).toEqual({ dimension: 'test', value: 'fp-1', label: 'test_pay', y: 4, n: 30 })
  })

  it('the Other roll-up is no mark: it is not one bucket, and the rows endpoint refuses it', () => {
    const other = only(bars, (bar) => bar.key === OTHER_KEY)
    expect(rankedBarMark(other, dims, sizes)).toBeUndefined()
  })

  it('no dimension, no mark', () => {
    expect(rankedBarMark(bars[0], { axis: null, series: null }, sizes)).toBeUndefined()
  })
})

describe('statusBarMark / statusSegmentMark', () => {
  const dims = barMarkDimensions(SUITE_BY_STATUS)
  const sizes = barSampleSizes(SUITE_BY_STATUS)
  const model = statusBarModel(statusRowsFromSeries(SUITE_BY_STATUS))
  const payments = only(model.bars, (bar) => bar.key === 'payments')

  it('a whole bar: its suite, the bar total as y', () => {
    expect(statusBarMark(payments, dims, sizes)).toEqual({ dimension: 'suite', value: 'payments', label: 'Payments', y: 43, n: 43 })
  })

  it('a segment: the suite, with the status as context, its TRUE count as y (in either mode)', () => {
    const expected = {
      dimension: 'suite',
      value: 'payments',
      label: 'Payments',
      y: 3,
      n: 3,
      context: [{ dimension: 'status', value: 'failed' }],
    }
    expect(statusSegmentMark(payments, 'failed', dims, sizes)).toEqual(expected)
    const percent = statusBarModel(statusRowsFromSeries(SUITE_BY_STATUS), { mode: 'percent' })
    const shared = only(percent.bars, (bar) => bar.key === 'payments')
    expect(statusSegmentMark(shared, 'failed', dims, sizes)).toEqual(expected)
  })

  it('a status the bar does not draw, a bar of no dimension, a series of no dimension: no segment mark', () => {
    expect(statusSegmentMark(payments, 'broken', dims, sizes)).toBeUndefined()
    expect(statusSegmentMark(payments, 'failed', { axis: 'suite', series: null }, sizes)).toBeUndefined()
    expect(statusSegmentMark(payments, 'failed', { axis: null, series: 'status' }, sizes)).toBeUndefined()
    expect(statusBarMark(payments, { axis: null, series: 'status' }, sizes)).toBeUndefined()
  })

  it('the Other roll-up bar is no mark, whole or segment', () => {
    const rolled = statusBarModel([{ key: OTHER_KEY, label: 'Other', counts: { failed: 2 } }]).bars[0]
    expect(statusBarMark(rolled, dims, sizes)).toBeUndefined()
    expect(statusSegmentMark(rolled, 'failed', dims, sizes)).toBeUndefined()
  })
})
