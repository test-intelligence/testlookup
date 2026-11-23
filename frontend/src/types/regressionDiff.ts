/**
 * `GET /api/v1/runs/{run_id}/regression-diff` (the run page's Changes tab).
 *
 * W10 FAIL-09: the commits are the stored per-project range (the Suspects
 * panel's), read from the project's GitHub / GHE integration, with
 * `commit_range_status` saying why there are none. Every W10 field is optional:
 * an older server, and every fixture written before W10, renders today's tab.
 */

/** Why a run's commit list is unavailable (the server's `COMMIT_RANGE_REASONS`, in its order). */
export const COMMIT_RANGE_REASONS = [
  'offline_mode',
  'github_checks_off',
  'no_integration',
  'integration_disabled',
  'repo_mismatch',
  'no_token',
  'no_commit_hash',
  'no_baseline_commit',
  'not_resolved',
  'fetch_failed',
  'no_commits',
] as const

export type CommitRangeReason = (typeof COMMIT_RANGE_REASONS)[number]

export interface CommitRangeStatus {
  /** Commits are listed. */
  available: boolean
  /** connector | supplied | unavailable */
  source: string
  /** supplied | green_baseline | last_completed_run | unavailable */
  base_source: string
  /** False: counted from the last completed run, not the last green one. */
  base_anchor_is_strong: boolean
  base_run_id?: string | null
  /** The build the commit range starts after (NOT necessarily the diff's baseline). */
  base_build_number?: string | null
  /** A `COMMIT_RANGE_REASONS` value (a plain string: an unknown one still renders); null when available. */
  reason?: string | null
  commit_count: number
  resolved_at?: string | null
}

export interface RegressionDiffCommit {
  /** The first 8 characters. */
  sha: string
  message: string
  author?: string | null
  /** The commit's committed_at. */
  timestamp?: string | null
  full_sha?: string | null
  /** An https link built by the server; rendered only when it matches `^https?://`. */
  commit_url?: string | null
}

export interface RegressionDiff {
  baseline_available: boolean
  baseline_run_id?: string
  baseline_build_number?: string
  /**
   * D-10: how the baseline was chosen. `passing` = the last run of this branch with no failed or broken test;
   * `threshold` = none, so the nearest earlier run at or above `baseline_threshold` %. Absent on an older server.
   */
  baseline_kind?: 'passing' | 'threshold' | null
  /** D-10: the fallback's minimum pass rate (RELEASE_PASS_RATE_THRESHOLD * 0.85; 76.5 by default). */
  baseline_threshold?: number
  /**
   * D-10: the run's first failing test (by name), on both answers; null when nothing failed. `?bisect=top`
   * opens it when no failure is new.
   */
  first_failing_test?: { test_name: string; suite_name: string | null; test_fingerprint: string } | null
  pass_rate?: number
  baseline_pass_rate?: number
  pass_rate_delta?: number
  /** W23b: `test_fingerprint` (optional; an older server sends none) opens the test's narrowed range. */
  new_failing_tests?: Array<{ test_name: string; suite_name: string | null; test_fingerprint?: string | null }>
  new_failing_count?: number
  resolved_count?: number
  commit_range?: RegressionDiffCommit[]
  commit_range_status?: CommitRangeStatus
  /** The live-run payload (the run is still executing). */
  status?: string
  diff_available?: boolean
  reason?: string
  /**
   * W22a ACT-04: set when this run's stored baseline was later purged (the
   * build number survives the purge). Both answers (baseline found or not)
   * carry it; absent on an older server, `null` when nothing was purged.
   */
  baseline_purged?: RegressionDiffBaselinePurged | null
  /**
   * W23b FAIL-13: the configuration drift against this answer's baseline (baseline-found answers only).
   * `unknown` on purpose: read it through `parseConfigDrift` (`types/configDrift.ts`) at the use site, so a
   * malformed or older server value cannot type-lie; absent → the Changes tab renders exactly as before.
   */
  config_drift?: unknown
}

/** `baseline_purged`: the purged baseline's build (may be null) and when the purge removed it. */
export interface RegressionDiffBaselinePurged {
  build_number: string | null
  purged_at: string
}
