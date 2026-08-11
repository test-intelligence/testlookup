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
  /** Results whose reported status was outside PASSED/FAILED/SKIPPED/BROKEN. */
  unknown_tests?: number
  total_tests: number
  pass_rate: number
  duration_ms?: number
  /** Run start timestamp (TestRun.start_time). Set by ingestion / live close. */
  start_time?: string | null
  /** Run completion timestamp (TestRun.end_time). May be null while a live run is still in flight. */
  end_time?: string | null
  created_at: string
  ocp_pod_name?: string
  release_name?: string
  release_id?: string
  trigger_source?: string
  /** How the run entered TestLookup: 'live' | 'sdk' | 'upload' | 'file' | 'unknown'.
   *  Drives the "Uploaded" badge. Optional for older API responses. */
  ingestion_source?: string | null
  primary_suite_name?: string | null
  suite_names?: string[] | null
  /** Human-readable, per-(project, primary_suite_name) run number, 1-based.
   *  Computed server-side via ROW_NUMBER() so it's stable across pages.
   *  Optional for backward-compat with older API responses. */
  run_seq?: number | null
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
  /**
   * User this failure was auto-assigned to at ingest time (migration 0080).
   * NULL when the test passed, when the project has no resolvable owner,
   * or when the row pre-dates the auto-assignment feature. Resolution at
   * assignment time: TestSuiteOwner → default QA lead → manager → NULL.
   */
  assigned_to_user_id?: string | null
}

export type RunTestCaseListResponse = PaginatedResponse<RunTestCase>

/**
 * Index-only attachment reference captured alongside a granular step snapshot
 * (Phase 1: metadata only — ``source_ref`` is a reference, bytes are not
 * proxied). ``test_step_id`` is null for test-level (non-step) attachments.
 */
export interface TestAttachment {
  id: string
  test_step_id: string | null
  name: string
  source_ref: string | null
  media_type: string | null
  created_at: string
}

/**
 * One node in the granular step tree for a logical test. The snapshot is
 * latest-run-only per (project, fingerprint), so the steps reflect whichever
 * run most recently ingested this test — which may be newer than the run being
 * viewed. ``parameters`` is whatever the parser stored (Allure emits a list of
 * ``{name, value}``); the endpoint has no response_model so it is untyped JSON.
 */
export interface TestStep {
  id: string
  parent_step_id: string | null
  ordinal: number
  depth: number
  name: string
  keyword?: string | null
  status: string
  duration_ms?: number | null
  start_ms?: number | null
  assertion_message?: string | null
  assertion_trace?: string | null
  expected_value?: string | null
  actual_value?: string | null
  parameters?: unknown
  created_at: string
  steps: TestStep[]
  attachments: TestAttachment[]
}

/** One run in the cross-run pass/fail timeline (most-recent-first, capped 50). */
export interface TestCaseHistoryPoint {
  run_id: string | null
  run_label: string
  build_number: string | null
  run_seq: number | null
  status: string
  duration_ms: number | null
  created_at: string | null
}

/** Flakiness signal — reuses analytics_service thresholds + test_health_coach impact/classification. */
export interface TestCaseFlakiness {
  is_flaky: boolean
  failure_rate: number
  failure_rate_pct: number
  impact_score: number
  classification: string
  window_days: number
  total_runs: number
  passed: number
  failed: number
}

/** Test-case metadata block (owner, suite, first/last seen, timestamps). */
export interface TestCaseMetadata {
  owner: string | null
  assigned_to_user_id: string | null
  suite: string | null
  severity: string | null
  feature: string | null
  first_seen_run_id: string | null
  first_seen_run_label: string | null
  first_seen_at: string | null
  last_seen_run_id: string | null
  last_seen_run_label: string | null
  last_seen_at: string | null
  created_at: string | null
  updated_at: string | null
}

/** Response of ``GET /api/v1/runs/{run_id}/tests/{test_id}/history``. */
export interface TestCaseHistory {
  run_id: string
  test_id: string
  test_fingerprint: string | null
  test_name: string
  history: TestCaseHistoryPoint[]
  flakiness: TestCaseFlakiness
  metadata: TestCaseMetadata
}

/** One adjacent-run transition of a single step between PASSED and FAILED. */
export interface StepFlip {
  ordinal: number
  step_name: string
  from_run_id: string
  to_run_id: string
  from_status: string
  to_status: string
  /** "regression" (PASSED→FAILED) | "recovery" (FAILED→PASSED). */
  direction: string
}

/** Per-step roll-up across the analysed window — the actionable unit. */
export interface StepFlipSummary {
  ordinal: number
  step_name: string
  flip_count: number
  runs_observed: number
  last_status: string
  is_flaky: boolean
}

/** Cross-run step-flip report computed over the per-run step window. */
export interface StepFlipReport {
  has_step_flip: boolean
  runs_analyzed: number
  total_flips: number
  flips: StepFlip[]
  flipping_steps: StepFlipSummary[]
  summary: string
}

/** Response of ``GET /api/v1/runs/{run_id}/tests/{test_id}/step-flips``. */
export interface TestStepFlips {
  run_id: string
  test_id: string
  test_fingerprint: string | null
  report: StepFlipReport
}

/** One run test that has at least one flickering step (run-level roll-up). */
export interface RunStepFlipTest {
  test_id: string
  test_name: string
  test_fingerprint: string
  status: string
  report: StepFlipReport
}

/** Response of ``GET /api/v1/runs/{run_id}/step-flips`` — run-level roll-up of
 *  which TESTS in the run have a cross-run flickering step (FLK-P6 slice 5). */
export interface RunStepFlips {
  run_id: string
  project_id: string
  tests_analyzed: number
  tests_with_flips: number
  total_flips: number
  /** True when the run had more anchored tests than the analysis cap. */
  truncated: boolean
  tests: RunStepFlipTest[]
}

/** Response of ``GET /api/v1/runs/{run_id}/tests/{test_id}/steps``. */
export interface TestStepsTree {
  run_id: string
  test_id: string
  test_name: string
  status: string
  step_count: number | null
  retry_count: number | null
  is_flaky_run: boolean | null
  stack_trace: string | null
  steps: TestStep[]
  /** Test-level attachments (those with ``test_step_id === null``). */
  attachments: TestAttachment[]
}
