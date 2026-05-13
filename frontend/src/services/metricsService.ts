import type { DashboardSummary } from '@/types/analytics'
import type { TrendResponse } from '@/types/metrics'
import { getData } from './http'

export const metricsService = {
  getSummary: (projectId: string | null, days = 7, suiteName?: string | null) =>
    getData<DashboardSummary>('/api/v1/metrics/summary', {
      params: { ...(projectId ? { project_id: projectId } : {}), days, ...(suiteName ? { suite_name: suiteName } : {}) },
    }),

  getTrends: (projectId: string | null, days = 7, suiteName?: string | null) =>
    getData<TrendResponse>('/api/v1/metrics/trends', {
      params: { ...(projectId ? { project_id: projectId } : {}), days, ...(suiteName ? { suite_name: suiteName } : {}) },
    }),
}
export type { TrendPoint, TrendResponse } from '@/types/metrics'
