/**
 * The hermetic answers of the pages P4 and P5 rebuilt whose fixtures lived
 * inside their own fold specs: Flaky tests, the Inbox, a test case, Test
 * cases, Pipeline runs (`/agents`), the release gate's stored blockers and
 * Releases. Moved here VERBATIM (UX
 * redesign P6) so the fold budget (`tests/ci-e2e/fold-budget.spec.ts`) opens
 * each page on the same answers as its own spec: a spec cannot import another
 * spec. Not a spec file.
 *
 * Every factory returns fresh state per call, as each spec's did: a proposal,
 * a decision or a trigger made in one test never leaks into the next.
 */
import type { ApiHandlers, ApiRequest } from '../../lib/production-pages'
import { daysAgo, isoAgo, LAYOUT, PROJECT_ID, RUN_ID } from './fixtures'
import { RUN_PAGE } from './fixtures-run'

const P = PROJECT_ID

// ── Flaky tests (`/flaky`, from `fold-flaky.spec.ts`) ──────────────────────

const RECOMMENDATIONS = ['QUARANTINE', 'INVESTIGATE', 'MONITOR', 'INVESTIGATE', 'QUARANTINE', 'MONITOR'] as const

/** Twelve flaky tests, the first three recommended for quarantine or investigation. */
const ENTRIES = Array.from({ length: 12 }, (_, i) => ({
  test_fingerprint: `fp-${String(i).padStart(2, '0')}`,
  test_name: `test_checkout_flow_${i}`,
  suite_name: i % 2 === 0 ? 'Checkout' : 'Payments',
  failure_rate: 0.6 - i * 0.04,
  total_runs: 30,
  failed_runs: 18 - i,
  flaky_since: isoAgo(20 - i),
  last_failure_at: isoAgo(i % 3),
  quarantine_recommendation: RECOMMENDATIONS[i % RECOMMENDATIONS.length],
  stabilization_actions: ['Wait for the network to settle before asserting'],
  impact_score: 90 - i * 5,
  status_history: ['PASSED', 'FAILED', 'PASSED', 'FAILED', 'PASSED', 'PASSED', 'FAILED', 'PASSED'],
  flaky_confidence_low: 0.4,
  flaky_confidence_high: 0.7,
  flaky_likely_cause: i === 0 ? 'Timing: a wait races the response' : null,
}))

const COACH = { project_id: P, total_flaky: 9, quarantine_candidates: 4, entries: ENTRIES }

function quarantineRow(i: number, status: string, fingerprint = `fp-q${i}`) {
  return {
    id: `00000000-0000-4000-8000-0000000003${String(i).padStart(2, '0')}`,
    project_id: P,
    test_fingerprint: fingerprint,
    test_name: `test_quarantine_${i}`,
    suite_name: 'Checkout',
    status,
    detection_method: 'flip_rate',
    flip_rate: 0.3,
    flip_window_size: 10,
    pass_count: 7,
    fail_count: 3,
    detected_at: isoAgo(6),
    last_failure_at: isoAgo(1),
    proposed_at: isoAgo(5),
    approved_at: null,
    approved_by_user_id: null,
    rejected_at: null,
    rejected_by_user_id: null,
    quarantine_start: null,
    quarantine_expires_at: null,
    quarantine_duration_days: 14,
    recheck_at: null,
    rationale: null,
    reviewer_notes: status === 'REJECTED' ? 'Real regression, not a flake' : null,
    owner_user_id: null,
    owner_name: 'QA Lead',
    defect_id: null,
    defect_jira_key: null,
    defect_jira_url: null,
    defect_external_status: null,
    defect_external_status_conflict: false,
    sla_days: null,
    stale_at: null,
    stale: false,
    consecutive_passes: 0,
    ready_to_promote: false,
    created_at: isoAgo(6),
    updated_at: isoAgo(1),
  }
}

/** The first detected test already has a live proposal; one more is quarantined; two are settled. */
const LIVE = [
  quarantineRow(1, 'PROPOSED', 'fp-00'),
  quarantineRow(2, 'PROPOSED'),
  quarantineRow(3, 'QUARANTINED'),
]
const SETTLED = [quarantineRow(4, 'RELEASED'), quarantineRow(5, 'REJECTED')]

