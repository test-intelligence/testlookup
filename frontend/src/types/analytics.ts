import type { PaginatedResponse } from './common'

export interface DashboardMetricValue {
  value: number | string
  trend?: number | null
  trend_direction?: 'up' | 'down' | 'flat'
}

export interface DashboardSummary {
  release_readiness?: 'GREEN' | 'AMBER' | 'RED'
  total_executions_7d?: DashboardMetricValue
  avg_pass_rate_7d?: DashboardMetricValue
  active_defects?: DashboardMetricValue
  flaky_test_count?: DashboardMetricValue
  new_failures_24h?: DashboardMetricValue
  avg_duration_ms?: DashboardMetricValue
}

export interface FlakyTestItem {
  test_fingerprint: string
  test_name: string
  suite_name?: string
  total_runs: number
  fail_count: number
  failure_rate_pct: number
}

export interface FailureCategoryItem {
  category: string
  count: number
}

export interface TopFailingItem {
  test_name: string
  fail_count: number
}

export interface CoverageSummary {
  unique_tests: number
  suite_count: number
  total_executions: number
  avg_pass_rate: number
  days_with_runs: number
}

export interface CoverageSuite {
  suite_name: string
  unique_tests: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
}

export interface CoverageResponse {
  summary: CoverageSummary
  suites: CoverageSuite[]
}

export interface DefectItem {
  id: string
  jira_ticket_id?: string
  jira_ticket_url?: string
  jira_status?: string
  failure_category?: string
  resolution_status: string
  ai_confidence_score?: number
  created_at: string
  resolved_at?: string
  test_name: string
  suite_name?: string
}

export type DefectResponse = PaginatedResponse<DefectItem>

export interface SuiteDetailSummary {
  unique_tests: number
  total_executions: number
  passed: number
  failed: number
  pass_rate: number
  avg_duration_ms: number | null
}

export interface SuiteDetailTestCase {
  test_fingerprint: string
  test_name: string
  class_name: string | null
  total_executions: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
  avg_duration_ms: number | null
  last_status: string | null
  last_error: string | null
  last_run_at: string | null
  is_flaky: boolean
}

export interface SuiteDetailRun {
  test_run_id: string
  build_number: string | null
  run_date: string | null
  passed: number
  failed: number
  skipped: number
  pass_rate: number
}

export interface SuiteDetailResponse {
  summary: SuiteDetailSummary
  test_cases: SuiteDetailTestCase[]
  recent_runs: SuiteDetailRun[]
}
