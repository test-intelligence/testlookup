import type {
  RunTestCase,
  RunTestCaseListResponse,
  TestRun,
  TestRunListResponse,
  TestStepsTree,
} from '@/types/runs'
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

  /** Granular step/attachment tree for a test (latest-run-only snapshot).
   *  Lazy/separate from the test-case detail payload; empty arrays when no
   *  granular detail was captured (non-Allure/pytest runs). */
  getTestSteps: (runId: string, testId: string) =>
    getData<TestStepsTree>(`/api/v1/runs/${runId}/tests/${testId}/steps`),

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
    suiteName?: string | null,
  ) =>
    getData<{ ids: string[]; count: number; truncated: boolean }>(
      '/api/v1/runs/failed-ids',
      {
        params: {
          ...(projectId ? { project_id: projectId } : {}),
          days,
          ...(onlyPending ? { only_pending: true } : {}),
          ...(suiteName ? { suite_name: suiteName } : {}),
        },
      },
    ),
}
