/**
 * The release filter, from the store to the wire.
 *
 * S5-1 built the store and S5-2 built the picker, but nothing read either — the
 * control changed state and every page kept rendering unfiltered data. A filter
 * that appears to work and does nothing is worse than an absent one, because
 * the reader trusts the number in front of them.
 *
 * These tests cover the three ways that wiring goes wrong:
 *
 *  1. The id never reaches the request, so the filter is decorative.
 *  2. The id reaches the request but NOT the SWR key, so switching releases
 *     serves the previous release's cached response under the new name — the
 *     worst outcome, because it is confidently wrong rather than empty.
 *  3. The id is sent when it must not be — in All Projects mode, where it would
 *     filter every project by one project's release, and where NFR1 requires the
 *     request be byte-identical to its pre-release-axis form.
 */
import type { ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import type { Mock } from 'vitest'
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

import { analyticsService } from '@/services/analyticsService'
import {
  useCoverage,
  useFailureCategories,
  useFlakyTests,
  useSuiteDetail,
  useTopFailing,
} from './useMetrics'
import { useReleaseScope } from './useReleaseScope'
import { useReleaseStore } from '@/store/releaseStore'
import { ALL_PROJECTS_ID } from '@/store/projectStore'

const PROJECT_A = 'aaaaaaaa-0000-0000-0000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-0000-0000-000000000002'
const REL_1 = 'rrrrrrrr-0000-0000-0000-000000000001'
const REL_2 = 'rrrrrrrr-0000-0000-0000-000000000002'

const getCoverage = vi.mocked(analyticsService.getCoverage)
const getSuiteDetail = vi.mocked(analyticsService.getSuiteDetail)

/**
 * A private SWR cache per render.
 *
 * SWR's default cache is module-global, so a key already fetched by an earlier
 * test is served from memory and the fetcher is never called. That makes a
 * "did it refetch?" assertion depend on which tests ran before it — and, worse,
 * would let a genuinely broken cache key PASS here simply because some other
 * test had populated the entry. `dedupingInterval: 0` disables the 2s window
 * for the same reason.
 */
function wrapper({ children }: { children: ReactNode }) {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
  )
}

describe('useReleaseScope', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    mocked.projectState = { activeProjectId: PROJECT_A }
    vi.clearAllMocks()
  })

  it('is null when nothing is selected', () => {
    const { result } = renderHook(() => useReleaseScope())
    expect(result.current).toBeNull()
  })

  it('returns the selection when a single project is pinned', () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    const { result } = renderHook(() => useReleaseScope())
    expect(result.current).toBe(REL_1)
  })

  it('refuses to scope in All Projects mode, even holding a selection', () => {
    // The picker disables itself here, but a disabled control is a UI
    // convention, not an enforcement — the store can still hold a value from
    // before the switch. Sending it would filter EVERY project's data by ONE
    // project's release: not a narrower answer, a wrong one.
    mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    const { result } = renderHook(() => useReleaseScope())

    expect(result.current).toBeNull()
  })

  it('refuses to scope before a project has resolved', () => {
    mocked.projectState = { activeProjectId: null }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    const { result } = renderHook(() => useReleaseScope())

    expect(result.current).toBeNull()
  })

  it('refuses All Projects even when the pairing agrees', () => {
    // Mutation testing showed the pairing check below had made the
    // All-Projects clause unfalsifiable: in every other case the two guards
    // catch the same states, so removing the All-Projects one changed nothing
    // and no test noticed.
    //
    // This is the one state that separates them — a selection recorded AGAINST
    // the sentinel, which the store's own API does not produce today but is
    // reachable by any direct `setActiveRelease` caller. Releases cannot be
    // enumerated across projects, so there is nothing for the id to mean here
    // regardless of what it is paired with.
    mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: ALL_PROJECTS_ID })

    const { result } = renderHook(() => useReleaseScope())

    expect(result.current).toBeNull()
  })

  it('refuses to scope project B by a release belonging to project A', () => {
    // The window between a project switch and reconciliation. `ReleasePicker`
    // clears an orphaned selection from a passive `useEffect`, but SWR
    // revalidates from a LAYOUT effect, which runs first in the same commit —
    // so for one render `activeProjectId` is already B while the store still
    // holds A's release, and every mounted hook would fire a request for
    // project B scoped by project A's release. A 403 on that request toasts.
    //
    // Depending on the recorded pairing instead of on effect ordering is what
    // makes this deterministic.
    mocked.projectState = { activeProjectId: PROJECT_B }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    const { result } = renderHook(() => useReleaseScope())

    expect(result.current).toBeNull()
  })
})

