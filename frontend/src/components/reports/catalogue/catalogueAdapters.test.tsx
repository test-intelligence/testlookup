/**
 * K4: payloads the report pages already hold, as the canonical `ChartResponse`
 * the catalogue frames read. Every output goes through the contract validator
 * (C3), the way `useChartData` would put it: an adapter whose output the
 * validator refuses would be an error frame in production.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { SeriesChart } from '@/lib/viz/contracts'
import ChartTable from '@/components/charts/ChartTable'
import { validateChartResponse, type ChartResponse } from '@/components/charts/chartState'
import { statusCountsFromSeries, statusDonutModel } from '@/components/charts/DonutChart.model'
import { barsFromSeries, statusRowsFromSeries } from '@/components/charts/BarChart.model'
import {
  MISSING_COUNT_REASON,
  UNKNOWN_NOT_DERIVABLE_REASON,
  UNKNOWN_OVER_TOTAL_REASON,
  chartResponseFromClusters,
  chartResponseFromStatusCounts,
  chartResponseFromSuiteRows,
  displayText,
  statusCountsFromTrendPoints,
} from './catalogueAdapters'

const HOSTILE = '<img src=x onerror="window.__xss=1">'
const LONE_SURROGATE = 'suite\uD800name'

/** The validated value, or a failure naming every error. */
function valid(response: ChartResponse): ChartResponse {
  const checked = validateChartResponse(response)
  if (!checked.ok) throw new Error(`contract refused the adapter output: ${checked.errors.join('; ')}`)
  return checked.value
}

const seriesOf = (response: ChartResponse) => response.series as SeriesChart
const pointAt = (response: ChartResponse, seriesKey: string, x: string) =>
  seriesOf(response)
    .series.find((s) => s.key === seriesKey)
    ?.points.find((p) => p.x === x)

describe('chartResponseFromStatusCounts (K4)', () => {
  it('one category series, x = status, in the fixed status order, meta null', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: 4, skipped: 6 }))
    expect(response.meta).toBeNull()
    const series = seriesOf(response)
    expect(series.dimensions).toEqual(['status'])
    expect(series.x_type).toBe('category')
    expect(series.series).toHaveLength(1)
    expect(series.series[0].points.map((p) => [p.x, p.y, p.n])).toEqual([
      ['passed', 80, 80],
      ['failed', 10, 10],
      ['broken', 4, 4],
      ['skipped', 6, 6],
    ])
  })

  it('the donut reads it unchanged: its totals are the counts', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: 4, skipped: 6, total: 104 }))
    const model = statusDonutModel(statusCountsFromSeries(response.series))
    expect(model.total).toBe(104)
  })

  it('a missing count is null with a reason, never 0', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: undefined, skipped: null }))
    for (const status of ['broken', 'skipped']) {
      const point = pointAt(response, 'count', status)
      expect(point?.y).toBeNull()
      expect(point?.n).toBe(0)
      expect(point?.measured).toBe(false)
      expect(point?.reason).toBe(MISSING_COUNT_REASON)
    }
  })

  it('a count that is not a count (a string, NaN, negative) is missing, not a number', () => {
    const response = valid(
      chartResponseFromStatusCounts({
        passed: '80' as unknown as number,
        failed: Number.NaN,
        broken: -3,
        skipped: Infinity,
      }),
    )
    for (const status of ['passed', 'failed', 'broken', 'skipped']) expect(pointAt(response, 'count', status)?.y).toBeNull()
  })

  it('unknown is the residual of the total: total minus the four statuses', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: 4, skipped: 6, total: 107 }))
    expect(pointAt(response, 'count', 'unknown')?.y).toBe(7)
    // The residual, never the total itself (that would count every execution twice).
    const counts = statusCountsFromSeries(response.series)
    expect(Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0)).toBe(107)
  })

  it('a zero residual is a measured zero', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 1, failed: 1, broken: 1, skipped: 1, total: 4 }))
    expect(pointAt(response, 'count', 'unknown')?.y).toBe(0)
  })

  it('no total: unknown is omitted, not zero (the payload does not carry it)', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: 4, skipped: 6 }))
    expect(pointAt(response, 'count', 'unknown')).toBeUndefined()
  })

  it('an explicit unknown count is used as it is', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 5, failed: 0, broken: 0, skipped: 0, unknown: 2, total: 99 }))
    expect(pointAt(response, 'count', 'unknown')?.y).toBe(2)
  })

  it('a total with a status missing cannot give a residual: unknown is null, with why', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: null, skipped: 6, total: 107 }))
    const point = pointAt(response, 'count', 'unknown')
    expect(point?.y).toBeNull()
    expect(point?.reason).toBe(UNKNOWN_NOT_DERIVABLE_REASON)
  })

  it('statuses that add up to more than the total: unknown is null, never negative', () => {
    const response = valid(chartResponseFromStatusCounts({ passed: 80, failed: 10, broken: 4, skipped: 6, total: 90 }))
    const point = pointAt(response, 'count', 'unknown')
    expect(point?.y).toBeNull()
    expect(point?.reason).toBe(UNKNOWN_OVER_TOTAL_REASON)
  })
})

