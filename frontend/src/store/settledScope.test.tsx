/**
 * VIZ-303 performance: "filter changes debounced 250 ms; in-flight requests
 * aborted" — at the scope → request boundary, not per page.
 *
 *  - A burst of picker changes produces ONE settled scope, 250 ms after the
 *    last change, and so ONE request per panel — while the controls (the UI
 *    clock) follow every click.
 *  - A request built from a scope that has since been superseded is aborted,
 *    and the abort never reaches the page as an error (or a toast).
 *  - Requests with no scope parameter — existence probes — are never touched.
 *
 * The abort half goes through the REAL shared axios instance (its
 * interceptors are the boundary) with a hand-driven adapter.
 */
import type { ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import useSWR, { SWRConfig } from 'swr'
import { CanceledError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: toastError, success: vi.fn() }) }))

import { api } from '@/services/api'
import { getData } from '@/services/http'
import { isScopeSuperseded, scopedFetch, scopeSupersededMiddleware, trackedScopeRequestCount } from '@/services/scopeAbort'
import { usePageSuiteFilter } from '@/hooks/useSuiteScope'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { keyPart, scopeArg, scopeParam } from '@/lib/scopeParams'
import { useMultiFiltersFlagStore } from './multiFiltersFlag'
import { useProjectStore } from './projectStore'
import { useReleaseStore } from './releaseStore'
import { SCOPE_SETTLE_MS, isScopeSettlePending, settleScopeNow, useSettledScopeStore } from './settledScope'
import { useSuiteStore } from './suiteStore'

const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, use: [scopeSupersededMiddleware] }}>
      {children}
    </SWRConfig>
  )
}

function reset() {
  localStorage.clear()
  useMultiFiltersFlagStore.setState({ enabled: true })
  useProjectStore.setState({ activeProjectId: PROJECT_A })
  useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
  settleScopeNow()
}

describe('settled scope: a burst of filter changes is one request', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('5 changes in 200 ms -> ONE fetch, 250 ms after the last; the controls update on every change', async () => {
    const fetcher = vi.fn(async (key: readonly unknown[]) => key.join('|'))
    const { result } = renderHook(
      () => {
        const suite = usePageSuiteFilter()
        const release = useReleaseScope()
        const swr = useSWR(['metrics', keyPart(suite.suiteFilter), keyPart(scopeArg(release))], fetcher)
        return { suite, data: swr.data }
      },
      { wrapper },
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetcher).toHaveBeenCalledTimes(1) // the unfiltered first load
    fetcher.mockClear()

    const steps: Array<() => void> = [
      () => useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A),
      () => useSuiteStore.getState().setActiveSuites(['payments', 'cart'], PROJECT_A),
      () => useReleaseStore.getState().setActiveReleases([R1], PROJECT_A),
      () => useSuiteStore.getState().setActiveSuites(['payments', 'cart', 'search'], PROJECT_A),
      () => useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A),
    ]
    const expectedUi = [1, 2, 2, 3, 3]
    for (const [i, step] of steps.entries()) {
      if (i > 0) await act(async () => { await vi.advanceTimersByTimeAsync(50) })
      act(step)
      // UI clock: the control shows the click at once...
      expect(result.current.suite.suiteNames).toHaveLength(expectedUi[i])
      // ...data clock: nothing is fetched mid-burst.
      expect(fetcher).not.toHaveBeenCalled()
    }
    // 5 changes over 200 ms. Just short of the quiet period: still nothing.
    await act(async () => { await vi.advanceTimersByTimeAsync(SCOPE_SETTLE_MS - 1) })
    expect(fetcher).not.toHaveBeenCalled()
    expect(isScopeSettlePending()).toBe(true)

    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toEqual(['metrics', ['cart', 'payments', 'search'].join('\u001f'), [R1, R2].join('\u001f')])

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(result.current.data).toBe(`metrics|${['cart', 'payments', 'search'].join('\u001f')}|${[R1, R2].join('\u001f')}`)
  })

  it('the settled lists keep their identity when only the other dimension changes', () => {
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    settleScopeNow()
    const suites = useSettledScopeStore.getState().suiteNames
    useReleaseStore.getState().setActiveReleases([R1], PROJECT_A)
    settleScopeNow()
    expect(useSettledScopeStore.getState().suiteNames).toBe(suites)
    expect(useSettledScopeStore.getState().releaseIds).toEqual([R1])
  })
})

