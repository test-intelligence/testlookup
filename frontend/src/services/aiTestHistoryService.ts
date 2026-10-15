import type { AITestHistory } from '@/types/aiTestHistory'
import { getData } from './http'

/**
 * W03 AIH-05: the stored AI versions of one test result (contract §6.4 R5).
 *
 * `GET /api/v1/runs/{run_id}/tests/{test_id}/ai-history`: member-level (a viewer
 * gets 200), 404 when the test is not a test of that run. No Mongo behind it, so
 * it never 503s. Only the root-cause excerpt is returned; the full analysis stays
 * behind `GET /analyze/{id}`. Rides the shared Axios base via the `http` helpers.
 */
export function getTestAIVersions(runId: string, testId: string): Promise<AITestHistory> {
  return getData<AITestHistory>(
    `/api/v1/runs/${encodeURIComponent(runId)}/tests/${encodeURIComponent(testId)}/ai-history`,
  )
}

export const aiTestHistoryService = { getTestAIVersions }
