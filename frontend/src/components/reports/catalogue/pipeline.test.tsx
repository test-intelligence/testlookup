/**
 * Plan 4.3: the chart pipeline the catalogue is the FIRST production user of
 * (`useCatalogChartData` -> `useChartData` -> `chartGet`), exercised end to
 * end with only the network mocked — the unit tests of each piece mock the
 * piece next to it, so nothing had proved them together.
 *
 *   - at most 4 chart requests in flight, however many sections mount;
 *   - two sections with the SAME scope (Trends' multi-series and heatmap)
 *     make ONE request;
 *   - a superseded request (a filter change) is aborted, frees its slot and
 *     never shows as an error.
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta } from '@/lib/viz/contracts'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

import { __resetChartConcurrency, MAX_CONCURRENT_CHART_REQUESTS } from '@/services/chartApi'
import { useCatalogChartData, type CatalogParams } from '@/components/charts/chartCatalogSources'

interface Pending {
  params: CatalogParams
  resolve: () => void
  aborted: boolean
}

let pending: Pending[] = []
let inFlight = 0
let peak = 0

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-14', days: 14, timezone: 'UTC' },
  },
  totals: { matched_runs: 1, total_runs: 1, matched_executions: 10, total_executions: 10 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-14T00:00:00Z',
  as_of: '2026-09-14T00:00:00Z',
}

const PAYLOAD = {
  meta: META,
  series: {
    kind: 'series',
    dimensions: ['day', 'suite'],
    x_type: 'time',
    series: [{ key: 'auth', label: 'auth', points: [{ x: '2026-09-01', y: 90, n: 10 }] }],
  },
}

function canceled(): Error {
  const error = new Error('canceled')
  error.name = 'CanceledError'
  return error
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>{children}</SWRConfig>
)

const params = (metric: string, days = 14): CatalogParams => ({ project_id: 'p1', days, metric, group_by: ['day', 'suite'] })

beforeEach(() => {
  pending = []
  inFlight = 0
  peak = 0
  get.mockReset()
  get.mockImplementation((_url: string, config: { params: CatalogParams; signal?: AbortSignal }) => {
    inFlight += 1
    peak = Math.max(peak, inFlight)
    return new Promise((resolve, reject) => {
      const entry: Pending = {
        params: config.params,
        aborted: false,
        resolve: () => {
          inFlight -= 1
          resolve({ data: PAYLOAD, headers: { 'x-request-id': 'req-1' } })
        },
      }
      pending.push(entry)
      config.signal?.addEventListener('abort', () => {
        entry.aborted = true
        inFlight -= 1
        reject(canceled())
      })
    })
  })
})

afterEach(() => __resetChartConcurrency())

describe('catalogue chart pipeline (plan 4.3)', () => {
  it('six sections mounting at once: never more than 4 requests in flight, all six drawn', async () => {
    const metrics = ['a', 'b', 'c', 'd', 'e', 'f']
    const { result } = renderHook(
      () => metrics.map((metric) => useCatalogChartData('chart-data', { params: params(metric), everHadData: true })),
      { wrapper },
    )
    await waitFor(() => expect(pending).toHaveLength(MAX_CONCURRENT_CHART_REQUESTS))
    expect(peak).toBe(4)
    // Drain: each completion admits the next one.
    while (pending.some((entry) => !entry.aborted)) {
      const next = pending.shift() as Pending
      if (next.aborted) continue
      await act(async () => next.resolve())
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
    }
    await waitFor(() => expect(result.current.every((state) => state.status === 'ready')).toBe(true))
    expect(get).toHaveBeenCalledTimes(6)
    expect(peak).toBeLessThanOrEqual(MAX_CONCURRENT_CHART_REQUESTS)
  })

  it('two sections on one scope (multi-series + heatmap) make ONE request', async () => {
    const shared = params('pass_rate')
    const { result } = renderHook(
      () => [
        useCatalogChartData('chart-data', { params: shared, everHadData: true }),
        // A separately built but equal params object: what a second component would pass.
        useCatalogChartData('chart-data', { params: { ...shared, group_by: ['day', 'suite'] }, everHadData: true }),
      ],
      { wrapper },
    )
    await waitFor(() => expect(pending).toHaveLength(1))
    await act(async () => pending[0].resolve())
    await waitFor(() => expect(result.current.every((state) => state.status === 'ready')).toBe(true))
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('a filter change aborts the superseded request, frees its slot and shows no error', async () => {
    const { result, rerender } = renderHook(
      ({ days }) => useCatalogChartData('chart-data', { params: params('pass_rate', days), everHadData: true }),
      { wrapper, initialProps: { days: 14 } },
    )
    await waitFor(() => expect(pending).toHaveLength(1))
    rerender({ days: 30 })
    await waitFor(() => expect(pending).toHaveLength(2))
    expect(pending[0].aborted).toBe(true)
    expect(inFlight).toBe(1)
    expect(result.current.status).not.toBe('error')
    await act(async () => pending[1].resolve())
    await waitFor(() => expect(result.current.status).toBe('ready'))
  })
})