describe('settled scope: superseded requests are aborted, never shown', () => {
  interface Pending {
    config: InternalAxiosRequestConfig
    settle: (data: unknown) => void
    outcome: 'pending' | 'resolved' | 'aborted'
  }
  let pending: Pending[] = []
  const originalAdapter = api.defaults.adapter

  const adapter: AxiosAdapter = (config) =>
    new Promise((resolve, reject) => {
      const entry: Pending = {
        config,
        outcome: 'pending',
        settle: (data) => {
          entry.outcome = 'resolved'
          resolve({ data, status: 200, statusText: 'OK', headers: {}, config })
        },
      }
      pending.push(entry)
      config.signal?.addEventListener?.('abort', () => {
        entry.outcome = 'aborted'
        reject(new CanceledError(undefined, config))
      })
    })

  const suiteOf = (p: Pending) => (p.config.params as Record<string, unknown> | undefined)?.suite_name
  const findSummary = (suite: string) =>
    pending.filter((p) => p.config.url === '/api/v1/metrics/summary' && suiteOf(p) === suite)

  beforeEach(() => {
    vi.useFakeTimers()
    reset()
    pending = []
    toastError.mockClear()
    api.defaults.adapter = adapter
  })
  afterEach(() => {
    api.defaults.adapter = originalAdapter
    vi.useRealTimers()
  })

  it('aborts the superseded request; the page sees no error and no toast; the existence probe is untouched', async () => {
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    settleScopeNow()

    const { result } = renderHook(
      () => {
        const { suiteFilter } = usePageSuiteFilter()
        // Read during render, as a page does (SWR re-renders on what is read).
        // A scoped hook's fetcher opts in (scopedFetch), as useMetrics does.
        const { data, error } = useSWR(['summary', keyPart(suiteFilter)], () =>
          scopedFetch(() =>
            getData<string>('/api/v1/metrics/summary', { params: { days: 30, ...scopeParam('suite_name', suiteFilter) } }),
          ),
        )
        return { data, error }
      },
      { wrapper },
    )
    // An UNFILTERED existence probe in flight at the same time.
    const probe = getData<string>('/api/v1/runs', { params: { page: 1, size: 1, days: 0 } })
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    const first = findSummary('payments')
    expect(first).toHaveLength(1)
    expect(first[0].config.signal, 'a scope-following request carries a scope signal').toBeDefined()
    const probeReq = pending.find((p) => p.config.url === '/api/v1/runs')
    expect(probeReq?.config.signal, 'an existence probe is never tracked').toBeUndefined()

    // The scope moves on.
    act(() => { useSuiteStore.getState().setActiveSuites(['cart'], PROJECT_A) })
    await act(async () => { await vi.advanceTimersByTimeAsync(SCOPE_SETTLE_MS) })

    expect(first[0].outcome).toBe('aborted')
    expect(probeReq?.outcome).toBe('pending')
    expect(findSummary('cart')).toHaveLength(1)
    expect(result.current.error).toBeUndefined()
    expect(toastError).not.toHaveBeenCalled()

    await act(async () => {
      findSummary('cart')[0].settle('cart-data')
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data).toBe('cart-data')
    expect(result.current.error).toBeUndefined()

    // Back to the scope whose request was aborted: its cached rejection is
    // not shown — it is asked again.
    act(() => { useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A) })
    await act(async () => { await vi.advanceTimersByTimeAsync(SCOPE_SETTLE_MS) })
    expect(result.current.error).toBeUndefined()
    const again = findSummary('payments').filter((p) => p.outcome === 'pending')
    expect(again).toHaveLength(1)
    await act(async () => {
      again[0].settle('payments-data')
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data).toBe('payments-data')
    expect(result.current.error).toBeUndefined()
    expect(toastError).not.toHaveBeenCalled()

    // The probe completes normally.
    probeReq?.settle('probe')
    await expect(probe).resolves.toBe('probe')
    expect(trackedScopeRequestCount()).toBe(0)
  })

  it('a hook that STAYS on an aborted key (its suite is its own) hides the abort and asks again', async () => {
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    settleScopeNow()
    const { result } = renderHook(
      () => {
        // A scoped fetcher whose own suite happens to equal the global selection.
        const { data, error } = useSWR(['own-suite', 'payments'], () =>
          scopedFetch(() => getData<string>('/api/v1/metrics/summary', { params: { suite_name: 'payments' } })),
        )
        return { data, error }
      },
      { wrapper },
    )
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(findSummary('payments')).toHaveLength(1)

    act(() => { useSuiteStore.getState().setActiveSuites(['cart'], PROJECT_A) })
    await act(async () => { await vi.advanceTimersByTimeAsync(SCOPE_SETTLE_MS) })
    expect(findSummary('payments')[0].outcome).toBe('aborted')
    expect(result.current.error).toBeUndefined()
    const retried = findSummary('payments').filter((p) => p.outcome === 'pending')
    expect(retried).toHaveLength(1)
    // Not tracked this time (it no longer matches the settled scope): safe.
    expect(retried[0].config.signal).toBeUndefined()
    await act(async () => {
      retried[0].settle('own')
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.data).toBe('own')
    expect(toastError).not.toHaveBeenCalled()
  })

  it('the rejection is marked superseded (the SWR middleware and the toast policy key on it)', async () => {
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    settleScopeNow()
    const request = scopedFetch(() => getData('/api/v1/metrics/summary', { params: { suite_name: 'payments' } }))
    const caught = request.catch((error: unknown) => error)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    useSuiteStore.getState().setActiveSuites(['cart'], PROJECT_A)
    settleScopeNow()
    expect(isScopeSuperseded(await caught)).toBe(true)
    expect(toastError).not.toHaveBeenCalled()
  })

  it('flag off: nothing is tracked or aborted', async () => {
    useMultiFiltersFlagStore.setState({ enabled: false })
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    settleScopeNow()
    void scopedFetch(() => getData('/api/v1/metrics/summary', { params: { suite_name: 'payments' } })).catch(() => undefined)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(pending[0].config.signal).toBeUndefined()
    useSuiteStore.getState().setActiveSuites(['cart'], PROJECT_A)
    settleScopeNow()
    expect(pending[0].outcome).toBe('pending')
    pending[0].settle('ok')
  })

  it('a page-local suite that is not the global selection is never aborted', async () => {
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    settleScopeNow()
    // Suite detail asks about ITS suite, whatever the global filter says.
    void scopedFetch(() => getData('/api/v1/metrics/suite-detail', { params: { suite_name: 'checkout' } })).catch(() => undefined)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    useSuiteStore.getState().setActiveSuites(['cart'], PROJECT_A)
    settleScopeNow()
    expect(pending[0].outcome).toBe('pending')
    pending[0].settle('ok')
  })
})
