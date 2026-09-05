/**
 * `/my-failures` had a release filter that did nothing.
 *
 * The backend gained `release_id` during the epic — the changelog entry says
 * "`/my-failures` … now honour the release" — and the frontend hook was never
 * updated. So the picker sat in the header, visible on the page, changing
 * nothing. An INERT filter is worse than an absent one: the reader takes the
 * list as "2.4.0's failures assigned to me" when it is the project's.
 *
 * `useMyFailuresCount` is scoped alongside it for consistency of the pair, NOT
 * because the sidebar badge would otherwise disagree — an earlier draft of this
 * file claimed that and it is wrong. The sidebar renders
 * `useMyFailuresCountUnscoped`, which spans every project and every release on
 * purpose. `useMyFailuresCount` currently has no caller in the app at all; it
 * is kept correct so that wiring it later cannot reintroduce the inert filter
 * this file exists for.
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

vi.mock('@/services/myFailuresService', () => ({
  myFailuresService: {
    list: vi.fn(async () => ({ items: [], total: 0 })),
    count: vi.fn(async () => ({ count: 0 })),
  },
}))

import { myFailuresService } from '@/services/myFailuresService'
import { useMyFailures, useMyFailuresCount, useMyFailuresCountUnscoped } from './useMyFailures'
import { useReleaseStore } from '@/store/releaseStore'

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const REL_1 = 'rrrrrrrr-0000-0000-0000-000000000001'
const REL_2 = 'rrrrrrrr-0000-0000-0000-000000000002'

const list = vi.mocked(myFailuresService.list)
const count = vi.mocked(myFailuresService.count)

/** Private SWR cache per render — the module-global default would serve an
 *  earlier test's entry and make "did it refetch?" depend on test order. */
function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
  )
}

const lastListParams = () => list.mock.calls[list.mock.calls.length - 1][0]
const lastCountParams = () => count.mock.calls[count.mock.calls.length - 1][0]

beforeEach(() => {
  localStorage.clear()
  useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
  mocked.projectState = { activeProjectId: PROJECT_A }
  list.mockClear()
  count.mockClear()
})

describe('useMyFailures — the release the picker is showing', () => {
  it('sends the selected release', async () => {
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    renderHook(() => useMyFailures({ days: 30, page: 1, size: 25 }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(
      lastListParams().release_id,
      'the inbox was requested project-wide while a release was selected',
    ).toBe(REL_1)
  })

  it('sends no release when none is selected', async () => {
    // NFR1: omitting it must leave the request exactly as it was, so the
    // backend's conditional fragment stays out of the SQL.
    renderHook(() => useMyFailures({ days: 30 }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(lastListParams().release_id ?? null).toBeNull()
  })

  it('drops a release belonging to another project', async () => {
    // `useReleaseScope` enforces this: a release id means nothing in a project
    // that does not own it, and filtering by one would empty the inbox while
    // the picker still showed a release name.
    useReleaseStore.getState().setActiveRelease(REL_1, 'some-other-project')

    renderHook(() => useMyFailures({ days: 30 }), { wrapper })

    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(lastListParams().release_id ?? null).toBeNull()
  })

  it('refetches when the release changes', async () => {
    // The SWR key, not just the request. Without the release in the deps the
    // hook serves the previous release's page from cache under the new
    // release's name.
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    const { rerender } = renderHook(() => useMyFailures({ days: 30 }), { wrapper })
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1))

    useReleaseStore.getState().setActiveRelease(REL_2, PROJECT_A)
    rerender()

    await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
    expect(lastListParams().release_id).toBe(REL_2)
  })

  it('still sends the parameters it always sent', async () => {
    // The control: a param builder that dropped everything would satisfy the
    // omission test above.
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    renderHook(() => useMyFailures({ days: 14, page: 3, size: 50, scope: 'team' }), {
      wrapper,
    })

    await waitFor(() => expect(list).toHaveBeenCalled())
    const p = lastListParams()
    expect(p.days).toBe(14)
    expect(p.page).toBe(3)
    expect(p.size).toBe(50)
    expect(p.scope).toBe('team')
  })
})

describe('useMyFailuresCount — kept consistent with the list', () => {
  it('sends the same release the list does', async () => {
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    renderHook(
      () => {
        useMyFailures({ days: 30 })
        useMyFailuresCount({ days: 30 })
      },
      { wrapper },
    )

    await waitFor(() => expect(count).toHaveBeenCalled())
    expect(
      lastCountParams().release_id,
      'the count hook answers a different question from the list it mirrors',
    ).toBe(lastListParams().release_id)
  })

  it('sends none when none is selected', async () => {
    renderHook(() => useMyFailuresCount({ days: 30 }), { wrapper })

    await waitFor(() => expect(count).toHaveBeenCalled())
    expect(lastCountParams().release_id ?? null).toBeNull()
  })
})

describe('useMyFailuresCountUnscoped — deliberately left alone', () => {
  it('never sends a release, even when one is selected', async () => {
    // This badge spans every project. A release belongs to one project, so
    // filtering a cross-project count by it would answer a question nobody
    // asked. The control that stops "scope everything" being applied blindly.
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    renderHook(() => useMyFailuresCountUnscoped({ days: 30 }), { wrapper })

    await waitFor(() => expect(count).toHaveBeenCalled())
    expect(lastCountParams().release_id ?? null).toBeNull()
    expect(lastCountParams().project_id ?? null).toBeNull()
  })
})