describe('statusCountsFromTrendPoints (K4)', () => {
  const day = (passed: number, failed: number, broken: number, skipped: number, total?: number) => ({
    date: '2026-09-01',
    passed,
    failed,
    broken,
    skipped,
    pass_rate: 0,
    ...(total === undefined ? {} : { total }),
  })

  it('sums the window, and carries the total only when every point has one', () => {
    expect(statusCountsFromTrendPoints([day(10, 1, 1, 1, 14), day(20, 2, 0, 0, 23)])).toEqual({
      passed: 30,
      failed: 3,
      broken: 1,
      skipped: 1,
      total: 37,
    })
    expect(statusCountsFromTrendPoints([day(10, 1, 1, 1, 14), day(20, 2, 0, 0)]).total).toBeUndefined()
  })

  it('a status one day did not report is missing for the window, not undercounted', () => {
    const counts = statusCountsFromTrendPoints([day(10, 1, 1, 1), { ...day(20, 2, 0, 0), broken: undefined as unknown as number }])
    expect(counts.broken).toBeNull()
    expect(counts.passed).toBe(30)
  })

  it('feeds the donut adapter: totals equal the trend foot strip by construction', () => {
    const points = [day(10, 1, 1, 1, 14), day(20, 2, 0, 0, 23)]
    const response = valid(chartResponseFromStatusCounts(statusCountsFromTrendPoints(points)))
    expect(statusDonutModel(statusCountsFromSeries(response.series)).total).toBe(37)
  })
})

describe('chartResponseFromSuiteRows (K4)', () => {
  const rows = [
    { suite_name: 'auth', passed: 50, failed: 1, broken: 0, skipped: 2 },
    { suite_name: 'cart', passed: 30, failed: 5, broken: 3, skipped: 0 },
    { suite_name: 'search', passed: 40, failed: 2, broken: 2, skipped: 1 },
  ]

  it('statuses as series, suites as x with display names, worst (failed + broken) first', () => {
    const response = valid(chartResponseFromSuiteRows(rows))
    const series = seriesOf(response)
    expect(series.dimensions).toEqual(['suite', 'status'])
    expect(series.series.map((s) => s.key)).toEqual(['passed', 'failed', 'broken', 'skipped'])
    const drawn = statusRowsFromSeries(series)
    expect(drawn.map((row) => row.label)).toEqual(['cart', 'search', 'auth'])
    expect(drawn[0].counts).toEqual({ passed: 30, failed: 5, broken: 3, skipped: 0 })
  })

  it('two suites with one name are two bars, never merged', () => {
    const response = valid(
      chartResponseFromSuiteRows([
        { suite_name: 'e2e', passed: 1, failed: 1, broken: 0, skipped: 0 },
        { suite_name: 'e2e', passed: 2, failed: 0, broken: 0, skipped: 0 },
      ]),
    )
    expect(statusRowsFromSeries(response.series)).toHaveLength(2)
  })

  it('a missing count is null, never 0', () => {
    const response = valid(chartResponseFromSuiteRows([{ suite_name: 'auth', passed: 5, failed: 1 }]))
    const series = seriesOf(response)
    const broken = series.series.find((s) => s.key === 'broken')?.points[0]
    expect(broken?.y).toBeNull()
    expect(broken?.reason).toBe(MISSING_COUNT_REASON)
  })

  it('no unknown series: the summary rows do not carry it', () => {
    expect(seriesOf(chartResponseFromSuiteRows(rows)).series.some((s) => s.key === 'unknown')).toBe(false)
  })

  it('an empty list is an empty (valid) chart', () => {
    const response = valid(chartResponseFromSuiteRows([]))
    expect(seriesOf(response).series.every((s) => s.points.length === 0)).toBe(true)
  })
})

