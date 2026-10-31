/**
 * W25a HYG-04 phase 1: the suite-membership parity harness (the 7-day cutover clock).
 *
 * The constants mirror `backend/app/services/suite_parity_vocab.py` value for
 * value (pinned by `backend/tests/test_suite_parity_vocab_parity.py`, which
 * reads this file as TEXT — keep every constant a plain literal, one record
 * entry per line). Enum-like fields are plain strings on the wire.
 *
 * Suite names, test names and project names below come from customer data:
 * render them as React text, never as HTML, and never use them to build an href.
 */

export const RUN_STATUSES = ['running', 'clean', 'diffs', 'failed'] as const
export type SuiteParityRunStatus = (typeof RUN_STATUSES)[number]

export const RUN_TRIGGERS = ['beat', 'admin'] as const
export type SuiteParityRunTrigger = (typeof RUN_TRIGGERS)[number]

/** Count as differences and break the streak. */
export const BLOCKING_REASONS = [
  'missing_in_canonical',
  'missing_in_legacy',
  'retired_but_seen',
  'retire_pending',
  'name_drift',
  'first_seen_differs',
] as const

/** The canonical model's documented semantics: reported, never counted against the cutover. */
export const EXPECTED_REASONS = [
  'deletion_window',
  'legacy_stale',
  'moved_suite',
  'default_suite',
  'catalog_only',
  'name_stale',
  'first_seen_multi_suite',
] as const

export type SuiteParityReason = (typeof BLOCKING_REASONS)[number] | (typeof EXPECTED_REASONS)[number]

/** Informational counts on a run (not derivable from the catalog after the cutover). */
export const INFO_COUNTS = ['legacy_modified_events', 'legacy_restored_events'] as const

/** D26: seven consecutive clean UTC days allow the cutover. */
export const REQUIRED_CLEAN_DAYS = 7
/** The history read covers runs started in the last 14 UTC days. */
export const HISTORY_DAYS = 14
/** Sample rows kept per reason on one run. */
export const SAMPLE_PER_REASON = 20

/** One sentence per reason (the card's sample table "Reason" column). */
export const REASON_LABEL: Record<string, string> = {
  missing_in_canonical: 'Missing from the catalog',
  missing_in_legacy: 'Missing from the old tables',
  retired_but_seen: 'Retired, but ran recently',
  retire_pending: 'Not retired yet',
  name_drift: 'Name differs',
  first_seen_differs: 'First run differs',
  deletion_window: 'Absent from one run only',
  legacy_stale: 'Suite no longer runs',
  moved_suite: 'Moved to another suite',
  default_suite: 'In the default suite',
  catalog_only: 'Catalog only',
  name_stale: 'Renamed since',
  first_seen_multi_suite: 'In several suites',
}

/** One comparison run (`GET /api/v1/admin/maintenance/suite-membership-parity` → `runs[]`). */
export interface SuiteParityRunSummary {
  id: string
  trigger: string
  status: string
  started_at: string
  finished_at?: string | null
  /** The admin's username for trigger="admin"; null for the beat or a removed user. */
  requested_by_name?: string | null
  window_runs: number
  projects_checked: number
  keys_compared: number
  blocking_diffs: number
  expected_divergences: number
}

export interface SuiteParityHistory {
  /** Runs started in the last HISTORY_DAYS UTC days, newest first. */
  runs: SuiteParityRunSummary[]
  streak_days: number
  required_days?: number
  cutover_ready: boolean
  /** The reconciler's window today (0 = the reconciler is off). */
  window_runs_now?: number
}

export interface SuiteParityProjectLine {
  project_id: string
  /** Null for a project deleted since the run. */
  project_name?: string | null
  blocking: number
  expected: number
}

export interface SuiteParitySampleRow {
  reason: string
  blocking: boolean
  project_id: string
  project_name?: string | null
  suite: string
  fingerprint: string
  test_name?: string | null
  /** "active" | "removed" | "absent" */
  legacy: string
  /** "active" | "retired" | "other_suite" | "absent" */
  canonical: string
}

/** `GET /api/v1/admin/maintenance/suite-membership-parity/{parity_run_id}`. */
export interface SuiteParityRunDetail extends SuiteParityRunSummary {
  /** Every reason + INFO_COUNTS, zeros included. */
  counts: Record<string, number>
  per_project: SuiteParityProjectLine[]
  sample: SuiteParitySampleRow[]
  error_code?: string | null
}

/** `POST /api/v1/admin/maintenance/suite-membership-parity` → 202. */
export interface SuiteParityQueued {
  queued: boolean
  task_id: string
  task_name: string
}
