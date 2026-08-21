import type { PaginatedResponse } from './common'

export interface DashboardMetricValue {
  value: number | string
  trend?: number | null
  trend_direction?: 'up' | 'down' | 'flat'
  /**
   * F-067: the POPULATION a rate is computed over. /overview counts every
   * execution ("per test execution", 81.0%) while the Summary Report counts
   * each distinct test once ("per unique test", 83.3%) — same window, both
   * correct. Optional because only the pass-rate metric carries it, and
   * because a cached pre-#588 payload will not have it.
   */
  basis?: string | null
  basis_label?: string | null
}

export type ReleaseReadinessBand = 'red' | 'orange' | 'yellow' | 'green'

export interface DashboardSummary {
  // null when the backend has no test executions to grade in the selected
  // window. UI renders a neutral "Pending" banner in that case.
  release_readiness?: 'GREEN' | 'AMBER' | 'RED' | null
  // 4-band classification from the active ReleaseGatePolicy. Falls back
  // to undefined when no policy is configured — the legacy 3-state
  // ``release_readiness`` is the source of truth in that case.
  release_readiness_band?: ReleaseReadinessBand | null
  release_readiness_downgrades?: string[]
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
  class_name?: string
  total_runs: number
  fail_count: number
  failure_rate_pct: number
  // 'auto' = intermittent pass/fail detected from history; 'manual' = a human
  // triaged it FLAKY_TEST on /my-failures (merged so /failures agrees with
  // /flaky-coach). Manual entries carry failure_rate_pct=100 as a marker.
  source?: 'auto' | 'manual'
  // FLK-P4 likely-cause attribution from intermittency signals (null when the
  // fingerprint has no granular window to attribute).
  likely_cause?: string | null
  likely_cause_code?: string | null
}

export interface FailureCategoryItem {
  category: string
  count: number
  /**
   * Derived failure-kind triad (US-9.1): 'product' | 'test_code' |
   * 'infrastructure' | 'unknown'. AI-derived from the category — see
   * backend/app/services/failure_kind.py (frontend mirror in
   * utils/failureKind.ts).
   */
  kind?: string
}

/** One bucket of the by-kind aggregation served alongside category items. */
export interface FailureKindCount {
  kind: string
  count: number
}

/** One row of the kind evidence checklist (AI-4). */
export interface KindEvidenceCheck {
  /** memory_recall | infra_shape | history_pattern | status_signal | classifier */
  check: string
  /** supports | contradicts | neutral | unavailable */
  verdict: string
  detail: string
}

/**
 * Evidence-checklist record backing an AI-classified failure kind (AI-4).
 * Mirrors backend/app/services/kind_evidence.py — deterministic re-weighing
 * of existing signals, no extra LLM call; basis is honest about that
 * ('heuristic_estimate' unless pinned by a human correction →
 * 'human_corrected').
 */
export interface KindEvidence {
  schema_version: number
  kind: string
  confidence: number
  confidence_basis: 'heuristic_estimate' | 'human_corrected' | 'empirical' | string
  pinned_by_human_correction: boolean
  classifier_confidence: number
  checks: KindEvidenceCheck[]
}

export interface KindEvidenceResponse {
  found: boolean
  test_case_id: string | null
  kind_evidence: KindEvidence | null
}

export interface FailureCategoriesResponse {
  items: FailureCategoryItem[]
  /** Parallel AI-derived kind aggregation — zero counts included. */
  by_kind?: FailureKindCount[]
  period_days?: number
}

export interface TopFailingItem {
  test_name: string
  fail_count: number
  /**
   * Stable test identity — ``sha256(class_name::test_name)[:16]``, same
   * formula as ingestion. The backend's top-failing query has always
   * returned it (it GROUPs BY fingerprint); declared here so the mute-to-
   * quarantine and analysis-lookup flows (US-2.4) can use it.
   */
  test_fingerprint?: string | null
  /** Suite the failing test belongs to (NULL for tests with no suite tag). */
  suite_name?: string | null
  /** Class / module qualifier from the test runner output. */
  class_name?: string | null
  /** AI-resolved failure category (PRODUCT_BUG / INFRASTRUCTURE / FLAKY / …). */
  failure_category?: string | null
  /**
   * Derived failure-kind triad (US-9.1), category-only fidelity (the
   * BROKEN-status nudge can't apply to fingerprint aggregates).
   */
  failure_kind?: string | null
  /** ISO timestamp of the most recent failure in the window. */
  last_failed?: string | null
  /**
   * FAILURE LOCATION (Phase 5): name of the first FAILED/BROKEN granular step
   * for this test, read from the LATEST-RUN-ONLY snapshot. Optional + may be
   * null when the test has no captured step data (or in unscoped/multi-tenant
   * views where the snapshot anchor can't be resolved).
   */
  failure_step?: string | null
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
  // US-6.2: when the Jira status mirror was last refreshed + whether Jira
  // says Done while the failure signature still fired recently.
  external_status_at?: string | null
  external_status_conflict?: boolean
  failure_category?: string
  resolution_status: string
  ai_confidence_score?: number
  created_at: string
  resolved_at?: string
  test_name: string
  suite_name?: string
}

export type DefectResponse = PaginatedResponse<DefectItem>

export type DefectIntakeSeverity = 'P0' | 'P1' | 'P2' | 'P3'

export type DefectIntakeCategory =
  | 'PRODUCT_BUG'
  | 'INFRASTRUCTURE'
  | 'TEST_DATA'
  | 'AUTOMATION_DEFECT'
  | 'FLAKY'
  | 'UNKNOWN'

export interface DefectIntakePayload {
  project_id: string
  title: string
  description?: string
  severity: DefectIntakeSeverity
  failure_category: DefectIntakeCategory
  component?: string
  test_name?: string
  suite_name?: string
  jira_ticket_url?: string
}

export interface DefectIntakeResponse {
  id: string
  project_id: string
  title: string
  severity: DefectIntakeSeverity
  failure_category?: string
  component?: string
  test_name?: string
  suite_name?: string
  jira_ticket_id?: string
  jira_ticket_url?: string
  resolution_status: string
  ai_confidence_score?: number
  created_at: string
}

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