const FLAKY_STATS = {
  detected: 0,
  proposed: 2,
  approved: 0,
  quarantined: 1,
  recheck_scheduled: 0,
  re_quarantined: 0,
  released: 1,
  rejected: 1,
  expired: 0,
  total_live: 3,
}

/**
 * Fresh state per test: a proposal made in one test never leaks into the
 * next. `proposals` records every POST /quarantine body.
 */
export function flakyHandlers(proposals: unknown[] = []): ApiHandlers {
  const live = [...LIVE]
  return [
    ...LAYOUT,
    [`/api/v1/projects/${P}/flaky-coach`, () => COACH],
    ['/api/v1/quarantine/stats', () => ({ ...FLAKY_STATS, proposed: live.filter((r) => r.status === 'PROPOSED').length })],
    [
      '/api/v1/quarantine',
      ({ url }) => (url.searchParams.get('live_only') === 'true' ? live : [...live, ...SETTLED]),
    ],
    [
      '/api/v1/quarantine',
      ({ route }) => {
        const body = route.request().postDataJSON() as { test_fingerprint: string; test_name: string }
        proposals.push(body)
        const row = { ...quarantineRow(9, 'PROPOSED', body.test_fingerprint), test_name: body.test_name }
        live.push(row)
        return row
      },
      'POST',
    ],
  ]
}

// ── The Inbox (`/my-failures`, from `fold-inbox.spec.ts`) ──────────────────

/** 25 of 40 open failures (two pages), the inbox's page size. */
const ASSIGNED_FAILURES = Array.from({ length: 25 }, (_, i) => ({
  id: `00000000-0000-4000-8000-0000000004${String(i).padStart(2, '0')}`,
  test_name: `test_checkout_step_${i}`,
  suite_name: i % 2 === 0 ? 'Checkout' : 'Payments',
  class_name: null,
  status: i % 4 === 0 ? 'BROKEN' : 'FAILED',
  severity: ['critical', 'major', 'minor', null][i % 4],
  failure_category: null,
  error_message: `AssertionError: expected 200 but got 50${i % 10}`,
  duration_ms: 1200,
  created_at: isoAgo(0, i + 1),
  test_run_id: `00000000-0000-4000-8000-0000000005${String(i % 5).padStart(2, '0')}`,
  build_number: `${100 + i}`,
  run_seq: 40 - (i % 5),
  project_id: P,
  project_name: 'Checkout',
  navigation_url: `/runs/00000000-0000-4000-8000-0000000005${String(i % 5).padStart(2, '0')}/tests/f${i}`,
  failure_count: (i % 6) + 1,
  last_failure_step: i % 3 === 0 ? 'Click "Pay"' : null,
  assignment_reason: i === 1 ? 'via CODEOWNERS: src/checkout/**' : null,
}))

const ASSIGNED = { items: ASSIGNED_FAILURES, total: 40, page: 1, size: 25, pages: 2, unresolved_total: 40 }

const REVIEW_ID = '00000000-0000-4000-8000-000000000601'

function review(state: string) {
  return {
    id: REVIEW_ID,
    project_id: P,
    kind: 'report',
    subject_type: 'pipeline_run',
    subject_id: 'pipeline-1',
    pipeline_run_id: 'pipeline-1',
    test_run_id: '00000000-0000-4000-8000-000000000500',
    workflow_type: 'deep',
    state,
    reviewed: state !== 'pending_review',
    reviewed_at: state === 'pending_review' ? null : isoAgo(0),
    reason_code: null,
    notes: null,
    evidence_bundle_sha256: null,
    superseded_by: null,
    created_at: isoAgo(1),
    requires_human_review: true,
    ai_disclaimer: 'AI-generated content. Verify before acting.',
    ai_disclaimer_version: '2026-09-12.v1',
  }
}

