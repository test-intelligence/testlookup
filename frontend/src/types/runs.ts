import type { PaginatedResponse } from './common'

export interface TestRun {
  id: string
  project_id?: string
  project_name?: string
  build_number: number | string
  jenkins_job?: string
  branch?: string
  status: string
  passed_tests: number
  failed_tests: number
  skipped_tests: number
  broken_tests?: number
  total_tests: number
  pass_rate: number
  duration_ms?: number
  created_at: string
  ocp_pod_name?: string
  release_name?: string
  release_id?: string
  trigger_source?: string
}

export type TestRunListResponse = PaginatedResponse<TestRun>

export interface RunTestCase {
  id: string
  test_name: string
  full_name?: string
  class_name?: string
  suite_name?: string
  status: string
  duration_ms?: number
  failure_category?: string
  severity?: string
  feature?: string
  owner?: string
  created_at: string
  tags?: string[]
  error_message?: string
  has_attachments?: boolean
  ocp_pod_name?: string
}

export type RunTestCaseListResponse = PaginatedResponse<RunTestCase>
