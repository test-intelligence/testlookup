/**
 * The Run Compare picker's reads (FAIL-06).
 *
 *  - `useRunSearch`: one page of `GET /runs` for a search (`q`: build, branch,
 *    CI job, commit prefix), newest first, 20 a page, ALL TIME (`days: 0`) and
 *    every release: a baseline is not narrowed by the top bar's release
 *    (D-10). One SWR entry per page; the picker keeps the pages it loaded.
 *  - `useRunSummary`: one run's row for a picker's label (`GET /runs/{id}`),
 *    read once — unlike `useRun`, which polls every 5 s for a live run.
 *
 * A NEW file on purpose: `hooks/useRuns.ts` is replaced by a fixed-factory
 * `vi.mock` in ~40 test files.
 */
import useSWR from 'swr'
import { runListService } from '@/services/runListService'
import type { TestRun, TestRunListResponse } from '@/types/runs'
import { useProjectScopedSWR } from './useProjectScopedSWR'

/** Runs per picker page. */
export const RUN_SEARCH_PAGE_SIZE = 20

export function useRunSearch({
  q, suiteName, page, enabled,
}: {
  q: string
  suiteName?: string | null
  page: number
  enabled: boolean
}) {
  const query = q.trim()
  const suite = suiteName?.trim() ?? ''
  return useProjectScopedSWR<TestRunListResponse>(
    'run-search',
    (projectId) => runListService.searchRuns(projectId, {
      q: query, suite_name: suite || null, page, size: RUN_SEARCH_PAGE_SIZE, days: 0,
    }),
    { revalidateOnFocus: false },
    [query, suite, page],
    enabled,
  )
}

export function useRunSummary(runId: string | null | undefined) {
  return useSWR<TestRun>(
    runId ? ['run-summary', runId] : null,
    ([, id]: readonly [string, string]) => runListService.getRun(id, { suppressToast: true }),
    { revalidateOnFocus: false },
  )
}