function proposal(i: number, status: string) {
  return {
    id: `00000000-0000-4000-8000-0000000007${String(i).padStart(2, '0')}`,
    project_id: P,
    test_fingerprint: `fp-q${i}`,
    test_name: `test_quarantine_${i}`,
    suite_name: 'Checkout',
    status,
    detection_method: 'flip_rate',
    flip_rate: 0.3,
    flip_window_size: 10,
    pass_count: 7,
    fail_count: 3,
    detected_at: isoAgo(6),
    last_failure_at: isoAgo(1),
    proposed_at: isoAgo(5),
    approved_at: null,
    approved_by_user_id: null,
    rejected_at: null,
    rejected_by_user_id: null,
    quarantine_start: null,
    quarantine_expires_at: null,
    quarantine_duration_days: 14,
    recheck_at: null,
    rationale: null,
    reviewer_notes: null,
    owner_user_id: null,
    owner_name: 'QA Lead',
    defect_id: null,
    defect_jira_key: null,
    defect_jira_url: null,
    defect_external_status: null,
    defect_external_status_conflict: false,
    sla_days: null,
    stale_at: null,
    stale: false,
    consecutive_passes: 0,
    ready_to_promote: false,
    created_at: isoAgo(6),
    updated_at: isoAgo(1),
  }
}

function approvalCase(id: string, status: string) {
  return {
    id,
    project_id: P,
    title: `Checkout case ${id}`,
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    test_suite_id: null,
    status,
    version: 1,
    is_automated: false,
    automation_status: 'manual',
    ai_generated: false,
    created_at: isoAgo(3),
    updated_at: isoAgo(2),
  }
}

const INBOX_STATS = {
  detected: 0, proposed: 2, approved: 0, quarantined: 1, recheck_scheduled: 0,
  re_quarantined: 0, released: 0, rejected: 0, expired: 0, total_live: 3,
}

/** Fresh state per test: a decision taken in one test never leaks into the next. */
export function inboxHandlers(): ApiHandlers {
  let reviewState = 'pending_review'
  return [
    ...LAYOUT,
    ['/api/v1/me/assigned-failures', () => ASSIGNED],
    [
      `/api/v1/projects/${P}/reviews`,
      ({ url }) => {
        const filter = url.searchParams.get('state')
        return !filter || filter === reviewState ? [review(reviewState)] : []
      },
    ],
    [
      `/api/v1/reviews/${REVIEW_ID}/accept`,
      () => {
        reviewState = 'accepted'
        return review(reviewState)
      },
      'POST',
    ],
    ['/api/v1/quarantine/stats', () => INBOX_STATS],
    ['/api/v1/quarantine', () => [proposal(1, 'PROPOSED'), proposal(2, 'DETECTED'), proposal(3, 'QUARANTINED')]],
    [
      '/api/v1/test-management/cases',
      ({ url }) => {
        const items = url.searchParams.get('status') === 'review_requested'
          ? [approvalCase('c1', 'review_requested')]
          : [approvalCase('c2', 'under_review')]
        return { items, total: items.length, page: 1, size: 50, pages: 1 }
      },
    ],
  ]
}

// ── A test case (`/runs/:runId/tests/:testId`, from `fold-test-case.spec.ts`) ──

export const TEST_CASE_ID = '77777777-7777-4777-8777-777777777777'
export const TEST_CASE_NAME = 'checkout total includes tax'
const TEST_CASE_FULL_NAME = 'com.example.checkout.TotalSpec.checkoutTotalIncludesTax'
export const TEST_CASE_ERROR = 'AssertionError: expected total 108.00 but was 100.00'
const TRACE = [
  TEST_CASE_ERROR,
  '    at org.junit.Assert.assertEquals(Assert.java:117)',
  '    at com.example.checkout.TotalSpec.checkoutTotalIncludesTax(TotalSpec.java:42)',
  '    at java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)',
].join('\n')
export const TEST_CASE_ROOT_CAUSE =
  'The tax line is skipped when the cart holds a discounted item: TaxCalculator returns 0 for a negative line.'
export const TEST_CASE_FINGERPRINT = 'fp-checkout-total-includes-tax'
/** The page's route. */
export const TEST_CASE_PATH = `/runs/${RUN_ID}/tests/${TEST_CASE_ID}`
/** The page's API root: `${TEST_CASE_API}/rich-detail`, `/history`, `/steps`, `/step-flips`. */
export const TEST_CASE_API = `/api/v1/runs/${RUN_ID}/tests/${TEST_CASE_ID}`

