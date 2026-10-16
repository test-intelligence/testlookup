import type { EnvelopeMeta } from '@/lib/viz/contracts'
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
  /**
   * TRND-05, `avg_pass_rate_7d` only: the pass rate's move against the
   * previous window of the same length, in percentage points (80 -> 88 = 8).
   * `trend` is the RELATIVE change in percent (10 for the same move). `null`
   * when either window has no executions; absent in a pre-TRND-05 payload.
   */
  pass_rate_delta_pp?: number | null
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
   * The run holding that most recent failure (HON-05). The row's Suspects
   * panel attributes commits against it; it used to take the project's newest
   * FAILED run, which for any other test is a run it did not fail in.
   */
  last_failed_run_id?: string | null
  /** That run's build number, to name it in the Suspects panel (HON-05). */
  last_failed_build?: string | null
  /**
   * FAILURE LOCATION (Phase 5): name of the first FAILED/BROKEN granular step
   * for this test, read from the LATEST-RUN-ONLY snapshot. Optional + may be
   * null when the test has no captured step data (or in unscoped/multi-tenant
   * views where the snapshot anchor can't be resolved).
   */
  failure_step?: string | null
  /**
   * FAIL-01 per-failure detail. True only when exactly one project is in
   * scope; when false every key below is null and the expanded row asks the
   * user to select a project.
   */
  detail_available?: boolean
  /** The run of the test's newest FAILED/BROKEN row inside the request's scope. */
  latest_run_id?: string | null
  /** That row's id: the "Open in run" target (`/runs/{run}/tests/{case}`). */
  latest_test_case_id?: string | null
  /** That row's `created_at`, ISO UTC. */
  latest_failed_at?: string | null
  /**
   * First line of its error message (server-truncated to 300 chars,
   * secrets redacted); null for a blank message. Render as TEXT only.
   */
  latest_error?: string | null
  /** First 20 lines of its stack trace (each <= 300 chars, redacted); null when none. TEXT only. */
  latest_stack_head?: string | null
  /** The eligible assignee's name, else the ownership-rule team, else null. */
  owner?: string | null
  /** `assignee` | `rule` | `component_map` | `test_label` | null (plain str, never an enum). */
  owner_source?: string | null
  /** The team the project's ownership rules give the test (also beside an assignee). */
  owner_team?: string | null
}

/**
 * FAIL-02 `GET /api/v1/analytics/time-to-fix`: the median time from a test's
 * first failure to its next pass over the window. `measured: false` (fewer
 * than `min_fixes` fixes) leaves both hour figures null — never 0.
 */
export interface TimeToFixResponse {
  measured: boolean
  reason: string | null
  median_hours: number | null
  mean_hours: number | null
  fixed: number
  open: number
  excluded_flaky_tests: number
  excluded_oscillating_tests: number
  min_fixes: number
  window_days: number
  meta?: EnvelopeMeta
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
  /**
   * W08 TRND-07: the suite's newest run in the window (ISO UTC) and that run's
   * id, for the "last run" chip. Optional: a payload from before W08 has neither.
   */
  last_run_at?: string | null
  last_run_id?: string | null
}

export interface CoverageResponse {
  summary: CoverageSummary
  suites: CoverageSuite[]
}

/**
 * W08 TRND-07 `GET /api/v1/analytics/coverage/history`: one point per stored
 * daily snapshot, ascending; a day without a snapshot is ABSENT (a gap).
 */
export interface CoverageHistoryPoint {
  /** YYYY-MM-DD, the UTC day the snapshot describes. */
  day: string
  catalogue_tests: number
  /** Distinct tests executed in the 30 days ending `day`. */
  exercised_tests: number
  /** 1 dp; `null` when the catalogue was empty (not measured, never 0). */
  exercised_pct: number | null
  suites: number
}

export interface CoverageHistory {
  window_days: number
  /** `false` before the project's first snapshot; `reason` says so. */
  measured: boolean
  reason: string | null
  first_snapshot_day: string | null
  points: CoverageHistoryPoint[]
  meta?: EnvelopeMeta
}

