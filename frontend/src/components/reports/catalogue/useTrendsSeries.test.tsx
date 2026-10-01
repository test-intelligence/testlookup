/**
 * K7: Summary's trend, through the chart pipeline (`chartGet` + `useChartData`)
 * rather than the page's toast-raising `useTrendData`.
 */
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

let activeProjectId: string | null = 'proj-1'
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId }),
}))

import { __resetChartConcurrency } from '@/services/chartApi'
import { TRENDS_SERIES_URL, useTrendsSeries, validateTrendResponse } from './useTrendsSeries'

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>{children}</SWRConfig>
)

const point = (date: string, passed: number, failed: number) => ({
  date,
  passed,
  failed,
  skipped: 1,
  broken: 0,
  total: passed + failed + 1,
  pass_rate: Math.round((passed / (passed + failed)) * 1000) / 10,
})

const TRENDS = { data: [point('2026-09-01', 90, 10), point('2026-09-02', 95, 5)], period_days: 30 }

const httpError = (status: number, requestId: string) =>
  Object.assign(new Error(`status ${status}`), { response: { status, headers: { 'x-request-id': requestId }, data: {} } })

describe('useTrendsSeries (K7)', () => {
  beforeEach(() => {
    get.mockReset()
    activeProjectId = 'proj-1'
  })
  afterEach(() => __resetChartConcurrency())

  it('asks /metrics/trends toast-free, with the project, the window and the release as a scalar', async () => {
    get.mockResolvedValue({ data: TRENDS, headers: {} })
    const { result } = renderHook(() => useTrendsSeries(30, 'R1'), { wrapper })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(get).toHaveBeenCalledTimes(1)
    const [url, config] = get.mock.calls[0] as [string, { params: Record<string, unknown>; suppressToast: boolean; signal: unknown }]
    expect(url).toBe(TRENDS_SERIES_URL)
    expect(url).toBe('/api/v1/metrics/trends')
    expect(config.suppressToast).toBe(true)
    expect(config.signal).toBeInstanceOf(AbortSignal)
    expect(config.params).toEqual({ project_id: 'proj-1', days: 30, release_id: 'R1' })
  })

  it('draws the validated payload: ready with the points, meta null (the endpoint sends none)', async () => {
    get.mockResolvedValue({ data: TRENDS, headers: {} })
    const { result } = renderHook(() => useTrendsSeries(30, null), { wrapper })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    const state = result.current
    if (state.status !== 'ready') throw new Error('not ready')
    expect(state.data.data).toHaveLength(2)
    expect(state.meta).toBeNull()
  })

  it('clamps the window: a raw 365 goes out as 90', async () => {
    get.mockResolvedValue({ data: TRENDS, headers: {} })
    renderHook(() => useTrendsSeries(365, null), { wrapper })
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect((get.mock.calls[0][1] as { params: { days: number } }).params.days).toBe(90)
  })

  it('several releases go out as a list; none is no key at all', async () => {
    get.mockResolvedValue({ data: TRENDS, headers: {} })
    renderHook(() => useTrendsSeries(7, ['R2', 'R1']), { wrapper })
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect((get.mock.calls[0][1] as { params: Record<string, unknown> }).params).toEqual({
      project_id: 'proj-1',
      days: 7,
      release_id: ['R1', 'R2'],
    })
  })

  it('asks nothing until the project resolves', async () => {
    activeProjectId = null
    const { result } = renderHook(() => useTrendsSeries(30, null), { wrapper })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(get).not.toHaveBeenCalled()
    expect(result.current.status).toBe('loading')
  })

  it('All Projects: no project_id and no release', async () => {
    activeProjectId = 'all'
    get.mockResolvedValue({ data: TRENDS, headers: {} })
    renderHook(() => useTrendsSeries(30, 'R1'), { wrapper })
    await waitFor(() => expect(get).toHaveBeenCalled())
    expect((get.mock.calls[0][1] as { params: Record<string, unknown> }).params).toEqual({ days: 30 })
  })

  it('a server failure is the frame’s error, carrying the request id (no toast was asked for)', async () => {
    get.mockRejectedValue(httpError(500, 'req-500'))
    const { result } = renderHook(() => useTrendsSeries(30, null), { wrapper })
    await waitFor(() => expect(result.current.status).toBe('error'))
    const state = result.current
    if (state.status !== 'error') throw new Error('not error')
    expect(state.error.requestId).toBe('req-500')
    expect(state.error.kind).toBe('server')
  })

  it('a malformed payload is an error naming the request, never a half-drawn chart', async () => {
    get.mockResolvedValue({ data: { data: [{ date: '2026-09-01' }], period_days: 30 }, headers: { 'x-request-id': 'req-bad' } })
    const { result } = renderHook(() => useTrendsSeries(30, null), { wrapper })
    await waitFor(() => expect(result.current.status).toBe('error'))
    const state = result.current
    if (state.status !== 'error') throw new Error('not error')
    expect(state.error.kind).toBe('invalid-payload')
    expect(state.error.requestId).toBe('req-bad')
  })

  it('an empty window is filtered-empty (Summary only mounts it inside its has-data branch)', async () => {
    get.mockResolvedValue({ data: { data: [], period_days: 30 }, headers: {} })
    const { result } = renderHook(() => useTrendsSeries(30, null), { wrapper })
    await waitFor(() => expect(result.current.status).toBe('filtered-empty'))
  })

  it('takes the caller’s existence probe when it has one', async () => {
    get.mockResolvedValue({ data: { data: [], period_days: 30 }, headers: {} })
    const { result } = renderHook(() => useTrendsSeries(30, null, { everHadData: false }), { wrapper })
    await waitFor(() => expect(result.current.status).toBe('never-had-data'))
  })
})

describe('validateTrendResponse (K7)', () => {
  it('accepts the /metrics/trends shape', () => {
    expect(validateTrendResponse(TRENDS).ok).toBe(true)
    const { total: _total, ...noTotal } = point('2026-09-03', 1, 0)
    void _total
    expect(validateTrendResponse({ data: [noTotal], period_days: 7 }).ok).toBe(true)
  })

  it.each([
    ['not an object', null],
    ['an array', []],
    ['no data list', { period_days: 7 }],
    ['a point that is not an object', { data: [1], period_days: 7 }],
    ['a point with no date', { data: [{ ...point('x', 1, 0), date: undefined }], period_days: 7 }],
    ['a count that is not a number', { data: [{ ...point('2026-09-01', 1, 0), failed: '3' }], period_days: 7 }],
    ['a negative count', { data: [{ ...point('2026-09-01', 1, 0), broken: -1 }], period_days: 7 }],
    ['a non-finite rate', { data: [{ ...point('2026-09-01', 1, 0), pass_rate: Number.NaN }], period_days: 7 }],
    ['a total that is not a number', { data: [{ ...point('2026-09-01', 1, 0), total: 'many' }], period_days: 7 }],
    ['no period', { data: [] }],
  ])('refuses %s', (_name, payload) => {
    const checked = validateTrendResponse(payload)
    expect(checked.ok).toBe(false)
  })
})
