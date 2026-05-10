import type { RunTestCase, RunTestCaseListResponse, TestRun, TestRunListResponse } from '@/types/runs'
import { getData, postData } from './http'

export const runsService = {
  list: (projectId: string | null, params?: Record<string, unknown>) =>
    getData<TestRunListResponse>('/api/v1/runs', {
      params: { ...(projectId ? { project_id: projectId } : {}), ...params },
    }),

  get: (runId: string) =>
    getData<TestRun>(`/api/v1/runs/${runId}`),

  listTests: (runId: string, params?: Record<string, unknown>) =>
    getData<RunTestCaseListResponse>(`/api/v1/runs/${runId}/tests`, { params }),

  getTest: (runId: string, testId: string) =>
    getData<RunTestCase>(`/api/v1/runs/${runId}/tests/${testId}`),

  setRelease: (runId: string, releaseName: string) =>
    postData(`/api/v1/runs/${runId}/release`, { release_name: releaseName }),

  /** Lightweight list of FAILED run IDs for the bulk-trigger "all pages"
   *  shortcut. Server caps at 1000 by default; truncated=true means more
   *  exist than were returned. ``onlyPending=true`` excludes runs that
   *  already have an active or recent (≤2h) agent pipeline. */
  listFailedIds: (
    projectId: string | null,
    days: number,
    onlyPending = false,
  ) =>
    getData<{ ids: string[]; count: number; truncated: boolean }>(
      '/api/v1/runs/failed-ids',
      {
        params: {
          ...(projectId ? { project_id: projectId } : {}),
          days,
          ...(onlyPending ? { only_pending: true } : {}),
        },
      },
    ),
}