/** W08 TRND-07 `GET /api/v1/analytics/coverage/untagged-tests`: one test behind the untagged callout. */
export interface UntaggedTest {
  /** `null` when the fingerprint has no catalogue row yet (listed, not movable). */
  canonical_id: string | null
  test_fingerprint: string
  test_name: string
  class_name: string | null
  catalogue_suite_id: string | null
  catalogue_suite: string | null
  executions: number
  last_executed_at: string | null
}

export interface UntaggedTestList {
  items: UntaggedTest[]
  /** Distinct untagged tests in the window, beyond this page. */
  total: number
  page: number
  size: number
  window_days: number
  meta?: EnvelopeMeta
}

// ── W08 defensive guards: a missing or malformed answer is `null`, a single
// malformed item is dropped. No component reads `.points` / `.items` off a raw
// response (W07's "batch.items is not iterable" crash).

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function finite(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** A string, or `null` for null / absent; `undefined` for any other type (malformed). */
function strOrNull(v: unknown): string | null | undefined {
  if (v === null || v === undefined) return null
  return typeof v === 'string' ? v : undefined
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

function parseHistoryPoint(raw: unknown): CoverageHistoryPoint | null {
  if (!isRecord(raw)) return null
  const { day, catalogue_tests, exercised_tests, exercised_pct, suites } = raw
  if (typeof day !== 'string' || !ISO_DAY.test(day.slice(0, 10))) return null
  if (!finite(catalogue_tests) || !finite(exercised_tests) || !finite(suites)) return null
  if (exercised_pct !== null && exercised_pct !== undefined && !finite(exercised_pct)) return null
  return {
    day: day.slice(0, 10),
    catalogue_tests,
    exercised_tests,
    exercised_pct: finite(exercised_pct) ? exercised_pct : null,
    suites,
  }
}

export function parseCoverageHistory(raw: unknown): CoverageHistory | null {
  if (!isRecord(raw)) return null
  if (!Array.isArray(raw.points)) return null
  if (typeof raw.measured !== 'boolean') return null
  const reason = strOrNull(raw.reason)
  const first = strOrNull(raw.first_snapshot_day)
  if (reason === undefined || first === undefined) return null
  const points: CoverageHistoryPoint[] = []
  for (const item of raw.points) {
    const point = parseHistoryPoint(item)
    if (point) points.push(point)
  }
  points.sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0))
  return {
    window_days: finite(raw.window_days) ? raw.window_days : 0,
    measured: raw.measured,
    reason,
    first_snapshot_day: first,
    points,
    meta: isRecord(raw.meta) ? (raw.meta as unknown as EnvelopeMeta) : undefined,
  }
}

function parseUntaggedTest(raw: unknown): UntaggedTest | null {
  if (!isRecord(raw)) return null
  const { test_fingerprint, test_name, executions } = raw
  if (typeof test_fingerprint !== 'string' || test_fingerprint === '') return null
  if (typeof test_name !== 'string') return null
  if (!finite(executions)) return null
  const canonical = strOrNull(raw.canonical_id)
  const cls = strOrNull(raw.class_name)
  const suiteId = strOrNull(raw.catalogue_suite_id)
  const suite = strOrNull(raw.catalogue_suite)
  const last = strOrNull(raw.last_executed_at)
  if ([canonical, cls, suiteId, suite, last].some((v) => v === undefined)) return null
  return {
    canonical_id: canonical === '' ? null : (canonical ?? null),
    test_fingerprint,
    test_name,
    class_name: cls ?? null,
    catalogue_suite_id: suiteId ?? null,
    catalogue_suite: suite ?? null,
    executions,
    last_executed_at: last ?? null,
  }
}

