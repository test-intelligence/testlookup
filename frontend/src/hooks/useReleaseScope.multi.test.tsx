/**
 * VIZ-303 behind `viz_multi_filters`: the release scope becomes a list.
 *
 *  1. Flag OFF is today's scalar — asserted here as well as by the unmodified
 *     `useReleaseScope.test.tsx`.
 *  2. Flag ON: one release still reaches the service as the SCALAR (the wire
 *     rule), several as a sorted array.
 *  3. SWR keys carry a sorted joined STRING. An array rebuilt each render in a
 *     key or dep list would change identity every render — a refetch loop.
 *  4. Existence probes never receive the release filter, list or not.
 *  5. `/runs` and `/me/assigned-failures` take the list too (E3: both declare
 *     repeatable `release_id` / `suite_name`), with the same wire rule; the
 *     wire itself (repeated keys, never `[]=`) is `useRuns.wire.test.tsx`.
 */
import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocked = vi.hoisted(() => ({
  projectState: { activeProjectId: null as string | null },
}))

vi.mock('@/store/projectStore', async importOriginal => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof mocked.projectState) => unknown) =>
      selector ? selector(mocked.projectState) : mocked.projectState,
  }
})

vi.mock('@/services/analyticsService', () => ({
  analyticsService: {
    getCoverage: vi.fn(async () => ({ items: [] })),
    getFlakyTests: vi.fn(async () => ({ items: [] })),
    getFailureCategories: vi.fn(async () => ({ items: [] })),
    getTopFailing: vi.fn(async () => ({ items: [] })),
    getSuiteDetail: vi.fn(async () => ({ items: [] })),
  },
}))
vi.mock('@/services/metricsService', () => ({
  metricsService: {
    getSummary: vi.fn(async () => ({})),
    getTrends: vi.fn(async () => ({ data: [] })),
  },
}))
vi.mock('@/services/runsService', () => ({
  runsService: { list: vi.fn(async () => ({ items: [], total: 0 })) },
}))
vi.mock('@/services/myFailuresService', () => ({
  myFailuresService: {
    list: vi.fn(async () => ({ items: [], total: 0 })),
    count: vi.fn(async () => ({ count: 0 })),
  },
}))

import { analyticsService } from '@/services/analyticsService'
import { metricsService } from '@/services/metricsService'
import { runsService } from '@/services/runsService'
import { myFailuresService } from '@/services/myFailuresService'
import { useCoverage, useDashboardSummary, useSuiteDetail, useTrendData } from './useMetrics'
import { useReleaseScope } from './useReleaseScope'
import { useRuns } from './useRuns'
import { useMyFailures } from './useMyFailures'
import { useReleaseStore } from '@/store/releaseStore'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { settleScopeNow } from '@/store/settledScope'

const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'

function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
  )
}

const getCoverage = vi.mocked(analyticsService.getCoverage)
const getSuiteDetail = vi.mocked(analyticsService.getSuiteDetail)
const getSummary = vi.mocked(metricsService.getSummary)
const runsList = vi.mocked(runsService.list)

/** The selection is `ids`, SETTLED: these tests are about what a settled
 *  scope sends, not the 250 ms debounce (store/settledScope.test.tsx). */
function select(ids: string[]) {
  useReleaseStore.getState().setActiveReleases(ids, PROJECT_A)
  settleScopeNow()
}

describe('useReleaseScope with viz_multi_filters', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
    settleScopeNow()
    useMultiFiltersFlagStore.setState({ enabled: false })
    mocked.projectState = { activeProjectId: PROJECT_A }
    vi.clearAllMocks()
  })

  it('flag off: the scalar first id, exactly as before — even holding a list', () => {
    select([R2, R1])
    const { result } = renderHook(() => useReleaseScope())
    expect(result.current).toBe(R2)
  })

  it('flag on: the whole selection, sorted', () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    select([R2, R1])
    const { result } = renderHook(() => useReleaseScope())
    expect(result.current).toEqual([R1, R2])
  })

  it('flag on: null — not [] — with nothing selected', () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    const { result } = renderHook(() => useReleaseScope())
    expect(result.current).toBeNull()
  })

  it('flag on: the returned list keeps its identity across renders', () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    select([R2, R1])
    const { result, rerender } = renderHook(() => useReleaseScope())
    const first = result.current
    rerender()
    rerender()
    expect(result.current).toBe(first)
  })

  it('flag on: one release reaches the service as the legacy SCALAR', async () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    select([R1])
    renderHook(() => useCoverage(30), { wrapper })
    await waitFor(() => expect(getCoverage).toHaveBeenCalled())
    expect(getCoverage.mock.calls[0][3]).toBe(R1)
  })

  it('flag on: several releases reach the service as a sorted list', async () => {
    useMultiFiltersFlagStore.setState({ enabled: true })
    select([R2, R1])
    renderHook(() => useCoverage(30), { wrapper })
    await waitFor(() => expect(getCoverage).toHaveBeenCalled())
    expect(getCoverage.mock.calls[0][3]).toEqual([R1, R2])
  })
})

