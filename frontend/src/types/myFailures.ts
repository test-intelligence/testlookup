/**
 * Types for the "My Failures" inbox — the calling user's auto-assigned failures
 * (TestCase.assigned_to_user_id from migration 0080). The backend writes
 * navigation_url so the frontend never has to reconstruct deep links.
 */
/**
 * Per-failure triage workflow state (matches backend ``TriageStatus`` enum,
 * migration 0088). PENDING_REVIEW is the only status that shows on /my-failures;
 * the others are resolution states the assignee or a QA Lead picks to drop
 * the row off the inbox.
 */
export type TriageStatus =
  | 'PENDING_REVIEW'
  | 'REVIEWED_APPROVED'
  | 'DEFECT_CREATED'
  | 'WONT_FIX'
  | 'AUTOMATION_SCRIPT_ISSUE'
  | 'FLAKY_TEST'

export interface MyFailureItem {
  id: string
  test_name: string
  suite_name?: string | null
  class_name?: string | null
  status: string
  severity?: string | null
  failure_category?: string | null
  /** Truncated to 280 chars by the backend — full message lives on the run-detail drawer. */
  error_message?: string | null
  duration_ms?: number | null
  created_at: string
  test_run_id: string
  build_number?: string | null
  project_id: string
  project_name?: string | null
  navigation_url: string
  /** Workflow state; the inbox filters to PENDING_REVIEW only. */
  triage_status?: TriageStatus
  /** Optional context — typically a defect link or rationale. */
  triage_notes?: string | null
  /** Per-(project, primary_suite_name) run number, 1-based, computed
   *  server-side via ROW_NUMBER(). The inbox shows this instead of the
   *  opaque SDK build_number. */
  run_seq?: number | null
  /**
   * Times this test (same project + suite + class + name) has failed for the
   * current user within the selected window. Lets the inbox surface repeat
   * offenders without a follow-up fetch. Defaults to 1 for older payloads.
   */
  failure_count?: number
}

export interface MyFailureListResponse {
  items: MyFailureItem[]
  total: number
  page: number
  size: number
  pages: number
  /**
   * Same-filter total without pagination — used so the sidebar badge stays
   * accurate while the user is paging through the table. Currently equal to
   * ``total`` (no resolved concept yet), but kept distinct for forward compat.
   */
  unresolved_total: number
}
