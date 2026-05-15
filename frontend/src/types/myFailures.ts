/**
 * Types for the "My Failures" inbox — the calling user's auto-assigned failures
 * (TestCase.assigned_to_user_id from migration 0080). The backend writes
 * navigation_url so the frontend never has to reconstruct deep links.
 */
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
