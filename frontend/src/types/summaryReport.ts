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
  /** F-067: the population this rate is over — "per unique test" here. */
  pass_rate_basis?: string | null
  pass_rate_basis_label?: string | null
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
  /**
   * Step-level success rate for this suite (% of captured steps that passed),
   * where granular step data exists (Phase 5 enrichment). Optional + defaults
   * to None on the backend — absent for suites whose tests have no captured
   * steps. ``passed_steps`` / ``total_steps`` back the percentage.
   */
  step_success_rate?: number | null
  passed_steps?: number | null
  total_steps?: number | null
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
