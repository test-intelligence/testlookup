import { api } from './api'

export interface ValueMetrics {
  period_days: number
  project_id: string | null
  triage_time_saved_minutes: number
  triage_time_saved_hours: number
  defects_auto_grouped: number
  tests_grouped: number
  duplicate_tickets_avoided: number
  defects_promoted: number
  flaky_tests_identified: number
  quarantine_recommended: number
  risky_releases_blocked: number
  releases_conditional: number
  release_overrides: number
  intelligence_reports_generated: number
}

export const valueMetricsService = {
  get: (projectId?: string, days = 30) =>
    api.get<ValueMetrics>('/api/v1/value-metrics', {
      params: { ...(projectId ? { project_id: projectId } : {}), days },
    }).then(r => r.data),

  exportUrl: (projectId?: string, days = 30) => {
    const params = new URLSearchParams({ days: String(days) })
    if (projectId) params.set('project_id', projectId)
    return `/api/v1/value-metrics/export?${params}`
  },
}
