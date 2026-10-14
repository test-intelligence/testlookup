import useSWR from 'swr'
import {
  durationRegressionsService,
  type DurationRegressions,
} from '@/services/durationRegressionsService'

/**
 * EXEC-05: a run's duration regressions. Asks nothing until there is a run
 * id, so a tab that is not open (or a run still loading) makes no request.
 */
export function useDurationRegressions(runId: string | null | undefined) {
  const { data, error, isLoading } = useSWR<DurationRegressions>(
    runId ? ['duration-regressions', runId] : null,
    () => {
      if (!runId) throw new Error('A run id is required')
      return durationRegressionsService.get(runId)
    },
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading }
}
