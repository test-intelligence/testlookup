import useSWR from 'swr'
import { runsService } from '@/services/runsService'
import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'
import { useReleaseScope } from './useReleaseScope'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

interface UseRunsOptions {
  /**
   * Ask about the PROJECT, not the current view.
   *
   * The global release filter is right for anything the reader is looking at,
   * and wrong for a question like "has this project ever had a run?" —
   * `OverviewPage` asks exactly that to decide whether to show the first-run
   * setup wizard. While it inherited the filter, `?release=unattributed` on a
   * project with runs but none in that bucket returned zero rows, and a
   * populated project was told "No test runs here yet" with setup instructions.
   *
   * Note the shape of that bug: the same call already opts out of the `days`
   * window, with a comment saying it must tell "no runs in the last 7 days"
   * from "no runs at all". The release axis reintroduced the identical
   * confusion through a different door, which is why this is a named option
   * rather than another quietly-omitted parameter.
   */
  ignoreGlobalRelease?: boolean
}

export function useRuns(
  params?: Record<string, unknown>,
  opts?: UseRunsOptions,
) {
  const globalReleaseId = useReleaseScope()
  const releaseId = opts?.ignoreGlobalRelease ? null : globalReleaseId
  return useProjectScopedSWR(
    'runs',
    (projectId) =>
      runsService.list(projectId, {
        // Global filter first, caller's params second, so an EXPLICIT
        // `release_id` from a page that is already about one release wins over
        // the header picker rather than being silently overridden by it.
        ...(releaseId ? { release_id: releaseId } : {}),
        ...params,
      }),
    { refreshInterval: REFRESH_INTERVALS.ACTIVE },
    // The EFFECTIVE release, in the deps and not just the params: without it,
    // switching releases would reuse the previous release's cached run list
    // under the new release's name — and an opted-out query would share a
    // cache entry with the filtered one and get whichever landed first.
    [params, releaseId],
  )
}

export function useRun(runId?: string) {
  // Poll the run summary every 5s so a page open during a live run
  // sees status/totals tick in real time. For completed runs SWR's
  // default dedupe makes this a cheap no-op (no actual change → no
  // re-render). The same hook is also used by /deep-investigate's
  // fallback fetch which benefits from staying current too.
  return useSWR(
    runId ? ['run', runId] : null,
    () => runsService.get(runId as string),
    { refreshInterval: REFRESH_INTERVALS.REALTIME },
  )
}

export function useTestCases(runId?: string, params?: Record<string, unknown>) {
  // Same 5s polling so per-test rows surface as the Phase 4.5 drain
  // persists them mid-session. Without this, the user has to manually
  // refresh to see new cases land — and the page commonly shows an
  // empty table for the first ~30s of a live run.
  return useSWR(
    runId ? ['test-cases', runId, params] : null,
    () => runsService.listTests(runId as string, params),
    { refreshInterval: REFRESH_INTERVALS.REALTIME },
  )
}

export function useTestCase(runId?: string, testId?: string) {
  return useSWR(
    runId && testId ? ['test-case', runId, testId] : null,
    async () => {
      try {
        return await runsService.getEnrichedTest(runId as string, testId as string)
      } catch (error) {
        // Older deployments do not expose the additive endpoint yet. Only
        // fall back for endpoint absence; a real authorization/server error
        // must remain visible to the caller.
        const status = (error as { response?: { status?: unknown } })?.response?.status
        if (status === 404 || status === 405) return runsService.getTest(runId as string, testId as string)
        throw error
      }
    },
  )
}

/**
 * Granular step/attachment tree for a test. Lazy — fetched separately from the
 * test-case detail so the detail view renders immediately and the (potentially
 * larger) step snapshot streams in. Latest-run-only snapshot per logical test.
 */
export function useTestSteps(runId?: string, testId?: string) {
  return useSWR(
    runId && testId ? ['test-steps', runId, testId] : null,
    () => runsService.getTestSteps(runId as string, testId as string)
  )
}

/**
 * Cross-run pass/fail history + flakiness + metadata for a logical test. Lazy —
 * fetched separately from the test-case detail so the detail renders immediately
 * and the (project-scoped) history timeline streams in. Read-only.
 */
export function useTestCaseHistory(runId?: string, testId?: string) {
  return useSWR(
    runId && testId ? ['test-case-history', runId, testId] : null,
    () => runsService.getTestHistory(runId as string, testId as string)
  )
}

/**
 * Cross-run step-flip report for a logical test (FLK-P6). Surfaces which step
 * oscillated PASSED↔FAILED across runs — step-level flakiness rather than a
 * whole-test verdict. Lazy — fetched separately from the test-case detail so the
 * detail renders immediately. Read-only, project-scoped server-side.
 */
export function useTestStepFlips(runId?: string, testId?: string) {
  return useSWR(
    runId && testId ? ['test-step-flips', runId, testId] : null,
    () => runsService.getTestStepFlips(runId as string, testId as string)
  )
}

/**
 * Run-level roll-up of cross-run step-flip (FLK-P6). Surfaces which TESTS in the
 * run have a flickering step — step-level flakiness across the whole run rather
 * than per-test. Lazy — fetched separately from the run-intelligence payload so
 * the page renders immediately. Read-only, project-scoped server-side.
 */
export function useRunStepFlips(runId?: string) {
  return useSWR(
    runId ? ['run-step-flips', runId] : null,
    () => runsService.getRunStepFlips(runId as string)
  )
}


/**
 * Per-failure attribution verdicts for a run (roadmap Phase 4).
 *
 * Deliberately NOT polled. A verdict is composed from a nightly score, a
 * nightly cluster and the run's own commit range — none of which move while
 * someone reads the page — so re-fetching would cost queries and change
 * nothing.
 */
export function useRunAttribution(runId?: string) {
  return useSWR(
    runId ? ['run-attribution', runId] : null,
    ([, id]: readonly [string, string]) => runsService.attribution(id),
    { revalidateOnFocus: false },
  )
}

/**
 * The single most recent run regardless of the selected window.
 *
 * Only fetched when ``enabled`` — a page asks for this to explain an EMPTY
 * window ("your most recent run was 10 days ago"), so on the normal path it
 * must cost nothing. ``useProjectScopedSWR`` has no enabled gate, hence the
 * explicit null key here.
 */
export function useMostRecentRun(enabled: boolean) {
  const projectId = useActiveProjectId()
  const fetcherProjectId = projectId === ALL_PROJECTS_ID ? null : projectId
  return useSWR(
    enabled && projectId !== null ? ['runs-most-recent', projectId] : null,
    () => runsService.list(fetcherProjectId, { page: 1, size: 1, days: 365 }),
    { revalidateOnFocus: false },
  )
}
