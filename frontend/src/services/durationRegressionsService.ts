/**
 * EXEC-05: a run's duration regressions (`GET /api/v1/runs/{run_id}/duration-regressions`).
 *
 * `measured: false` with a `reason` means the run could not be judged (not
 * enough history, detection off for the project, not evaluated yet). An empty
 * `spikes` list on a measured run is "nothing got slower"; on an unmeasured
 * one it says nothing, and the UI must not read it as "no regressions".
 */
import { getData } from './http'

/** A PASSED test slower than its baseline (>= 3 sigma over 10+ runs). */
export interface DurationSpike {
  test_fingerprint: string
  test_name: string | null
  suite_name: string | null
  observed_ms: number
  baseline_mean_ms: number
  baseline_stddev_ms: number
  baseline_p95_ms: number
  sample_count: number
  sigma: number
  delta_ms: number | null
  ratio: number | null
  previous_ms: number | null
  /** The previous run of the test was already this slow. */
  was_slow_before: boolean
  /** Slow on this run and not on the previous one. */
  new: boolean
}

/** A suite whose median over its last 5 runs is >= 1.5x the median of the 20 before. */
export interface SuiteDurationRegression {
  suite_name: string | null
  suite_key: string
  /** `wall` (wall clock) or `summed` (summed test time). */
  metric: string
  ratio: number | null
  recent_median_ms: number | null
  baseline_median_ms: number | null
  recent_runs: number
  baseline_runs: number
  observed_ms: number | null
  was_slow_before: boolean
  new: boolean
}

export interface DurationCheck {
  measured?: boolean
  reason?: string | null
  history?: number
  required_history?: number
  [key: string]: unknown
}

export interface DurationRegressions {
  run_id: string
  schema_version: number
  measured: boolean
  reason: string | null
  /** False: computed when asked, the run was not evaluated on ingest. */
  stored: boolean
  evaluated_at: string | null
  spikes: DurationSpike[]
  spike_count: number
  suites: SuiteDurationRegression[]
  suite_regression_count: number
  test_check: DurationCheck
  suite_check: DurationCheck
  notified: { tests?: string[]; suites?: string[] }
}

export const durationRegressionsService = {
  get: (runId: string) =>
    getData<DurationRegressions>(`/api/v1/runs/${encodeURIComponent(runId)}/duration-regressions`),
}
