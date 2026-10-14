/**
 * EXEC-03: execution-metrics API types (contract: docs/deferred-scope-2026-10-13/work/EXEC-03-contract.md).
 *
 * Units: every `*_ms` is milliseconds; every `*_at` / window bound is an ISO-8601 UTC instant; `day` is a UTC
 * `YYYY-MM-DD`; `*_pct` is percent; `*_share` is 0-1; `ratio` / `parallelism` are plain ratios.
 * `null` = NOT MEASURED (render "not reported" / a gap) — never 0. A block with `measured: false` carries a
 * human-readable `reason` (render as plain text). Names are untrusted text: never render them as HTML.
 */
import type { DurationRegressions } from '@/services/durationRegressionsService'

export type TestTimingSource = 'report' | 'derived' | 'live'
export type SuiteTimingSource = 'report' | 'derived' | 'derived_sum'

export interface Measured {
  measured: boolean
  reason: string | null
}

export interface RunTimingFigures {
  wall_clock_ms: number | null
  summed_ms: number | null
  parallelism: number | null
  /** null = not timed yet (backfill pending). */
  timing_source: SuiteTimingSource | null
  exec_started_at: string | null
  exec_finished_at: string | null
  test_count: number
  timed_count: number
  duration_count: number
}

export interface RunTimingPhases extends Measured {
  tests_with_phases: number
  setup_ms: number | null
  call_ms: number | null
  teardown_ms: number | null
  setup_share: number | null
  teardown_share: number | null
}

export interface SuiteExecution {
  suite_name: string
  suite_key: string
  source: SuiteTimingSource
  started_at: string | null
  finished_at: string | null
  wall_ms: number | null
  summed_ms: number | null
  setup_ms: number | null
  teardown_ms: number | null
  test_count: number
  timed_count: number
  passed: number
  failed: number
  broken: number
  skipped: number
  unknown: number
  parallelism: number | null
}

export interface RunTimingSuites extends Measured {
  total: number
  truncated: boolean
  items: SuiteExecution[]
}

export interface DurationBaseline {
  mean_ms: number
  stddev_ms: number
  p95_ms: number | null
  sample_count: number
  last_observed_ms: number | null
  last_observed_at: string | null
}

export interface RunTimingTest {
  test_id: string
  test_name: string
  test_fingerprint: string | null
  suite_name: string | null
  status: string
  duration_ms: number | null
  started_at: string | null
  finished_at: string | null
  setup_ms: number | null
  call_ms: number | null
  teardown_ms: number | null
  worker_id: string | null
  timing_source: TestTimingSource | null
  baseline: DurationBaseline | null
  baseline_ratio: number | null
}

export interface RunTimingSlowest extends Measured {
  limit: number
  total: number
  items: RunTimingTest[]
}

export interface RunTimingLane {
  worker_id: string | null
  test_count: number
  busy_ms: number | null
  started_at: string | null
  finished_at: string | null
  span_ms: number | null
}

export interface RunTimingWorkers extends Measured {
  total: number
  truncated: boolean
  lanes: RunTimingLane[]
}

export interface RunTimelineItem {
  test_id: string
  test_name: string
  suite_name: string | null
  status: string
  worker_id: string | null
  started_at: string
  finished_at: string
  offset_ms: number
  duration_ms: number | null
}

export interface RunTimeline extends Measured {
  limit: number
  total: number
  truncated: boolean
  origin: string | null
  end: string | null
  items: RunTimelineItem[]
}

/** `GET /api/v1/runs/{run_id}/timing` */
export interface RunTiming {
  run_id: string
  project_id: string
  status: string
  build_number: string | null
  branch: string | null
  created_at: string | null
  run: RunTimingFigures
  phases: RunTimingPhases
  suites: RunTimingSuites
  slowest: RunTimingSlowest
  workers: RunTimingWorkers
  timeline: RunTimeline
  duration_findings: DurationRegressions | null
}

export interface ExecutionPercentiles extends Measured {
  n: number
  p50_ms: number | null
  p95_ms: number | null
}

export interface SuiteHistoryPoint {
  run_id: string
  build_number: string | null
  run_created_at: string
  branch: string | null
  run_status: string | null
  source: SuiteTimingSource
  wall_ms: number | null
  summed_ms: number | null
  parallelism: number | null
  started_at: string | null
  finished_at: string | null
  test_count: number
  timed_count: number
  passed: number
  failed: number
  broken: number
  skipped: number
  unknown: number
}

