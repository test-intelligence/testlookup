import useSWR from 'swr'
import toast from 'react-hot-toast'
import { valueMetricsService, type ValueMetrics } from '@/services/valueMetricsService'

/**
 * Value-metrics fetch as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { setLoading(true); service.get()
 * .then(setMetrics) ... })` in ValueMetricsPage — the kind of effect the
 * react-hooks `set-state-in-effect` rule (correctly) discourages. SWR owns the
 * loading/data state declaratively, so the page no longer drives state from an
 * effect.
 *
 * Keyed on `[projectId, days]` exactly like the old effect's dependency array,
 * so it refetches on either change. `projectId` is `undefined` in all-projects
 * mode (the service then omits the `project_id` filter), and the key is never
 * null — the page always fetches, matching the prior behaviour.
 */
export function useValueMetrics(projectId: string | undefined, days = 30) {
  const { data, error, isLoading } = useSWR<ValueMetrics>(
    ['value-metrics', projectId ?? '__all__', days],
    () => valueMetricsService.get(projectId, days),
    {
      revalidateOnFocus: false,
      onError: () => toast.error('Failed to load value metrics'),
    },
  )

  return {
    metrics: data,
    isLoading,
    isError: !!error,
  }
}
