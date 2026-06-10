import useSWR from 'swr'
import { runsService } from '@/services/runsService'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'

export function useRuns(params?: Record<string, unknown>) {
  return useProjectScopedSWR(
    'runs',
    (projectId) => runsService.list(projectId, params),
    { refreshInterval: REFRESH_INTERVALS.ACTIVE },
    [params],
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
    () => runsService.getTest(runId as string, testId as string)
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
