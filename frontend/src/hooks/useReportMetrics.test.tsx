/**
 * `useReportMetrics`: ONE `/metrics/summary` request per scope, the C1 wire
 * shape, a stable SWR key, and a `meta` that is validated before use.
 * Real hook and real SWR; only the HTTP helper is faked.
 */
import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GALLERY_CASES } from '@/pages/dev/reportContextFixtures'

vi.mock('@/services/http', () => ({
  getData: vi.fn(),
  postData: vi.fn(),
  putData: vi.fn(),
  patchData: vi.fn(),
  deleteData: vi.fn(),
}))

import { getData } from '@/services/http'
import { api } from '@/services/api'
import { isScopedFetchActive } from '@/services/scopeAbort'
import {
  clampSummaryDays,
  readMeta,
  REPORT_METRICS_ENDPOINT,
  reportMetricsKey,
  summaryWindow,
  useReportMetrics,
  type ReportMetricsScope,
} from './useReportMetrics'

const get = vi.mocked(getData)
const META = GALLERY_CASES.find((c) => c.id === 'filtered')?.meta

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
}

const scope = (overrides: Partial<ReportMetricsScope> = {}): ReportMetricsScope => ({
  projectId: 'p1',
  allProjects: false,
  releaseIds: [],
  suiteNames: [],
  windowDays: 30,
  ...overrides,
})

const summaryCalls = () => get.mock.calls.filter(([url]) => url === REPORT_METRICS_ENDPOINT)

/** The params of the one summary request the hook makes for `s`. */
async function paramsFor(s: ReportMetricsScope) {
  get.mockReset()
  get.mockResolvedValue({ meta: META })
  const view = renderHook(() => useReportMetrics(s), { wrapper })
  await waitFor(() => expect(summaryCalls()).toHaveLength(1))
  view.unmount()
  return (summaryCalls()[0][1] as { params: Record<string, unknown> }).params
}

/** Every chrome request opts in to the C6 block (fix round B, m5): the server computes it only when asked. */
const INCLUDE = { include: 'report_metrics' }

describe('the wire (C1)', () => {
  it('no selection → no release_id / suite_name parameter at all', async () => {
    expect(await paramsFor(scope())).toEqual({ project_id: 'p1', days: 30, ...INCLUDE })
  })

  it('exactly one value stays a scalar, as the legacy call sent it', async () => {
    expect(await paramsFor(scope({ releaseIds: ['r1'], suiteNames: ['payments'] }))).toEqual({
      project_id: 'p1',
      days: 30,
      release_id: 'r1',
      suite_name: 'payments',
      ...INCLUDE,
    })
  })

  it('several values repeat the key (sorted, deduplicated)', async () => {
    const params = await paramsFor(scope({ releaseIds: ['r2', 'r1', 'r2'], suiteNames: ['cart', 'payments'] }))
    expect(params).toEqual({
      project_id: 'p1',
      days: 30,
      release_id: ['r1', 'r2'],
      suite_name: ['cart', 'payments'],
      ...INCLUDE,
    })
    // Through the real axios instance: repeated keys, never `release_id[]=`.
    const uri = api.getUri({ url: REPORT_METRICS_ENDPOINT, params })
    expect(uri).toContain('release_id=r1&release_id=r2')
    expect(uri).not.toContain('%5B%5D')
  })

  it('All projects: no project_id and never a release filter (release_requires_project)', async () => {
    expect(await paramsFor(scope({ allProjects: true, projectId: null, releaseIds: ['r1'], suiteNames: ['s'] }))).toEqual({
      days: 30,
      suite_name: 's',
      ...INCLUDE,
    })
  })

  it('always opts in to report_metrics, and never raises the global toast', async () => {
    get.mockReset()
    get.mockResolvedValue({ meta: META })
    const view = renderHook(() => useReportMetrics(scope()), { wrapper })
    await waitFor(() => expect(summaryCalls()).toHaveLength(1))
    view.unmount()
    const config = summaryCalls()[0][1] as { params: Record<string, unknown>; suppressToast?: boolean }
    expect(config.params.include).toBe('report_metrics')
    expect(config.suppressToast).toBe(true)
  })

  it('is issued inside scopedFetch, so a superseded scope aborts it like the page reads', async () => {
    get.mockReset()
    let marked: boolean | null = null
    get.mockImplementation(async () => {
      marked = isScopedFetchActive()
      return { meta: META }
    })
    const view = renderHook(() => useReportMetrics(scope()), { wrapper })
    await waitFor(() => expect(marked).not.toBeNull())
    view.unmount()
    expect(marked).toBe(true)
  })

  it('never sends a window /metrics/summary rejects (0 = All time, 365): clamped to 1..90', async () => {
    expect((await paramsFor(scope({ windowDays: 0 }))).days).toBe(1)
    expect((await paramsFor(scope({ windowDays: 365 }))).days).toBe(90)
    expect((await paramsFor(scope({ windowDays: Number.NaN }))).days).toBe(90)
  })
})

