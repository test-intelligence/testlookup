/**
 * The report-scope URL sync and the rest of the `viz_multi_filters` UI are a
 * LAZY chunk (`multiFiltersRuntime.ts`) behind an eager gate, to keep the
 * eager bundle within its budget. Two properties that split must keep:
 *
 *  1. Flag OFF (or unknown) never downloads the runtime — the flag-off
 *     session pays nothing for the feature.
 *  2. Flag ON never reads 'on' before the sync is mounted. If 'on' were
 *     published first, the legacy ReleasePicker loop would stand down with
 *     nothing owning the URL — and if the legacy loop ran instead, it would
 *     publish `?release=<first id>` and collapse a saved multi-selection
 *     (E3 review, proof A). Lazy loading must not reopen that window.
 *
 * The runtime import is observed through `vi.mock` of the runtime module: its
 * factory runs only when something actually imports the module.
 */
import { act, render, waitFor } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'
const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'

vi.mock('@/services/releasesService', () => ({
  releasesService: {
    list: vi.fn(async () => ({ items: [{ id: R1, name: '2026.09' }, { id: R2, name: '2026.10' }], total: 2 })),
    get: vi.fn(),
  },
}))
vi.mock('@/services/suitesService', () => ({ suitesService: { list: vi.fn(async () => ({ items: [], total: 0 })) } }))
const flagStatus = vi.hoisted(() => vi.fn())
vi.mock('@/services/featureFlagService', () => ({ featureFlagService: { status: flagStatus, list: vi.fn(async () => []) } }))

const runtimeImported = vi.hoisted(() => vi.fn())
vi.mock('./multiFiltersRuntime', async (importOriginal) => {
  runtimeImported()
  return importOriginal()
})

import ScopeUrlSyncGate from './ScopeUrlSyncGate'
import { ReleasePicker } from './ReleasePicker'
import { useMultiFiltersRuntimeStore } from './multiFiltersRuntimeLoader'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { selectReleaseIds, useReleaseStore } from '@/store/releaseStore'

let router: ReturnType<typeof createMemoryRouter>
/** Every `release` value list the address bar has shown, in order. */
let releaseHistory: string[][]

function mount() {
  router = createMemoryRouter(
    [{
      path: '/trends',
      element: (
        <>
          <ScopeUrlSyncGate />
          <ReleasePicker />
        </>
      ),
    }],
    { initialEntries: ['/trends'] },
  )
  releaseHistory = []
  router.subscribe((state) => {
    releaseHistory.push(new URLSearchParams(state.location.search).getAll('release'))
  })
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
  useMultiFiltersFlagStore.setState({ enabled: false, resolved: false })
  useProjectStore.setState({ activeProjectId: PROJECT_A })
  useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
})

// Order matters: the runtime, once imported, stays in the module cache, so
// the flag-off cases run before the flag-on one.
describe('ScopeUrlSyncGate', () => {
  it('flag OFF: publishes off and never imports the multi-filters runtime', async () => {
    flagStatus.mockResolvedValue({ key: 'viz_multi_filters', enabled: false })
    useReleaseStore.getState().setActiveRelease(R1, PROJECT_A)
    mount()
    await waitFor(() => expect(useMultiFiltersFlagStore.getState().resolved).toBe(true))
    expect(useMultiFiltersFlagStore.getState().enabled).toBe(false)
    // The legacy loop runs exactly as before: it publishes the scalar.
    await waitFor(() => expect(router.state.location.search).toBe(`?release=${R1}`))
    await tick()
    expect(runtimeImported).not.toHaveBeenCalled()
    expect(useMultiFiltersRuntimeStore.getState().runtime).toBeNull()
  })

  it('flag UNKNOWN: publishes nothing and imports nothing', async () => {
    flagStatus.mockImplementation(() => new Promise(() => undefined))
    mount()
    await tick(80)
    expect(useMultiFiltersFlagStore.getState().resolved).toBe(false)
    expect(runtimeImported).not.toHaveBeenCalled()
  })

  it('flag ON: loads the runtime, and "on" is published only once the sync is loaded', async () => {
    flagStatus.mockResolvedValue({ key: 'viz_multi_filters', enabled: true })
    useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A)
    const runtimeAtOn: boolean[] = []
    const unsubscribe = useMultiFiltersFlagStore.subscribe((s, prev) => {
      if (s.enabled && !prev.enabled) runtimeAtOn.push(useMultiFiltersRuntimeStore.getState().runtime !== null)
    })
    mount()
    await waitFor(() => expect(useMultiFiltersFlagStore.getState().enabled).toBe(true))
    unsubscribe()
    expect(runtimeImported).toHaveBeenCalledTimes(1)
    expect(runtimeAtOn).toEqual([true])
    // The saved two-release selection survives and is published whole.
    await waitFor(() => expect(router.state.location.search).toContain(R2))
    expect(new URLSearchParams(router.state.location.search).getAll('release').sort()).toEqual([R1, R2])
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])
    expect(releaseHistory, 'no URL may carry a one-release collapse of the selection').not.toContainEqual([R1])
  })
})
