import type { DashboardSummary } from '@/types/analytics'
import type { TrendResponse } from '@/types/metrics'
import { getData } from './http'

/** Release scoping, OMITTED when absent — see `analyticsService.releaseParam`.
 *  The backend mirrors it: no SQL fragment, and no `release=` segment in the
 *  Redis cache key, so an unfiltered request is byte-identical to what it was
 *  before this axis existed. */
function releaseParam(releaseId: string | null | undefined): Record<string, string> {
  return releaseId ? { release_id: releaseId } : {}
}

export const metricsService = {
  getSummary: (
    projectId: string | null,
    days = 7,
    suiteName?: string | null,
    releaseId?: string | null,
  ) =>
    getData<DashboardSummary>('/api/v1/metrics/summary', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        days,
        ...(suiteName ? { suite_name: suiteName } : {}),
        ...releaseParam(releaseId),
      },
    }),

  getTrends: (
    projectId: string | null,
    days = 7,
    suiteName?: string | null,
    releaseId?: string | null,
  ) =>
    getData<TrendResponse>('/api/v1/metrics/trends', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        days,
        ...(suiteName ? { suite_name: suiteName } : {}),
        ...releaseParam(releaseId),
      },
    }),
}
export type { TrendPoint, TrendResponse } from '@/types/metrics'
