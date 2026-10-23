import useSWR from 'swr'
import { getData } from '@/services/http'
import type { RegressionDiff } from '@/types/regressionDiff'

/**
 * The run's regression diff, for the Changes tab (moved out of `RunChangesTab`
 * in W10 FAIL-09: pages import hooks, hooks own the fetching).
 *
 * The SWR key is byte-identical to the one the tab used inline
 * (`regression-diff-${runId}`): `RunDetailPage.tabs.test.tsx` answers it by that
 * string, and `fold-run.spec.ts` counts the tab's two reads.
 */
export function useRegressionDiff(runId: string) {
  const { data, isLoading, error } = useSWR<RegressionDiff>(
    `regression-diff-${runId}`,
    () => getData<RegressionDiff>(`/api/v1/runs/${runId}/regression-diff`),
    { revalidateOnFocus: false },
  )
  return { data, isLoading, error }
}