const STEPS = [
  { name: 'open the cart', status: 'PASSED' },
  { name: 'apply the discount code', status: 'PASSED' },
  { name: 'read the total', status: 'FAILED' },
].map((s, i) => ({
  id: `step-${i + 1}`,
  parent_step_id: null,
  ordinal: i + 1,
  depth: 0,
  name: s.name,
  keyword: null,
  status: s.status,
  duration_ms: 400 + i * 300,
  start_ms: i * 700,
  assertion_message: s.status === 'FAILED' ? TEST_CASE_ERROR : null,
  assertion_trace: null,
  expected_value: s.status === 'FAILED' ? '108.00' : null,
  actual_value: s.status === 'FAILED' ? '100.00' : null,
  parameters: [],
  created_at: isoAgo(0, 2),
  steps: [],
  attachments: [],
}))

/** `GET /runs/{run}/tests/{test}/rich-detail`: the enriched contract (`types/test-case-detail.ts`). */
const DETAIL = {
  contract: 'test-case-detail',
  schema_version: 1,
  contract_version: '1',
  id: TEST_CASE_ID,
  test_name: TEST_CASE_NAME,
  full_name: TEST_CASE_FULL_NAME,
  class_name: 'TotalSpec',
  suite_name: 'Checkout',
  status: 'FAILED',
  duration_ms: 4_210,
  severity: 'critical',
  feature: 'Totals',
  owner: 'payments-team',
  created_at: isoAgo(0, 2),
  tags: ['smoke', 'checkout'],
  error_message: TEST_CASE_ERROR,
  has_attachments: false,
  canonical_test_case_id: '88888888-8888-4888-8888-888888888888',
  project_id: PROJECT_ID,
  identity: {
    test_case_id: TEST_CASE_ID,
    test_run_id: RUN_ID,
    history_id: 'hist-checkout-total-tax',
    full_name: TEST_CASE_FULL_NAME,
    fingerprint: TEST_CASE_FINGERPRINT,
  },
  classification: {
    suite: { name: 'Checkout', legacy_name: 'Checkout' },
    class_name: 'TotalSpec',
    severity: 'critical',
    feature: 'Totals',
    owner: 'payments-team',
    framework: 'junit',
    tags: ['smoke', 'checkout'],
    components: [],
    labels: [],
  },
  execution: {
    status: 'FAILED',
    duration_ms: 4_210,
    retry_count: 0,
    is_flaky_run: false,
    step_count: 3,
    steps_present: true,
    has_attachments: false,
    failure_category: 'PRODUCT_BUG',
    error_message: TEST_CASE_ERROR,
    stack_trace: TRACE,
    parameters: [],
  },
  provenance: {
    source_test_run_id: RUN_ID,
    parser_format: 'junit',
    parser_version: '2.4.1',
    format: 'junit',
    source_file: null,
    field_sources: { identity: 'source_report', suite: 'source_report' },
    warnings: [],
  },
  steps_present: true,
  steps: STEPS,
  attachments: [],
  definition: null,
  links: [],
  extensions: null,
}

/** `GET .../history`: ten runs, newest first, three of them red. */
const HISTORY = {
  run_id: RUN_ID,
  test_id: TEST_CASE_ID,
  test_fingerprint: TEST_CASE_FINGERPRINT,
  test_name: TEST_CASE_NAME,
  history: Array.from({ length: 10 }, (_, i) => ({
    run_id: i === 0 ? RUN_ID : `44444444-4444-4444-8444-${String(100000000000 + i)}`,
    run_label: `Run #${60 - i}`,
    build_number: String(240 - i),
    run_seq: 60 - i,
    status: [0, 3, 7].includes(i) ? 'FAILED' : 'PASSED',
    duration_ms: 3_900 + i * 40,
    created_at: isoAgo(i, 2),
  })),
  flakiness: {
    is_flaky: true,
    failure_rate: 0.3,
    failure_rate_pct: 30,
    impact_score: 4.2,
    classification: 'INVESTIGATE',
    window_days: 30,
    total_runs: 10,
    passed: 7,
    failed: 3,
  },
  metadata: {
    owner: 'payments-team',
    assigned_to_user_id: null,
    suite: 'Checkout',
    severity: 'critical',
    feature: 'Totals',
    first_seen_run_id: null,
    first_seen_run_label: 'Run #12',
    first_seen_at: isoAgo(28),
    last_seen_run_id: RUN_ID,
    last_seen_run_label: 'Run #60',
    last_seen_at: isoAgo(0, 2),
    created_at: isoAgo(28),
    updated_at: isoAgo(0, 2),
  },
}