describe('SWR key stability (no refetch per render)', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
    settleScopeNow()
    useMultiFiltersFlagStore.setState({ enabled: true })
    mocked.projectState = { activeProjectId: PROJECT_A }
    vi.clearAllMocks()
  })

  it('re-rendering with the same multi-selection and suite list fetches ONCE', async () => {
    select([R2, R1])
    // A fresh suite array every render — exactly what a page hands in.
    const { rerender } = renderHook(() => useDashboardSummary(30, ['payments', 'cart']), { wrapper })
    await waitFor(() => expect(getSummary).toHaveBeenCalledTimes(1))
    for (let i = 0; i < 5; i++) rerender()
    await new Promise(r => setTimeout(r, 30))
    expect(getSummary).toHaveBeenCalledTimes(1)
  })

  it('no SWR key carries an array — the release list is ONE sorted joined string', async () => {
    select([R2, R1])
    const keys: unknown[] = []
    const recordKeys = (useSWRNext: (...a: unknown[]) => unknown) =>
      (key: unknown, ...rest: unknown[]) => {
        keys.push(key)
        return useSWRNext(key, ...rest)
      }
    function recordingWrapper({ children }: { children: ReactNode }) {
      return (
        <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, use: [recordKeys as never] }}>
          {children}
        </SWRConfig>
      )
    }
    renderHook(
      () => {
        useCoverage(30, ['payments', 'cart'])
        useSuiteDetail('Checkout', 30)
      },
      { wrapper: recordingWrapper },
    )
    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalled())
    const parts = keys.filter(Array.isArray).flat()
    expect(parts.length).toBeGreaterThan(0)
    for (const part of parts) expect(Array.isArray(part), `key part ${String(part)}`).toBe(false)
    // The release list is present — as one string, in sorted order.
    expect(parts).toContain([R1, R2].join('\u001f'))
  })

  it('reordering the same selection is the same key', async () => {
    select([R2, R1])
    const { rerender } = renderHook(() => useTrendData(30), { wrapper })
    await waitFor(() => expect(vi.mocked(metricsService.getTrends)).toHaveBeenCalledTimes(1))
    act(() => select([R1, R2]))
    rerender()
    await new Promise(r => setTimeout(r, 30))
    expect(vi.mocked(metricsService.getTrends)).toHaveBeenCalledTimes(1)
  })

  it('useSuiteDetail (hand-built key) fetches once across renders and refetches on a real change', async () => {
    select([R2, R1])
    const { rerender } = renderHook(() => useSuiteDetail('Checkout', 30), { wrapper })
    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalledTimes(1))
    for (let i = 0; i < 5; i++) rerender()
    await new Promise(r => setTimeout(r, 30))
    expect(getSuiteDetail).toHaveBeenCalledTimes(1)
    expect(getSuiteDetail.mock.calls[0][3]).toEqual([R1, R2])

    act(() => select([R1]))
    rerender()
    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalledTimes(2))
    expect(getSuiteDetail.mock.calls[1][3]).toBe(R1)
  })
})

describe('existence probes never see the list; /runs and /me/assigned-failures do', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
    settleScopeNow()
    useMultiFiltersFlagStore.setState({ enabled: true })
    mocked.projectState = { activeProjectId: PROJECT_A }
    vi.clearAllMocks()
  })

  it('the "ever had a run?" probe (ignoreGlobalRelease) carries no release under a multi-selection', async () => {
    select([R1, R2])
    renderHook(() => useRuns({ page: 1, size: 1 }, { ignoreGlobalRelease: true }), { wrapper })
    await waitFor(() => expect(runsList).toHaveBeenCalled())
    const params = runsList.mock.calls[0][1] as Record<string, unknown>
    expect(params).not.toHaveProperty('release_id')
    expect(params).not.toHaveProperty('suite_name')
  })

  it('the probe carries no release under a SINGLE selection either', async () => {
    select([R1])
    renderHook(() => useRuns({ page: 1, size: 1 }, { ignoreGlobalRelease: true }), { wrapper })
    await waitFor(() => expect(runsList).toHaveBeenCalled())
    expect(runsList.mock.calls[0][1]).not.toHaveProperty('release_id')
  })

  it('the suite-integrity diagnostic (releaseScoped: false) carries no release list', async () => {
    select([R1, R2])
    renderHook(() => useSuiteDetail('Checkout', 30, { releaseScoped: false }), { wrapper })
    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalled())
    expect(getSuiteDetail.mock.calls[0][3] ?? null).toBeNull()
  })

  it('/runs gets the scalar for one release and the sorted list for several', async () => {
    select([R1])
    const one = renderHook(() => useRuns({ page: 1 }), { wrapper })
    await waitFor(() => expect(runsList).toHaveBeenCalledTimes(1))
    expect((runsList.mock.calls[0][1] as Record<string, unknown>).release_id).toBe(R1)
    one.unmount()

    runsList.mockClear()
    select([R2, R1])
    renderHook(() => useRuns({ page: 1 }), { wrapper })
    await waitFor(() => expect(runsList).toHaveBeenCalledTimes(1))
    expect((runsList.mock.calls[0][1] as Record<string, unknown>).release_id).toEqual([R1, R2])
  })

  it('/runs gets a scalar suite for one and the sorted list for several', async () => {
    const one = renderHook(() => useRuns({ page: 1, suite_name: ['payments'] }), { wrapper })
    await waitFor(() => expect(runsList).toHaveBeenCalledTimes(1))
    expect((runsList.mock.calls[0][1] as Record<string, unknown>).suite_name).toBe('payments')
    one.unmount()

    runsList.mockClear()
    renderHook(() => useRuns({ page: 1, suite_name: ['payments', 'cart'] }), { wrapper })
    await waitFor(() => expect(runsList).toHaveBeenCalledTimes(1))
    expect((runsList.mock.calls[0][1] as Record<string, unknown>).suite_name).toEqual(['cart', 'payments'])
  })

  it('/me/assigned-failures gets every selected release', async () => {
    select([R2, R1])
    renderHook(() => useMyFailures({ days: 30 }), { wrapper })
    await waitFor(() => expect(vi.mocked(myFailuresService.list)).toHaveBeenCalled())
    expect(vi.mocked(myFailuresService.list).mock.calls[0][0].release_id).toEqual([R1, R2])
  })
})