describe('chartResponseFromClusters (K4)', () => {
  it('one slice per cluster, sized by its member count', () => {
    const response = valid(
      chartResponseFromClusters([
        { cluster_id: 'c1', label: 'Timeouts', size: 12 },
        { cluster_id: 'c2', label: 'Null pointer', size: 3 },
      ]),
    )
    expect(barsFromSeries(response.series).map((bar) => [bar.label, bar.value])).toEqual([
      ['Timeouts', 12],
      ['Null pointer', 3],
    ])
    expect(seriesOf(response).dimensions).toEqual(['failure_cluster'])
  })

  it('duplicate cluster ids are still two slices', () => {
    const response = valid(
      chartResponseFromClusters([
        { cluster_id: 'c1', label: 'A', size: 1 },
        { cluster_id: 'c1', label: 'B', size: 2 },
      ]),
    )
    expect(barsFromSeries(response.series)).toHaveLength(2)
  })

  it('a missing size is null, never 0', () => {
    const response = valid(chartResponseFromClusters([{ id: 'c1', label: 'A', size: null }]))
    expect(seriesOf(response).series[0].points[0].y).toBeNull()
  })
})

describe('hostile names (K4)', () => {
  it('every adapter keeps a hostile name as the exact text, and the table shows it as text', () => {
    const responses = [
      chartResponseFromSuiteRows([{ suite_name: HOSTILE, passed: 1, failed: 1, broken: 0, skipped: 0 }]),
      chartResponseFromClusters([{ cluster_id: HOSTILE, label: HOSTILE, size: 2 }]),
    ].map(valid)
    for (const response of responses) {
      const { container, unmount } = render(<ChartTable caption="t" series={response.series} autoFocus={false} />)
      expect(container.querySelector('img, b')).toBeNull()
      expect(screen.getByText((text) => text.includes(HOSTILE))).toBeInTheDocument()
      unmount()
    }
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })

  it('a name that is a prototype key is a label, not a property lookup', () => {
    for (const name of ['__proto__', 'constructor', 'prototype']) {
      const response = valid(chartResponseFromSuiteRows([{ suite_name: name, passed: 1, failed: 0, broken: 0, skipped: 0 }]))
      expect(statusRowsFromSeries(response.series)[0].label).toBe(name)
      const clusters = valid(chartResponseFromClusters([{ cluster_id: name, label: name, size: 1 }]))
      expect(barsFromSeries(clusters.series)[0].label).toBe(name)
    }
  })

  it('a lone surrogate (malformed UTF-16 from an ingested file) cannot make the contract refuse the chart', () => {
    expect(displayText(LONE_SURROGATE, '')).toBe('suite�name')
    expect(displayText('ok 😀 pair', '')).toBe('ok 😀 pair')
    expect(displayText('a\uDC00b', '')).toBe('a�b')
    expect(displayText('end\uD800', '')).toBe('end�')
    valid(chartResponseFromSuiteRows([{ suite_name: LONE_SURROGATE, passed: 1 }]))
    valid(chartResponseFromClusters([{ cluster_id: LONE_SURROGATE, label: LONE_SURROGATE, size: 1 }]))
  })

  it('a missing or non-text name gets a stated placeholder, never "undefined"', () => {
    expect(displayText(undefined, '(no suite)')).toBe('(no suite)')
    expect(displayText(null, '(no suite)')).toBe('(no suite)')
    expect(displayText('   ', '(no suite)')).toBe('(no suite)')
    expect(displayText(42, '(no suite)')).toBe('42')
    expect(displayText({ toString: () => 'x' }, '(none)')).toBe('(none)')
  })
})
