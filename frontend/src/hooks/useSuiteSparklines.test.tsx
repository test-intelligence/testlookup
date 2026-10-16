/**
 * TRND-06 (a): the Suite pass rates sparklines ride on "Pass rate by suite"'s
 * request. The params are the catalogue's default request, deep-equal, so the
 * SWR key is the same and ONE `top_n=7` request serves the card and the chart;
 * nothing is asked until the card is near, nor for a one-day window.
 */
import { act, render, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import { DEFAULT_SERIES_CONFIG, seriesParams } from '@/components/charts/chartCustomiseModel'
import { catalogueParams } from '@/components/reports/catalogue/catalogueScope'
import { __resetChartConcurrency } from '@/services/chartApi'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))
vi.mock('@/components/reports/catalogue/useEverHadRun', () => ({ useEverHadRun: (enabled: boolean) => (enabled ? true : null) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: 'p1' }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

import TrendsCatalogue from '@/components/reports/catalogue/TrendsCatalogue'
import { sparklineLines, useSuiteSparklines } from './useSuiteSparklines'

const CHART_DATA_URL = '/api/v1/analytics/chart-data'
const DAYS = ['2026-09-15', '2026-09-16', '2026-09-17']
const META = {
  schema_version: 1,
  scope: { projects: [], releases: [], suites: [], window: { from: DAYS[0], to: DAYS[2], days: 3, timezone: 'UTC' } },
  totals: { matched_runs: 3, total_runs: 3, matched_executions: 30, total_executions: 30 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-17T12:00:00Z',
  as_of: '2026-09-17T12:00:00Z',
}
const PAYLOAD = {
  meta: META,
  series: {
    kind: 'series',
    dimensions: ['day', 'suite'],
    x_type: 'time',
    series: [
      { key: 'checkout', label: 'Checkout', points: DAYS.map((x, i) => ({ x, y: 90 + i, n: 10 })) },
      {
        key: 'payments',
        label: 'payments',
        // Out of order on the wire, and one day with nothing evaluated: a gap, never 0.
        points: [
          { x: DAYS[2], y: 70, n: 10 },
          { x: DAYS[0], y: 80, n: 10 },
          { x: DAYS[1], y: null, n: 0, measured: false, reason: 'no evaluated executions' },
        ],
      },
      { key: '__other__', label: 'Other', points: DAYS.map((x) => ({ x, y: 50, n: 10 })) },
    ],
  },
}

const chartDataCalls = (): CatalogParams[] =>
  get.mock.calls.filter(([url]) => url === CHART_DATA_URL).map(([, config]) => (config as { params: CatalogParams }).params)

function makeWrapper() {
  const cache = new Map()
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <SWRConfig value={{ provider: () => cache, dedupingInterval: 2000, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>{children}</ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
}

beforeEach(() => {
  window.localStorage.clear()
  get.mockReset()
  get.mockImplementation(() => Promise.resolve({ data: PAYLOAD, headers: { 'x-request-id': 'req-1' } }))
})
afterEach(() => __resetChartConcurrency())

describe('useSuiteSparklines', () => {
  it('asks exactly the catalogue default suite-series request (deep-equal params)', async () => {
    const { result } = renderHook(() => useSuiteSparklines(14, null, true), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.state).toBe('ready'))
    const catalogueDefault = catalogueParams({
      projectId: 'p1',
      allProjects: false,
      days: 14,
      extra: seriesParams(DEFAULT_SERIES_CONFIG),
      maxDays: 90,
    })
    expect(chartDataCalls()).toEqual([catalogueDefault])
    expect(chartDataCalls()[0]).toEqual({ metric: 'pass_rate', group_by: ['day', 'suite'], top_n: 7, project_id: 'p1', days: 14 })
  })

  it('shares ONE request with "Pass rate by suite" when both are on the page', async () => {
    const wrapper = makeWrapper()
    function Both() {
      const sparks = useSuiteSparklines(14, null, true)
      return (
        <>
          <output data-testid="spark-state">{sparks.state}</output>
          <TrendsCatalogue days={14} suiteFilter={null} sections={['trends-multi-series']} />
        </>
      )
    }
    const view = render(<Both />, { wrapper })
    await waitFor(() => expect(view.getByTestId('spark-state')).toHaveTextContent('ready'))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(chartDataCalls().filter((params) => params.top_n !== undefined)).toHaveLength(1)
  })

  it('asks nothing until the card is near, nor for a one-day window', async () => {
    const { result, rerender } = renderHook(({ near, days }) => useSuiteSparklines(days, null, near), {
      wrapper: makeWrapper(),
      initialProps: { near: false, days: 14 },
    })
    expect(result.current.state).toBe('idle')
    rerender({ near: true, days: 1 })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(chartDataCalls()).toHaveLength(0)
    expect(result.current.state).toBe('idle')
    expect(result.current.lineOf('Checkout')).toBeUndefined()
  })

  it('matches a suite ignoring case; one outside the 7 busiest is undefined; a gap stays null', async () => {
    const { result } = renderHook(() => useSuiteSparklines(14, null, true), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.state).toBe('ready'))
    expect(result.current.lineOf(' CHECKOUT ')).toEqual([90, 91, 92])
    expect(result.current.lineOf('Payments')).toEqual([80, null, 70])
    expect(result.current.lineOf('billing')).toBeUndefined()
    // "Other" is the fold, not a suite.
    expect(result.current.lineOf('__other__')).toBeUndefined()
  })

  it('a failed read is unavailable: every row is "—", nothing throws', async () => {
    get.mockImplementation(() => Promise.reject(Object.assign(new Error('boom'), { response: { status: 500, data: {} } })))
    const { result } = renderHook(() => useSuiteSparklines(14, null, true), { wrapper: makeWrapper() })
    await waitFor(() => expect(result.current.state).toBe('unavailable'))
    expect(result.current.lineOf('checkout')).toBeUndefined()
  })
})

describe('sparklineLines (defensive)', () => {
  it.each<[unknown]>([[undefined], [null], [{}], ['x'], [[null, 'a', { key: 3 }, { key: 'a', points: 'p' }]]])(
    '%j → no lines',
    (raw) => {
      expect(sparklineLines(raw).size).toBe(0)
    },
  )
})
