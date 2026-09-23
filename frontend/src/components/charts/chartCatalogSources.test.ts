/**
 * One pipeline, three sources (VIZ-401 / VIZ-402): the two older analytics
 * endpoints and VIZ-203's `chart-data` all reach the charts as the SAME
 * validated `ChartResponse`.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { validateChartResponse } from './chartState'
import { CATALOG_URLS, catalogFetcher, useCatalogChartData } from './chartCatalogSources'
import { barsFromSeries } from './BarChart.model'
import { statusCountsFromSeries } from './DonutChart.model'

const chartGet = vi.hoisted(() => vi.fn())

vi.mock('@/services/chartApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/chartApi')>()),
  chartGet,
}))

const signal = new AbortController().signal

beforeEach(() => {
  chartGet.mockReset()
})

describe('catalogFetcher', () => {
  it('asks the right endpoint with the caller’s parameters, and nothing else', async () => {
    chartGet.mockResolvedValue({ data: { items: [] }, requestId: 'req-1' })
    const fetcher = catalogFetcher('top-failing', { project_id: 'p1', days: 30 })
    const result = await fetcher(['top-failing', {}], { signal })
    expect(chartGet).toHaveBeenCalledWith(CATALOG_URLS['top-failing'], {
      params: { project_id: 'p1', days: 30 },
      signal,
    })
    // The request id travels WITH the payload, so an invalid one can name it.
    expect(result.requestId).toBe('req-1')
  })

  it('adapts /analytics/top-failing into a validated ChartResponse', async () => {
    chartGet.mockResolvedValue({
      data: { items: [{ test_name: 'test_a', fail_count: 12 }, { test_name: 'test_b', fail_count: 30 }] },
      requestId: null,
    })
    const { data } = await catalogFetcher('top-failing', {})(['top-failing', {}], { signal })
    const checked = validateChartResponse(data)
    expect(checked.ok ? [] : checked.errors).toEqual([])
    if (!checked.ok) return
    // Keyed by identity (here: by position, as neither row has a fingerprint)
    // and LABELLED by name, so two tests that share a name stay two bars.
    expect(barsFromSeries(checked.value.series)).toEqual([
      { key: 'test_a#0', label: 'test_a', value: 12 },
      { key: 'test_b#1', label: 'test_b', value: 30 },
    ])
  })

  it('adapts /analytics/failure-categories into a validated ChartResponse', async () => {
    chartGet.mockResolvedValue({ data: { items: [{ category: 'PRODUCT_BUG', count: 9 }] }, requestId: null })
    const { data } = await catalogFetcher('failure-categories', {})(['failure-categories', {}], { signal })
    const checked = validateChartResponse(data)
    expect(checked.ok ? [] : checked.errors).toEqual([])
  })

  it('passes a chart-data payload through untouched — it is already C2 + C3', async () => {
    const payload = {
      meta: null,
      series: {
        kind: 'series',
        dimensions: ['status'],
        x_type: 'category',
        series: [{ key: 'e', label: 'E', points: [{ x: 'passed', y: 7, n: 7 }] }],
      },
    }
    chartGet.mockResolvedValue({ data: payload, requestId: null })
    await catalogFetcher('chart-data', { metric: 'executions', group_by: 'status' })(['chart-data', {}], { signal })
    expect(chartGet).toHaveBeenCalledWith(CATALOG_URLS['chart-data'], {
      params: { metric: 'executions', group_by: 'status' },
      signal,
    })
    const passed = await catalogFetcher('chart-data', {})(['chart-data', {}], { signal })
    expect(passed.data).toBe(payload)
    const checked = validateChartResponse(passed.data)
    expect(checked.ok ? [] : checked.errors).toEqual([])
    if (checked.ok) expect(statusCountsFromSeries(checked.value.series)).toEqual({ passed: 7 })
  })

  it('refuses a payload that fails the contract rather than half-drawing it', async () => {
    chartGet.mockResolvedValue({ data: { meta: null, series: { kind: 'nonsense' } }, requestId: null })
    const { data } = await catalogFetcher('chart-data', {})(['chart-data', {}], { signal })
    expect(validateChartResponse(data).ok).toBe(false)
  })
})

describe('useCatalogChartData', () => {
  it('resolves to a ready state a chart can draw', async () => {
    chartGet.mockResolvedValue({ data: { items: [{ test_name: 'test_a', fail_count: 3 }] }, requestId: 'req-2' })
    const { result } = renderHook(() =>
      useCatalogChartData('top-failing', { params: { project_id: 'p1' }, everHadData: true }),
    )
    await waitFor(() => expect(result.current.status).toBe('ready'))
  })

  it('asks for nothing at all while the caller has no parameters', () => {
    const { result } = renderHook(() => useCatalogChartData('top-failing', { params: null, everHadData: null }))
    expect(result.current.status).toBe('loading')
    expect(chartGet).not.toHaveBeenCalled()
  })
})
