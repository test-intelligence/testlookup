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
  managed_test_case_id: string | null
  review_tag: string | null
  tags: string[] | null
  run_count: number | null
  created_at: string
  updated_at: string | null
}

export interface CanonicalTestCaseListResponse {
  items: CanonicalTestCase[]
  total: number
}

export interface CanonicalRunHistoryItem {
  test_case_id: string
  test_run_id: string
  status: string
  duration_ms: number | null
  suite_name: string | null
  created_at: string
}

export interface CanonicalRunHistoryResponse {
  items: CanonicalRunHistoryItem[]
  total: number
}
