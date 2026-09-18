import useSWR, { mutate } from 'swr'
import toast from 'react-hot-toast'
import { valueMetricsService } from '@/services/valueMetricsService'
import type { ValueMethodology, ValueMetrics } from '@/types/valueMetrics'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { useActiveProjectId } from './useProjectScopedSWR'

/**
 * Value-metrics fetch as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { setLoading(true); service.get()
 * .then(setMetrics) ... })` in ValueMetricsPage — the kind of effect the
 * react-hooks `set-state-in-effect` rule (correctly) discourages. SWR owns the
 * loading/data state declaratively, so the page no longer drives state from an
 * effect.
 *
 * Keyed on `[projectId, days, months]` so it refetches on any change.
 * `projectId` is `undefined` in all-projects mode (the service then omits the
 * `project_id` filter), and the key is never null — the page always fetches,
 * matching the prior behaviour. `months` (default 6) drives the hours-saved
 * monthly series per the US-12.1 contract.
 */
export function useValueMetrics(projectId: string | undefined, days = 30, months = 6) {
  const { data, error, isLoading, mutate: boundMutate } = useSWR<ValueMetrics>(
    ['value-metrics', projectId ?? '__all__', days, months],
    () => valueMetricsService.get(projectId, days, months),
    {
      revalidateOnFocus: false,
      onError: () => toast.error('Failed to load value metrics'),
    },
  )

  return {
    metrics: data,
    error,
    isLoading,
    isError: !!error,
    /** Bound revalidate for THIS key (survives scoped SWR caches in tests). */
    refresh: boundMutate,
  }
}

/** Revalidate every cached value-metrics key (page + Overview KPI). */
export function refreshValueMetrics() {
  return mutate((key: unknown) => Array.isArray(key) && key[0] === 'value-metrics')
}

/**
 * Project-scoped value-metrics fetch for the Overview KPI card (US-12.2).
 *
 * Uses the SAME SWR key shape as `useValueMetrics` (with its defaults
 * days=30 / months=6) so when both Overview and the Value Metrics page are
 * warm they dedupe to ONE fetch. Skips fetching entirely while no project is
 * selected (`activeProjectId === null`), mirroring useProjectScopedSWR.
 *
 * Deliberately NO error toast here: the Overview card is an optional
 * enrichment — on failure or insufficient data the card is simply omitted.
 */
export function useValueMetricsKpi() {
  const activeProjectId = useActiveProjectId()
  const projectId = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined)
  const { data } = useSWR<ValueMetrics>(
    activeProjectId !== null ? ['value-metrics', projectId ?? '__all__', 30, 6] : null,
    () => valueMetricsService.get(projectId, 30, 6),
    { revalidateOnFocus: false },
  )
  return { metrics: data }
}

/**
 * Methodology payload for the "How is this calculated?" panel. Fetched
 * lazily — pass `enabled=false` until the panel is opened.
 */
export function useValueMethodology(enabled: boolean) {
  const { data, error, isLoading } = useSWR<ValueMethodology>(
    enabled ? ['value-metrics-methodology'] : null,
    () => valueMetricsService.getMethodology(),
    { revalidateOnFocus: false },
  )
  return {
    methodology: data,
    isLoading,
    isError: !!error,
  }
}
