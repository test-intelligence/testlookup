import useSWR from 'swr'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { useProjectScopedSWR } from './useProjectScopedSWR'
import { myFailuresService } from '@/services/myFailuresService'

/**
 * Paginated list of the caller's auto-assigned failures.
 * Project-scoped via the active project store: ALL_PROJECTS_ID fans out
 * across every accessible project; a specific UUID narrows. Polls every
 * 30s (POLLING tier) so new ingests appear without a manual refresh.
 */
export function useMyFailures(params: { days?: number; page?: number; size?: number }) {
  return useProjectScopedSWR(
    'my-failures',
    (projectId) => myFailuresService.list({ project_id: projectId, ...params }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [params.days, params.page, params.size],
  )
}

/**
 * Sidebar badge count. Independent SWR from the list so the badge stays
 * fresh even when the inbox page is closed. Same project scope as the
 * page — when the user switches project, the badge re-keys automatically.
 */
export function useMyFailuresCount(params: { days?: number } = {}) {
  return useProjectScopedSWR(
    'my-failures-count',
    (projectId) => myFailuresService.count({ project_id: projectId, days: params.days }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [params.days],
  )
}

/** Non-project-scoped variant for the top-level "all my work" sidebar badge. */
export function useMyFailuresCountUnscoped(params: { days?: number } = {}) {
  return useSWR(
    ['my-failures-count-unscoped', params.days],
    () => myFailuresService.count({ days: params.days }),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
  )
}