export function parseUntaggedTests(raw: unknown): UntaggedTestList | null {
  if (!isRecord(raw)) return null
  if (!Array.isArray(raw.items)) return null
  const items: UntaggedTest[] = []
  for (const item of raw.items) {
    const test = parseUntaggedTest(item)
    if (test) items.push(test)
  }
  return {
    items,
    total: finite(raw.total) ? raw.total : items.length,
    page: finite(raw.page) ? raw.page : 1,
    size: finite(raw.size) ? raw.size : items.length,
    window_days: finite(raw.window_days) ? raw.window_days : 0,
    meta: isRecord(raw.meta) ? (raw.meta as unknown as EnvelopeMeta) : undefined,
  }
}

export interface DefectItem {
  id: string
  /** The defect's own title, severity (CRITICAL/HIGH/MEDIUM/LOW) and component;
   *  null on defects created without them (the dev seed's, for one). */
  title?: string | null
  severity?: string | null
  component?: string | null
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
  // W06 (all optional: a fixture or a cached payload from before the wave has none of them).
  /** The defect's project: the row menu PATCHes `/projects/{project_id}/defects/{id}`. */
  project_id?: string
  /** 'P0'..'P3', derived server-side (`defect_status.priority_sql`); the page's
   *  `classifySeverity` is only the fallback for a row without it. */
  priority?: string | null
  /** DEF-02: the defect's own team, else the one the project's ownership rules give it; null = none. */
  owner_team?: string | null
  /** Where `owner_team` came from: 'defect' | 'rule' | 'component_map' | 'test_label'. */
  owner_source?: string | null
  /** DEF-01: the assigned person; `assignee_name` is null for an ineligible (service,
   *  synthetic, inactive) account even when the id is stored. */
  assignee_user_id?: string | null
  assignee_name?: string | null
  /** 'suggested' | 'pending_review' | 'approved' | 'executed' | 'rejected'. */
  approval_status?: string | null
  /** DEF-06: what the approval's Jira filing did: 'filed' | 'linked' | 'not_filed' | 'failed' | 'unknown';
   *  null when no filing was attempted. `jira_filing_reason` names why (an availability reason or the server's text). */
  jira_filing_status?: string | null
  jira_filing_reason?: string | null
  /** The release the defect was found in (already on the wire, now typed). */
  release_name?: string | null
}

export type DefectResponse = PaginatedResponse<DefectItem> & { meta?: EnvelopeMeta }

/** DEF-04 `GET /analytics/defects/trend`: one point per UTC day of the window. */
export interface DefectTrendPoint {
  date: string
  opened: number
  closed: number
  /** Open (active) defects at the END of the day. */
  open_backlog: number
  p0p1_open: number
  /** Mean days to resolve over the trailing 7 points; null when nothing was resolved in them. */
  mttr_days: number | null
}

export interface DefectTrendTotals {
  open: number
  p0_open: number
  p1_open: number
  /** Over the whole window; null when nothing was resolved in it. */
  mttr_days: number | null
  resolved_in_window: number
  opened_in_window: number
  closed_in_window: number
  opened_last_7d: number
  closed_last_7d: number
  net_last_7d: number
  /** Legacy closed rows without a `resolved_at`: excluded from the points, counted here. */
  closed_without_time: number
}

export interface DefectTrendResponse {
  points: DefectTrendPoint[]
  totals: DefectTrendTotals
  window_days: number
  meta?: EnvelopeMeta
}

/** DEF-03 `GET /analytics/defects/escape-rate`. `rate` is null below the sample floor (`measured: false`). */
export interface DefectEscapeRateResponse {
  rate: number | null
  escaped: number
  attributed: number
  in_window: number
  measured: boolean
  reason: string | null
  sample_floor: number
  window_days: number
  outcomes: { incidents: number; rollbacks: number; releases: number }
  meta?: EnvelopeMeta
}

/** The scope the three defect hooks (list, trend, escape rate) send. Unset keys are not sent. */
export interface DefectScopeQuery {
  releaseIds?: string[]
  releaseMatch?: 'found_in' | 'affects'
  suiteName?: string | null
  days?: number
}

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
  /** W06 DEF-03: the releases the defect affects (ids of THIS project's releases; the
   *  server refuses any other with one 422). Sent only when one is chosen. */
  affects_releases?: string[]
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
