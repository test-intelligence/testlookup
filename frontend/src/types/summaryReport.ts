/**
 * Types for the Project Summary Report — consolidated pass/fail/skip/flaky
 * snapshot per project. Mirrors ``backend/app/models/schemas.py``
 * SummaryReportResponse.
 */

export type SummaryReportMode = 'window' | 'latest'

export interface SummaryTotals {
  total_test_cases: number
  passed: number
  failed: number
  skipped: number
  broken: number
  evaluated: number
  pass_rate_pct: number
  fail_rate_pct: number
  skip_rate_pct: number
  broken_rate_pct: number
  /** Pass rate that ignores skipped tests — matches the /overview headline. */
  weighted_pass_rate_pct: number
}

export interface SummarySuiteRow {
  suite_name: string
  total: number
  passed: number
  failed: number
  skipped: number
  broken: number
  pass_rate_pct: number
  weighted_pass_rate_pct: number
  last_run_at: string | null
}

export interface SummaryTopFailingTest {
  suite_name: string | null
  class_name: string | null
  test_name: string
  failures: number
}

export interface SummaryReport {
  project_id: string | null
  project_name: string | null
  mode: SummaryReportMode
  window_days: number
  generated_at: string
  period_start: string
  period_end: string
  totals: SummaryTotals
  run_count: number
  /** ``null`` in ``latest`` mode where the denominator is meaningless. */
  runs_per_day: number | null
  avg_duration_ms: number
  latest_run_at: string | null
  flaky_test_count: number
  flaky_rate_pct: number
  suites: SummarySuiteRow[]
  top_failing_tests: SummaryTopFailingTest[]
}