/** `GET .../steps`: the same tree (the enriched detail already carries it). */
const STEP_TREE = {
  run_id: RUN_ID,
  test_id: TEST_CASE_ID,
  test_name: TEST_CASE_NAME,
  status: 'FAILED',
  step_count: 3,
  retry_count: 0,
  is_flaky_run: false,
  stack_trace: TRACE,
  steps: STEPS,
  attachments: [],
}

/** `GET .../step-flips`: steady steps over six runs. */
const STEP_FLIPS = {
  run_id: RUN_ID,
  test_id: TEST_CASE_ID,
  test_fingerprint: TEST_CASE_FINGERPRINT,
  report: { has_step_flip: false, runs_analyzed: 6, total_flips: 0, flips: [], flipping_steps: [], summary: '' },
}

/** `GET /analyze/{test}`: the stored AI analysis (`types/ai.ts` AnalysisResult). */
const ANALYSIS = {
  test_case_id: TEST_CASE_ID,
  analysis_id: 'analysis-1',
  root_cause_summary: TEST_CASE_ROOT_CAUSE,
  failure_category: 'PRODUCT_BUG',
  backend_error_found: false,
  pod_issue_found: false,
  is_flaky: false,
  confidence_score: 82,
  recommended_actions: ['Guard TaxCalculator against negative discount lines.'],
  role_actions: { qa: '', developer: 'Fix TaxCalculator.lineTax for negative lines.', sre: '', release_manager: '' },
  evidence_references: [{ source: 'stacktrace', reference_id: 'st-1', excerpt: 'TotalSpec.java:42' }],
  tools_used: ['fetch_allure_stacktrace'],
  confidence_why: { evidence_count: 1, data_sources: ['stacktrace'], is_llm_inference: true, investigation_depth: 'standard' },
  llm_provider: 'ollama',
  llm_model: 'llama3',
  requires_human_review: false,
}

/** One failed test of the visual specs' run `RUN_ID`. */
export const TEST_CASE: ApiHandlers = [
  ...LAYOUT,
  [`${TEST_CASE_API}/rich-detail`, () => DETAIL],
  [`${TEST_CASE_API}/history`, () => HISTORY],
  [`${TEST_CASE_API}/steps`, () => STEP_TREE],
  [`${TEST_CASE_API}/step-flips`, () => STEP_FLIPS],
  [`/api/v1/analyze/${TEST_CASE_ID}`, () => ANALYSIS],
]

// ── Test cases (`/test-management`, from `fold-test-cases.spec.ts`) ────────

const CASE_STATUSES = ['active', 'draft', 'review_requested', 'approved', 'active', 'under_review', 'active', 'draft'] as const
const CASES = CASE_STATUSES.map((status, i) => ({
  id: `0000000${i}-aaaa-4000-8000-00000000000${i}`,
  project_id: PROJECT_ID,
  title: `Checkout case ${i + 1}: the cart survives a refresh`,
  test_type: 'functional',
  priority: ['critical', 'high', 'medium', 'low'][i % 4],
  severity: 'major',
  suite_name: i % 2 ? 'Auth' : 'Checkout',
  test_suite_id: null,
  status,
  version: 1,
  is_automated: i % 3 === 0,
  automation_status: i % 3 === 0 ? 'automated' : 'manual',
  ai_generated: false,
  source: 'managed',
  allowed_actions: ['deprecate'],
  created_at: isoAgo(40 + i),
  updated_at: isoAgo(2 + i),
}))

