import { useMemo } from 'react'
import { useProjectScopedSWR } from '@/hooks/useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { testManagementService } from '@/services/testManagementService'

/** One suite's run aggregates, as `GET /api/v1/test-management/suites` returns them. */
export type SuiteAggregate = Awaited<ReturnType<typeof testManagementService.listSuites>>[number]

/**
 * The suites list's run columns (UX redesign P4): pass rate, last run,
 * executions, failing tests and owner, per suite NAME, from the endpoint the
 * Test Cases › Suites tab already reads (nothing new on the server).
 *
 * Only for ONE project. That endpoint groups by name with no project id, and
 * answers a non-admin's All-Projects request with an empty list, so across
 * projects a name could join the wrong suite: `enabled` false asks nothing.
 */
export function useSuiteAggregates(enabled: boolean) {
  const { data, error, isLoading } = useProjectScopedSWR(
    'suite-aggregates',
    (projectId) => testManagementService.listSuites(projectId),
    { refreshInterval: REFRESH_INTERVALS.BACKGROUND, revalidateOnFocus: false },
    [],
    enabled,
  )
  const byName = useMemo(() => {
    const map = new Map<string, SuiteAggregate>()
    for (const row of data ?? []) if (row.suite_name) map.set(row.suite_name.trim(), row)
    return map
  }, [data])
  return { byName, error, isLoading, loaded: data !== undefined }
}