describe('the release reaches the request', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    mocked.projectState = { activeProjectId: PROJECT_A }
    vi.clearAllMocks()
  })

  it('omits release_id entirely when no release is selected (NFR1)', async () => {
    renderHook(() => useCoverage(30), { wrapper })

    await waitFor(() => expect(getCoverage).toHaveBeenCalled())
    // Not `null`, not `''` — ABSENT. The guarantee is that a caller asking for
    // no release produces the exact request it produced before this axis
    // existed, and a trailing `release_id=` is not that request.
    const releaseArg = getCoverage.mock.calls[0][3]
    expect(releaseArg ?? null).toBeNull()
  })

  it('sends the selected release', async () => {
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    renderHook(() => useCoverage(30), { wrapper })

    await waitFor(() => expect(getCoverage).toHaveBeenCalled())
    expect(getCoverage.mock.calls[0][3]).toBe(REL_1)
  })

  it('refetches when the release changes rather than serving the previous one', async () => {
    // The cache-poisoning case. If `releaseId` reached the params but not the
    // SWR key, this second render would reuse the first response and show
    // release 1's numbers while the header said release 2.
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    const { rerender } = renderHook(() => useCoverage(30), { wrapper })
    await waitFor(() => expect(getCoverage).toHaveBeenCalledTimes(1))

    useReleaseStore.setState({ activeReleaseId: REL_2, scopedProjectId: PROJECT_A })
    rerender()

    await waitFor(() => expect(getCoverage).toHaveBeenCalledTimes(2))
    expect(getCoverage.mock.calls[1][3]).toBe(REL_2)
  })

  it('does not send the release in All Projects mode', async () => {
    mocked.projectState = { activeProjectId: ALL_PROJECTS_ID }
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    renderHook(() => useCoverage(30), { wrapper })

    await waitFor(() => expect(getCoverage).toHaveBeenCalled())
    expect(getCoverage.mock.calls[0][3] ?? null).toBeNull()
  })

  it('carries the release through EVERY release-capable hook', async () => {
    // A sweep rather than five near-identical tests, because the risk is a
    // SIXTH hook added later against a release-capable endpoint that quietly
    // omits the axis. One that does will render unfiltered data under a
    // release heading and no test would notice.
    // `getSuiteDetail(projectId, suiteName, days, releaseId)` and the other
    // four `(projectId, days, suiteName, releaseId)` both put the release
    // last, which is what makes one index right for all five.
    const releaseArgIndex = 3
    const wired: Array<[string, () => unknown, Mock]> = [
      ['useCoverage', () => useCoverage(30), vi.mocked(analyticsService.getCoverage)],
      ['useFlakyTests', () => useFlakyTests(30), vi.mocked(analyticsService.getFlakyTests)],
      [
        'useFailureCategories',
        () => useFailureCategories(30),
        vi.mocked(analyticsService.getFailureCategories),
      ],
      ['useTopFailing', () => useTopFailing(30), vi.mocked(analyticsService.getTopFailing)],
      [
        'useSuiteDetail',
        () => useSuiteDetail('Checkout', 30),
        vi.mocked(analyticsService.getSuiteDetail),
      ],
    ]
    // The backend accepts release_id on exactly six endpoints; five of them are
    // reached from this module (`/flaky-scores` has no frontend hook). Pinning
    // the count means adding a hook without adding it here fails HERE, rather
    // than the loop silently iterating over a shorter list and asserting
    // nothing.
    expect(wired).toHaveLength(5)

    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    for (const [name, useHook, serviceFn] of wired) {
      serviceFn.mockClear()
      renderHook(useHook, { wrapper })
      await waitFor(() => expect(serviceFn, name).toHaveBeenCalled())
      // Assert the POSITION, not mere presence. `toContain` passes for
      // `getFlakyTests(projectId, days, releaseId, suiteName)` — arguments
      // transposed — which in production sends `suite_name=<a uuid>` (empty
      // page) and no `release_id` at all (unfiltered data). Presence is not
      // correctness when the signature is positional.
      const args = serviceFn.mock.calls[0] as unknown[]
      expect(args[releaseArgIndex], `${name} must pass the release id in position ${releaseArgIndex}`).toBe(REL_1)
    }
  })

  it('lets a caller opt out, so a filter cannot retract an integrity warning', async () => {
    // Found by review, and a REGRESSION this wiring introduced rather than a
    // pre-existing gap.
    //
    // `SuiteCasesPage` compares these run-level totals against an UNSCOPED list
    // of test cases to distinguish "this suite never ingested anything" from
    // "runs landed but the per-test rows were dropped" — an ingestion bug it
    // reports in red. Once the totals became release-scoped and the case list
    // did not, selecting a release the suite has no runs in zeroed the totals
    // and the warning silently disappeared: the page then showed a benign empty
    // state over a real defect.
    //
    // A comparison needs both halves on the same scope. This pins the opt-out
    // that keeps them there.
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    renderHook(() => useSuiteDetail('Checkout', 30, { releaseScoped: false }), { wrapper })

    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalled())
    expect(getSuiteDetail.mock.calls[0][3] ?? null).toBeNull()
  })

  it('does not refetch the opted-out caller when the release changes', async () => {
    // The other half: if the release still reached the KEY while being stripped
    // from the request, the diagnostic would re-fetch identical data on every
    // release change — and, worse, look release-aware to the next reader.
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
    const { rerender } = renderHook(
      () => useSuiteDetail('Checkout', 30, { releaseScoped: false }),
      { wrapper },
    )
    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalledTimes(1))

    useReleaseStore.setState({ activeReleaseId: REL_2, scopedProjectId: PROJECT_A })
    rerender()

    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalledTimes(1))
  })

  it('rebuilds the SWR key on EVERY release-capable hook, not just some', async () => {
    // Review found the key pinned on only two of the five. Dropping `releaseId`
    // from `useFlakyTests`' deps survived the whole suite — the exact
    // cache-poisoning case this file's docstring calls the worst outcome, on
    // the hook that feeds /failures and /trends.
    //
    // Sending the id and keying on it are SEPARATE failures: the sweep above
    // catches an id that never reaches the request, and this catches one that
    // reaches the request but not the cache entry.
    const keyed: Array<[string, () => unknown, Mock]> = [
      ['useCoverage', () => useCoverage(30), vi.mocked(analyticsService.getCoverage)],
      ['useFlakyTests', () => useFlakyTests(30), vi.mocked(analyticsService.getFlakyTests)],
      [
        'useFailureCategories',
        () => useFailureCategories(30),
        vi.mocked(analyticsService.getFailureCategories),
      ],
      ['useTopFailing', () => useTopFailing(30), vi.mocked(analyticsService.getTopFailing)],
      [
        'useSuiteDetail',
        () => useSuiteDetail('Checkout', 30),
        vi.mocked(analyticsService.getSuiteDetail),
      ],
    ]
    expect(keyed).toHaveLength(5)

    for (const [name, useHook, serviceFn] of keyed) {
      serviceFn.mockClear()
      useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })
      const { rerender } = renderHook(useHook, { wrapper })
      await waitFor(() => expect(serviceFn, name).toHaveBeenCalledTimes(1))

      useReleaseStore.setState({ activeReleaseId: REL_2, scopedProjectId: PROJECT_A })
      rerender()

      await waitFor(() =>
        expect(serviceFn, `${name} must refetch when the release changes`).toHaveBeenCalledTimes(2),
      )
    }
  })

  it('carries the release through suite-detail, which builds its own key', async () => {
    // This hook does not go through `useProjectScopedSWR`, so it is the one
    // that can silently miss the axis while its five siblings carry it.
    useReleaseStore.setState({ activeReleaseId: REL_1, scopedProjectId: PROJECT_A })

    const { rerender } = renderHook(() => useSuiteDetail('Checkout', 30), { wrapper })
    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalledTimes(1))
    expect(getSuiteDetail.mock.calls[0][3]).toBe(REL_1)

    useReleaseStore.setState({ activeReleaseId: REL_2, scopedProjectId: PROJECT_A })
    rerender()

    await waitFor(() => expect(getSuiteDetail).toHaveBeenCalledTimes(2))
    expect(getSuiteDetail.mock.calls[1][3]).toBe(REL_2)
  })
})