/** `GET /api/v1/analytics/execution/suite-history` (points OLDEST FIRST). */
export interface SuiteExecutionHistory extends Measured {
  project_id: string | null
  suite_name: string | null
  suite_key: string | null
  branch: string | null
  days: number
  limit: number
  total: number
  truncated: boolean
  points: SuiteHistoryPoint[]
  stats: {
    wall: ExecutionPercentiles
    summed: ExecutionPercentiles
    parallelism_p50: number | null
  }
  meta?: Record<string, unknown>
}

export interface ExecutionDayPoint {
  day: string
  runs: number
  wall_clock_ms: number | null
  wall_runs: number
  summed_ms: number | null
  summed_runs: number
}

export interface ExecutionSuiteRow {
  suite_key: string
  suite_name: string
  current: { runs: number; wall: ExecutionPercentiles; summed: ExecutionPercentiles }
  previous: { runs: number; wall: ExecutionPercentiles; summed: ExecutionPercentiles }
  wall_p50_change_pct: number | null
  wall_p95_change_pct: number | null
  summed_p50_change_pct: number | null
  summed_p95_change_pct: number | null
}

export interface SuiteRegressionItem {
  suite_key: string
  /** null when no row of the suite carried a name; show `suite_key` then. */
  suite_name: string | null
  branch: string | null
  metric: 'wall' | 'summed'
  ratio: number
  recent_median_ms: number
  baseline_median_ms: number
  recent_runs: number
  baseline_runs: number
  observed_ms: number | null
  was_slow_before: boolean
  latest_run_id: string | null
  latest_run_at: string | null
}

export interface TestRegressionItem {
  test_fingerprint: string
  test_name: string | null
  suite_name: string | null
  baseline_mean_ms: number
  baseline_p95_ms: number | null
  sample_count: number
  window_p50_ms: number
  window_n: number
  ratio: number
}

export interface SlowestTestRow {
  test_fingerprint: string
  test_name: string | null
  suite_name: string | null
  n: number
  p50_ms: number | null
  p95_ms: number | null
  max_ms: number | null
}

export interface DurationHistogramBucket {
  lower_ms: number
  /** null = the open-ended top bucket. */
  upper_ms: number | null
  count: number
}

export interface DurationHistogram extends Measured {
  n: number
  buckets: DurationHistogramBucket[]
  p50_ms: number | null
  p95_ms: number | null
}

/** `GET /api/v1/analytics/execution-metrics` */
export interface ExecutionMetrics extends Measured {
  project_id: string | null
  days: number
  branch: string | null
  window_start: string
  window_end: string
  previous_window_start: string
  totals: {
    runs: number
    wall_clock_ms: number | null
    wall_runs: number
    summed_ms: number | null
    summed_runs: number
    parallelism: number | null
  }
  per_day: { grain: 'run' | 'suite'; points: ExecutionDayPoint[] }
  suites: { limit: number; total: number; truncated: boolean; items: ExecutionSuiteRow[] }
  regressions: {
    rule: Record<string, number | boolean>
    suites: Measured & { evaluated: number; considered: number; total: number; items: SuiteRegressionItem[] }
    tests: Measured & { considered: number; total: number; items: TestRegressionItem[] }
  }
  slowest_tests: Measured & { limit: number; items: SlowestTestRow[] }
  /** TRND-09 (VIZ-406): absent on a server older than the histogram. */
  duration_histogram?: DurationHistogram
  meta?: Record<string, unknown>
}

/**
 * EXEC-03 §4: the timing of one point of `GET /runs/{run_id}/tests/{test_id}/history` (additive fields on
 * `TestCaseHistoryPoint`). Optional: a server older than EXEC-03 does not send them; null = the format carried none.
 */
export interface TestHistoryPointTiming {
  started_at?: string | null
  finished_at?: string | null
  setup_ms?: number | null
  call_ms?: number | null
  teardown_ms?: number | null
  worker_id?: string | null
  timing_source?: TestTimingSource | null
}

/** EXEC-03 §4: the test's duration percentiles over the history window (every execution, not only the points). */
export interface TestDurationStats extends Measured {
  window_days: number
  n: number
  p50_ms: number | null
  p95_ms: number | null
  max_ms: number | null
}

/** EXEC-03 §4: the additive fields of the test history response. Absent on an older server. */
export interface TestHistoryDurationFields {
  duration_stats?: TestDurationStats | null
  /** null = the test has no `perf_baselines` row in this project. */
  baseline?: DurationBaseline | null
}

export interface ExecutionScope {
  projectId: string | null | undefined
  days?: number
  branch?: string | null
  releaseIds?: string[]
  suiteNames?: string[]
}
