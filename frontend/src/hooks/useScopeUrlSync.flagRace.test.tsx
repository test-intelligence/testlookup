/**
 * The `viz_multi_filters` flag has THREE states on the client, not two:
 * unknown (its status request is in flight), on, off. Treating "unknown" as
 * "off" let the legacy single-release machinery run in the window before the
 * flag answered, and it wrote over a saved multi-selection.
 *
 *  A. A saved [R1, R2] survives page load: while the flag is unknown the
 *     legacy `ReleasePicker` loop must not publish `?release=R1` (which the
 *     multi sync then adopts as a one-release link).
 *  B. The flag is per project. On a switch to a project whose status is not
 *     cached yet, the last resolved value is held until the new one arrives —
 *     multi never flickers on -> off -> on (the off window runs the legacy
 *     effects again and collapses the selection).
 *
 * Reviewer proofs A and B (E3 review, 2026-09-22), turned into tests.
 */
import { act, render, renderHook, waitFor } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { Outlet, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'
const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-4000-8000-000000000002'

vi.mock('@/services/releasesService', () => ({
  releasesService: {
    list: vi.fn(async (projectId: string | null) => {
      const items = projectId === PROJECT_A ? [{ id: R1, name: '2026.09' }, { id: R2, name: '2026.10' }] : []
      return { items, total: items.length }
    }),
    get: vi.fn(),
  },
}))
vi.mock('@/services/suitesService', () => ({ suitesService: { list: vi.fn(async () => ({ items: [], total: 0 })) } }))
const flagStatus = vi.hoisted(() => vi.fn())
vi.mock('@/services/featureFlagService', () => ({ featureFlagService: { status: flagStatus, list: vi.fn(async () => []) } }))

import { useScopeUrlSync, useScopeUrlSyncCore } from '@/hooks/useScopeUrlSync'
import { useFeatureFlagStatus } from '@/hooks/useFeatureFlags'
import { ReleasePicker } from '@/components/layout/ReleasePicker'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { useSuiteStore } from '@/store/suiteStore'

let router: ReturnType<typeof createMemoryRouter>
const releaseParams = () => router.state.location.search === ''
  ? []
  : new URLSearchParams(router.state.location.search).getAll('release')

function mount(Host: () => ReactElement, initial: string) {
  router = createMemoryRouter(
    [{ path: '/', element: <Host />, children: [{ path: 'trends', element: <div>trends</div> }] }],
    { initialEntries: [initial] },
  )
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  )
}

const tick = (ms = 50) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })

beforeEach(() => {
  localStorage.clear()
  flagStatus.mockReset()
  // The app's real starting point: the flag has not answered yet.
  useMultiFiltersFlagStore.setState({ enabled: false, resolved: false })
  useProjectStore.setState({ activeProjectId: PROJECT_A })
  useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
})

describe('A: the flag answers after the release list', () => {
  it('keeps a saved two-release selection: nothing legacy runs while the flag is unknown', async () => {
    useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A)
    function Host() {
      useScopeUrlSyncCore()
      return (<><ReleasePicker /><Outlet /></>)
    }
    mount(Host, '/trends')
    // Long enough for the release list to load and every effect to run.
    await tick(100)
    expect(releaseParams(), 'the legacy loop must not publish ?release=R1 while the flag is unknown').toEqual([])
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])

    await act(async () => { useMultiFiltersFlagStore.getState().setEnabled(true) })
    await waitFor(() => expect(releaseParams()).toEqual([R1, R2]))
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])
  })

  it('flag resolved OFF: the legacy loop runs exactly as before (publishes the scalar)', async () => {
    useReleaseStore.getState().setActiveRelease(R1, PROJECT_A)
    function Host() {
      useScopeUrlSyncCore()
      return (<><ReleasePicker /><Outlet /></>)
    }
    mount(Host, '/trends')
    await tick(50)
    expect(releaseParams()).toEqual([])
    await act(async () => { useMultiFiltersFlagStore.getState().setEnabled(false) })
    await waitFor(() => expect(releaseParams()).toEqual([R1]))
  })
})

describe('B: the flag is held across a project switch', () => {
  it('multi stays on across a switch to a project whose flag is not cached yet', async () => {
    flagStatus.mockImplementation(async () => {
      await new Promise((r) => setTimeout(r, 20))
      return { key: 'viz_multi_filters', enabled: true }
    })
    const seen: boolean[] = []
    const unsubscribe = useMultiFiltersFlagStore.subscribe((s) => { seen.push(s.enabled) })
    function Host() {
      useScopeUrlSync()
      return <Outlet />
    }
    mount(Host, '/trends')
    await waitFor(() => expect(useMultiFiltersFlagStore.getState().enabled).toBe(true))
    seen.length = 0
    await act(async () => { useProjectStore.setState({ activeProjectId: PROJECT_B }) })
    await waitFor(() => expect(flagStatus).toHaveBeenCalledWith('viz_multi_filters', PROJECT_B))
    await tick(60)
    unsubscribe()
    expect(useMultiFiltersFlagStore.getState().enabled).toBe(true)
    expect(seen, 'the flag must not read false while project B is resolving').not.toContain(false)
  })

  it('a project whose flag is OFF does turn it off once its answer arrives', async () => {
    flagStatus.mockImplementation(async (_key: string, projectId: string | null) => ({
      key: 'viz_multi_filters',
      enabled: projectId === PROJECT_A,
    }))
    function Host() {
      useScopeUrlSync()
      return <Outlet />
    }
    mount(Host, '/trends')
    await waitFor(() => expect(useMultiFiltersFlagStore.getState().enabled).toBe(true))
    await act(async () => { useProjectStore.setState({ activeProjectId: PROJECT_B }) })
    await waitFor(() => expect(useMultiFiltersFlagStore.getState().enabled).toBe(false))
    expect(useMultiFiltersFlagStore.getState().resolved).toBe(true)
  })

  it('stays UNKNOWN while the first answer is in flight; a failed status request resolves to off', async () => {
    let fail: (e: Error) => void = () => undefined
    flagStatus.mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    function Host() {
      useScopeUrlSync()
      return <Outlet />
    }
    mount(Host, '/trends')
    await tick(30)
    expect(useMultiFiltersFlagStore.getState().resolved).toBe(false)
    await act(async () => { fail(new Error('boom')) })
    await waitFor(() => expect(useMultiFiltersFlagStore.getState().resolved).toBe(true))
    expect(useMultiFiltersFlagStore.getState().enabled).toBe(false)
  })
})

describe('useFeatureFlagStatus', () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
  )
  it('is undefined (unknown) while loading, never false', async () => {
    let answer: (v: unknown) => void = () => undefined
    flagStatus.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    const { result } = renderHook(() => useFeatureFlagStatus('viz_multi_filters'), { wrapper })
    expect(result.current).toBeUndefined()
    await act(async () => { answer({ key: 'viz_multi_filters', enabled: false }) })
    await waitFor(() => expect(result.current).toBe(false))
  })
})
