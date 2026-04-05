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
}