const pageOf = <T>(items: T[], size = 25) => ({ items, total: items.length, page: 1, size, pages: items.length ? 1 : 0 })

/** The suites list (by name) and the first-class suites (with ids) the Suites tab links to. */
export const TEST_CASES_SUITE_IDS: Record<string, string> = {
  Checkout: '5a000000-0000-4000-8000-000000000001',
  Auth: '5a000000-0000-4000-8000-000000000002',
}
const CASE_SUITES = Object.keys(TEST_CASES_SUITE_IDS).map((name, i) => ({
  suite_name: name,
  test_count: 4,
  passed_count: 3,
  failed_count: 1,
  last_run_at: isoAgo(1),
  last_run_id: null,
  pass_rate: 75,
  run_count: 3 + i,
  total_executions: 12,
  total_passed: 9,
  total_failed: 3,
  total_skipped: 0,
  total_broken: 0,
}))

/** The page has no visual-spec fixtures, so its answers are these. */
export const TEST_CASES: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/test-management/cases', ({ url }) => {
    const status = url.searchParams.get('status')
    const size = Number(url.searchParams.get('size') ?? 25)
    return pageOf(status ? CASES.filter((c) => c.status === status) : CASES, size)
  }],
  ['/api/v1/test-management/audit', () => pageOf([], 5)],
  ['/api/v1/test-management/cases/evidence-gaps', () => ({ items: [], total: 0 })],
  ['/api/v1/canonical-test-cases/orphaned', () => pageOf([])],
  ['/api/v1/test-management/suites', () => CASE_SUITES],
  ['/api/v1/suites', () => ({
    items: Object.entries(TEST_CASES_SUITE_IDS).map(([name, id]) => ({
      id,
      project_id: PROJECT_ID,
      name,
      description: null,
      is_default: false,
      tags: null,
      test_case_count: 4,
      created_at: isoAgo(60),
      updated_at: null,
    })),
    total: 2,
  })],
  ['/api/v1/auth/users', () => []],
  [`/api/v1/projects/${PROJECT_ID}/members`, () => []],
  ['/api/v1/test-management/strategies', () => []],
  ['/api/v1/test-management/plans', () => pageOf([])],
]

// ── Pipeline runs (`/agents`, from `fold-agents.spec.ts`) ──────────────────

/**
 * The Run page's answers (the run's pipeline, its stages, timeline and AI
 * report), plus the live-runs list and the trigger this page also asks for.
 * `triggered` records every POST body to the trigger.
 */
export function agentsHandlers(triggered: unknown[] = []): ApiHandlers {
  return [
    ...RUN_PAGE,
    ['/api/v1/agents/active-runs', () => ({ active_runs: [] })],
    [
      '/api/v1/agents/pipelines/trigger',
      ({ route }) => {
        triggered.push(route.request().postDataJSON())
        return { message: 'queued', task_id: 'task-1', run_id: RUN_ID }
      },
      'POST',
    ],
  ]
}

// ── Release gate (`/release-gate/:runId`, from `fold-release-gate.spec.ts`) ──

const READINESS = /^\/api\/v1\/release-readiness\/[^/]+$/

/** Four stored blockers (the card shows the first three), over the shared handlers' floored NO_GO. */
const GATE_BLOCKERS = ['Checkout down in 3 suites', 'Payment gateway timeout', 'Session cookie not cleared', 'Login 500 on retry']

/** `releaseGateOn`'s answers, its stored decision carrying `GATE_BLOCKERS`. */
export function withGateBlockers(handlers: ApiHandlers): ApiHandlers {
  const stored = handlers.find(([matcher]) => matcher instanceof RegExp && matcher.source === READINESS.source)
  if (!stored) throw new Error('releaseGateOn no longer answers release-readiness')
  return [
    [READINESS, (request: ApiRequest) => ({ ...(stored[1](request) as object), blocking_issues: GATE_BLOCKERS })],
    ...handlers,
  ]
}

// ── Releases (`/releases`, from `releases-panel.spec.ts`) ──────────────────

