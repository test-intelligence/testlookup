// Types for the TestSuite + CanonicalTestCase API (Phase 5 of the test-case
// <-> test-suite linking feature). Shapes mirror
// backend/app/models/schemas.py:TestSuiteResponse / CanonicalTestCaseResponse.

export interface TestSuite {
  id: string
  project_id: string
  name: string
  description: string | null
  is_default: boolean
  tags: string[] | null
  test_case_count: number | null
  created_at: string
  updated_at: string | null
}

export interface TestSuiteListResponse {
  items: TestSuite[]
  total: number
}

export interface TestSuiteCreatePayload {
  project_id: string
  name: string
  description?: string | null
  tags?: string[] | null
  /** Optional owner picked at creation. Backend enforces QA_LEAD role; a
   *  non-eligible user returns HTTP 400. Leaving unset falls back to the
   *  project's default QA lead via ``resolve_suite_owner``. */
  owner_user_id?: string | null
}

export interface TestSuiteUpdatePayload {
  name?: string
  description?: string | null
  tags?: string[] | null
}

export interface CanonicalTestCase {
  id: string
  project_id: string
  test_suite_id: string
  test_suite_name: string | null
  test_fingerprint: string
  test_name: string
  class_name: string | null
  status: 'active' | 'deleted' | 'needs_review' | string
  source: 'execution' | 'managed' | 'linked' | string
  first_seen_run_id: string | null
  last_seen_run_id: string | null
  /** Per-run TestCase.id in last_seen_run — drives "open test" deep-link. */
  last_seen_test_case_id?: string | null
  deleted_at_run_id: string | null
  deleted_observed_at?: string | null
  managed_test_case_id: string | null
  retirement_confirmed_at?: string | null
  retirement_confirmed_by_id?: string | null
  retirement_reason?: string | null
  review_tag: string | null
  tags: string[] | null
  run_count: number | null
  created_at: string
  updated_at: string | null
}

export interface CanonicalTestCaseListResponse {
  items: CanonicalTestCase[]
  total: number
  /** Set on paged lists; absent where the whole list is returned. */
  page?: number
  size?: number
}

export interface CanonicalPromotionResponse {
  canonical: CanonicalTestCase
  managed_case: import('./test-management').ManagedTestCase
}

export interface CanonicalRunHistoryItem {
  test_case_id: string
  test_run_id: string
  status: string
  duration_ms: number | null
  suite_name: string | null
  created_at: string
  // Run context — what makes the history a timeline rather than a list.
  build_number: string | null
  branch: string | null
  /**
   * Resolved environment, or null when the run never recorded one and nothing
   * could be derived. Null means UNKNOWN, not "default" — do not group nulls
   * together, or a corpus that simply never recorded an environment will look
   * perfectly environment-consistent.
   */
  environment: string | null
  /** 'explicit' | 'derived' | 'unknown' — how much to trust `environment`. */
  environment_source: string
  // Retry evidence: a retry is evidence, not a way to make the build green.
  retry_count: number | null
  is_flaky_run: boolean | null
}

export interface CanonicalRunHistoryResponse {
  items: CanonicalRunHistoryItem[]
  total: number
}

// Shape of POST /api/v1/canonical-test-cases/bulk-link — backend caps the
// batch at 200 and atomically rejects the whole call on any cross-project
// id. ``missing_ids`` surfaces ids the UI selected that no longer resolve
// (deleted in flight) so the page can clear them from selection.
export interface CanonicalTestCaseBulkLinkResponse {
  moved: number
  skipped_already_in_target: number
  missing_ids: string[]
}
