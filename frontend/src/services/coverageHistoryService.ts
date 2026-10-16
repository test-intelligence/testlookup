/**
 * W08 TRND-07: the two Coverage reads that are asked only when their tab or
 * panel opens (never on page load: `rollout-coverage-map.spec.ts` pins the
 * load's requests).
 *
 *   GET /api/v1/analytics/coverage/history         — the History tab
 *   GET /api/v1/analytics/coverage/untagged-tests  — the Fix tagging panel
 *
 * Both need ONE project (`project_required`): the hooks never ask in All
 * Projects. The answers are returned raw (`unknown`): the hooks run them
 * through `parseCoverageHistory` / `parseUntaggedTests`, so a malformed
 * answer degrades to "not available", never a crash.
 */
import { getData } from './http'

export const UNTAGGED_PAGE_SIZE = 50

export const coverageHistoryService = {
  getCoverageHistory: (projectId: string, days: number) =>
    getData<unknown>('/api/v1/analytics/coverage/history', {
      params: { project_id: projectId, days },
    }),

  getUntaggedTests: (projectId: string, days: number, page: number, size = UNTAGGED_PAGE_SIZE) =>
    getData<unknown>('/api/v1/analytics/coverage/untagged-tests', {
      params: { project_id: projectId, days, page, size },
    }),
}
