/**
 * W08 TRND-08: `GET /api/v1/value-metrics/by-team` — the hours-saved model
 * attributed to the project's owning teams. One project only (the server
 * answers 403 for a foreign project, 422 for none). Returned raw: the hook
 * runs it through `parseTeamValueMetrics`.
 *
 * A new file on purpose: `services/valueMetricsService.ts` is fixed-factory
 * mocked in two test files, where a function added to it would be `undefined`.
 */
import { getData } from './http'

export const teamValueMetricsService = {
  getByTeam: (projectId: string, days: number) =>
    getData<unknown>('/api/v1/value-metrics/by-team', { params: { project_id: projectId, days } }),
}
