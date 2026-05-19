/**
 * Per-TestCase human review API (migration 0081). Lets a reviewer transition
 * an AI-flagged failure from the implicit ``pending_review`` to a concrete
 * verdict (``reviewed`` / ``defect_filed`` / ``false_positive`` / ``reproducible``).
 */
import { getData, putData, deleteData } from './http'

export type TestExecutionReviewState =
  | 'pending_review'
  | 'reviewed'
  | 'defect_filed'
  | 'false_positive'
  | 'reproducible'

export interface TestExecutionReviewRead {
  id: string
  test_case_id: string
  project_id: string
  state: TestExecutionReviewState
  reviewed_by_user_id: string | null
  reviewed_by_username: string | null
  reviewed_by_full_name: string | null
  defect_link: string | null
  note: string | null
  transitioned_at: string | null
  created_at: string
  updated_at: string | null
}

export interface TestExecutionReviewUpdate {
  state: Exclude<TestExecutionReviewState, 'pending_review'>
  defect_link?: string | null
  note?: string | null
}

export const testExecutionReviewService = {
  /**
   * Fetch the current review for a test case. Resolves to ``null`` when
   * the backend returns 404 — that means "no review row yet, treat as
   * the implicit ``pending_review`` state." Same pattern as
   * ``aiService.getAnalysis``. Without this catch, every test-case
   * detail page open for a not-yet-reviewed case shows an error toast
   * even though the 404 is informational.
   */
  get: (testCaseId: string): Promise<TestExecutionReviewRead | null> =>
    getData<TestExecutionReviewRead>(`/api/v1/test-cases/${testCaseId}/review`)
      .catch((err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status
        if (status === 404) return null
        throw err
      }),

  /** Upsert. Use this for both first transition and subsequent state changes. */
  upsert: (testCaseId: string, payload: TestExecutionReviewUpdate) =>
    putData<TestExecutionReviewRead, TestExecutionReviewUpdate>(
      `/api/v1/test-cases/${testCaseId}/review`,
      payload,
    ),

  /** Clear the review row — reverts the case to pending_review. */
  clear: (testCaseId: string) =>
    deleteData(`/api/v1/test-cases/${testCaseId}/review`),
}
