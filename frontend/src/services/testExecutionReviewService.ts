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
   * Fetch the current review for a test case. Backend returns 404 when no
   * transition has happened yet — callers should treat that as "still in
   * the implicit pending_review state."
   */
  get: (testCaseId: string) =>
    getData<TestExecutionReviewRead>(`/api/v1/test-cases/${testCaseId}/review`),

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
