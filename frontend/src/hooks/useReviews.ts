import { useActiveProjectId, useProjectScopedSWR } from './useProjectScopedSWR'
import { REFRESH_INTERVALS } from '@/config/refreshIntervals'
import { reviewService } from '@/services/reviewService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { Review, ReviewStateFilter } from '@/types/review'

/**
 * The active project's review queue (E8.5).
 *
 * Reviews are listed per project, so nothing is fetched in All Projects mode:
 * the page renders the project picker instead.
 */
export function useProjectReviews(state: ReviewStateFilter = 'pending_review') {
  const projectId = useActiveProjectId()
  const enabled = projectId !== null && projectId !== ALL_PROJECTS_ID
  const { data, error, isLoading, mutate } = useProjectScopedSWR<Review[]>(
    'project-reviews',
    (pid) => (pid ? reviewService.list(pid, state) : Promise.resolve([])),
    { refreshInterval: REFRESH_INTERVALS.POLLING },
    [state],
    enabled,
  )
  return {
    reviews: data ?? [],
    isLoading,
    error,
    isError: !!error,
    refresh: mutate,
  }
}
