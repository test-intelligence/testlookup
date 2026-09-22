import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChartSeries, EnvelopeMeta } from '@/lib/viz/contracts'
import { validateChartResponse, type ChartResponse } from '@/components/charts/chartState'
import type { ChartFetchResult } from '@/services/chartApi'
import { useChartData, type ChartFetcher } from './useChartData'

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-07', days: 7, timezone: 'UTC' },
  },
  totals: { matched_runs: 3, total_runs: 5, matched_executions: 30, total_executions: 50 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-08T00:00:00Z',
  as_of: '2026-09-08T00:00:00Z',
}

const series = (y: number | null): ChartSeries => ({
  kind: 'series',
  dimensions: ['day'],
  x_type: 'time',
  series: [{ key: 'pass', label: 'Pass rate', points: [{ x: '2026-09-01', y, n: 1 }] }],
})

const ok = (y: number | null, requestId = 'r-ok'): ChartFetchResult => ({ data: { meta: META, series: series(y) }, requestId })

const httpError = (status: number, headers: Record<string, string> = {}, data: unknown = {}) =>
  Object.assign(new Error(`status ${status}`), { response: { status, headers, data } })

/** A fresh SWR cache per test, no deduping, so every test starts cold. */
const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>{children}</SWRConfig>
)

type Key = readonly [string, string]

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useChartData', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('goes loading → ready and validates at the boundary', async () => {
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => ok(90))
    const { result } = renderHook(
      () => useChartData<ChartResponse, Key>(['pass', 'a'], fetcher, { validate: validateChartResponse, everHadData: true }),
      { wrapper },
    )
    expect(result.current).toEqual({ status: 'loading' })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(fetcher).toHaveBeenCalledWith(['pass', 'a'], { signal: expect.any(AbortSignal) })
  })

  it('a payload that fails validation is an error with the request id — never data', async () => {
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => ({ data: { meta: null, series: { kind: 'pie' } }, requestId: 'r-invalid' }))
    const { result } = renderHook(
      () => useChartData<ChartResponse, Key>(['pass', 'bad'], fetcher, { validate: validateChartResponse, everHadData: true }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current).toMatchObject({ status: 'error', error: { kind: 'invalid-payload', requestId: 'r-invalid' } })
    expect('data' in result.current).toBe(false)
  })

  it('aborts the request for the old key and ignores its rejection', async () => {
    const first = deferred<ChartFetchResult>()
    const signals: AbortSignal[] = []
    const fetcher = vi.fn<ChartFetcher<Key>>((key, { signal }) => {
      signals.push(signal)
      if (key[1] === 'old') {
        signal.addEventListener('abort', () => first.reject(Object.assign(new Error('canceled'), { name: 'CanceledError' })))
        return first.promise
      }
      return Promise.resolve(ok(75, 'r-new'))
    })
    const { result, rerender } = renderHook(
      ({ filter }: { filter: string }) =>
        useChartData<ChartResponse, Key>(['pass', filter], fetcher, { validate: validateChartResponse, everHadData: true }),
      { wrapper, initialProps: { filter: 'old' } },
    )
    await waitFor(() => expect(signals).toHaveLength(1))
    rerender({ filter: 'new' })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(signals[0].aborted).toBe(true)
    expect(signals[1].aborted).toBe(false)
    if (result.current.status === 'ready') expect(result.current.data.series).toEqual(series(75))
    // The aborted request never surfaced as an error.
    await act(async () => {})
    expect(result.current.status).toBe('ready')
  })

  it('a request that resolves AFTER its key was superseded is not shown', async () => {
    const old = deferred<ChartFetchResult>()
    const fetcher = vi.fn<ChartFetcher<Key>>((key) => (key[1] === 'old' ? old.promise : Promise.resolve(ok(10))))
    const { result, rerender } = renderHook(
      ({ filter }: { filter: string }) => useChartData<ChartResponse, Key>(['pass', filter], fetcher, { everHadData: true }),
      { wrapper, initialProps: { filter: 'old' } },
    )
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
    rerender({ filter: 'new' })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    await act(async () => old.resolve(ok(99)))
    if (result.current.status === 'ready') expect(result.current.data.series).toEqual(series(10))
  })

  it('keeps the previous chart, dimmed, while a new key loads', async () => {
    const next = deferred<ChartFetchResult>()
    const fetcher = vi.fn<ChartFetcher<Key>>((key) => (key[1] === 'a' ? Promise.resolve(ok(50)) : next.promise))
    const { result, rerender } = renderHook(
      ({ filter }: { filter: string }) => useChartData<ChartResponse, Key>(['pass', filter], fetcher, { everHadData: true }),
      { wrapper, initialProps: { filter: 'a' } },
    )
    await waitFor(() => expect(result.current.status).toBe('ready'))
    rerender({ filter: 'b' })
    await waitFor(() => expect(result.current).toMatchObject({ status: 'ready', revalidating: true }))
    await act(async () => next.resolve(ok(60)))
    await waitFor(() => expect(result.current).toMatchObject({ status: 'ready', revalidating: false }))
  })

  it('never-had-data comes from the unfiltered probe, filtered-empty otherwise', async () => {
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => ok(null))
    const { result, rerender } = renderHook(
      ({ ever }: { ever: boolean | null }) => useChartData<ChartResponse, Key>(['pass', 'empty'], fetcher, { everHadData: ever }),
      { wrapper, initialProps: { ever: true as boolean | null } },
    )
    await waitFor(() => expect(result.current.status).toBe('filtered-empty'))
    rerender({ ever: false })
    expect(result.current.status).toBe('never-had-data')
    rerender({ ever: null })
    expect(result.current.status).toBe('loading')
  })

  it('401 stays loading (the refresh owns it); 403 is forbidden; 500 carries the request id and retries', async () => {
    let status = 401
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => {
      if (status === 200) return ok(80)
      throw httpError(status, { 'x-request-id': `r-${status}` })
    })
    const { result, rerender } = renderHook(
      ({ id }: { id: string }) => useChartData<ChartResponse, Key>(['pass', id], fetcher, { everHadData: true }),
      { wrapper, initialProps: { id: '401' } },
    )
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
    await act(async () => {})
    expect(result.current).toEqual({ status: 'loading' })

    status = 403
    rerender({ id: '403' })
    await waitFor(() => expect(result.current).toEqual({ status: 'forbidden', requestId: 'r-403' }))

    status = 500
    rerender({ id: '500' })
    await waitFor(() => expect(result.current).toMatchObject({ status: 'error', error: { kind: 'server', requestId: 'r-500' } }))
    status = 200
    await act(async () => {
      if (result.current.status === 'error') result.current.retry?.()
    })
    await waitFor(() => expect(result.current.status).toBe('ready'))
  })

  it('a 429 waits for Retry-After, then tries again by itself', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let calls = 0
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => {
      calls += 1
      if (calls === 1) throw httpError(429, { 'retry-after': '2' })
      return ok(70)
    })
    const { result } = renderHook(() => useChartData<ChartResponse, Key>(['pass', '429'], fetcher, { everHadData: true }), {
      wrapper,
    })
    await waitFor(() => expect(result.current).toMatchObject({ status: 'error', error: { kind: 'rate-limited', retryAfterSeconds: 2 } }))
    expect(fetcher).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100)
    })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('two consecutive 429s with the SAME Retry-After both re-arm the automatic retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let calls = 0
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => {
      calls += 1
      if (calls <= 2) throw httpError(429, { 'retry-after': '1' })
      return ok(70)
    })
    const { result } = renderHook(() => useChartData<ChartResponse, Key>(['pass', '429x2'], fetcher, { everHadData: true }), {
      wrapper,
    })
    await waitFor(() => expect(result.current).toMatchObject({ status: 'error', error: { kind: 'rate-limited' } }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(fetcher).toHaveBeenCalledTimes(3)
  })

  it('stops after 3 automatic 429 retries and leaves a manual Retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => {
      throw httpError(429, { 'retry-after': '1' })
    })
    const { result } = renderHook(() => useChartData<ChartResponse, Key>(['pass', '429-cap'], fetcher, { everHadData: true }), {
      wrapper,
    })
    await waitFor(() => expect(result.current.status).toBe('error'))
    for (let i = 0; i < 6; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100)
      })
    }
    expect(fetcher).toHaveBeenCalledTimes(4)
    const state = result.current
    if (state.status !== 'error') throw new Error('expected error')
    expect(state.error.kind).toBe('rate-limited')
    expect(state.error.message).not.toMatch(/will try again/)
    expect(state.retry).toBeTypeOf('function')
    await act(async () => state.retry?.())
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(5))
  })

  it('a 401 that survived the refresh retry is a session-expired error, not endless loading', async () => {
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => {
      throw Object.assign(httpError(401), { config: { _retry: true } })
    })
    const { result } = renderHook(() => useChartData<ChartResponse, Key>(['pass', '401-final'], fetcher, { everHadData: true }), {
      wrapper,
    })
    await waitFor(() => expect(result.current).toMatchObject({ status: 'error', error: { kind: 'session-expired' } }))
  })

  it('a null key never fetches and stays loading', async () => {
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => ok(1))
    const { result } = renderHook(() => useChartData<ChartResponse, Key>(null, fetcher, { everHadData: true }), { wrapper })
    await act(async () => {})
    expect(fetcher).not.toHaveBeenCalled()
    expect(result.current).toEqual({ status: 'loading' })
  })

  it('a superseded rejection on the CURRENT key asks again instead of stranding the chart', async () => {
    let calls = 0
    const fetcher = vi.fn<ChartFetcher<Key>>(async () => {
      calls += 1
      if (calls === 1) throw Object.assign(new Error('aborted elsewhere'), { name: 'AbortError' })
      return ok(5)
    })
    const { result } = renderHook(() => useChartData<ChartResponse, Key>(['pass', 'shared'], fetcher, { everHadData: true }), {
      wrapper,
    })
    await waitFor(() => expect(result.current.status).toBe('ready'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
