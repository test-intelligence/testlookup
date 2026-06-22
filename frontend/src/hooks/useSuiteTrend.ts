import useSWR from 'swr'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { testManagementService } from '@/services/testManagementService'
import { useActiveProjectId } from './useProjectScopedSWR'

/** One zero-filled day in a suite's run/result trend. */
export interface SuiteTrendPoint {
  date: string
  run_count: number
  total_tests: number
  passed_count: number
  failed_count: number
  skipped_count: number
  broken_count: number
}

/**
 * Per-day run/result trend for a suite as an SWR hook.
 *
 * Replaces a `(suiteName, activeProjectId, days)`-keyed load effect in
 * SuiteDetailPage — `useEffect(() => { setTrendLoading(true);
 * getSuiteTrend(...).then(res => setTrendPoints(res.points || []))
 * .catch(() => setTrendPoints([])) ... }, [suiteName, activeProjectId, days])`
 * — the kind of data-fetch effect the react-hooks `set-state-in-effect` rule
 * (correctly) discourages. SWR owns the loading/data state declaratively, so
 * the page no longer drives state from an effect.
 *
 * The fetch runs whenever a `suiteName` is present, for the active project OR
 * for the "all projects" view (`ALL_PROJECTS_ID` maps to a `null` project id,
 * exactly as the old effect did). `points` is `[]` while loading or on error,
 * preserving the prior `useState<SuiteTrendPoint[]>([])` semantics. `activeProjectId`
 * is part of the SWR key so switching projects refetches. `shouldRetryOnError`
 * is off so a failed load surfaces as empty immediately, matching the old
 * `.catch(() => setTrendPoints([]))`.
 */
export function useSuiteTrend(suiteName: string | null, days = 30) {
  const activeProjectId = useActiveProjectId()
  const fetchProjectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId
  const { data, isLoading } = useSWR(
    suiteName ? ['suite-trend', activeProjectId, suiteName, days] : null,
    () => testManagementService.getSuiteTrend(suiteName as string, fetchProjectId, days),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  return {
    points: (data?.points ?? []) as SuiteTrendPoint[],
    isLoading,
  }
}
