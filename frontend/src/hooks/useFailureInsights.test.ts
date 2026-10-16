/**
 * W07 FAIL-02: `useTimeToFix`, the Failures page's server-measured time to fix.
 *
 * What it must get right: every part of the scope that changes the request
 * (window, suite, top-bar release) is in the SWR key too — a release in the
 * params but not the key would serve one release's numbers under another's
 * name — an unset part is not sent, and All Projects asks without a project
 * (the server answers over every accessible project).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({ getTimeToFix: vi.fn() }))
vi.mock('@/services/analyticsService', () => ({ analyticsService: service }))
const project = vi.hoisted(() => ({ id: 'proj-1' as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: project.id }),
}))

import { TIME_TO_FIX_KEY, useTimeToFix } from './useFailureInsights'
import { useReleaseStore } from '@/store/releaseStore'
import type { TimeToFixResponse } from '@/types/analytics'

const MEASURED: TimeToFixResponse = {
  measured: true, reason: null, median_hours: 43.2, mean_hours: 50.1, fixed: 23, open: 4,
  excluded_flaky_tests: 2, excluded_oscillating_tests: 1, min_fixes: 5, window_days: 30,
}

/** One cache per test, and a probe that lists its keys. */
function wrapper() {
  const cache = new Map()
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
  return { Wrapper, cache }
}

const keysOf = (cache: Map<string, unknown>) => [...cache.keys()].filter(k => k.includes(TIME_TO_FIX_KEY))

describe('useTimeToFix', () => {
  beforeEach(() => {
    project.id = 'proj-1'
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    service.getTimeToFix.mockReset()
    service.getTimeToFix.mockResolvedValue(MEASURED)
  })

  it('asks for the window of the active project and returns the server payload as is', async () => {
    const { Wrapper } = wrapper()
    const { result } = renderHook(() => useTimeToFix(30, null), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toEqual(MEASURED))
    expect(service.getTimeToFix).toHaveBeenCalledTimes(1)
    expect(service.getTimeToFix).toHaveBeenCalledWith('proj-1', { days: 30, releaseIds: null, suiteName: null })
  })

  it('keys on the window, the suite and the release: each change is a new request', async () => {
    const { Wrapper, cache } = wrapper()
    const { result, rerender } = renderHook(
      ({ days, suite }: { days: number; suite: string | null }) => useTimeToFix(days, suite),
      { wrapper: Wrapper, initialProps: { days: 30, suite: null as string | null } },
    )
    await waitFor(() => expect(result.current.data).toBeDefined())

    rerender({ days: 14, suite: null })
    await waitFor(() => expect(service.getTimeToFix).toHaveBeenCalledTimes(2))
    expect(service.getTimeToFix).toHaveBeenLastCalledWith('proj-1', { days: 14, releaseIds: null, suiteName: null })

    rerender({ days: 14, suite: 'Payments' })
    await waitFor(() => expect(service.getTimeToFix).toHaveBeenCalledTimes(3))
    expect(service.getTimeToFix).toHaveBeenLastCalledWith('proj-1', { days: 14, releaseIds: null, suiteName: 'Payments' })

    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    rerender({ days: 14, suite: 'Payments' })
    await waitFor(() => expect(service.getTimeToFix).toHaveBeenCalledTimes(4))
    expect(service.getTimeToFix).toHaveBeenLastCalledWith('proj-1', { days: 14, releaseIds: 'rel-1', suiteName: 'Payments' })

    // Four scopes, four cache entries (the release is in the key, not only the params).
    expect(keysOf(cache)).toHaveLength(4)
  })

  it('in All Projects asks without a project and ignores a stale release', async () => {
    project.id = '__ALL__'
    useReleaseStore.setState({ activeReleaseId: 'rel-1', scopedProjectId: 'proj-1' })
    const { Wrapper } = wrapper()
    const { result } = renderHook(() => useTimeToFix(7), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(service.getTimeToFix).toHaveBeenCalledWith(null, { days: 7, releaseIds: null, suiteName: undefined })
  })

  it('asks nothing before a project resolves', () => {
    project.id = null
    const { Wrapper } = wrapper()
    renderHook(() => useTimeToFix(30), { wrapper: Wrapper })
    expect(service.getTimeToFix).not.toHaveBeenCalled()
  })
})
