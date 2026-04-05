import type { DashboardSummary } from '@/types/analytics'
import type { TrendResponse } from '@/types/metrics'
import { getData } from './http'

export const metricsService = {
  getSummary: (projectId: string | null, days = 7) =>
    getData<DashboardSummary>('/api/v1/metrics/summary', {
      params: { ...(projectId ? { project_id: projectId } : {}), days },
    }),

  getTrends: (projectId: string | null, days = 7) =>
    getData<TrendResponse>('/api/v1/metrics/trends', {
      params: { ...(projectId ? { project_id: projectId } : {}), days },
    }),
}
export type { TrendPoint, TrendResponse } from '@/types/metrics'
