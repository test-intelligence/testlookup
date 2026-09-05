/**
 * `useSummaryReport` carries the global release filter.
 *
 * Reported from the deployment: on `/reports/summary?release=unattributed` the
 * filter changed nothing. The cause was not a scoping bug — the release had no
 * path to that page at all. `useSummaryReport` sent `project_id`, `days` and
 * `mode`; `/api/v1/reports/summary` and `build_summary_report` did not accept a
 * release on either side. S4a put six analytics endpoints on the release axis
 * and this page was not one of them.
 *
 * Two properties, and they fail differently
 * -----------------------------------------
 * The release has to reach the REQUEST, or the server answers project-wide.
 * And it has to be in the SWR KEY, or the first render after a selection is
 * served from the cached all-releases report — the page then shows unfiltered
 * numbers under a release filter until the next revalidation, which looks
 * exactly like the bug that was reported.
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

vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: { get: vi.fn(async () => ({ suites: [] })) },
}))

import { summaryReportService } from '@/services/summaryReportService'
import { useSummaryReport } from './useSummaryReport'
import { useReleaseStore } from '@/store/releaseStore'

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const REL_1 = 'rrrrrrrr-0000-0000-0000-000000000001'
const REL_2 = 'rrrrrrrr-0000-0000-0000-000000000002'

const get = vi.mocked(summaryReportService.get)

/** A fresh SWR cache per test.
 *
 * SWR's cache is module-global, so without a provider one test's response is
 * served to the next and a key change is indistinguishable from a cache hit —
 * which is the very thing these tests are checking.
 */
const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
    {children}
  </SWRConfig>
)

beforeEach(() => {
  get.mockClear()
  mocked.projectState.activeProjectId = PROJECT_A
  useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
})

describe('useSummaryReport — the release axis', () => {
  it('sends no release when none is selected', async () => {
    // NFR1: omitting the release must leave the request exactly as it was, so
    // every existing caller is unchanged and the backend's conditional
    // fragment stays out of the SQL.
    renderHook(() => useSummaryReport({ days: 30, mode: 'window' }), { wrapper })

    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(get.mock.calls[0][0].release_id ?? null).toBeNull()
  })

  it('sends the selected release', async () => {
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    renderHook(() => useSummaryReport({ days: 30, mode: 'window' }), { wrapper })

    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(
      get.mock.calls[0][0].release_id,
      'the summary report was requested project-wide under a release filter',
    ).toBe(REL_1)
  })

  it('refetches when the release changes', async () => {
    // The SWR KEY, not just the request. Without the release in the deps the
    // hook serves the previous report from cache and the page shows the old
    // numbers under the new filter.
    useReleaseStore.getState().setActiveRelease(REL_1, PROJECT_A)

    const { rerender } = renderHook(
      () => useSummaryReport({ days: 30, mode: 'window' }),
      { wrapper },
    )
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1))

    useReleaseStore.getState().setActiveRelease(REL_2, PROJECT_A)
    rerender()

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    expect(get.mock.calls[1][0].release_id).toBe(REL_2)
  })

  it('drops the release when it belongs to another project', async () => {
    // `useReleaseScope` enforces this: a release id means nothing in a project
    // that does not own it, and filtering by one would empty the report while
    // the picker still showed a release name.
    useReleaseStore.getState().setActiveRelease(REL_1, 'some-other-project')

    renderHook(() => useSummaryReport({ days: 30, mode: 'window' }), { wrapper })

    await waitFor(() => expect(get).toHaveBeenCalled())
    expect(get.mock.calls[0][0].release_id ?? null).toBeNull()
  })
})