const releasesPageOf = <T>(items: T[]) => ({ items, total: items.length, page: 1, size: 100, pages: items.length > 0 ? 1 : 0 })

const phase = (releaseId: string, id: string, name: string, phaseType: string, status: string, order: number) => ({
  id,
  release_id: releaseId,
  name,
  phase_type: phaseType,
  status,
  description: null,
  order_index: order,
  planned_start: null,
  planned_end: null,
  actual_start: null,
  actual_end: null,
  exit_criteria: null,
  notes: null,
  created_at: isoAgo(20),
  updated_at: isoAgo(1),
})

/** The in-progress release (a failed phase: a red blocker). */
export const RELEASE_ACTIVE_ID = 'aaaaaaaa-0000-4000-8000-000000000001'
const PLANNING_ID = 'aaaaaaaa-0000-4000-8000-000000000002'
const SHIPPED_ID = 'aaaaaaaa-0000-4000-8000-000000000003'

const release = (id: string, name: string, status: string, extra: Record<string, unknown> = {}) => ({
  id,
  project_id: PROJECT_ID,
  project_name: 'Checkout',
  name,
  version: name,
  description: null,
  status,
  planned_date: null,
  released_at: null,
  created_at: isoAgo(30),
  updated_at: isoAgo(1),
  phases: [],
  test_run_count: 0,
  ...extra,
})

/** One in progress (a failed phase: a red blocker), one planned with no phases, one shipped. */
const PAGE_RELEASES = [
  release(RELEASE_ACTIVE_ID, '2026.11', 'in_progress', {
    description: 'Checkout revamp',
    planned_date: daysAgo(-5),
    test_run_count: 2,
    phases: [
      phase(RELEASE_ACTIVE_ID, 'ph-smoke', 'Smoke', 'qa_testing', 'completed', 1),
      phase(RELEASE_ACTIVE_ID, 'ph-regression', 'Regression', 'qa_testing', 'failed', 2),
      phase(RELEASE_ACTIVE_ID, 'ph-e2e', 'E2E suites', 'qa_testing', 'in_progress', 3),
    ],
  }),
  release(PLANNING_ID, '2026.12', 'planning', { planned_date: daysAgo(-30) }),
  release(SHIPPED_ID, '2026.10', 'released', { released_at: isoAgo(10), test_run_count: 1 }),
]

const linkedRun = (id: string, build: string, passed: number, failed: number, ago: number) => ({
  id,
  build_number: build,
  status: failed > 0 ? 'failed' : 'passed',
  total_tests: passed + failed,
  passed_tests: passed,
  failed_tests: failed,
  broken_tests: 0,
  skipped_tests: 0,
  pass_rate: (passed / (passed + failed)) * 100,
  created_at: isoAgo(ago),
  primary_suite_name: 'checkout-e2e',
  suite_names: ['checkout-e2e'],
  phase_id: null,
})

function releaseDetailOf(id: string) {
  const base = PAGE_RELEASES.find((r) => r.id === id)
  if (!base) return null
  const runs = id === RELEASE_ACTIVE_ID ? [linkedRun('run-1', '412', 180, 20, 2), linkedRun('run-2', '415', 196, 4, 1)] : []
  const passed = runs.reduce((s, r) => s + r.passed_tests, 0)
  const failed = runs.reduce((s, r) => s + r.failed_tests, 0)
  return {
    ...base,
    linked_runs: runs,
    outcomes: [],
    metrics: {
      total_runs: runs.length,
      total_tests: passed + failed,
      total_passed: passed,
      total_failed: failed,
      avg_pass_rate: runs.length ? (passed / (passed + failed)) * 100 : null,
    },
  }
}

/** The list, each release's detail and its compliance packs (none). */
export const RELEASES_PAGE: ApiHandlers = [
  ['/api/v1/releases', () => releasesPageOf(PAGE_RELEASES)],
  [/^\/api\/v1\/releases\/[^/]+\/compliance-packs$/, () => []],
  [/^\/api\/v1\/releases\/[^/]+$/, ({ path }) => releaseDetailOf(path.split('/').pop() as string)],
  ...LAYOUT,
]
