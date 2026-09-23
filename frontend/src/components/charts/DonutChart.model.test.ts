/**
 * VIZ-401 — the status-distribution donut's model. Every edge case in the
 * story is a case here: fixed order, the centre total, count + percent labels,
 * percents that sum to 100.0, `unknown` as the fifth slice, flaky as no slice
 * at all, the full ring, the sub-2% slice and the all-zero hand-over.
 */
import { describe, expect, it } from 'vitest'
import { validateChartSeries, type SeriesChart } from '@/lib/viz/contracts'
import {
  MIN_SLICE_ARC_PERCENT,
  TINY_SLICE_PERCENT,
  categoryDonutModel,
  donutSeries,
  sliceLabel,
  statusCountsFromSeries,
  statusDonutModel,
} from './DonutChart.model'

const STORY = { passed: 880, failed: 60, broken: 20, skipped: 40 }

const sum = (values: number[]) => Number(values.reduce((a, b) => a + b, 0).toFixed(10))

describe('statusDonutModel', () => {
  it('draws the story: four slices, 1 000 in the centre, percents summing to 100.0', () => {
    const model = statusDonutModel(STORY)
    expect(model.slices.map((s) => s.status)).toEqual(['passed', 'failed', 'broken', 'skipped'])
    expect(model.total).toBe(1000)
    expect(model.slices.map((s) => s.value)).toEqual([880, 60, 20, 40])
    expect(model.slices.map((s) => s.percent)).toEqual([88, 6, 2, 4])
    expect(sum(model.slices.map((s) => s.percent))).toBe(100)
    expect(model.empty).toBe(false)
    expect(model.fullRing).toBe(false)
  })

  it('labels every slice with its count AND its percent', () => {
    const model = statusDonutModel(STORY)
    expect(model.slices.map(sliceLabel)).toEqual([
      'Passed 880 (88.0%)',
      'Failed 60 (6.0%)',
      'Broken 20 (2.0%)',
      'Skipped 40 (4.0%)',
    ])
  })

  it('keeps the fixed status order whatever the sizes are — charts stay comparable', () => {
    // skipped is the biggest here; it still comes last.
    const model = statusDonutModel({ skipped: 900, passed: 10, failed: 40, broken: 50 })
    expect(model.slices.map((s) => s.status)).toEqual(['passed', 'failed', 'broken', 'skipped'])
    expect(model.slices.map((s) => s.value)).toEqual([10, 40, 50, 900])
  })

  it('makes `unknown` the fifth slice when it is present, and omits it when it is not', () => {
    const withUnknown = statusDonutModel({ ...STORY, unknown: 12 })
    expect(withUnknown.slices.map((s) => s.status)).toEqual(['passed', 'failed', 'broken', 'skipped', 'unknown'])
    expect(withUnknown.slices).toHaveLength(5)
    expect(statusDonutModel(STORY).slices.map((s) => s.status)).not.toContain('unknown')
  })

  it('never makes flaky a slice — it is an attribute of a status, not a status', () => {
    const model = statusDonutModel({ ...STORY, flaky: 33 } as Record<string, number>)
    expect(model.slices.map((s) => s.status)).not.toContain('flaky')
    expect(model.slices).toHaveLength(4)
    expect(model.total).toBe(1000)
  })

  it('draws one status as a full, still-labelled ring', () => {
    const model = statusDonutModel({ passed: 412 })
    expect(model.slices).toHaveLength(1)
    expect(model.fullRing).toBe(true)
    expect(model.slices[0].percent).toBe(100)
    expect(sliceLabel(model.slices[0])).toBe('Passed 412 (100.0%)')
    expect(model.slices[0].tiny).toBe(false)
    expect(model.total).toBe(412)
  })

  it('moves a sub-2% slice label to the legend and keeps a minimum visible arc with the true value', () => {
    const model = statusDonutModel({ passed: 9_950, failed: 50 })
    const tiny = model.slices[1]
    expect(TINY_SLICE_PERCENT).toBe(2)
    expect(tiny.percent).toBeLessThan(TINY_SLICE_PERCENT)
    expect(tiny.tiny).toBe(true)
    expect(model.legendOnly.map((s) => s.status)).toEqual(['failed'])
    // The arc is padded so the slice can be seen and hovered…
    expect(tiny.arc).toBeGreaterThan(tiny.value)
    expect((tiny.arc / sum(model.slices.map((s) => s.arc))) * 100).toBeGreaterThanOrEqual(MIN_SLICE_ARC_PERCENT - 0.01)
    // …and the TRUE value is what the label, the tooltip and the table read.
    expect(tiny.value).toBe(50)
    expect(sliceLabel(tiny)).toBe('Failed 50 (0.5%)')
    // A slice at or above the threshold is drawn from its own value.
    expect(model.slices[0].arc).toBe(model.slices[0].value)
    expect(model.slices[0].tiny).toBe(false)
    expect(model.legendOnly).toHaveLength(1)
  })

  it('hands an all-zero (and an empty) breakdown over to the filtered-empty state', () => {
    for (const counts of [{}, { passed: 0, failed: 0, broken: 0, skipped: 0, unknown: 0 }]) {
      const model = statusDonutModel(counts)
      expect(model.empty, JSON.stringify(counts)).toBe(true)
      expect(model.slices).toEqual([])
      expect(model.total).toBe(0)
    }
  })

  it('drops a zero status rather than drawing a zero-width slice', () => {
    const model = statusDonutModel({ passed: 10, failed: 0, broken: 5 })
    expect(model.slices.map((s) => s.status)).toEqual(['passed', 'broken'])
  })
})

