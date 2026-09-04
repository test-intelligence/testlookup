/**
 * `useRuns` carries the global release filter.
 *
 * This hook feeds the run lists on /overview, /trends, /coverage and
 * /failures, so it is the single largest remaining unscoped surface. Before
 * this, picking a release changed the KPI cards and the trend chart on
 * /overview while the run list beneath them kept showing every run in the
 * project — the same screen answering one question two ways.
 *
 * The composition rule is the interesting part: a page that is ALREADY about
 * one release passes its own `release_id`, and that must win over the header
 * picker rather than being silently overridden by it.
 */
import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
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

vi.mock('@/services/runsService', () => ({
  runsService: { list: vi.fn(async () => ({ items: [], total: 0 })) },
}))

import { runsService } from '@/services/runsService'
import { useRuns } from './useRuns'
import { useReleaseStore } from '@/store/releaseStore'

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const REL_1 = 'rrrrrrrr-0000-0000-0000-000000000001'
const REL_2 = 'rrrrrrrr-0000-0000-0000-000000000002'

const list = vi.mocked(runsService.list)

/** Private SWR cache per render — the module-global default would serve an
 *  earlier test's entry and make "did it refetch?" depend on test order. */
function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
  )
}

const lastParams = () => list.mock.calls[list.mock.calls.length - 1][1] as Record<string, unknown>

describe('useRuns release scoping', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    mocked.projectState = { activeProjectId: PROJECT_A }
    vi.clearAllMocks()
  })

  it('omits release_id when no release is selected', async () => {
    renderHook(() => useRuns({ days: 30 }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    // NFR1: the request a caller makes without a release must be the one they
    // made before this axis existed.
    expect(lastParams()).not.toHaveProperty('release_id')
  })

  it('sends the selected release', async () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    renderHook(() => useRuns({ days: 30 }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(lastParams().release_id).toBe(REL_1)
  })

  it('refetches when the release changes rather than serving the previous list', async () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    const { rerender } = renderHook(() => useRuns({ days: 30 }), { wrapper })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))

    useReleaseStore.setState({ activeReleaseId: REL_2, scopedProjectId: PROJECT_A })
    rerender()

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(lastParams().release_id).toBe(REL_2)
  })

  it("lets a caller's explicit release win over the header picker", async () => {
    // A page already scoped to one release — a release detail view, say —
    // must not have its subject silently replaced by whatever the header
    // happens to hold.
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    renderHook(() => useRuns({ days: 30, release_id: REL_2 }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(lastParams().release_id).toBe(REL_2)
  })

  it('keeps the caller other params intact', async () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    renderHook(() => useRuns({ days: 7, status: 'failed' }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    // The release axis is additive. Displacing the window or the status would
    // answer a question the caller never asked.
    expect(lastParams()).toMatchObject({ days: 7, status: 'failed', release_id: REL_1 })
  })
})
