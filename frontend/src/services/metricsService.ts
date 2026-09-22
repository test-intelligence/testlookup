import type { DashboardSummary } from '@/types/analytics'
import type { TrendResponse } from '@/types/metrics'
import { getData } from './http'
import { scopeParam, type ScopeValue } from '@/lib/scopeParams'

/** Release scoping, OMITTED when absent — see `analyticsService.releaseParam`.
 *  The backend mirrors it: no SQL fragment, and no `release=` segment in the
 *  Redis cache key, so an unfiltered request is byte-identical to what it was
 *  before this axis existed. */
function releaseParam(releaseId: ScopeValue): Record<string, string | string[]> {
  return scopeParam('release_id', releaseId)
}

export const metricsService = {
  getSummary: (
    projectId: string | null,
    days = 7,
    suiteName?: ScopeValue,
    releaseId?: ScopeValue,
  ) =>
    getData<DashboardSummary>('/api/v1/metrics/summary', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseId),
      },
    }),

  getTrends: (
    projectId: string | null,
    days = 7,
    suiteName?: ScopeValue,
    releaseId?: ScopeValue,
  ) =>
    getData<TrendResponse>('/api/v1/metrics/trends', {
      params: {
        ...(projectId ? { project_id: projectId } : {}),
        days,
        ...scopeParam('suite_name', suiteName),
        ...releaseParam(releaseId),
      },
    }),
}
export type { TrendPoint, TrendResponse } from '@/types/metrics'
