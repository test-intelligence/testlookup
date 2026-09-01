import type {
  RunStepFlips,
  RunTestCaseListResponse,
  TestCaseHistory,
  TestRun,
  TestRunListResponse,
  TestStepFlips,
  TestStepsTree,
} from '@/types/runs'
import type { TestCaseDetailResponse } from '@/types/test-case-detail'
import type { RunAttributionResponse } from '@/types/attribution'
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
    getData<TestCaseDetailResponse>(`/api/v1/runs/${runId}/tests/${testId}`),

  getEnrichedTest: (runId: string, testId: string) =>
    getData<TestCaseDetailResponse>(`/api/v1/runs/${runId}/tests/${testId}/rich-detail`),

  /**
   * Per-failure attribution verdicts for a run (roadmap Phase 4).
   * Advisory only — the payload never authorises hiding a failure.
   */
  attribution: (runId: string) =>
    getData<RunAttributionResponse>(`/api/v1/runs/${runId}/attribution`),

  /** Granular step/attachment tree for a test (latest-run-only snapshot).
   *  Lazy/separate from the test-case detail payload; empty arrays when no
   *  granular detail was captured (non-Allure/pytest runs). */
  getTestSteps: (runId: string, testId: string) =>
    getData<TestStepsTree>(`/api/v1/runs/${runId}/tests/${testId}/steps`),

  /** Cross-run pass/fail history + flakiness + metadata for a logical test.
   *  Project-scoped server-side; lazy/separate from the test-case detail. */
  getTestHistory: (runId: string, testId: string) =>
    getData<TestCaseHistory>(`/api/v1/runs/${runId}/tests/${testId}/history`),

  /** Cross-run step-flip report — which step oscillated PASSED↔FAILED across
   *  runs (FLK-P6). Project-scoped server-side; lazy/separate from the detail. */
  getTestStepFlips: (runId: string, testId: string) =>
    getData<TestStepFlips>(`/api/v1/runs/${runId}/tests/${testId}/step-flips`),

  /** Run-level roll-up of cross-run step-flip — which TESTS in the run have a
   *  flickering step (FLK-P6). Project-scoped server-side; lazy/separate from
   *  the run-intelligence payload. */
  getRunStepFlips: (runId: string) =>
    getData<RunStepFlips>(`/api/v1/runs/${runId}/step-flips`),

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