describe('summaryWindow: the page snap first, then the endpoint cap', () => {
  it('a stored window the page offers is sent as is, with no note', () => {
    expect(summaryWindow(30)).toEqual({ pageDays: 30, days: 30 })
    expect(summaryWindow(14)).toEqual({ pageDays: 14, days: 14 })
  })

  it('snaps like the page: 0 ("All time" from /runs) is 24 h on a 1/7/14/30/90 page', () => {
    expect(summaryWindow(0)).toEqual({ pageDays: 1, days: 1 })
    // Summary report offers no 14: the page (and so the chrome) shows 7.
    expect(summaryWindow(14, [1, 7, 30, 90])).toEqual({ pageDays: 7, days: 7 })
  })

  it('a page offering a year gets 90 days, and says why', () => {
    expect(summaryWindow(365, [7, 30, 90, 365])).toEqual({ pageDays: 365, days: 90, note: 'max for summary' })
    expect(summaryWindow(365)).toEqual({ pageDays: 90, days: 90 })
  })

  it('clampSummaryDays keeps any number inside 1..90', () => {
    expect([0, -5, 1, 45.4, 90, 91, 365].map(clampSummaryDays)).toEqual([1, 1, 1, 45, 90, 90, 90])
  })
})

describe('reportMetricsKey', () => {
  it('is primitive-only and independent of array identity and order', () => {
    const a = reportMetricsKey(scope({ releaseIds: ['r2', 'r1'] }))
    const b = reportMetricsKey(scope({ releaseIds: ['r1', 'r2'] }))
    expect(a).toEqual(b)
    expect(a?.every((part) => typeof part === 'string')).toBe(true)
  })

  it('differs per filter, and is null when disabled', () => {
    expect(reportMetricsKey(scope({ suiteNames: ['a'] }))).not.toEqual(reportMetricsKey(scope()))
    expect(reportMetricsKey(scope({ windowDays: 7 }))).not.toEqual(reportMetricsKey(scope()))
    expect(reportMetricsKey(scope(), false)).toBeNull()
  })
})

describe('readMeta', () => {
  it('accepts a valid envelope', () => {
    expect(readMeta({ meta: META })).toEqual({ meta: META, errors: [] })
  })

  it('rejects an invalid or absent envelope with reasons', () => {
    expect(readMeta({})).toEqual({ meta: null, errors: ['absent: the response carries no meta'] })
    const bad = readMeta({ meta: { ...META, totals: { ...META?.totals, matched_runs: 999 } } })
    expect(bad.meta).toBeNull()
    expect(bad.errors.join(' ')).toMatch(/totals_subset/)
  })
})

describe('useReportMetrics', () => {
  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue({ total_executions_7d: { value: 412 }, meta: META })
  })

  it('makes ONE request and hands back the validated meta', async () => {
    const { result } = renderHook(() => useReportMetrics(scope({ releaseIds: ['r1'] })), { wrapper })
    await waitFor(() => expect(result.current.meta).not.toBeNull())
    expect(summaryCalls()).toHaveLength(1)
    expect(summaryCalls()[0][1]).toEqual({
      params: { project_id: 'p1', days: 30, release_id: 'r1', ...INCLUDE },
      suppressToast: true,
    })
    expect(result.current.summary?.total_executions_7d?.value).toBe(412)
    expect(result.current.metaErrors).toEqual([])
  })

  it('re-rendering with fresh arrays of the same scope does not refetch', async () => {
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useReportMetrics(scope({ releaseIds: ids, suiteNames: ['b', 'a'] })),
      { wrapper, initialProps: { ids: ['r2', 'r1'] } },
    )
    await waitFor(() => expect(result.current.meta).not.toBeNull())
    rerender({ ids: ['r1', 'r2'] })
    rerender({ ids: [...['r2', 'r1']] })
    expect(summaryCalls()).toHaveLength(1)
  })

  it('an invalid meta is surfaced as null + errors, never trusted', async () => {
    get.mockResolvedValue({ total_executions_7d: { value: 1 }, meta: { schema_version: 1 } })
    const { result } = renderHook(() => useReportMetrics(scope()), { wrapper })
    await waitFor(() => expect(result.current.summary).not.toBeNull())
    expect(result.current.meta).toBeNull()
    expect(result.current.metaErrors.length).toBeGreaterThan(0)
  })

  it('a failed request is a reason for the chrome, not an exception or a toast', async () => {
    get.mockRejectedValue(Object.assign(new Error('Unprocessable'), { response: { status: 422 } }))
    const { result } = renderHook(() => useReportMetrics(scope()), { wrapper })
    await waitFor(() => expect(result.current.errorReason).not.toBeNull())
    expect(result.current.errorReason).toBe('the metrics request failed (HTTP 422)')
    expect(result.current.meta).toBeNull()
  })

  it('disabled → no request', async () => {
    renderHook(() => useReportMetrics(scope(), { enabled: false }), { wrapper })
    await new Promise((r) => setTimeout(r, 20))
    expect(summaryCalls()).toHaveLength(0)
  })
})