describe('categoryDonutModel', () => {
  it('shares the rounding and tiny-slice rules with the status donut', () => {
    const model = categoryDonutModel([
      { key: 'product', label: 'Product bug', value: 1 },
      { key: 'infra', label: 'Infrastructure', value: 1 },
      { key: 'test', label: 'Test code', value: 1 },
    ])
    expect(model.slices.map((s) => s.percent)).toEqual([33.3, 33.3, 33.4])
    expect(sum(model.slices.map((s) => s.percent))).toBe(100)
    expect(model.slices.map((s) => s.label)).toEqual(['Product bug', 'Infrastructure', 'Test code'])
    expect(model.slices.every((s) => s.status === undefined)).toBe(true)
  })
})

describe('adapters', () => {
  const chartDataSeries: SeriesChart = {
    kind: 'series',
    dimensions: ['status'],
    x_type: 'category',
    series: [
      {
        key: 'executions',
        label: 'Executions',
        points: [
          { x: 'passed', y: 880, n: 880 },
          { x: 'failed', y: 60, n: 60 },
          { x: 'broken', y: 20, n: 20 },
          { x: 'skipped', y: 40, n: 40 },
        ],
      },
    ],
  }

  it('reads a chart-data `group_by=status` series', () => {
    expect(statusCountsFromSeries(chartDataSeries)).toEqual(STORY)
    expect(statusDonutModel(statusCountsFromSeries(chartDataSeries)).total).toBe(1000)
  })

  it('ignores an x value that is not a status (flaky never becomes a slice)', () => {
    const polluted: SeriesChart = {
      ...chartDataSeries,
      series: [{ ...chartDataSeries.series[0], points: [...chartDataSeries.series[0].points, { x: 'flaky', y: 9, n: 9 }] }],
    }
    expect(statusCountsFromSeries(polluted)).toEqual(STORY)
  })

  it('reads a null (unmeasured) point as no data, never as zero', () => {
    const withNull: SeriesChart = {
      ...chartDataSeries,
      series: [{ key: 'e', label: 'E', points: [{ x: 'passed', y: null, n: 0 }, { x: 'failed', y: 3, n: 3 }] }],
    }
    expect(statusCountsFromSeries(withNull)).toEqual({ failed: 3 })
  })

  it('turns the model back into a C3 series the frame table and summary can read', () => {
    const series = donutSeries(statusDonutModel(STORY))
    const checked = validateChartSeries(series)
    expect(checked.ok ? [] : checked.errors).toEqual([])
    expect(series.kind).toBe('series')
    const chart = series as SeriesChart
    expect(chart.x_type).toBe('category')
    expect(chart.series[0].points.map((p) => p.x)).toEqual(['Passed', 'Failed', 'Broken', 'Skipped'])
    // The table shows the TRUE value, never the padded arc.
    expect(chart.series[0].points.map((p) => p.y)).toEqual([880, 60, 20, 40])
  })
})
