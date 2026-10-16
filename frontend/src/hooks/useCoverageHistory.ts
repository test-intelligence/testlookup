/**
 * W08 TRND-07: the Coverage History tab's read and the Fix tagging panel's.
 *
 * Neither asks on page load: `useCoverageHistory` only while the History tab
 * is open, `useUntaggedTests` only while the panel is. Both need one project,
 * so the key is null in All Projects (the server would answer 422).
 *
 * Every answer goes through the type's guard: `history` / `list` is `null`
 * while loading AND for a malformed answer; `malformed` tells the two apart,
 * so the section says "not available" instead of spinning forever.
 */
import useSWR from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { coverageHistoryService, UNTAGGED_PAGE_SIZE } from '@/services/coverageHistoryService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import {
  parseCoverageHistory,
  parseUntaggedTests,
  type CoverageHistory,
  type UntaggedTestList,
} from '@/types/analytics'

export const COVERAGE_HISTORY_KEY = 'analytics-coverage-history'
export const UNTAGGED_TESTS_KEY = 'analytics-coverage-untagged-tests'

/** The pinned project, or `null` (none selected, or All Projects). */
function usePinnedProject(): string | null {
  const projectId = useProjectStore((state) => state.activeProjectId)
  return projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
}

export interface CoverageHistoryRead {
  history: CoverageHistory | null
  /** An answer arrived and failed the guard (or the request failed). */
  malformed: boolean
  isLoading: boolean
  error: unknown
  mutate: () => Promise<unknown>
}

export function useCoverageHistory(days: number, enabled: boolean): CoverageHistoryRead {
  const projectId = usePinnedProject()
  const { data, error, isLoading, mutate } = useSWR(
    enabled && projectId ? [COVERAGE_HISTORY_KEY, projectId, days] : null,
    // An empty body is an answer too (`null`): it must read as malformed, not as "still loading".
    ([, pid, d]: readonly [string, string, number]) => coverageHistoryService.getCoverageHistory(pid, d).then((raw) => raw ?? null),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, revalidateOnFocus: false },
  )
  const history = data === undefined ? null : parseCoverageHistory(data)
  return {
    history,
    malformed: (data !== undefined && history === null) || (error !== undefined && data === undefined),
    isLoading,
    error,
    mutate: () => mutate(),
  }
}

export interface UntaggedTestsRead {
  list: UntaggedTestList | null
  malformed: boolean
  isLoading: boolean
  error: unknown
}

export function useUntaggedTests(
  projectId: string | null,
  days: number,
  page: number,
  enabled: boolean,
): UntaggedTestsRead {
  const pinned = projectId && projectId !== ALL_PROJECTS_ID ? projectId : null
  const { data, error, isLoading } = useSWR(
    enabled && pinned ? [UNTAGGED_TESTS_KEY, pinned, days, page] : null,
    ([, pid, d, p]: readonly [string, string, number, number]) =>
      coverageHistoryService.getUntaggedTests(pid, d, p, UNTAGGED_PAGE_SIZE).then((raw) => raw ?? null),
    { revalidateOnFocus: false, keepPreviousData: true },
  )
  const list = data === undefined ? null : parseUntaggedTests(data)
  return {
    list,
    malformed: (data !== undefined && list === null) || (error !== undefined && data === undefined),
    isLoading,
    error,
  }
}
