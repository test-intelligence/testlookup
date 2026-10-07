/**
 * Deterministic API payloads for the production-page visual baselines
 * (`tests/visual/prod-*.visual.spec.ts`). Not a spec file, so Playwright
 * does not discover it.
 *
 * Every date is derived from `NOW` (never `Date.now()`), and the browser's
 * clock is pinned to the same instant, so "today", "3d ago" and every
 * relative label are the same on every run and every machine. Every number
 * is a literal or a pure function of a day index: no randomness.
 *
 * The shapes are copied from the service types (`src/types/*`,
 * `src/services/*`) and the page tests, not imported: the page modules
 * value-import `@/...`, which Playwright's plain-Node transform cannot
 * resolve, and a fixture that silently tracks a type change would hide the
 * change the baseline exists to show.
 *
 * The data is chosen to make the visuals under migration VISIBLE: broken
 * executions (the Overview chart drops them today), a gap and mixed/failing
 * days in the Trends window, red and green builds with an empty velocity
 * cell on Runs, a P0 on Defects, usage against a budget on Deep
 * investigation, and a floored release-gate decision.
 */
import { respond, type ApiHandler, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'

/** Friday 2026-09-18, 12:00 UTC. The browser clock is pinned here. */
export const NOW = new Date('2026-09-18T12:00:00Z')
const DAY_MS = 86_400_000

/** `YYYY-MM-DD` of the UTC day `n` days before `NOW`. */
export const daysAgo = (n: number): string => new Date(NOW.getTime() - n * DAY_MS).toISOString().slice(0, 10)
/** An ISO timestamp `n` days (and `hours` hours) before `NOW`. */
export const isoAgo = (n: number, hours = 0): string =>
  new Date(NOW.getTime() - n * DAY_MS - hours * 3_600_000).toISOString()

export const PROJECT_ID = '11111111-1111-4111-8111-111111111111'
export const RUN_ID = '33333333-3333-4333-8333-333333333333'

export const USER = {
  id: '00000000-0000-4000-8000-000000000001',
  email: 'lead@example.test',
  username: 'qa_lead',
  full_name: 'QA Lead',
  role: 'QA_LEAD',
  is_active: true,
  must_change_password: false,
  avatar_color: null,
}

export const PROJECTS = [
  {
    id: PROJECT_ID,
    name: 'Checkout',
    slug: 'checkout',
    description: '',
    is_active: true,
    created_at: '2026-01-05T09:00:00Z',
  },
]

const page = <T>(items: T[], size = 100) => ({
  items,
  total: items.length,
  page: 1,
  size,
  pages: items.length > 0 ? 1 : 0,
})

const intParam = (request: ApiRequest, name: string, fallback: number) => {
  const raw = request.url.searchParams.get(name)
  const n = raw == null ? NaN : Number(raw)
  return Number.isFinite(n) ? n : fallback
}

// ── Layout (sidebar, header, notifications) ────────────────────────────────

const AI_SETTINGS = {
  llm_provider: 'none',
  llm_model: '',
  llm_temperature: 0,
  llm_max_tokens: 0,
  ai_offline_mode: true,
  ai_offline_mode_source: 'override',
  ai_offline_mode_env_pinned: false,
  embedding_provider: 'none',
  embedding_model: '',
  ai_confidence_threshold: 0.7,
  ai_timeout_seconds: 30,
  deep_investigation_enabled: true,
  finetune_enabled: false,
  openai_key_set: false,
  google_key_set: false,
  anthropic_key_set: false,
  openrouter_key_set: false,
  analysis_mode: 'rules',
}

/** What every page's chrome asks for, whatever the page. */
export const LAYOUT: ApiHandlers = [
  ['/api/v1/projects', () => PROJECTS],
  ['/api/v1/settings/ai', () => AI_SETTINGS],
  ['/api/v1/releases', () => page([])],
  ['/api/v1/notifications/history/unread-count', () => ({ unread: 0 })],
  ['/api/v1/notifications/history', () => []],
  ['/api/v1/saved-views', () => []],
]

// ── The shared day series (metrics/trends) ─────────────────────────────────

/**
 * Days (counted back from today = 0) with no runs at all: a 7-day gap
 * inside the Trends 14-day window, so the cadence strip, the daily
 * breakdown and the pass-rate line all show one.
 */
const GAP_DAYS = new Set([5, 6, 7, 8, 9, 10, 11])

export interface TrendPoint {
  date: string
  passed: number
  failed: number
  skipped: number
  broken: number
  total: number
  pass_rate: number
}

/**
 * One day of executions, a pure function of how many days ago it is. Most
 * days have some failures (the cadence strip's "mixed" cell), yesterday is
 * all green, every third day has broken tests (Overview's chart drops
 * them), and day 3 is a bad day (mostly failing).
 */
function trendDay(n: number): TrendPoint {
  const bad = n === 3
  const clean = n === 1
  const passed = bad ? 18 : 150 + ((n * 37) % 60)
  const failed = bad ? 64 : clean ? 0 : 4 + ((n * 11) % 17)
  const skipped = 3 + ((n * 5) % 9)
  const broken = n % 3 === 0 ? 6 + (n % 4) : 0
  const evaluated = passed + failed + broken
  return {
    date: daysAgo(n),
    passed,
    failed,
    skipped,
    broken,
    total: passed + failed + skipped + broken,
    pass_rate: Math.round((passed / evaluated) * 10_000) / 100,
  }
}

/** `GET /metrics/trends?days=N`: the days with runs, oldest first. */
export function trendPoints(days: number): TrendPoint[] {
  const points: TrendPoint[] = []
  for (let n = days - 1; n >= 0; n--) if (!GAP_DAYS.has(n)) points.push(trendDay(n))
  return points
}

const trends = (request: ApiRequest) => {
  const days = intParam(request, 'days', 30)
  return { data: trendPoints(days), period_days: days }
}

const sumOf = (points: TrendPoint[], key: 'passed' | 'failed' | 'skipped' | 'broken' | 'total') =>
  points.reduce((s, p) => s + p[key], 0)

/** `GET /metrics/summary?days=N`, consistent with the day series. */
function summary(request: ApiRequest) {
  const days = intParam(request, 'days', 30)
  const points = trendPoints(days)
  const passed = sumOf(points, 'passed')
  const evaluated = passed + sumOf(points, 'failed') + sumOf(points, 'broken')
  return {
    release_readiness: 'AMBER',
    release_readiness_band: 'orange',
    release_readiness_downgrades: [],
    total_executions_7d: { value: sumOf(points, 'total'), trend: 4.2, trend_direction: 'up' },
    avg_pass_rate_7d: {
      value: Math.round((passed / evaluated) * 1000) / 10,
      trend: -1.8,
      trend_direction: 'down',
      basis: 'executions',
      basis_label: 'per test execution',
    },
    active_defects: { value: 7, trend: 2, trend_direction: 'up' },
    flaky_test_count: { value: 5, trend: 0, trend_direction: 'flat' },
    new_failures_24h: { value: 9, trend: 3, trend_direction: 'up' },
    avg_duration_ms: { value: 512_400 },
  }
}

// ── Runs (/runs list) ──────────────────────────────────────────────────────

/**
 * Thirteen builds in the 30-day window (`n` = days ago), newest first, none
 * in the Trends gap. Red and green interleave, so the build strips show
 * both; the newest two are red (a red streak); yesterday's is a 73% build
 * and day 3's a 23% one, so the hub's pass-rate meters cover all three tone
 * bands. Thirteen, not fourteen, leaves one empty cell in the Runs
 * velocity strip.
 */
const RUN_SPECS: { n: number; hours: number; failed: number; broken: number; suite: string }[] = [
  { n: 0, hours: 2, failed: 12, broken: 3, suite: 'Auth' },
  { n: 1, hours: 3, failed: 70, broken: 0, suite: 'Checkout' },
  { n: 2, hours: 1, failed: 0, broken: 0, suite: 'Auth' },
  { n: 3, hours: 4, failed: 64, broken: 9, suite: 'Payments' },
  { n: 4, hours: 2, failed: 0, broken: 0, suite: 'Checkout' },
  { n: 12, hours: 5, failed: 3, broken: 0, suite: 'Auth' },
  { n: 13, hours: 3, failed: 0, broken: 0, suite: 'Payments' },
  { n: 14, hours: 2, failed: 0, broken: 0, suite: 'Checkout' },
  { n: 15, hours: 6, failed: 5, broken: 2, suite: 'Auth' },
  { n: 16, hours: 1, failed: 0, broken: 0, suite: 'Payments' },
  { n: 18, hours: 2, failed: 0, broken: 0, suite: 'Checkout' },
  { n: 20, hours: 3, failed: 9, broken: 0, suite: 'Auth' },
  { n: 22, hours: 4, failed: 0, broken: 0, suite: 'Payments' },
]

export function runs() {
  return RUN_SPECS.map((spec, i) => {
    const build = 240 - i
    // Day 3's build is the bad one (a pass rate in the twenties).
    const passed = spec.n === 3 ? 22 : 180 + ((i * 13) % 40)
    const skipped = 2 + (i % 4)
    const total = passed + spec.failed + skipped + spec.broken
    const evaluated = passed + spec.failed + spec.broken
    const start = isoAgo(spec.n, spec.hours)
    const duration = 420_000 + ((i * 53_000) % 300_000)
    return {
      id: i === 0 ? RUN_ID : `44444444-4444-4444-8444-${String(100000000000 + i)}`,
      project_id: PROJECT_ID,
      project_name: 'Checkout',
      build_number: build,
      jenkins_job: 'checkout-ci',
      branch: 'main',
      status: spec.failed + spec.broken > 0 ? 'FAILED' : 'PASSED',
      passed_tests: passed,
      failed_tests: spec.failed,
      skipped_tests: skipped,
      broken_tests: spec.broken,
      unknown_tests: 0,
      total_tests: total,
      pass_rate: Math.round((passed / evaluated) * 10_000) / 100,
      duration_ms: duration,
      start_time: start,
      end_time: new Date(Date.parse(start) + duration).toISOString(),
      created_at: start,
      release_name: undefined,
      trigger_source: 'ci',
      ingestion_source: 'sdk',
      primary_suite_name: spec.suite,
      suite_names: [spec.suite],
      run_seq: 60 - i,
    }
  })
}

/** `GET /runs?days=&size=&status=`: the builds in the window, newest first. */
function runList(request: ApiRequest) {
  const size = intParam(request, 'size', 100)
  const days = intParam(request, 'days', 0)
  const status = request.url.searchParams.get('status')
  const all = runs().filter((run, i) => {
    const n = RUN_SPECS[i].n
    if (days > 0 && n >= days) return false
    if (status && run.status !== status) return false
    return true
  })
  return { ...page(all.slice(0, size), size), total: all.length }
}

export const RUNS: ApiHandlers = [['/api/v1/runs', runList]]

// ── Overview (/overview) ───────────────────────────────────────────────────

const VALUE_MONTHS = ['2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01', '2026-09-01']

export function valueMetrics() {
  const monthly = VALUE_MONTHS.map((month, i) => {
    const triage = 6 + i * 3.5
    const quarantine = 2 + ((i * 3) % 5)
    const dedup = 1.5 + i * 1.25
    return {
      month,
      auto_triaged: 20 + i * 9,
      clustered_failures: 14 + i * 4,
      duplicates_absorbed: 3 + i,
      quarantine_suppressed_failures: 5 + ((i * 3) % 7),
      runs_unblocked_proxy: 1 + (i % 3),
      hours_triage: triage,
      hours_quarantine: quarantine,
      hours_dedup: dedup,
      hours_total: triage + quarantine + dedup,
    }
  })
  return {
    period_days: 30,
    project_id: PROJECT_ID,
    triage_time_saved_minutes: 1_560,
    triage_time_saved_hours: 26,
    defects_auto_grouped: 18,
    tests_grouped: 64,
    duplicate_tickets_avoided: 9,
    defects_promoted: 4,
    flaky_tests_identified: 5,
    quarantine_recommended: 3,
    risky_releases_blocked: 2,
    releases_conditional: 3,
    release_overrides: 1,
    intelligence_reports_generated: 12,
    available: true,
    insufficient_data_reason: null,
    headline: { hours_saved_30d: 31.5, fte_equivalent_30d: 0.2 },
    monthly,
    assumptions: { triage_minutes_per_failure: 15, blocked_run_wait_minutes: 30, defect_filing_minutes: 10 },
    assumptions_source: 'default',
    methodology_version: 1,
  }
}

export const OVERVIEW: ApiHandlers = [
  ...LAYOUT,
  ...RUNS,
  ['/api/v1/metrics/summary', summary],
  ['/api/v1/metrics/trends', trends],
  [
    '/api/v1/analytics/failure-categories',
    () => ({
      items: [
        { category: 'ASSERTION', count: 21, kind: 'product' },
        { category: 'TIMEOUT', count: 9, kind: 'infrastructure' },
        { category: 'LOCATOR', count: 6, kind: 'test_code' },
      ],
      by_kind: [
        { kind: 'product', count: 21 },
        { kind: 'infrastructure', count: 9 },
        { kind: 'test_code', count: 6 },
        { kind: 'unknown', count: 0 },
      ],
      period_days: 30,
    }),
  ],
  ['/api/v1/value-metrics', valueMetrics],
  [
    /^\/api\/v1\/projects\/[^/]+\/activity$/,
    () => ({ items: [], next_cursor: null, ledger_started_at: null, window: { since: null, until: null } }),
  ],
]

// ── Suite detail (/coverage/suite?name=Auth) ───────────────────────────────

export const SUITE = 'Auth'

function suiteTrend(request: ApiRequest) {
  const days = intParam(request, 'days', 30)
  const points = []
  // Zero-filled per day, oldest first (the service's contract).
  for (let n = days - 1; n >= 0; n--) {
    const ran = !GAP_DAYS.has(n) && n % 2 === 0
    const passed = ran ? 40 + ((n * 7) % 15) : 0
    const failed = ran ? (n * 3) % 7 : 0
    const skipped = ran ? 1 + (n % 3) : 0
    const broken = ran && n % 4 === 0 ? 2 : 0
    points.push({
      date: daysAgo(n),
      run_count: ran ? 1 + (n % 2) : 0,
      total_tests: passed + failed + skipped + broken,
      passed_count: passed,
      failed_count: failed,
      skipped_count: skipped,
      broken_count: broken,
    })
  }
  return { suite_name: SUITE, days, points }
}

function suiteDetail() {
  const recent = [0, 2, 4, 12, 14, 16, 18, 20].map((n, i) => {
    const failed = (n * 3) % 7
    const passed = 40 + ((n * 7) % 15)
    const skipped = 1 + (n % 3)
    return {
      test_run_id: `55555555-5555-4555-8555-${String(100000000000 + i)}`,
      build_number: String(240 - i * 2),
      run_date: isoAgo(n, 2),
      passed,
      failed,
      skipped,
      pass_rate: Math.round((passed / (passed + failed)) * 1000) / 10,
    }
  })
  const cases = ['login succeeds', 'login rejects bad password', 'token refresh', 'logout clears session'].map(
    (name, i) => ({
      test_fingerprint: `fp-auth-${i}`,
      test_name: name,
      class_name: 'AuthSpec',
      total_executions: 20,
      passed: 20 - i * 2,
      failed: i * 2,
      skipped: 0,
      pass_rate: ((20 - i * 2) / 20) * 100,
      avg_duration_ms: 1_200 + i * 300,
      last_status: i === 3 ? 'FAILED' : 'PASSED',
      last_error: i === 3 ? 'AssertionError: session cookie still present' : null,
      last_run_at: isoAgo(0, 2),
      is_flaky: i === 2,
    }),
  )
  return {
    summary: {
      unique_tests: cases.length,
      total_executions: 80,
      passed: 68,
      failed: 12,
      pass_rate: 85,
      avg_duration_ms: 1_650,
    },
    test_cases: cases,
    recent_runs: recent,
  }
}

// ── The suite page (/suites/:id) and the suites list (/suites), UX P4 ──────
//
// `/coverage/suite?name=Auth` now redirects to `/suites/<SUITE_ID>?tab=charts`
// (resolved by name through `/api/v1/suites`), so the suite detail fixture
// answers the suite page's own reads too: the suite, its catalog (canonical
// cases; four share a fingerprint with the analytics rows above, one has never
// run in the window), and the suites list's per-suite aggregates.

export const SUITE_ID = '66666666-6666-4666-8666-666666666666'
/** A second suite of the project: the default one, with no aggregates (its columns read "—"). */
export const SECOND_SUITE_ID = '77777777-7777-4777-8777-777777777777'

const SUITE_ROW = {
  id: SUITE_ID,
  project_id: PROJECT_ID,
  name: SUITE,
  description: 'Sign-in, sessions and tokens',
  is_default: false,
  tags: null,
  test_case_count: 5,
  created_at: '2026-02-01T00:00:00Z',
  updated_at: null,
}

const SECOND_SUITE_ROW = {
  ...SUITE_ROW,
  id: SECOND_SUITE_ID,
  name: 'All Tests',
  description: null,
  is_default: true,
  test_case_count: 0,
}

/** The newest run of the suite (`suiteDetail().recent_runs[0]`). */
const SUITE_LATEST_RUN_ID = '55555555-5555-4555-8555-100000000000'

function suiteCatalog() {
  const names = ['login succeeds', 'login rejects bad password', 'token refresh', 'logout clears session', 'password reset email']
  const items = names.map((name, i) => ({
    id: `88888888-8888-4888-8888-00000000000${i}`,
    project_id: PROJECT_ID,
    test_suite_id: SUITE_ID,
    test_suite_name: SUITE,
    // The last one never ran in the window: no analytics row shares its fingerprint.
    test_fingerprint: i < 4 ? `fp-auth-${i}` : 'fp-auth-catalog-only',
    test_name: name,
    class_name: 'AuthSpec',
    status: 'active',
    source: i < 4 ? 'execution' : 'managed',
    first_seen_run_id: i < 4 ? SUITE_LATEST_RUN_ID : null,
    last_seen_run_id: i < 4 ? SUITE_LATEST_RUN_ID : null,
    last_seen_test_case_id: null,
    deleted_at_run_id: null,
    managed_test_case_id: null,
    review_tag: null,
    tags: null,
    run_count: i < 4 ? 20 : null,
    created_at: '2026-02-01T00:00:00Z',
    updated_at: null,
  }))
  return { items, total: items.length }
}

/** `GET /api/v1/test-management/suites`: one row per suite NAME (the default suite has none). */
function suiteAggregates() {
  return [
    {
      suite_name: SUITE,
      test_count: 4,
      passed_count: 3,
      failed_count: 1,
      last_run_at: isoAgo(0, 2),
      last_run_id: SUITE_LATEST_RUN_ID,
      pass_rate: 75,
      run_count: 12,
      total_executions: 80,
      total_passed: 68,
      total_failed: 10,
      total_skipped: 0,
      total_broken: 2,
      owner_user_id: USER.id,
      owner_email: USER.email,
      owner_full_name: USER.full_name,
      owner_is_fallback: false,
    },
  ]
}

export const SUITE_DETAIL: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/analytics/suite-detail', suiteDetail],
  [/^\/api\/v1\/test-management\/suites\/[^/]+\/trend$/, suiteTrend],
  ['/api/v1/test-management/suites', suiteAggregates],
  [/^\/api\/v1\/suites\/[^/]+\/test-cases$/, suiteCatalog],
  [/^\/api\/v1\/suites\/[^/]+$/, (request) => (request.path.endsWith(SECOND_SUITE_ID) ? SECOND_SUITE_ROW : SUITE_ROW)],
  [
    '/api/v1/suites',
    () =>
      page([
        SUITE_ROW,
        SECOND_SUITE_ROW,
      ]),
  ],
]

// ── Value metrics (/value-metrics) ─────────────────────────────────────────

export const VALUE_METRICS: ApiHandlers = [...LAYOUT, ['/api/v1/value-metrics', valueMetrics]]

// ── Runs (/runs) ───────────────────────────────────────────────────────────

/**
 * `GET /runs/{id}/intelligence` as the Runs table's AI-verdict column reads it
 * (UX redesign P4, D2; `src/pages/runsList/aiVerdict.ts`): only the fields the
 * column uses. By build, newest first: a No-Go awaiting review, a Conditional
 * awaiting review (the widest cell), a reviewed Go, an analysis with no
 * recommendation ("—"), and the rest never analysed ("—").
 */
function runVerdict(request: ApiRequest) {
  const id = request.path.split('/')[4]
  const i = runs().findIndex((run) => run.id === id)
  const decided = (recommendation: string, state: string) => ({
    intelligence_available: true,
    structured_summary: null,
    top_analyses: [],
    release_decision: { recommendation, risk_score: 50, composite_risk: 50, blocking_issues: [], conditions_for_go: [], reasoning: '' },
    requires_human_review: state === 'pending_review',
    review: { state, message: '' },
  })
  if (i === 0) return decided('NO_GO', 'pending_review')
  if (i === 1) return decided('CONDITIONAL_GO', 'pending_review')
  if (i === 2) return decided('GO', 'accepted')
  return {
    intelligence_available: i === 3,
    structured_summary: null,
    top_analyses: [],
    release_decision: null,
    requires_human_review: false,
    review: { state: 'not_applicable', message: '' },
  }
}

export const RUNS_PAGE: ApiHandlers = [
  ...LAYOUT,
  ...RUNS,
  [/^\/api\/v1\/runs\/[^/]+\/intelligence$/, runVerdict],
]

// ── Coverage and flaky tests (shared by Trends, Coverage, Failures) ────────

const COVERAGE_SUITES = [
  { suite_name: 'Auth', unique_tests: 42, passed: 610, failed: 38, skipped: 12 },
  { suite_name: 'Checkout', unique_tests: 57, passed: 820, failed: 21, skipped: 9 },
  { suite_name: 'Payments', unique_tests: 33, passed: 240, failed: 190, skipped: 14 },
  { suite_name: 'Search', unique_tests: 25, passed: 300, failed: 96, skipped: 4 },
  { suite_name: 'Notifications', unique_tests: 12, passed: 150, failed: 0, skipped: 2 },
].map((s) => ({ ...s, pass_rate: Math.round((s.passed / (s.passed + s.failed)) * 10_000) / 100 }))

function coverage(request: ApiRequest) {
  const days = intParam(request, 'days', 30)
  const points = trendPoints(days)
  return {
    summary: {
      unique_tests: COVERAGE_SUITES.reduce((s, x) => s + x.unique_tests, 0),
      suite_count: COVERAGE_SUITES.length,
      total_executions: sumOf(points, 'total'),
      avg_pass_rate: 86.4,
      days_with_runs: points.length,
    },
    suites: COVERAGE_SUITES,
  }
}

const FLAKY_TESTS = {
  items: [
    {
      test_fingerprint: 'fp-flaky-1',
      test_name: 'token refresh',
      suite_name: 'Auth',
      class_name: 'AuthSpec',
      total_runs: 12,
      fail_count: 4,
      failure_rate_pct: 33.3,
      source: 'auto',
      likely_cause: 'Timing-sensitive wait',
      likely_cause_code: 'timing',
    },
    {
      test_fingerprint: 'fp-flaky-2',
      test_name: 'apply coupon',
      suite_name: 'Checkout',
      class_name: 'CouponSpec',
      total_runs: 10,
      fail_count: 2,
      failure_rate_pct: 20,
      source: 'auto',
      likely_cause: null,
      likely_cause_code: null,
    },
  ],
}

const ANALYTICS: ApiHandlers = [
  ['/api/v1/analytics/coverage', coverage],
  ['/api/v1/analytics/flaky-tests', () => FLAKY_TESTS],
  ['/api/v1/metrics/trends', trends],
  ['/api/v1/metrics/summary', summary],
]

// ── Trends (/trends, which resets the window to 14 days) ───────────────────

export const TRENDS: ApiHandlers = [...LAYOUT, ...RUNS, ...ANALYTICS]

// ── Defects (/defects) ─────────────────────────────────────────────────────

/**
 * The page derives severity from category + AI confidence (a product bug at
 * >= 80% is a P0), so the queue has two open P0s (the older 4 days old: the
 * "Oldest open P0" bar sits past the 2-day warn threshold), P1s, an infra
 * P2, a flaky P3 and three resolved rows for the MTTR.
 */
const DEFECT_SPECS: {
  test: string
  suite: string
  category: string
  confidence: number
  status: string
  ageDays: number
  resolvedAfterDays?: number
  jira?: string
}[] = [
  { test: 'checkout total includes tax', suite: 'Checkout', category: 'PRODUCT_BUG', confidence: 92, status: 'OPEN', ageDays: 4, jira: 'SHOP-812' },
  { test: 'card declined shows reason', suite: 'Payments', category: 'PRODUCT_BUG', confidence: 88, status: 'IN_PROGRESS', ageDays: 1 },
  { test: 'login rejects bad password', suite: 'Auth', category: 'PRODUCT_BUG', confidence: 61, status: 'OPEN', ageDays: 2, jira: 'SHOP-799' },
  { test: 'search paginates', suite: 'Search', category: 'PRODUCT_BUG', confidence: 55, status: 'OPEN', ageDays: 6 },
  { test: 'refund webhook retried', suite: 'Payments', category: 'INFRASTRUCTURE', confidence: 70, status: 'OPEN', ageDays: 3 },
  { test: 'token refresh', suite: 'Auth', category: 'FLAKY', confidence: 64, status: 'OPEN', ageDays: 8 },
  { test: 'apply coupon', suite: 'Checkout', category: 'PRODUCT_BUG', confidence: 84, status: 'RESOLVED', ageDays: 9, resolvedAfterDays: 2, jira: 'SHOP-760' },
  { test: 'address autocomplete', suite: 'Checkout', category: 'PRODUCT_BUG', confidence: 72, status: 'CLOSED', ageDays: 12, resolvedAfterDays: 4, jira: 'SHOP-741' },
  { test: 'email receipt sent', suite: 'Notifications', category: 'INFRASTRUCTURE', confidence: 66, status: 'RESOLVED', ageDays: 6, resolvedAfterDays: 1 },
]

function defects() {
  const items = DEFECT_SPECS.map((d, i) => ({
    id: `77777777-7777-4777-8777-${String(100000000000 + i)}`,
    jira_ticket_id: d.jira,
    jira_ticket_url: d.jira ? `https://jira.example.test/browse/${d.jira}` : undefined,
    jira_status: d.jira ? (d.status === 'OPEN' ? 'To Do' : 'Done') : undefined,
    external_status_at: null,
    external_status_conflict: false,
    failure_category: d.category,
    resolution_status: d.status,
    ai_confidence_score: d.confidence,
    created_at: isoAgo(d.ageDays, 3),
    resolved_at: d.resolvedAfterDays != null ? isoAgo(d.ageDays - d.resolvedAfterDays, 3) : undefined,
    test_name: d.test,
    suite_name: d.suite,
  }))
  return { ...page(items, 20), total: items.length }
}

const INTEGRATIONS = {
  jira_enabled: false,
  jira_domain: null,
  jira_email: null,
  jira_token_set: false,
  jira_default_project_key: '',
  splunk_enabled: false,
  splunk_base_url: null,
  splunk_token_set: false,
  ocp_enabled: false,
  ocp_api_url: null,
  ocp_token_set: false,
  ocp_default_namespace: '',
  slack_enabled: false,
  slack_webhook_url: null,
  slack_webhook_set: false,
  slack_default_channel: '',
  teams_enabled: false,
  teams_webhook_url: null,
  teams_webhook_set: false,
  github_repo: null,
  github_token_set: false,
}

export const DEFECTS: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/analytics/defects', defects],
  ['/api/v1/settings/integrations', () => INTEGRATIONS],
]

// ── Deep investigation (/deep-investigate/<run>) ───────────────────────────

/** Month-to-date LLM spend against a hard-cap budget: the Spend MTD bars. */
const LLM_USAGE = {
  project_id: PROJECT_ID,
  period_start: '2026-09-01T00:00:00Z',
  period_end: '2026-10-01T00:00:00Z',
  total_cost_usd: 18.42,
  total_input_tokens: 1_204_000,
  total_output_tokens: 186_500,
  total_llm_calls: 412,
  cap_hits: 0,
  included_usd: 25,
  hard_cap_usd: 50,
  utilization_pct: 36.84,
  status: 'OK',
}

const LLM_QUOTA = {
  id: '88888888-8888-4888-8888-888888888888',
  project_id: PROJECT_ID,
  enabled: true,
  period_type: 'monthly',
  included_usd: 25,
  overage_rate_usd: 1,
  hard_cap_usd: 50,
  soft_warn_threshold_pct: 80,
  at_cap_action: 'SOFT_WARN',
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  updated_by_user_id: null,
}

/** Shape from `agentGovernanceService.test.ts`: the investigator's config view. */
const INVESTIGATOR_CONFIG = {
  agent_id: 'investigator',
  source: 'default',
  config_version: 0,
  valid: true,
  errors: [],
  updated_at: null,
  updated_by: null,
  config: {
    agent_id: 'investigator',
    enabled: true,
    mode: 'shadow',
    extensions: {
      fixer: null,
      investigator: {
        budgets: { max_runs_per_day: 10, max_llm_calls_per_run: 30, max_tokens_per_run: 60_000, max_seconds_per_run: 300 },
        shadow_runs_completed: 7,
        promotion_note: null,
      },
    },
  },
}

const INTEGRATION_STATUS = ['jira', 'splunk', 'github'].map((provider, i) => ({
  provider,
  status: i === 1 ? 'down' : 'healthy',
  last_checked_at: isoAgo(0, 1),
  message: i === 1 ? 'connection refused' : null,
  response_ms: 120 + i * 40,
  consecutive_failures: i === 1 ? 3 : 0,
  last_success_at: isoAgo(i === 1 ? 2 : 0, 1),
}))

/**
 * Three clusters whose confidences land in three severity bands (0.91 P0,
 * 0.72 P1, 0.50 P2): the "Eligible failures" distribution bar has three
 * segments and the average (0.71) sits just over the 0.7 target.
 */
const CLUSTER_SPECS = [
  { label: 'Payment gateway timeout', size: 7, confidence: 0.91, service: 'payments' },
  { label: 'Tax rounding mismatch', size: 4, confidence: 0.72, service: 'checkout' },
  { label: 'Session cookie not cleared', size: 1, confidence: 0.5, service: 'auth' },
]

const clusters = () =>
  CLUSTER_SPECS.map((c, i) => ({
    cluster_id: `cluster-${i + 1}`,
    label: c.label,
    representative_error: `${c.label}: expected success, got error`,
    member_test_ids: Array.from({ length: c.size }, (_, j) => `tc-${i}-${j}`),
    size: c.size,
    cohesion_score: c.confidence,
  }))

const findings = () =>
  CLUSTER_SPECS.map((c, i) => ({
    cluster_id: `cluster-${i + 1}`,
    root_cause: `${c.label} in the ${c.service} service.`,
    failure_category: 'PRODUCT_BUG',
    confidence_score: c.confidence,
    causal_chain: null,
    evidence: null,
    affected_services: [c.service],
    contract_violations: null,
    recommended_actions: null,
    origin: 'pipeline',
    confidence_basis: 'heuristic_estimate',
  }))

const PIPELINE_STATUS = {
  pipeline_run_id: '99999999-9999-4999-8999-999999999999',
  workflow_type: 'deep',
  status: 'completed',
  started_at: isoAgo(0, 1.5),
  completed_at: isoAgo(0, 1.25),
  error: null,
  stage_summary: { completed: 5, failed: 0, skipped: 0, pending: 0 },
}

const DECISION_TRAIL = {
  run_id: RUN_ID,
  pipeline_run_id: PIPELINE_STATUS.pipeline_run_id,
  workflow_type: 'deep',
  pipeline_status: 'completed',
  started_at: PIPELINE_STATUS.started_at,
  completed_at: PIPELINE_STATUS.completed_at,
  total_cost_usd: 0.42,
  total_tokens: 18_400,
  stages: [],
  workflow_events: [],
  per_test: [],
  mode_distribution: {},
  fallback_count: 0,
}

export const DEEP_INVESTIGATION: ApiHandlers = [
  ...LAYOUT,
  ...RUNS,
  [/^\/api\/v1\/runs\/[0-9a-f-]{36}$/, ({ path }) => runs().find((r) => r.id === path.split('/')[4]) ?? respond(404, { detail: 'Run not found' })],
  [/^\/api\/v1\/deep-investigate\/[^/]+\/clusters$/, clusters],
  [/^\/api\/v1\/deep-investigate\/[^/]+\/findings$/, findings],
  [/^\/api\/v1\/agents\/runs\/[^/]+\/pipeline-status$/, () => PIPELINE_STATUS],
  [/^\/api\/v1\/runs\/[^/]+\/decision-trail$/, () => DECISION_TRAIL],
  ['/api/v1/integration-health/status', () => INTEGRATION_STATUS],
  [/^\/api\/v1\/projects\/[^/]+\/llm-usage$/, () => LLM_USAGE],
  [/^\/api\/v1\/projects\/[^/]+\/llm-quota$/, () => LLM_QUOTA],
  [/^\/api\/v1\/projects\/[^/]+\/agent-configs\/investigator$/, () => INVESTIGATOR_CONFIG],
  [/^\/api\/v1\/projects\/[^/]+\/investigations$/, () => ({ items: [], total: 0 })],
]

// ── Run intelligence (/runs/<run>/intelligence) ────────────────────────────

/**
 * `MOCK_INTELLIGENCE` of `RunIntelligencePage.test.tsx`, re-dated to `NOW`
 * and moved to a CONDITIONAL_GO at risk 58: the composite-risk fill (G6)
 * stops between its 30 and 70 ticks.
 */
const RISK_DIMENSIONS = [
  { name: 'user_impact', label: 'User Impact', score: 64, weight: 0.3, contribution: 19.2 },
  { name: 'reproducibility', label: 'Reproducibility', score: 72, weight: 0.25, contribution: 18 },
  { name: 'blast_radius', label: 'Blast Radius', score: 40, weight: 0.25, contribution: 10 },
  { name: 'recency', label: 'Recency', score: 54, weight: 0.2, contribution: 10.8 },
]

function runIntelligence() {
  const run = runs()[0]
  return {
    intelligence_available: true,
    run: {
      id: RUN_ID,
      build_number: String(run.build_number),
      status: 'FAILED',
      total_tests: run.total_tests,
      passed_tests: run.passed_tests,
      failed_tests: run.failed_tests,
      skipped_tests: run.skipped_tests,
      pass_rate: run.pass_rate,
      branch: 'main',
      duration_ms: run.duration_ms,
      start_time: run.start_time,
      end_time: run.end_time,
      ocp_namespace: 'qa',
    },
    structured_summary: {
      executive_summary: '12 failures detected across 2 suites. Primary cause: payment gateway timeouts.',
      layer1_executive: '12 failures detected across 2 suites. Primary cause: payment gateway timeouts.',
      layer2_incident: {
        what_failed: 'Payments suite tests',
        likely_cause: 'Gateway timeout',
        scope: 'payment-service',
        criticality: 'HIGH',
      },
      layer3_evidence: {},
      layer4_action_plan: {
        immediate_mitigation: 'Raise the gateway client timeout',
        fix_recommendations: ['Add a retry budget to the gateway client'],
        owner_hints: { sre: 'Check gateway latency', developer: 'Review client timeout' },
      },
      generated_at: isoAgo(0, 1),
      schema_version: 2,
    },
    failure_clusters: [
      {
        id: 'cluster-row-1',
        cluster_id: 'cl-1',
        label: 'Gateway timeouts',
        size: 8,
        representative_error: 'TimeoutError: gateway did not answer within 5000 ms',
        member_test_ids: ['t1', 't2', 't3', 't4', 't5', 't6', 't7', 't8'],
        cohesion_score: 0.85,
        criticality_level: 'HIGH',
        // The verdict card's dimension grid reads the top cluster's scores.
        dimension_scores: RISK_DIMENSIONS,
      },
    ],
    category_breakdown: { INFRASTRUCTURE: 8, PRODUCT_BUG: 4 },
    affected_suites: [
      { suite: 'Payments', failed_count: 8 },
      { suite: 'Auth', failed_count: 4 },
    ],
    release_decision: {
      recommendation: 'CONDITIONAL_GO',
      risk_score: 58,
      reasoning: 'Infrastructure failures dominate; product risk is contained to one suite.',
      blocking_issues: [],
      conditions_for_go: ['Re-run the Payments suite after the gateway fix'],
    },
    top_analyses: [],
    avg_confidence: 84,
    pipeline_stages: [
      {
        stage_name: 'summary',
        status: 'completed',
        started_at: isoAgo(0, 1.2),
        completed_at: isoAgo(0, 1.1),
        skipped_reason: null,
        execution_path: 'executed',
        fallback_used: false,
      },
    ],
    role_actions: {},
    all_green: false,
    dimension_scores: RISK_DIMENSIONS,
    what_changed_since_last_good_run: null,
    defect_candidates: [],
    summary_modes: null,
    provenance: null,
  }
}

export const RUN_INTELLIGENCE: ApiHandlers = [
  ...LAYOUT,
  [/^\/api\/v1\/runs\/[^/]+\/intelligence$/, runIntelligence],
  [/^\/api\/v1\/runs\/[^/]+\/decision-reports$/, () => []],
  [
    /^\/api\/v1\/runs\/[^/]+\/step-flips$/,
    () => ({
      run_id: RUN_ID,
      project_id: PROJECT_ID,
      tests_analyzed: 0,
      tests_with_flips: 0,
      total_flips: 0,
      truncated: false,
      tests: [],
    }),
  ],
  // The page marks the "view intelligence" onboarding step done on load
  // (fire and forget, errors swallowed); answered so it is not an unmocked call.
  [
    /^\/api\/v1\/onboarding\/[^/]+\/complete$/,
    () => ({ project_id: PROJECT_ID, steps: [], completed_count: 0, total_count: 0, progress_pct: 0, is_complete: false }),
    'POST',
  ],
]

// ── Release gate (/release-gate/<run>) ─────────────────────────────────────

/**
 * `flooredDecision()` of `ReleaseGatePage.test.tsx`: a NO_GO whose
 * composite (60) was raised by the pass-rate floor, not by the dimensions.
 */
const FLOORED_DECISION = {
  run_id: RUN_ID,
  recommendation: 'NO_GO',
  risk_score: 60,
  composite_risk: 60,
  dimension_scores: [
    { name: 'reproducibility', label: 'Reproducibility', score: 55.6, weight: 0.15, contribution: 8.34 },
    { name: 'blast_radius', label: 'Blast Radius', score: 22.2, weight: 0.15, contribution: 3.33 },
    { name: 'diagnosis_confidence', label: 'Diagnosis Confidence', score: 100, weight: 0.05, contribution: 5.0 },
  ],
  blocking_issues: [],
  conditions_for_go: [],
  reasoning: 'Quick-look decision derived from this run aggregates',
  score_model_version: 1,
  input_snapshot: { verdict_driver: 'pass_rate_floor', no_go_floor_pct: 63.0 },
  cluster_insights: [],
  baseline_diff: null,
  open_defects_by_component: [],
  human_override: null,
  overridden_by: null,
  original_recommendation: null,
  original_risk_score: null,
  override_audit: [],
  pass_rate: 44.4,
  build_number: '240',
  policy_id: null,
  policy_version: null,
  policy_level: 'hardcoded',
  rule_evaluations: [],
}

export const RELEASE_GATE: ApiHandlers = [
  ...LAYOUT,
  ...RUNS,
  [/^\/api\/v1\/release-readiness\/[^/]+$/, () => FLOORED_DECISION],
  // `MOCK_SCORING_MODEL` of `RunIntelligencePage.test.tsx`.
  [
    '/api/v1/scoring-model',
    () => ({
      version: 1,
      go_threshold: 20,
      no_go_threshold: 50,
      dimensions: [
        { name: 'reproducibility', weight: 0.15, description: 'How consistently the issue can be reproduced.' },
        { name: 'blast_radius', weight: 0.15, description: 'How much of the product the failures touch.' },
        { name: 'diagnosis_confidence', weight: 0.05, description: 'How sure the analysis is of the cause.' },
      ],
    }),
  ],
]

/**
 * A text that would run script if any code path turned a name into markup.
 * It must reach the screen as these literal characters, and
 * `window.__xss` must stay undefined.
 */
export const HOSTILE_NAME = '<img src=x onerror="window.__xss=1">'

/**
 * The floored NO_GO with three linked failure clusters (Wave 2.6 C0): the
 * "Linked Failure Clusters" card is drawn only when the decision carries
 * `cluster_insights`, and `FLOORED_DECISION` has none, so today's baseline
 * never shows it. Sizes 7 / 4 / 1 (the share a cluster chart would draw),
 * one label hostile, one criticality missing ("unclassified").
 * A separate decision, not a change to `FLOORED_DECISION`, so the committed
 * `gate-risk-gauge` baseline keeps exactly its inputs (the workflow timeline
 * above the card reads the cluster count).
 */
const GATE_CLUSTER_SPECS: { label: string; size: number; criticality: 'CRITICAL' | 'HIGH' | null }[] = [
  { label: 'Payment gateway timeout', size: 7, criticality: 'CRITICAL' },
  { label: HOSTILE_NAME, size: 4, criticality: 'HIGH' },
  { label: 'Session cookie not cleared', size: 1, criticality: null },
]

export const GATE_CLUSTERS = GATE_CLUSTER_SPECS.map((c, i) => ({
  id: `gate-cluster-row-${i + 1}`,
  cluster_id: `gate-cluster-${i + 1}`,
  label: c.label,
  size: c.size,
  representative_error: `${c.label}: expected success, got error`,
  member_test_ids: Array.from({ length: c.size }, (_, j) => `tc-gate-${i}-${j}`),
  cohesion_score: 0.9 - i * 0.2,
  criticality_level: c.criticality,
  dimension_scores: [],
}))
// The clustered decision is `releaseGateOn({ clusters: GATE_CLUSTERS })` (Phase D
// S2 deleted its flag-off handler set, `RELEASE_GATE_CLUSTERED`).

// ── Summary report (/reports/summary) ──────────────────────────────────────

/**
 * Six suites for the Summary report (Wave 2.6 C0, `SUMMARY_REPORT`), in
 * `latest` mode (the page's default: each suite's newest run). One suite has
 * no passing test at all; two carry step data, so the "Step %" column is
 * drawn with a dash for the other four. `lastRun` is `[days ago, hours]`,
 * all distinct, so the default "Last run, newest first" order is fixed.
 */
const SUMMARY_SUITE_SPECS: {
  name: string
  passed: number
  failed: number
  skipped: number
  broken: number
  lastRun: [number, number]
  steps?: [number, number]
}[] = [
  { name: 'Auth', passed: 40, failed: 3, skipped: 1, broken: 0, lastRun: [0, 2], steps: [212, 220] },
  { name: 'Checkout', passed: 55, failed: 2, skipped: 0, broken: 1, lastRun: [1, 3], steps: [301, 330] },
  { name: 'Payments', passed: 20, failed: 11, skipped: 2, broken: 3, lastRun: [0, 5] },
  { name: 'Search', passed: 22, failed: 4, skipped: 1, broken: 0, lastRun: [2, 1] },
  { name: 'Notifications', passed: 12, failed: 0, skipped: 0, broken: 0, lastRun: [4, 2] },
  { name: 'Legacy import', passed: 0, failed: 6, skipped: 2, broken: 1, lastRun: [12, 5] },
]

/**
 * Twelve top failing tests, most failures first. Two share the name
 * "login times out" in two suites (they are two tests, not one), one name
 * is `HOSTILE_NAME`, one row has neither suite nor class, and the counts tie
 * at 5 and at 2.
 */
const SUMMARY_TOP_FAILING: [string, string | null, string | null, number][] = [
  ['card declined shows reason', 'Payments', 'CardSpec', 11],
  ['login times out', 'Auth', 'LoginSpec', 9],
  ['login times out', 'Checkout', 'GuestLoginSpec', 8],
  [HOSTILE_NAME, 'Search', 'SearchSpec', 6],
  ['refund webhook retried', 'Payments', 'RefundSpec', 5],
  ['legacy csv import keeps encoding', 'Legacy import', 'ImportSpec', 5],
  ['checkout total includes tax', 'Checkout', 'TotalSpec', 4],
  ['search paginates', 'Search', 'SearchSpec', 3],
  ['token refresh', 'Auth', 'AuthSpec', 2],
  ['apply coupon', 'Checkout', 'CouponSpec', 2],
  ['orphaned result without a suite', null, null, 1],
  ['legacy xml import keeps order', 'Legacy import', 'ImportSpec', 1],
]

const pct1 = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 1000) / 10 : 0)

/**
 * `GET /reports/summary?days=&mode=`. In `window` mode every count is the
 * latest-run count times the suite's runs in the window (a pure function of
 * the suite's index), so the two modes differ the way the service's do:
 * window totals are volume, latest totals one snapshot.
 */
export function summaryReport(request: ApiRequest) {
  const days = intParam(request, 'days', 30)
  const mode = request.url.searchParams.get('mode') === 'window' ? 'window' : 'latest'
  const scale = (i: number) => (mode === 'window' ? 2 + (i % 3) : 1)
  const suites = SUMMARY_SUITE_SPECS.map((s, i) => {
    const k = scale(i)
    const passed = s.passed * k
    const failed = s.failed * k
    const skipped = s.skipped * k
    const broken = s.broken * k
    const total = passed + failed + skipped + broken
    return {
      suite_name: s.name,
      total,
      passed,
      failed,
      skipped,
      broken,
      pass_rate_pct: pct1(passed, total),
      weighted_pass_rate_pct: pct1(passed, passed + failed + broken),
      last_run_at: isoAgo(s.lastRun[0], s.lastRun[1]),
      step_success_rate: s.steps ? pct1(s.steps[0], s.steps[1]) : null,
      passed_steps: s.steps ? s.steps[0] : null,
      total_steps: s.steps ? s.steps[1] : null,
    }
  })
  const sum = (key: 'total' | 'passed' | 'failed' | 'skipped' | 'broken') => suites.reduce((n, s) => n + s[key], 0)
  const total = sum('total')
  const passed = sum('passed')
  const failed = sum('failed')
  const skipped = sum('skipped')
  const broken = sum('broken')
  const evaluated = passed + failed + broken
  const runCount = mode === 'window' ? 42 : 14
  return {
    project_id: PROJECT_ID,
    project_name: 'Checkout',
    mode,
    window_days: days,
    generated_at: NOW.toISOString(),
    period_start: isoAgo(days),
    period_end: NOW.toISOString(),
    totals: {
      total_test_cases: total,
      passed,
      failed,
      skipped,
      broken,
      evaluated,
      pass_rate_pct: pct1(passed, total),
      pass_rate_basis: 'unique_tests',
      pass_rate_basis_label: 'per unique test',
      fail_rate_pct: pct1(failed, total),
      skip_rate_pct: pct1(skipped, total),
      broken_rate_pct: pct1(broken, total),
      weighted_pass_rate_pct: pct1(passed, evaluated),
    },
    run_count: runCount,
    runs_per_day: mode === 'window' ? Math.round((runCount / days) * 100) / 100 : null,
    avg_duration_ms: 512_400,
    latest_run_at: isoAgo(0, 2),
    flaky_test_count: 3,
    flaky_rate_pct: pct1(3, total),
    flaky_criteria: { window_runs: 10, min_runs: 5, min_flips: 2, min_failure_ratio: 0.1, max_failure_ratio: 0.9 },
    suites,
    top_failing_tests: SUMMARY_TOP_FAILING.map(([test_name, suite_name, class_name, failures]) => ({
      suite_name,
      class_name,
      test_name,
      failures: mode === 'window' ? failures * 2 : failures,
    })),
  }
}

/** `/reports/summary`: the report, and (VIZ-607) the reader's background
 *  exports, none here, so the Background exports panel stays hidden. */
export const SUMMARY_REPORT: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/reports/summary', summaryReport],
  ['/api/v1/reports/summary/exports', () => []],
]

// ── Intelligence hub (/intelligence) ───────────────────────────────────────

/**
 * The newest six builds only, so the runs table (the region around the
 * pass-rate meters, G8) stays short; they cover all three meter bands
 * (>= 80, 60-80, < 60).
 */
function hubRuns(request: ApiRequest) {
  const items = runs().slice(0, 6)
  return { ...page(items, intParam(request, 'size', 50)), total: items.length }
}

export const INTELLIGENCE_HUB: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/runs', hubRuns],
  [/^\/api\/v1\/projects\/[^/]+\/llm-usage$/, () => LLM_USAGE],
  [/^\/api\/v1\/projects\/[^/]+\/llm-quota$/, () => LLM_QUOTA],
]

// ── Coverage (/coverage) ───────────────────────────────────────────────────

export const COVERAGE: ApiHandlers = [...LAYOUT, ...RUNS, ...ANALYTICS]

// ── Failure analysis (/failures) ───────────────────────────────────────────

const FAILURE_CATEGORIES = {
  items: [
    { category: 'ASSERTION', count: 21, kind: 'product' },
    { category: 'TIMEOUT', count: 9, kind: 'infrastructure' },
    { category: 'LOCATOR', count: 6, kind: 'test_code' },
  ],
  by_kind: [
    { kind: 'product', count: 21 },
    { kind: 'infrastructure', count: 9 },
    { kind: 'test_code', count: 6 },
    { kind: 'unknown', count: 0 },
  ],
  period_days: 30,
}

const TOP_FAILING = {
  items: [
    ['card declined shows reason', 'Payments', 9, 'ASSERTION', 'product'],
    ['checkout total includes tax', 'Checkout', 7, 'ASSERTION', 'product'],
    ['refund webhook retried', 'Payments', 5, 'TIMEOUT', 'infrastructure'],
    ['search paginates', 'Search', 3, 'LOCATOR', 'test_code'],
  ].map(([test, suite, count, category, kind], i) => ({
    test_name: test,
    fail_count: count,
    test_fingerprint: `fp-top-${i}`,
    suite_name: suite,
    class_name: `${suite}Spec`,
    failure_category: category,
    failure_kind: kind,
    last_failed: isoAgo(i === 0 ? 0 : i + 1, 2),
    failure_step: null,
  })),
}

const JIRA_METADATA = {
  available: false,
  reason: 'not_configured',
  projects: [],
  issue_types: [],
  default_project_key: null,
  webhook_available: false,
}

export const FAILURES: ApiHandlers = [
  ...LAYOUT,
  ...RUNS,
  ...ANALYTICS,
  ['/api/v1/analytics/failure-categories', () => FAILURE_CATEGORIES],
  ['/api/v1/analytics/top-failing', () => TOP_FAILING],
  [/^\/api\/v1\/projects\/[^/]+\/defects\/jira\/metadata$/, () => JIRA_METADATA],
  [
    /^\/api\/v1\/runs\/[^/]+\/suspects$/,
    ({ url, path }) => ({
      run_id: path.split('/')[4],
      cluster_id: null,
      fingerprint: url.searchParams.get('fingerprint'),
      available: false,
      reason: 'no_commit_range',
      caveat: 'No commit range was recorded for this run.',
      suspects: [],
    }),
  ],
]

// ── Wave 2.6: the catalogue with its flags ON ──────────────────────────────
//
// Everything below is used ONLY by the flag-on specs (`tests/ci-e2e/rollout-*`
// and the `*-on` visual specs). The flag-off handler lists above are not
// touched, so every committed flag-off PNG keeps exactly its inputs.
//
// The `chart-data` payloads follow the contract shapes (`contracts/viz/
// fixtures/chart_series/valid/series_chart_data_top_n_other.json` for the
// series, `envelope/valid/*.json` for `meta`): values in PERCENTAGE POINTS,
// zero-filled days, an unmeasured bucket as `y: null` with `measured: false`
// and a reason. A payload the client validator refuses draws an error frame;
// every flag-on spec asserts that no frame is in an error state, so a bad
// fixture fails there by name instead of as a screenshot of an error.

/** Release ids (UUID-shaped, like the server's). */
export const RELEASE_ID = {
  current: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000001',
  august: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000002',
  july: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000003',
  hostile: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000004',
  planned: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000005',
  undated: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000006',
  empty: 'aaaaaaaa-aaaa-4aaa-8aaa-000000000007',
} as const

interface ReleaseSpec {
  id: string
  name: string
  status: string
  /** Days before `NOW` it was released (`released_at`). */
  released?: number
  /** Days before `NOW` it is planned (`planned_date`). */
  planned?: number
  runs: number
  /** Days of runs from its start (the length of its comparison line). */
  length: number
  /** Its pass rate on the `day`-th day since its start. */
  rate: (day: number) => number
}

/**
 * Seven releases of `PROJECT_ID`, chosen for the markers and the comparison:
 * `2026.09` (the gate run's own) released 10 days ago, inside every window
 * and at 100% on every day (the verdict-integrity case: a NO_GO beside a
 * perfect release); `2026.10` only PLANNED, 3 days ago (a planned-date
 * marker); `2026.08` 40 days ago (outside Overview's 30 days: counted as
 * outside the window, not dropped); a hostile name 55 days ago; `2026.07` 70
 * days ago; one with no date at all (never drawn); one with no runs
 * (`test_run_count: 0`, left out of the comparison).
 */
const RELEASE_SPECS: ReleaseSpec[] = [
  { id: RELEASE_ID.current, name: '2026.09', status: 'released', released: 10, runs: 9, length: 10, rate: () => 100 },
  { id: RELEASE_ID.planned, name: '2026.10', status: 'planned', planned: 3, runs: 2, length: 3, rate: (d) => 88 - d * 2 },
  { id: RELEASE_ID.august, name: '2026.08', status: 'released', released: 40, runs: 14, length: 26, rate: (d) => 82 + ((d * 7) % 11) },
  { id: RELEASE_ID.hostile, name: HOSTILE_NAME, status: 'released', released: 55, runs: 6, length: 12, rate: (d) => 71 + ((d * 5) % 9) },
  { id: RELEASE_ID.july, name: '2026.07', status: 'released', released: 70, runs: 12, length: 20, rate: (d) => 76 + ((d * 3) % 13) },
  { id: RELEASE_ID.undated, name: 'next', status: 'draft', runs: 0, length: 0, rate: () => 0 },
  { id: RELEASE_ID.empty, name: '2026.06', status: 'released', released: 85, runs: 0, length: 0, rate: () => 0 },
]

export const RELEASES = RELEASE_SPECS.map((r, i) => ({
  id: r.id,
  project_id: PROJECT_ID,
  project_name: 'Checkout',
  name: r.name,
  version: r.name,
  description: null,
  status: r.status,
  planned_date: r.planned !== undefined ? daysAgo(r.planned) : null,
  released_at: r.released !== undefined ? isoAgo(r.released, 2) : null,
  created_at: isoAgo(100 - i),
  updated_at: isoAgo(1),
  phases: [],
  test_run_count: r.runs,
}))

/** The top bar's release list with the releases above (put first: first match wins). */
const RELEASES_LIST: ApiHandlers = [['/api/v1/releases', () => page(RELEASES)]]

/** `meta` of a `chart-data` answer over the `days` ending today. */
function chartMeta(days: number, over: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    scope: {
      projects: [{ id: PROJECT_ID, name: 'Checkout' }],
      releases: [],
      suites: [],
      window: { from: daysAgo(days - 1), to: daysAgo(0), days, timezone: 'UTC' },
    },
    totals: { matched_runs: 13, total_runs: 13, matched_executions: 2_840, total_executions: 2_840 },
    pass_rate_basis: 'executions',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: daysAgo(0),
    generated_at: NOW.toISOString(),
    as_of: NOW.toISOString(),
    // Not in the C2 type yet; the frames read it defensively (plan OD-17).
    definitions: { grain: 'execution_row' },
    ...over,
  }
}

const NO_RUNS_REASON = 'no executions in this bucket'

/** A bucket with no data: a gap, with the server's reason. */
const gapPoint = (x: string, reason = NO_RUNS_REASON) => ({ x, y: null, n: 0, measured: false, reason })

/**
 * The seven busiest suites in the server's order (by executions), one of
 * them hostile, then the server's "Other" fold of four more. Legacy import is
 * the worst, so the heatmap must draw it first although it arrives sixth.
 */
const SERIES_SUITES: { key: string; label: string; base: number; swing: number; n: number }[] = [
  { key: 'Checkout', label: 'Checkout', base: 96, swing: 1, n: 120 },
  { key: 'Auth', label: 'Auth', base: 93, swing: 2, n: 110 },
  { key: 'Payments', label: 'Payments', base: 71, swing: 4, n: 90 },
  { key: 'Search', label: 'Search', base: 84, swing: 3, n: 70 },
  { key: 'Notifications', label: 'Notifications', base: 99, swing: 0.5, n: 40 },
  { key: 'Legacy import', label: 'Legacy import', base: 52, swing: 5, n: 30 },
  { key: HOSTILE_NAME, label: HOSTILE_NAME, base: 88, swing: 2, n: 20 },
  { key: '__other__', label: 'Other', base: 90, swing: 1.5, n: 60 },
]

const WOBBLE = [0, -1.5, 1, -3, 2, 0.5, -2, 1.5, -0.5, 2.5, -1, 0, 1, -2.5]

/** A rate in 0..100 with one decimal, a pure function of the suite and the day. */
const wobble = (base: number, swing: number, n: number) =>
  Math.round(Math.min(100, Math.max(0, base + WOBBLE[n % WOBBLE.length] * swing)) * 10) / 10

/**
 * `chart-data?metric=pass_rate&group_by=day&group_by=suite&top_n=7`: the
 * Trends multi-series AND heatmap (one request). The 7-day gap of the Trends
 * fixture is a gap in every suite; Notifications also did not run 2 days ago.
 */
function suiteSeries(days: number, suiteNames: string[] = []) {
  // C1: Compare names its suites (`suite_name`, case-insensitive on the
  // server) and folds nothing: just those suites, no "Other", no cut.
  const named = new Set(suiteNames.map((name) => name.trim().toLowerCase()))
  const suites = named.size > 0 ? SERIES_SUITES.filter((suite) => named.has(suite.key.toLowerCase())) : SERIES_SUITES
  return {
    meta: chartMeta(
      days,
      named.size > 0
        ? {}
        : {
            truncated: true,
            truncated_total: 11,
            truncated_axes: { series: { dimension: 'suite', kept: 7, total: 11 } },
          },
    ),
    series: {
      kind: 'series',
      dimensions: ['day', 'suite'],
      x_type: 'time',
      series: suites.map((suite) => ({
        key: suite.key,
        label: suite.label,
        points: Array.from({ length: days }, (_, i) => {
          const n = days - 1 - i
          const x = daysAgo(n)
          if (GAP_DAYS.has(n) || (suite.key === 'Notifications' && n === 2)) return gapPoint(x)
          return { x, y: wobble(suite.base, suite.swing, n), n: suite.n + (n % 5), measured: true, reason: null }
        }),
      })),
    },
  }
}

/**
 * `chart-data?metric=duration_p50|duration_p95&group_by=day`, in ms. The gap
 * days have neither; 4 days ago the p95 was not measured (a gap in the p95
 * line only, never 0 ms); 13 days ago the p95 is BELOW the p50 (an inverted
 * day, which the band notice names).
 */
function durationSeries(days: number, metric: 'duration_p50' | 'duration_p95') {
  const p95 = metric === 'duration_p95'
  return {
    meta: chartMeta(days),
    series: {
      kind: 'series',
      dimensions: ['day'],
      x_type: 'time',
      series: [
        {
          key: metric,
          label: p95 ? 'p95' : 'p50',
          points: Array.from({ length: days }, (_, i) => {
            const n = days - 1 - i
            const x = daysAgo(n)
            if (GAP_DAYS.has(n)) return gapPoint(x)
            if (p95 && n === 4) return gapPoint(x, 'too few executions for a 95th percentile')
            const p50 = 1_800 + ((n * 170) % 900)
            const value = p95 ? (n === 13 ? p50 - 300 : p50 * 3 + ((n * 410) % 2_500)) : p50
            return { x, y: value, n: 180 + ((n * 13) % 40), measured: true, reason: null }
          }),
        },
      ],
    },
  }
}

/**
 * `chart-data?metric=pass_rate&group_by=day&group_by=release&release_id=...`:
 * one series per requested release that exists, over the window, each
 * measured for `length` days from its start. The lengths differ, so aligned
 * on their starts some lines run past others.
 */
function releaseSeries(days: number, releaseIds: string[], rate?: number) {
  const picked = releaseIds
    .map((id) => RELEASE_SPECS.find((r) => r.id === id))
    .filter((r): r is ReleaseSpec => r !== undefined)
  return {
    meta: chartMeta(days, {
      definitions: { grain: 'run_aggregate' },
      comparability: { comparable: true, reason: null, reason_code: null },
    }),
    series: {
      kind: 'series',
      dimensions: ['day', 'release'],
      x_type: 'time',
      series: picked.map((release) => {
        const start = release.released ?? release.planned ?? 0
        return {
          key: release.id,
          label: release.name,
          points: Array.from({ length: days }, (_, i) => {
            const n = days - 1 - i
            const since = start - n
            if (since < 0 || since >= release.length) return gapPoint(daysAgo(n))
            const y = rate ?? release.rate(since)
            return { x: daysAgo(n), y, n: 40 + ((since * 7) % 30), measured: true, reason: null }
          }),
        }
      }),
    },
  }
}

/**
 * A `{meta, series}` chart response as the SERVER sends it: every analytics
 * route answers `with_meta(payload, meta)` (`backend/app/services/
 * analytics_meta.py`), i.e. the C3 series' keys at the TOP level and `meta`
 * beside them, so a chart-data body's `series` is the ARRAY of lines.
 * Wave 3 FK0 found that every Wave 2.6 fixture was hand-wrapped as
 * `{meta, series: {kind, ...}}`, which hid that the client read only the
 * wrapped form; the client now reshapes the wire body
 * (`chartResponseFromEnvelope`), and these fixtures send the wire body so the
 * e2e runs that reshape. The data is unchanged, so every flag-on PNG is too.
 */
export function onTheWire(response: { meta: unknown; series: Record<string, unknown> }): Record<string, unknown> {
  return { ...response.series, meta: response.meta }
}

/** Every `chart-data` request the catalogue makes, answered by its parameters. */
function chartData(request: ApiRequest, releaseRate?: number) {
  const q = request.url.searchParams
  const days = intParam(request, 'days', 30)
  const metric = q.get('metric')
  const groupBy = q.getAll('group_by').join(',')
  if (metric === 'pass_rate' && groupBy === 'day,suite') return onTheWire(suiteSeries(days, q.getAll('suite_name')))
  if ((metric === 'duration_p50' || metric === 'duration_p95') && groupBy === 'day') {
    return onTheWire(durationSeries(days, metric))
  }
  if (metric === 'pass_rate' && groupBy === 'day,release') {
    return onTheWire(releaseSeries(days, q.getAll('release_id'), releaseRate))
  }
  const ladder = ladderChartData(request)
  if (ladder) return ladder
  // An unexpected chart request is a fixture gap: answer it loudly.
  return respond(400, { detail: `no chart-data fixture for metric=${metric} group_by=${groupBy}` })
}

export const CHART_DATA_PATH = '/api/v1/analytics/chart-data'
const CHART_DATA: ApiHandlers = [[CHART_DATA_PATH, (request) => chartData(request)]]

/**
 * `GET /analytics/top-failing`: twelve tests, most failures first, a tie at
 * the 10th / 11th place (3 and 3), a hostile name, one name in two suites.
 */
const TOP_FAILING_12: [string, string, number][] = [
  ['card declined shows reason', 'Payments', 14],
  ['login times out', 'Auth', 11],
  ['login times out', 'Checkout', 9],
  [HOSTILE_NAME, 'Search', 8],
  ['refund webhook retried', 'Payments', 7],
  ['checkout total includes tax', 'Checkout', 6],
  ['search paginates', 'Search', 5],
  ['token refresh', 'Auth', 4],
  ['apply coupon', 'Checkout', 4],
  ['address autocomplete', 'Checkout', 3],
  ['email receipt sent', 'Notifications', 3],
  ['legacy csv import keeps encoding', 'Legacy import', 1],
]

export const TOP_FAILING_ROWS = TOP_FAILING_12.map(([test, suite, count], i) => ({
  test_name: test,
  fail_count: count,
  test_fingerprint: `fp-top12-${i}`,
  suite_name: suite,
  class_name: 'SuiteSpec',
  failure_category: i % 3 === 0 ? 'TIMEOUT' : 'ASSERTION',
  failure_kind: i % 3 === 0 ? 'infrastructure' : 'product',
  last_failed: isoAgo(i % 4, 2),
  failure_step: null,
}))

export const TOP_FAILING_PATH = '/api/v1/analytics/top-failing'
const TOP_FAILING_HANDLER: ApiHandlers = [[TOP_FAILING_PATH, () => ({ items: TOP_FAILING_ROWS })]]

/** /overview with the catalogue: + top failing (Failure categories draws the page's own read). */
export const OVERVIEW_ON: ApiHandlers = [...RELEASES_LIST, ...TOP_FAILING_HANDLER, ...OVERVIEW]

// `TRENDS_ON` and `SUITE_DETAIL_ON` are declared after `WAVE3` (end of file): they answer the Wave 3 reads too.

/** /reports/summary with the catalogue: + the trend (`/metrics/trends`). */
export const SUMMARY_REPORT_ON: ApiHandlers = [...RELEASES_LIST, ...SUMMARY_REPORT, ['/api/v1/metrics/trends', trends]]


/** Seven clusters: past the donut's five, so the share is a ranked bar. */
const GATE_CLUSTER_SPECS_7: { label: string; size: number }[] = [
  { label: 'Payment gateway timeout', size: 9 },
  { label: 'Tax rounding mismatch', size: 6 },
  { label: HOSTILE_NAME, size: 5 },
  { label: 'Session cookie not cleared', size: 4 },
  { label: 'Search index stale', size: 3 },
  { label: 'Coupon expiry off by one', size: 2 },
  { label: 'Webhook signature mismatch', size: 1 },
]

export const GATE_CLUSTERS_7 = GATE_CLUSTER_SPECS_7.map((c, i) => ({
  id: `gate7-cluster-row-${i + 1}`,
  cluster_id: `gate7-cluster-${i + 1}`,
  label: c.label,
  size: c.size,
  representative_error: `${c.label}: expected success, got error`,
  member_test_ids: Array.from({ length: c.size }, (_, j) => `tc-gate7-${i}-${j}`),
  cohesion_score: 0.9 - i * 0.1,
  criticality_level: i < 2 ? 'CRITICAL' : 'HIGH',
  dimension_scores: [],
}))

/** A GO (the other half of verdict integrity: a GO beside a 0% release). */
const GO_DECISION = {
  ...FLOORED_DECISION,
  recommendation: 'GO',
  risk_score: 12,
  composite_risk: 12,
  input_snapshot: {},
  reasoning: 'All dimensions under the GO threshold',
  pass_rate: 97.5,
}

export interface GateOnOptions {
  /** The decision's clusters (default none, as in `FLOORED_DECISION`). */
  clusters?: readonly unknown[]
  /** The run's release (default 2026.09); `null`: the run has no release. */
  releaseId?: string | null
  /** The release list (default `RELEASES`). */
  releases?: readonly unknown[]
  /** The GO decision instead of the floored NO_GO. */
  go?: boolean
  /** Every measured release day at this rate (100 or 0 for the integrity cases). */
  releaseRate?: number
}

/**
 * /release-gate/<run> with the catalogue: + `GET /runs/{id}` (the run's
 * release) and one `chart-data` by release. The decision is the committed
 * `FLOORED_DECISION` (NO_GO) unless `go`.
 */
export function releaseGateOn(options: GateOnOptions = {}): ApiHandlers {
  const decision = { ...(options.go ? GO_DECISION : FLOORED_DECISION), cluster_insights: options.clusters ?? [] }
  const releaseId = options.releaseId === undefined ? RELEASE_ID.current : options.releaseId
  const releaseList = options.releases ?? RELEASES
  const release = RELEASES.find((r) => r.id === releaseId)
  return [
    ['/api/v1/releases', () => page([...releaseList])],
    [CHART_DATA_PATH, (request) => chartData(request, options.releaseRate)],
    [
      /^\/api\/v1\/runs\/[0-9a-f-]{36}$/,
      ({ path }) => {
        const run = runs().find((r) => r.id === path.split('/')[4])
        if (!run) return respond(404, { detail: 'Run not found' })
        return { ...run, release_id: releaseId, release_name: release?.name ?? null }
      },
    ],
    [/^\/api\/v1\/release-readiness\/[^/]+$/, () => decision],
    ...RELEASE_GATE,
  ]
}

// ── Wave 3: the advanced sections (both flags ON) ──────────────────────────
//
// Used ONLY by the Wave 3 flag-on specs (`tests/ci-e2e/rollout-heatmaps`,
// `-coverage-map`, `-failure-groups`, `-scatter`, `-drill`, `-cross-filter`,
// the Wave 3 lines of `rollout-trends` / `rollout-suite-detail`, and the Wave 3
// regions of the `*-on` visual specs). The flag-off handler lists are not
// touched, so every committed flag-off PNG keeps exactly its inputs.
//
// Every body is the server's WIRE shape, transcribed (not imported, see the
// header) from the response builders named on each: the C3 keys at the TOP
// level with `meta` beside them (`with_meta`), the envelope keys a route
// lifts into `meta` (`truncated`, `truncated_axes`, `outside_window`) absent
// from the body, ids and keys spelled as the SQL spells them (suite keys
// LOWER-cased, `s:` / `c:` / `t:` node ids, UPPERCASE sentinel ids), and
// `meta.scope.suites` the suites the SQL applied (`build_meta`).
// `tests/ci-e2e/rollout-fixtures.spec.ts` validates every one with the
// client's own `validateAnyChartSeries` / `validateEnvelopeMeta`, so a bad
// fixture fails there by name, never as an error frame in a screenshot.

/** A 250-character name: every label channel must cut or wrap it, never spill. */
export const HOSTILE_LONG_NAME = `Checkout regression ${'with a very long generated test name '.repeat(8)}`.slice(0, 250)
/** The hostile names every Wave 3 label channel carries: markup, two Object members, and a 250-character name. */
export const HOSTILE_NAMES = [HOSTILE_NAME, 'constructor', '__proto__', HOSTILE_LONG_NAME] as const

export const HEATMAP_PATH = '/api/v1/analytics/heatmap'
export const COVERAGE_MAP_PATH = '/api/v1/analytics/coverage-map'
export const FAILURE_GROUPS_PATH = '/api/v1/analytics/failure-groups'
export const SYSTEMIC_CLUSTERS_PATH = '/api/v1/analytics/systemic-clusters'
export const TEST_SCATTER_PATH = '/api/v1/analytics/test-scatter'
export const CHART_ROWS_PATH = '/api/v1/analytics/chart-data/rows'

/**
 * An analytics refusal as `core/analytics_errors.error_body` writes it (a 422
 * carries `param` and `allowed`). The client never sends one of these
 * requests; a fixture answers it so a section that did would fail loudly.
 */
export const refusal = (code: string, param: string | null = null) => ({
  code,
  message: `refused: ${code}`,
  param,
  allowed: null,
  request_id: `req-fixture-${code}`,
  detail: `refused: ${code}`,
})

/** chart-data's suite KEY: `LOWER(effective suite)` (`chart_data_service.DIMENSIONS['suite']`). */
export const suiteKey = (label: string) => label.toLowerCase()

/**
 * `build_meta`'s `scope.suites`: the suites the SQL applied, through
 * `suite_keys` (trimmed, lower-cased, blanks dropped, duplicates collapsed).
 * The drill ladder's stale-response guard reads it (FK5 decision 7).
 */
function scopedTo<T>(request: ApiRequest, body: T): T {
  const suites = [
    ...new Set(
      request.url.searchParams
        .getAll('suite_name')
        .map((name) => name.trim().toLowerCase())
        .filter(Boolean),
    ),
  ]
  const record = body as unknown as { meta?: { scope?: Record<string, unknown> } }
  if (!record.meta?.scope || suites.length === 0) return body
  return { ...record, meta: { ...record.meta, scope: { ...record.meta.scope, suites } } } as unknown as T
}

/** Two decimals, as Python's `round(x, 2)` (every value here is exact enough not to hit a half). */
const round2 = (value: number) => Math.round(value * 100) / 100

interface StatusCounts {
  passed: number
  failed: number
  broken: number
  skipped: number
  unknown: number
}

const NO_COUNTS: StatusCounts = { passed: 0, failed: 0, broken: 0, skipped: 0, unknown: 0 }

/** `n` executions, `skipped` of them skipped, the rest `rate` % passed; a third of the failures broken. */
function statusCounts(n: number, rate: number, skipped = 0): StatusCounts {
  const evaluated = n - skipped
  const passed = Math.round((evaluated * rate) / 100)
  const bad = evaluated - passed
  const broken = Math.floor(bad / 3)
  return { passed, failed: bad - broken, broken, skipped, unknown: 0 }
}

const evaluatedOf = (c: StatusCounts) => c.passed + c.failed + c.broken
const executionsOf = (c: StatusCounts) => c.passed + c.failed + c.broken + c.skipped + c.unknown

/** `core/pass_rate.canonical_pass_rate`, two decimals; `null` (never 0) with nothing evaluated. */
function passRateOf(c: StatusCounts): number | null {
  const evaluated = evaluatedOf(c)
  return evaluated === 0 ? null : round2((c.passed / evaluated) * 100)
}

/** Code-unit order, never the locale's (the server sorts `COLLATE "C"`). */
const byCode = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

/** An instant as Python's `datetime.isoformat()` writes a UTC one (`+00:00`, no millisecond part when 0). */
const pyInstant = (iso: string) => iso.replace('.000Z', '+00:00')

// ── /analytics/heatmap (BE1, `heatmap_service.build_heatmap` + the router) ─

interface HeatRow {
  key: string
  label: string
  /** One per column; `null`: no execution of that row in that column. */
  cells: (StatusCounts | null)[]
}

/** The server's row rank (`definitions.rows`): most failed + broken, then most executions, then key by code point. */
function rankHeatRows(rows: HeatRow[]): HeatRow[] {
  const sum = (row: HeatRow, of: (c: StatusCounts) => number) => row.cells.reduce((total, c) => total + (c ? of(c) : 0), 0)
  return [...rows].sort(
    (a, b) =>
      sum(b, (c) => c.failed + c.broken) - sum(a, (c) => c.failed + c.broken) ||
      sum(b, executionsOf) - sum(a, executionsOf) ||
      byCode(a.key, b.key),
  )
}

/** `_cell` for a rate kind: no execution -> `null, n 0` with zero counts; nothing evaluated -> `null` with its real n. */
function rateCell(x: number, y: number, counts: StatusCounts | null) {
  if (counts === null) return { x, y, value: null, n: 0, counts: { ...NO_COUNTS } }
  return { x, y, value: passRateOf(counts), n: executionsOf(counts), counts: { ...counts } }
}

interface AxisCut {
  dimension: string
  kept: number
  total: number
}

function heatmapMeta(days: number, kind: string, cut: { series?: AxisCut; x?: AxisCut }, rate: boolean) {
  const axes = { ...(cut.x ? { x: cut.x } : {}), ...(cut.series ? { series: cut.series } : {}) }
  const truncated = Object.keys(axes).length > 0
  return chartMeta(days, {
    pass_rate_basis: rate ? 'executions' : null,
    truncated,
    // The column axis first, as chart-data's scalar does.
    truncated_total: cut.x ? cut.x.total : cut.series ? cut.series.total : null,
    ...(truncated ? { truncated_axes: axes } : {}),
    definitions: {
      kind,
      grain: 'execution_row',
      window_clock: 'test_runs.created_at, UTC',
      timezone: 'UTC',
      rows: 'worst first: the most failed + broken executions in the window',
      ...(rate ? { value: 'Pass rate in percentage points (unit: percent, 0-100)' } : {}),
    },
  })
}

function rateMatrix(columns: readonly (readonly [string, string])[], rows: HeatRow[], meta: Record<string, unknown>) {
  const ranked = rankHeatRows(rows)
  return {
    kind: 'matrix',
    value_type: 'rate',
    x_labels: columns.map(([, label]) => label),
    y_labels: ranked.map((row) => row.label),
    x_keys: columns.map(([key]) => key),
    y_keys: ranked.map((row) => row.key),
    cells: ranked.flatMap((row, y) => row.cells.map((counts, x) => rateCell(x, y, counts))),
    unit: 'percent',
    meta,
  }
}

/** The suites the server ranked for the suite x day heatmap: it keeps the default rows and counts the rest. */
export const HEATMAP_SUITE_TOTAL = 11

/**
 * `kind=suite_day`: the seven suites of the Trends series (the hostile name
 * among them), one column per UTC day of the window, oldest first. The 7-day
 * gap of the Trends fixture is a gap in every row; Notifications did not run
 * 2 days ago; Search ran only SKIPPED tests 3 days ago (a cell with
 * executions and no rate: `null`, never 0%). 11 suites ranked, 7 kept
 * (`truncated_axes.series`), never an "Other" row.
 */
export function suiteDayMatrix(days: number) {
  const columns = Array.from({ length: days }, (_, i) => [daysAgo(days - 1 - i), daysAgo(days - 1 - i)] as const)
  const rows: HeatRow[] = SERIES_SUITES.filter((suite) => suite.key !== '__other__').map((suite) => ({
    key: suiteKey(suite.label),
    label: suite.label,
    cells: columns.map((_, i) => {
      const n = days - 1 - i
      if (GAP_DAYS.has(n) || (suite.key === 'Notifications' && n === 2)) return null
      const executions = suite.n + (n % 5)
      if (suite.key === 'Search' && n === 3) return { ...NO_COUNTS, skipped: executions }
      return statusCounts(executions, wobble(suite.base, suite.swing, n), n % 3)
    }),
  }))
  const cut = { series: { dimension: 'suite', kept: rows.length, total: HEATMAP_SUITE_TOTAL } }
  return rateMatrix(columns, rows, heatmapMeta(days, 'suite_day', cut, true))
}

/**
 * The suites of the Coverage page and the hostile ones (markup and two Object
 * members, as suite names an ingested CI file can carry), each with a base
 * pass rate.
 */
const MATRIX_SUITES: { label: string; base: number }[] = [
  { label: 'Auth', base: 94 },
  { label: 'Checkout', base: 97 },
  { label: 'Payments', base: 56 },
  { label: 'Search', base: 76 },
  { label: 'Notifications', base: 99 },
  { label: HOSTILE_NAME, base: 88 },
  { label: 'constructor', base: 91 },
  { label: '__proto__', base: 83 },
]

/**
 * Environment columns, busiest first: the key is `LOWER(TRIM(environment))`,
 * the label its ingested spelling; runs with none are the `(none)` bucket.
 */
export const HEATMAP_ENVIRONMENTS = [
  ['ci', 'CI'],
  ['staging', 'Staging'],
  ['production', 'Production'],
  ['(none)', '(none)'],
] as const

/**
 * `kind=suite_environment`: eight suites x four environments. Notifications
 * never ran in production (no execution: `null`, n 0); the hostile suite ran
 * only skipped tests with no environment (n 6, nothing evaluated: `null`).
 */
export function suiteEnvironmentMatrix(days: number) {
  const rows: HeatRow[] = MATRIX_SUITES.map((suite, s) => ({
    key: suiteKey(suite.label),
    label: suite.label,
    cells: HEATMAP_ENVIRONMENTS.map(([env], e) => {
      if (suite.label === 'Notifications' && env === 'production') return null
      if (suite.label === HOSTILE_NAME && env === '(none)') return { ...NO_COUNTS, skipped: 6 }
      const offset = [0, -4, 2, -10][e]
      return statusCounts(30 + e * 7 + s * 3, Math.min(100, suite.base + offset), e % 2)
    }),
  }))
  return rateMatrix(HEATMAP_ENVIRONMENTS, rows, heatmapMeta(days, 'suite_environment', {}, true))
}

/**
 * Release columns, oldest version first, then the runs no release claims
 * (`order_release_columns`: key `unattributed`, label `(unattributed)`): the
 * releases of `RELEASES` that ran, the hostile name among them.
 */
export const HEATMAP_RELEASES: readonly (readonly [string, string])[] = [
  [RELEASE_ID.july, '2026.07'],
  [RELEASE_ID.hostile, HOSTILE_NAME],
  [RELEASE_ID.august, '2026.08'],
  [RELEASE_ID.current, '2026.09'],
  [RELEASE_ID.planned, '2026.10'],
  ['unattributed', '(unattributed)'],
]

/** `kind=suite_release`: eight suites x six release columns; Notifications has nothing in 2026.10. */
export function suiteReleaseMatrix(days: number) {
  const rows: HeatRow[] = MATRIX_SUITES.map((suite, s) => ({
    key: suiteKey(suite.label),
    label: suite.label,
    cells: HEATMAP_RELEASES.map(([id], r) => {
      if (suite.label === 'Notifications' && id === RELEASE_ID.planned) return null
      const drift = [-6, -3, 0, 2, -8, -12][r]
      return statusCounts(20 + r * 5 + s * 2, Math.max(0, Math.min(100, suite.base + drift)), r % 3 === 2 ? 1 : 0)
    }),
  }))
  return rateMatrix(HEATMAP_RELEASES, rows, heatmapMeta(days, 'suite_release', {}, true))
}

/** Suite detail's last runs as test x run columns, oldest to newest (key = run id, label = build number, which may repeat). */
export const HEATMAP_RUNS = Array.from({ length: 12 }, (_, i) => {
  const build = i === 7 ? 228 : 218 + i * 2
  return [`77777777-7777-4777-8777-${String(i).padStart(12, '0')}`, String(build)] as const
})

/** The tests of the Auth suite that failed in the window (the hostile names among them), with a status per run. */
export const HEATMAP_TESTS: { fingerprint: string; name: string; pattern: string }[] = [
  // p passed, f failed, b broken, s skipped, - not in that run
  { fingerprint: 'fp-auth-3', name: 'logout clears session', pattern: 'ppfpfffbpfff' },
  { fingerprint: 'fp-auth-2', name: 'token refresh', pattern: 'pfpfp-pfpfpf' },
  { fingerprint: 'fp-auth-1', name: 'login rejects bad password', pattern: 'pppppfpppppb' },
  { fingerprint: 'fp-auth-h', name: HOSTILE_NAME, pattern: 'ppppfpppfppp' },
  { fingerprint: 'fp-auth-c', name: 'constructor', pattern: 'sppppppbpppp' },
  { fingerprint: 'fp-auth-p', name: '__proto__', pattern: '--ppppfppppp' },
  { fingerprint: 'fp-auth-l', name: HOSTILE_LONG_NAME, pattern: 'pppppppppfpp' },
]

const RUN_STATUS: Record<string, string | null> = { p: 'passed', f: 'failed', b: 'broken', s: 'skipped', '-': null }

/**
 * `kind=test_run` (a STATUS matrix, `_cell`'s non-rate branch: one execution
 * per cell, `value` its status, `null` with n 0 when the test was not in that
 * run; no `unit`, no `counts`; `pass_rate_basis: null`). Rows: the tests with
 * the most failed + broken executions in the window, fingerprint ascending on
 * a tie (a never-failing test is not a row).
 */
export function testRunMatrix(days: number) {
  const fails = (pattern: string) => [...pattern].filter((c) => c === 'f' || c === 'b').length
  const ranked = [...HEATMAP_TESTS].sort((a, b) => fails(b.pattern) - fails(a.pattern) || byCode(a.fingerprint, b.fingerprint))
  return {
    kind: 'matrix',
    value_type: 'status',
    x_labels: HEATMAP_RUNS.map(([, label]) => label),
    y_labels: ranked.map((t) => t.name),
    x_keys: HEATMAP_RUNS.map(([key]) => key),
    y_keys: ranked.map((t) => t.fingerprint),
    cells: ranked.flatMap((t, y) =>
      [...t.pattern].map((c, x) => {
        const status = RUN_STATUS[c]
        return status === null ? { x, y, value: null, n: 0 } : { x, y, value: status, n: 1 }
      }),
    ),
    meta: heatmapMeta(days, 'test_run', {}, false),
  }
}

/** `GET /analytics/heatmap?kind=...` (refusing what the service refuses; the client never asks it). */
function heatmap(request: ApiRequest) {
  const q = request.url.searchParams
  const kind = q.get('kind')
  const days = intParam(request, 'days', 30)
  const project = q.get('project_id')
  if ((kind === 'test_run' || kind === 'suite_release') && !project) return respond(422, refusal('project_required', 'project_id'))
  if (kind === 'suite_day' && days > 90) return respond(422, refusal('window_cap', 'days'))
  if (days < 1 || days > 365) return respond(422, refusal('window_range', 'days'))
  if (kind === 'suite_day') return scopedTo(request, suiteDayMatrix(days))
  if (kind === 'suite_environment') return scopedTo(request, suiteEnvironmentMatrix(days))
  if (kind === 'suite_release') return scopedTo(request, suiteReleaseMatrix(days))
  if (kind === 'test_run') return scopedTo(request, testRunMatrix(days))
  return respond(422, refusal('kind_enum', 'kind'))
}

// ── /analytics/coverage-map (BE2, `coverage_map_service.assemble`) ─────────

/** Separates a class node id's suite key from its class key (`KEY_SEPARATOR`, U+241F). */
export const COVERAGE_KEY_SEPARATOR = '␟'
/** The class key of tests with no class (`NO_CLASS_KEY`), labelled `(ungrouped)`. */
export const NO_CLASS_KEY = '__none__'

interface MapTest {
  fingerprint: string
  name: string
  /** Executions in the window; `null`: none (idle). */
  counts: StatusCounts | null
  /** Days since its last execution anywhere in the project; `null`: never seen. */
  lastDays: number | null
  /** Ingestion created it but its runs are gone (`unknown`, not `never`). */
  lost?: boolean
  flaky?: boolean
}

interface MapClass {
  key: string
  tests: MapTest[]
}

interface MapSuite {
  label: string
  classes: MapClass[]
}

/** Tests named `names`, each run 12-20 times at about `rate` %, last run `lastDays` (or one more) ago. */
function mapTests(prefix: string, names: string[], rate: number, lastDays: number, flaky: number[] = []): MapTest[] {
  return names.map((name, i) => ({
    fingerprint: `fp-${prefix}-${i}`,
    name,
    counts: statusCounts(12 + ((i * 5) % 9), Math.max(0, Math.min(100, rate - ((i * 7) % 15))), i % 3 === 2 ? 1 : 0),
    lastDays: lastDays + (i % 2),
    flaky: flaky.includes(i),
  }))
}

const numbered = (stem: string, count: number) => Array.from({ length: count }, (_, i) => `${stem} ${i + 1}`)

/** Tests with no execution in the window and `lastDays` since their last one (seen, idle). */
const idle = (tests: MapTest[]) => tests.map((t) => ({ ...t, counts: null }))

/**
 * The project's coverage map: ten suites under the root. Checkout is the
 * largest (one class is a FILE PATH, as pytest reports it); Auth holds the
 * Suite detail page's four tests, an `(ungrouped)` class and a hostile class;
 * Payments a test that never ran; Notifications ONE test; Legacy import never
 * ran at all (`never`); Archived lost its runs (`unknown`); Search last ran 40
 * days ago (seen, not run in a 30-day window); the hostile suite; and the
 * `(none)` bucket of rows with no suite, and a suite named `__proto__`.
 */
export const COVERAGE_MAP_SUITES: MapSuite[] = [
  {
    label: 'Checkout',
    classes: [
      { key: 'CheckoutSpec', tests: mapTests('checkout-0', numbered('checkout step', 8), 97, 0, [3]) },
      { key: 'CouponSpec', tests: mapTests('checkout-1', numbered('apply coupon', 5), 92, 1, [0]) },
      { key: 'tests/checkout/test_cart.py', tests: mapTests('checkout-2', numbered('cart total', 4), 99, 0) },
    ],
  },
  {
    label: 'Auth',
    classes: [
      {
        key: 'AuthSpec',
        tests: mapTests('auth', ['login succeeds', 'login rejects bad password', 'token refresh', 'logout clears session'], 90, 0, [2]),
      },
      { key: NO_CLASS_KEY, tests: mapTests('auth-n', ['smoke: home page', 'smoke: sign-in form'], 100, 0) },
      { key: HOSTILE_NAME, tests: mapTests('auth-h', ['constructor', '__proto__', HOSTILE_LONG_NAME], 70, 2) },
    ],
  },
  {
    label: 'Payments',
    classes: [
      { key: 'PaymentsSpec', tests: mapTests('payments-0', numbered('card payment', 6), 60, 0, [1, 4]) },
      {
        key: 'RefundSpec',
        tests: [
          ...mapTests('payments-1', numbered('refund', 2), 50, 3),
          { fingerprint: 'fp-payments-1-n', name: 'refund to a closed card', counts: null, lastDays: null },
        ],
      },
    ],
  },
  { label: 'Search', classes: [{ key: 'SearchSpec', tests: idle(mapTests('search', numbered('search query', 5), 80, 40)) }] },
  { label: 'Notifications', classes: [{ key: 'NotifySpec', tests: mapTests('notify', ['sends the receipt email'], 100, 5) }] },
  {
    label: 'Legacy import',
    classes: [
      {
        key: 'ImportSpec',
        tests: numbered('legacy import', 3).map((name, i) => ({ fingerprint: `fp-legacy-${i}`, name, counts: null, lastDays: null })),
      },
    ],
  },
  {
    label: 'Archived',
    classes: [
      {
        key: 'ArchivedSpec',
        tests: numbered('archived check', 2).map((name, i) => ({
          fingerprint: `fp-archived-${i}`,
          name,
          counts: null,
          lastDays: null,
          lost: true,
        })),
      },
    ],
  },
  { label: HOSTILE_NAME, classes: [{ key: 'HostileSpec', tests: mapTests('hostile', ['constructor', '__proto__'], 75, 1) }] },
  // A suite NAMED `__proto__` (its node id `s:__proto__`, its class `constructor`): a key that must stay data.
  { label: '__proto__', classes: [{ key: 'constructor', tests: mapTests('proto', ['constructor', '__proto__'], 88, 2) }] },
  { label: '(none)', classes: [{ key: NO_CLASS_KEY, tests: mapTests('nosuite', numbered('orphan test', 2), 85, 4) }] },
]

/** `node_stats` over a set of tests: every key present; null, never 0, with nothing to measure; `seen` iff a date. */
function nodeStats(tests: MapTest[]) {
  const sum = tests.reduce<StatusCounts>(
    (acc, t) =>
      t.counts
        ? {
            passed: acc.passed + t.counts.passed,
            failed: acc.failed + t.counts.failed,
            broken: acc.broken + t.counts.broken,
            skipped: acc.skipped + t.counts.skipped,
            unknown: acc.unknown + t.counts.unknown,
          }
        : acc,
    { ...NO_COUNTS },
  )
  const seen = tests.filter((t) => t.lastDays !== null).map((t) => t.lastDays as number)
  const last = seen.length > 0 ? Math.min(...seen) : null
  const flaky = tests.filter((t) => t.flaky).length
  return {
    test_count: tests.length,
    executions: executionsOf(sum),
    pass_rate: passRateOf(sum),
    flaky_count: flaky,
    flaky_share: tests.length > 0 ? Math.round((flaky / tests.length) * 10_000) / 10_000 : null,
    // The run's created_at, 2 h before NOW's hour: whole UTC days to NOW's day = `last`.
    last_executed_at: last === null ? null : pyInstant(isoAgo(last, 2)),
    staleness_days: last,
    recency: last !== null ? 'seen' : tests.some((t) => t.lost) ? 'unknown' : 'never',
  }
}

const classLabel = (key: string) => (key === NO_CLASS_KEY ? '(ungrouped)' : key)
const suiteTests = (suite: MapSuite) => suite.classes.flatMap((c) => c.tests)

/** One node (`value` = test_count, `measure` = the pass rate, every `stats` key). */
function treeNode(id: string, parentId: string | null, label: string, tests: MapTest[]) {
  const stats = nodeStats(tests)
  return { id, parent_id: parentId, label, value: stats.test_count, measure: stats.pass_rate, stats }
}

/** Children ranked as the SQL does: most tests first, then the key by code point. */
function rankChildren<T extends { key: string; tests: MapTest[] }>(children: T[]): T[] {
  return [...children].sort((a, b) => b.tests.length - a.tests.length || byCode(a.key, b.key))
}

/**
 * One level of the map (`assemble`): the parent as the single root, then its
 * children. No children -> `nodes: []` (the contract's "no data"), never a
 * root of zero tests. Depth 3 lists a class's tests by fingerprint.
 */
export function coverageMapLevel(days: number, depth: number, suite: string | null, classKey: string | null) {
  const meta = chartMeta(days, {
    definitions: {
      coverage: 'test_execution',
      coverage_note:
        'Test-execution coverage: how many tests sit under a node and how they ran in the window. It is not code coverage.',
      grain: 'execution_row',
      depth,
      level: ({ 1: 'suite', 2: 'class', 3: 'test' } as Record<number, string>)[depth],
      timezone: 'UTC',
    },
  })
  const empty = { kind: 'tree', nodes: [], meta }
  if (depth === 1) {
    const children = rankChildren(COVERAGE_MAP_SUITES.map((s) => ({ key: suiteKey(s.label), label: s.label, tests: suiteTests(s) })))
    return {
      kind: 'tree',
      nodes: [
        treeNode('all', null, 'All suites', children.flatMap((c) => c.tests)),
        ...children.map((c) => treeNode(`s:${c.key}`, 'all', c.label, c.tests)),
      ],
      meta,
    }
  }
  const found = COVERAGE_MAP_SUITES.find((s) => suiteKey(s.label) === suite)
  if (!found || suite === null) return empty
  const rootId = `s:${suite}`
  if (depth === 2) {
    return {
      kind: 'tree',
      nodes: [
        treeNode(rootId, null, found.label, suiteTests(found)),
        ...rankChildren(found.classes).map((c) =>
          treeNode(`c:${suite}${COVERAGE_KEY_SEPARATOR}${c.key}`, rootId, classLabel(c.key), c.tests),
        ),
      ],
      meta,
    }
  }
  const cls = found.classes.find((c) => c.key === classKey)
  if (!cls || classKey === null) return empty
  const classId = `c:${suite}${COVERAGE_KEY_SEPARATOR}${classKey}`
  const tests = [...cls.tests].sort((a, b) => byCode(a.fingerprint, b.fingerprint))
  return {
    kind: 'tree',
    nodes: [treeNode(classId, null, classLabel(classKey), cls.tests), ...tests.map((t) => treeNode(`t:${t.fingerprint}`, classId, t.name, [t]))],
    meta,
  }
}

/** `GET /analytics/coverage-map?depth=1|2|3[&suite][&class_key]` (refusals as `parse_level`'s). */
function coverageMap(request: ApiRequest) {
  const q = request.url.searchParams
  if (!q.get('project_id')) return respond(422, refusal('missing_parameter', 'project_id'))
  const depth = intParam(request, 'depth', 1)
  const suite = q.get('suite')
  const classKey = q.get('class_key')
  if (![1, 2, 3].includes(depth)) return respond(422, refusal('depth_enum', 'depth'))
  if (depth === 1 && (suite !== null || classKey !== null)) return respond(422, refusal('parent_unexpected', 'suite'))
  if (depth >= 2 && suite === null) return respond(422, refusal('parent_required', 'suite'))
  if (depth === 2 && classKey !== null) return respond(422, refusal('parent_unexpected', 'class_key'))
  if (depth === 3 && classKey === null) return respond(422, refusal('parent_required', 'class_key'))
  return scopedTo(request, coverageMapLevel(intParam(request, 'days', 30), depth, suite, classKey))
}

// ── /analytics/failure-groups (BE3, `failure_groups_service.assemble`) ─────

export const NO_MESSAGE_ID = '__NO_MESSAGE__'
export const SINGLETONS_ID = '__SINGLETONS__'

interface GroupSpec {
  signature: string
  label: string
  /** `[category, failures]`, most first (`_categories`). */
  categories: [string, number][]
  tests: number
  runs: number
  distinct: number
  /** The days (before NOW) of its first and last failure in the window. */
  first: number
  last: number
  /** `[fingerprint, name, failures]`, most first. */
  top: [string, string, number][]
}

/** 160 characters, the last one an ellipsis (`_label`). */
const groupLabel = (text: string) => (text.length <= 160 ? text : `${text.slice(0, 159)}…`)

/**
 * Eight groups, largest first: an assertion, a timeout, a refused
 * connection, a group whose most frequent line is MARKUP, two whose
 * signatures are Object members (`constructor`, `__proto__`), one whose line
 * is 250 characters (cut to 160), and a small one. Plus failures with no
 * message, singletons, nothing omitted.
 */
export const FAILURE_GROUP_SPECS: GroupSpec[] = [
  {
    signature: 'assertionerror: expected # to equal #',
    label: 'AssertionError: expected 200 to equal 500',
    categories: [
      ['product_bug', 15],
      ['flaky', 6],
    ],
    tests: 6,
    runs: 9,
    distinct: 4,
    first: 27,
    last: 0,
    top: [
      ['fp-top-0', 'card declined shows reason', 7],
      ['fp-top-1', 'checkout total includes tax', 6],
      ['fp-pay-2', 'refund amount matches', 4],
    ],
  },
  {
    signature: 'timeouterror: waiting for selector "#checkout" failed: timeout #ms exceeded',
    label: 'TimeoutError: waiting for selector "#checkout" failed: timeout 30000ms exceeded',
    categories: [['infrastructure', 12]],
    tests: 4,
    runs: 6,
    distinct: 1,
    first: 20,
    last: 1,
    top: [
      ['fp-top-2', 'refund webhook retried', 5],
      ['fp-co-4', 'checkout loads', 4],
    ],
  },
  {
    signature: 'connectionrefusederror: [errno #] connection refused',
    label: 'ConnectionRefusedError: [Errno 111] Connection refused',
    categories: [
      ['infrastructure', 7],
      ['unknown', 2],
    ],
    tests: 3,
    runs: 3,
    distinct: 1,
    first: 13,
    last: 12,
    top: [
      ['fp-pay-5', 'gateway health', 4],
      ['fp-pay-6', 'gateway retry', 3],
    ],
  },
  {
    signature: HOSTILE_NAME.toLowerCase(),
    label: HOSTILE_NAME,
    categories: [['unknown', 6]],
    tests: 2,
    runs: 4,
    distinct: 1,
    first: 9,
    last: 2,
    top: [
      ['fp-auth-h', HOSTILE_NAME, 4],
      ['fp-auth-l', HOSTILE_LONG_NAME, 2],
    ],
  },
  {
    signature: 'constructor',
    label: 'constructor',
    categories: [['test_data', 4]],
    tests: 1,
    runs: 4,
    distinct: 1,
    first: 6,
    last: 3,
    top: [['fp-auth-c', 'constructor', 4]],
  },
  {
    signature: '__proto__',
    label: '__proto__',
    categories: [['automation_defect', 3]],
    tests: 1,
    runs: 3,
    distinct: 1,
    first: 4,
    last: 1,
    top: [['fp-auth-p', '__proto__', 3]],
  },
  {
    signature: HOSTILE_LONG_NAME.toLowerCase().slice(0, 80),
    label: groupLabel(HOSTILE_LONG_NAME),
    categories: [['flaky', 2]],
    tests: 2,
    runs: 2,
    distinct: 2,
    first: 15,
    last: 14,
    top: [
      ['fp-auth-l', HOSTILE_LONG_NAME, 1],
      ['fp-co-9', 'checkout regression', 1],
    ],
  },
  {
    signature: "keyerror: 'sku'",
    label: "KeyError: 'sku'",
    categories: [['product_bug', 2]],
    tests: 1,
    runs: 2,
    distinct: 1,
    first: 3,
    last: 3,
    top: [['fp-co-2', 'order line items', 2]],
  },
]

const NO_MESSAGE = { failures: 4, tests: 2, runs: 3 }
const SINGLETONS = { groups: 5, failures: 5 }

export const groupFailures = (g: GroupSpec) => g.categories.reduce((sum, [, n]) => sum + n, 0)

/** Every failing execution in scope: the groups', the ones with no message and the singletons'. */
export const FAILURE_GROUPS_TOTAL =
  FAILURE_GROUP_SPECS.reduce((sum, g) => sum + groupFailures(g), 0) + NO_MESSAGE.failures + SINGLETONS.failures

/** `trend_axis`: every UTC day of the window (the Monday of each ISO week past 90 days), oldest first. */
function trendAxis(days: number): string[] {
  if (days <= 90) return Array.from({ length: days }, (_, i) => daysAgo(days - 1 - i))
  const monday = (n: number) => {
    const d = new Date(`${daysAgo(n)}T00:00:00Z`)
    return new Date(d.getTime() - ((d.getUTCDay() + 6) % 7) * DAY_MS).toISOString().slice(0, 10)
  }
  return [...new Set(Array.from({ length: days }, (_, i) => monday(days - 1 - i)))]
}

/** A group's failures spread over the days from its last to its first failure (sums to its failure_count). */
function groupTrend(g: GroupSpec, axis: string[], weekly: boolean) {
  const counts = new Map<string, number>()
  const span = g.first - g.last + 1
  for (let j = 0; j < groupFailures(g); j++) {
    const n = g.last + (j % span)
    const d = new Date(`${daysAgo(n)}T00:00:00Z`)
    const bucket = weekly ? new Date(d.getTime() - ((d.getUTCDay() + 6) % 7) * DAY_MS).toISOString().slice(0, 10) : daysAgo(n)
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1)
  }
  return axis.map((x) => ({ x, y: counts.get(x) ?? 0 }))
}

/** Jaccard edges between the groups above (by index): weight >= 0.2, strongest first. */
const GROUP_EDGES: [number, number, number][] = [
  [0, 1, 0.5],
  [4, 5, 0.4],
  [1, 2, 0.3333],
  [0, 3, 0.25],
]

/**
 * `GET /analytics/failure-groups[?include=edges]`: `nodes` (size = failures,
 * `group` = the dominant category), `edges` only with `include=edges` (source
 * < target by code point), `groups` in rank order, the roll-ups as objects
 * (never nodes). `empty`: a scope with no failing execution
 * (`total_failures: 0`, every share `null`).
 */
export function failureGroupsBody(days: number, options: { edges?: boolean; empty?: boolean } = {}) {
  const specs = options.empty ? [] : FAILURE_GROUP_SPECS
  const total = options.empty ? 0 : FAILURE_GROUPS_TOTAL
  const share = (n: number) => (total <= 0 ? null : n / total)
  const weekly = days > 90
  const axis = trendAxis(days)
  const edges =
    options.edges && !options.empty
      ? GROUP_EDGES.map(([a, b, weight]) => {
          const [source, target] = [specs[a].signature, specs[b].signature].sort(byCode)
          return { source, target, weight }
        })
      : []
  return {
    kind: 'graph',
    nodes: specs.map((g) => ({ id: g.signature, label: g.label, size: groupFailures(g), group: g.categories[0][0] })),
    edges,
    groups: specs.map((g) => ({
      id: g.signature,
      signature: g.signature,
      label: g.label,
      distinct_raw_lines: g.distinct,
      failure_count: groupFailures(g),
      affected_tests: g.tests,
      affected_runs: g.runs,
      first_seen: pyInstant(isoAgo(g.first, 3)),
      last_seen: pyInstant(isoAgo(g.last, 1)),
      share_of_failures: share(groupFailures(g)),
      categories: g.categories.map(([category, count]) => ({ category, count })),
      dominant_category: g.categories[0][0],
      trend: groupTrend(g, axis, weekly),
      top_tests: g.top.map(([fingerprint, name, count]) => ({ fingerprint, project_id: PROJECT_ID, name, count })),
    })),
    trend_grain: weekly ? 'week' : 'day',
    total_failures: total,
    no_message: {
      id: NO_MESSAGE_ID,
      failure_count: options.empty ? 0 : NO_MESSAGE.failures,
      affected_tests: options.empty ? 0 : NO_MESSAGE.tests,
      affected_runs: options.empty ? 0 : NO_MESSAGE.runs,
      share_of_failures: share(NO_MESSAGE.failures),
    },
    singletons: {
      id: SINGLETONS_ID,
      group_count: options.empty ? 0 : SINGLETONS.groups,
      failure_count: options.empty ? 0 : SINGLETONS.failures,
      share_of_failures: share(SINGLETONS.failures),
    },
    omitted: { group_count: 0, failure_count: 0, share_of_failures: share(0) },
    meta: chartMeta(days, {
      // The groups route passes no rate basis to `build_meta`.
      pass_rate_basis: null,
      definitions: {
        grain: 'execution_row',
        population: 'FAILED and BROKEN executions in scope; passed, skipped and unknown are not counted',
        label: 'the most frequent raw first line of the group, <= 160 characters, untrusted text',
      },
    }),
  }
}

function failureGroups(request: ApiRequest) {
  const include = request.url.searchParams.getAll('include')
  if (include.some((token) => token !== 'edges')) return respond(422, refusal('include_enum', 'include'))
  return scopedTo(request, failureGroupsBody(intParam(request, 'days', 30), { edges: include.includes('edges') }))
}

// ── /analytics/systemic-clusters (`routers/analytics.systemic_clusters`) ───

const SYSTEMIC_CLUSTER_SPECS: [string, string, string, string, number, number, [string, string, number][]][] = [
  [
    'sfc_001',
    '46b7fe3fb233da6b97021fc6646e24b3',
    'Payment gateway timeouts',
    'external_dependency',
    0.82,
    7,
    [
      ['fp-pay-5', 'gateway health', 7],
      ['fp-pay-6', 'gateway retry', 6],
      ['fp-top-2', 'refund webhook retried', 6],
      ['fp-top-0', 'card declined shows reason', 5],
    ],
  ],
  [
    'sfc_002',
    '9a0c3f1e2d4b5a69788766554433aa01',
    HOSTILE_NAME,
    'unknown',
    0.64,
    4,
    [
      ['fp-auth-h', HOSTILE_NAME, 4],
      ['fp-auth-c', 'constructor', 4],
      ['fp-auth-p', '__proto__', 3],
    ],
  ],
  [
    'sfc_003',
    '0f1e2d3c4b5a69788796a5b4c3d2e1f0',
    'Search index warm-up',
    'environment',
    0.55,
    3,
    [
      ['fp-search-0', 'search query 1', 3],
      ['fp-search-1', 'search query 2', 3],
    ],
  ],
]

/** Three clusters, largest first, keyed by `membership_key` (0193); one is hostile in its label and members. */
export const SYSTEMIC_CLUSTERS = SYSTEMIC_CLUSTER_SPECS.map(([clusterKey, membershipKey, label, cause, cohesion, runs, members]) => ({
  cluster_key: clusterKey,
  membership_key: membershipKey,
  label,
  cause_family: cause,
  size: members.length,
  cohesion,
  co_failure_runs: runs,
  window_days: 60,
  computed_at: pyInstant(isoAgo(0, 5)),
  members: members.map(([fingerprint, name, failureRuns]) => ({
    test_fingerprint: fingerprint,
    test_name: name,
    failure_runs: failureRuns,
  })),
}))

export const CLUSTERS_EMPTY_IS_NORMAL =
  'Most projects have no systemic clusters. An empty list means no group of tests met the co-failure cohesion bar, not that clustering failed.'

/** `{items, total, empty_is_normal, meta}`; `scope` only when a release or suite filter applied. */
export function systemicClustersBody(days: number, options: { empty?: boolean } = {}) {
  const items = options.empty ? [] : SYSTEMIC_CLUSTERS
  return { items, total: items.length, empty_is_normal: CLUSTERS_EMPTY_IS_NORMAL, meta: chartMeta(days, { pass_rate_basis: null }) }
}

function systemicClusters(request: ApiRequest) {
  if (!request.url.searchParams.get('project_id')) return respond(422, refusal('missing_parameter', 'project_id'))
  return scopedTo(request, systemicClustersBody(intParam(request, 'days', 30)))
}

// ── /analytics/test-scatter (BE4, `test_scatter_service.build_test_scatter`) ─

export interface ScatterTest {
  id: string
  label: string
  x: number
  y: number
  size: number
  n: number
}

/** Python's `statistics.median` (the mean of the two middle values for an even count). */
function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/**
 * `count` tests, a pure function of the index: p95 from 1 ms (the log floor,
 * the first test) to ~10 s on a log spread, executions 5..124, failure rate
 * over the EVALUATED executions (two tests in three never fail, so the median
 * rate is 0%), the hostile names at fixed indexes.
 */
function scatterTests(prefix: string, count: number, salt: number): ScatterTest[] {
  const named: Record<number, string> = { 3: HOSTILE_NAME, 5: 'constructor', 7: '__proto__', 9: HOSTILE_LONG_NAME }
  return Array.from({ length: count }, (_, i) => {
    const size = 5 + ((i * 13 + salt) % 120)
    const n = Math.max(1, size - (i % 4))
    const bad = (i * 7 + salt) % 3 === 0 ? (i * 17 + salt) % n : 0
    return {
      id: `fp-${prefix}-p${i}`,
      label: named[i] ?? `${prefix} test ${i + 1}`,
      x: i === 0 ? 1 : round2(10 ** (1 + ((i * 37 + salt) % 300) / 100)),
      y: round2((bad / n) * 100),
      size,
      n,
    }
  })
}

/** The Suite detail scatter (the Auth suite) and the project-wide one (Failures). */
export const SCATTER_SUITE_TESTS = scatterTests('auth', 40, 3)
export const SCATTER_PROJECT_TESTS = scatterTests('project', 90, 11)
/** Tests the server could not place, by reason (always sent, zeros included). */
export const SCATTER_EXCLUDED = { below_min_executions: 3, no_duration: 1, no_evaluated: 2 }

/** `{kind: points, x, y, size, points, medians?, excluded, meta}`; `medians` absent with no point (never 0, 0). */
export function testScatterBody(days: number, tests: ScatterTest[], options: { allExcluded?: boolean } = {}) {
  const points = options.allExcluded ? [] : tests
  return {
    kind: 'points',
    x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
    y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
    size: { key: 'executions', label: 'Executions' },
    points,
    ...(points.length > 0 ? { medians: { x: round2(median(points.map((p) => p.x))), y: round2(median(points.map((p) => p.y))) } } : {}),
    excluded: options.allExcluded ? { below_min_executions: 7, no_duration: 2, no_evaluated: 1 } : { ...SCATTER_EXCLUDED },
    meta: chartMeta(days, {
      definitions: {
        grain: 'execution_row',
        point: 'One test (its fingerprint) with at least min_executions executions in scope.',
        x: "p95 of duration_ms (percentile_cont) over the test's executions that carry a duration, on a log axis.",
      },
    }),
  }
}

function testScatter(request: ApiRequest) {
  const q = request.url.searchParams
  if (!q.get('project_id')) return respond(422, refusal('missing_parameter', 'project_id'))
  const order = q.get('order') ?? 'failures'
  if (order !== 'failures' && order !== 'volume') return respond(422, refusal('order_enum', 'order'))
  const tests = q.getAll('suite_name').length > 0 ? SCATTER_SUITE_TESTS : SCATTER_PROJECT_TESTS
  return scopedTo(request, testScatterBody(intParam(request, 'days', 30), tests))
}

// ── chart-data for the Failures drill ladder (VIZ-602, FK5) ────────────────

/** The ladder's suites and their executions by status over the window (keys lower-cased, labels as ingested). */
export const LADDER_SUITES: { label: string; counts: StatusCounts }[] = [
  { label: 'Payments', counts: { passed: 240, failed: 150, broken: 40, skipped: 14, unknown: 0 } },
  { label: 'Search', counts: { passed: 300, failed: 70, broken: 26, skipped: 4, unknown: 0 } },
  { label: 'Auth', counts: { passed: 610, failed: 30, broken: 8, skipped: 12, unknown: 0 } },
  { label: 'Checkout', counts: { passed: 820, failed: 16, broken: 5, skipped: 9, unknown: 0 } },
  { label: HOSTILE_NAME, counts: { passed: 40, failed: 6, broken: 0, skipped: 0, unknown: 1 } },
  { label: 'Notifications', counts: { passed: 150, failed: 0, broken: 0, skipped: 2, unknown: 0 } },
]
export const LADDER_STATUSES = ['passed', 'failed', 'broken', 'skipped', 'unknown'] as const
type LadderStatus = (typeof LADDER_STATUSES)[number]
const isLadderStatus = (value: string): value is LadderStatus => (LADDER_STATUSES as readonly string[]).includes(value)

const categoryPoint = (x: string, y: number) => ({ x, y, n: y, measured: true, reason: null })

/** A category chart-data body (`x_type: category`); `x_labels` maps a key to what the axis shows. */
function categoryBody(
  days: number,
  dimensions: string[],
  series: { key: string; label: string; points: unknown[] }[],
  xLabels?: Record<string, string>,
) {
  return { kind: 'series', dimensions, x_type: 'category', series, ...(xLabels ? { x_labels: xLabels } : {}), meta: chartMeta(days) }
}

/** The tests behind one (suite, status) leaf: six per pair, the hostile names among them. */
export function ladderTests(suite: string, status: string): { fingerprint: string; name: string; count: number }[] {
  const found = LADDER_SUITES.find((x) => suiteKey(x.label) === suite)
  const total = found && isLadderStatus(status) ? found.counts[status] : 0
  if (!found || total === 0) return []
  const names = [`${found.label} ${status} check`, HOSTILE_NAME, 'constructor', '__proto__', HOSTILE_LONG_NAME, `${found.label} other check`]
  const shares = [0.4, 0.2, 0.15, 0.1, 0.1, 0.05]
  const slug = suite.replace(/[^a-z0-9]/g, '') || 'x'
  return names.map((name, i) => ({ fingerprint: `fp-${slug}-${status}-${i}`, name, count: Math.max(1, Math.floor(total * shares[i])) }))
}

/**
 * The ladder's chart-data reads (FK5.md request 3): L0 executions by suite x
 * status; one suite's statuses; one status's suites; a (suite, status) leaf's
 * tests (`top_n`). `null` when the request is none of these.
 */
function ladderChartData(request: ApiRequest) {
  const q = request.url.searchParams
  const days = intParam(request, 'days', 30)
  const metric = q.get('metric') ?? ''
  const groupBy = q.getAll('group_by').join(',')
  const suites = q.getAll('suite_name').map((name) => name.trim().toLowerCase())
  const inScope = LADDER_SUITES.filter((s) => suites.length === 0 || suites.includes(suiteKey(s.label)))
  const labels = Object.fromEntries(inScope.map((s) => [suiteKey(s.label), s.label]))
  if (metric === 'executions' && groupBy === 'suite,status') {
    const series = LADDER_STATUSES.map((status) => ({
      key: status,
      label: status,
      points: inScope.map((s) => categoryPoint(suiteKey(s.label), s.counts[status])),
    }))
    return scopedTo(request, categoryBody(days, ['suite', 'status'], series, labels))
  }
  if (metric === 'executions' && groupBy === 'status') {
    const sum = (status: LadderStatus) => inScope.reduce((total, s) => total + s.counts[status], 0)
    const series = [{ key: 'value', label: 'executions', points: LADDER_STATUSES.map((st) => categoryPoint(st, sum(st))) }]
    return scopedTo(request, categoryBody(days, ['status'], series))
  }
  if (isLadderStatus(metric) && groupBy === 'suite') {
    const series = [{ key: 'value', label: metric, points: inScope.map((s) => categoryPoint(suiteKey(s.label), s.counts[metric])) }]
    return scopedTo(request, categoryBody(days, ['suite'], series, labels))
  }
  if (isLadderStatus(metric) && groupBy === 'test' && suites.length === 1) {
    const tests = ladderTests(suites[0], metric)
    const series = [{ key: 'value', label: metric, points: tests.map((t) => categoryPoint(t.fingerprint, t.count)) }]
    return scopedTo(request, categoryBody(days, ['test'], series, Object.fromEntries(tests.map((t) => [t.fingerprint, t.name]))))
  }
  return null
}

// ── /analytics/chart-data/rows (BE4, `chart_rows_service.assemble_page`) ───

/** `RECONCILIATION`: which mark field the rows add up to, by the chart's metric. */
const ROWS_RECONCILIATION: Record<string, [string, string]> = {
  executions: ['y', 'rows'],
  passed: ['y', 'rows'],
  failed: ['y', 'rows'],
  broken: ['y', 'rows'],
  skipped: ['y', 'rows'],
  unknown: ['y', 'rows'],
  failures: ['y', 'rows'],
  pass_rate: ['n', 'rows'],
  failure_rate: ['n', 'rows'],
}

/** The rows a metric counts among some executions (`ROW_PREDICATES`). */
function rowsOf(metric: string, c: StatusCounts): number {
  if (metric === 'executions') return executionsOf(c)
  if (metric === 'failures') return c.failed + c.broken
  if (metric === 'pass_rate' || metric === 'failure_rate') return evaluatedOf(c)
  return isLadderStatus(metric) ? c[metric] : 0
}

interface FixtureCell {
  x: number
  y: number
  n: number
  counts?: StatusCounts
}

/** The cell of a matrix whose row and column keys are these, or `null`. */
function matrixCell(matrix: { x_keys: readonly string[]; y_keys: readonly string[]; cells: FixtureCell[] }, row: string, column: string) {
  const y = matrix.y_keys.indexOf(row)
  const x = matrix.x_keys.indexOf(column)
  return matrix.cells.find((cell) => cell.x === x && cell.y === y) ?? null
}

/**
 * How many executions a selection holds, from the SAME fixtures the charts
 * drew, so a panel opened from a mark reconciles with it (a heatmap cell's
 * `n`, a scatter point's `n`, a group's failures, a ladder bar's `y`).
 */
export function rowsTotal(metric: string, sel: Record<string, string>, days: number): number {
  const keys = Object.keys(sel).sort().join(',')
  const fromCell = (cell: FixtureCell | null) => (cell === null ? 0 : cell.counts ? rowsOf(metric, cell.counts) : cell.n)
  if (keys === 'day,suite') return fromCell(matrixCell(suiteDayMatrix(Math.min(days, 90)), sel.suite, sel.day))
  if (keys === 'environment,suite') return fromCell(matrixCell(suiteEnvironmentMatrix(days), sel.suite, sel.environment))
  if (keys === 'release,suite') return fromCell(matrixCell(suiteReleaseMatrix(days), sel.suite, sel.release))
  if (keys === 'error_signature') {
    const group = FAILURE_GROUP_SPECS.find((g) => g.signature === sel.error_signature)
    return group ? groupFailures(group) : 0
  }
  if (keys === 'test') {
    const point = [...SCATTER_SUITE_TESTS, ...SCATTER_PROJECT_TESTS].find((p) => p.id === sel.test)
    if (point) return metric === 'executions' ? point.size : point.n
    const row = HEATMAP_TESTS.find((t) => t.fingerprint === sel.test)
    if (row) return [...row.pattern].filter((c) => c !== '-').length
    const covered = COVERAGE_MAP_SUITES.flatMap(suiteTests).find((t) => t.fingerprint === sel.test)
    return covered?.counts ? rowsOf(metric, covered.counts) : 0
  }
  const ladder = LADDER_SUITES.find((s) => suiteKey(s.label) === sel.suite)
  if (keys === 'suite') return ladder ? rowsOf(metric, ladder.counts) : 0
  if (keys === 'status') return isLadderStatus(sel.status) ? LADDER_SUITES.reduce((sum, s) => sum + s.counts[sel.status as LadderStatus], 0) : 0
  if (keys === 'status,suite') return ladder && isLadderStatus(sel.status) ? ladder.counts[sel.status] : 0
  if (keys === 'status,test') {
    for (const s of LADDER_SUITES) {
      const test = ladderTests(suiteKey(s.label), sel.status).find((t) => t.fingerprint === sel.test)
      if (test) return test.count
    }
  }
  return 0
}

/** The statuses a metric's rows can have, in a repeating order. */
function rowStatuses(metric: string, sel: Record<string, string>): string[] {
  if (sel.status) return [sel.status]
  if (metric === 'failures') return ['failed', 'broken']
  if (metric === 'pass_rate' || metric === 'failure_rate') return ['passed', 'failed', 'passed', 'broken']
  if (isLadderStatus(metric)) return [metric]
  return ['passed', 'failed', 'skipped', 'passed', 'broken']
}

const ROW_NAMES = ['checkout total includes tax', HOSTILE_NAME, 'constructor', '__proto__', HOSTILE_LONG_NAME, 'card declined shows reason']

/** A fingerprint's test name, from whichever fixture drew it (the heatmap, the scatter, the map, the ladder). */
function testNameOf(fingerprint: string): string {
  const named =
    HEATMAP_TESTS.find((t) => t.fingerprint === fingerprint)?.name ??
    [...SCATTER_SUITE_TESTS, ...SCATTER_PROJECT_TESTS].find((p) => p.id === fingerprint)?.label ??
    COVERAGE_MAP_SUITES.flatMap(suiteTests).find((t) => t.fingerprint === fingerprint)?.name ??
    LADDER_SUITES.flatMap((s) => LADDER_STATUSES.flatMap((st) => ladderTests(suiteKey(s.label), st))).find(
      (t) => t.fingerprint === fingerprint,
    )?.name
  return named ?? fingerprint
}

/** One page of rows, newest first (`row_item`); every name channel carries the hostile names. */
export function chartRowsPage(
  metric: string,
  sel: Record<string, string>,
  days: number,
  page: number,
  size: number,
  /** The request's `suite_name` scope, when it had one (every row is then in that suite). */
  scopeSuite?: string,
) {
  const total = rowsTotal(metric, sel, days)
  const statuses = rowStatuses(metric, sel)
  const start = (page - 1) * size
  const count = Math.max(0, Math.min(size, total - start))
  const [field, measure] = ROWS_RECONCILIATION[metric] ?? ['y', 'rows']
  const suite = sel.suite ?? (scopeSuite === undefined ? undefined : suiteKey(scopeSuite))
  const suiteLabel =
    suite === undefined ? null : ([...MATRIX_SUITES, ...LADDER_SUITES].find((s) => suiteKey(s.label) === suite)?.label ?? suite)
  const testName = sel.test === undefined ? null : testNameOf(sel.test)
  const items = Array.from({ length: count }, (_, k) => {
    const i = start + k
    const status = statuses[i % statuses.length]
    const failing = status === 'failed' || status === 'broken'
    const release = RELEASES[i % 3]
    return {
      id: `88888888-8888-4888-8888-${String(i).padStart(12, '0')}`,
      test_name: testName ?? ROW_NAMES[i % ROW_NAMES.length],
      test_fingerprint: sel.test ?? `fp-row-${i % ROW_NAMES.length}`,
      suite: suiteLabel ?? (i % 4 === 3 ? null : 'Payments'),
      status,
      duration_ms: i % 5 === 4 ? null : 800 + i * 37,
      run_id: `55555555-5555-4555-8555-${String(100000000000 + (i % 8))}`,
      release: i % 3 === 2 ? null : { id: release.id, name: release.name },
      created_at: pyInstant(isoAgo(Math.floor(i / 4), 2)),
      failure_category: failing ? (i % 2 === 0 ? 'product_bug' : 'infrastructure') : null,
      error_line: failing ? (i % 3 === 0 ? HOSTILE_NAME : `AssertionError: expected ${200 + i} to equal 500`) : null,
    }
  })
  return {
    items,
    total,
    page,
    size,
    pages: Math.ceil(total / size),
    reconciliation: { mark_field: field, measure, value: total },
    meta: chartMeta(days, {
      definitions: {
        rows: 'the executions behind the mark, newest first',
        chart_grain: 'execution_row',
        order: 'test_runs.created_at DESC, test_cases.id DESC',
      },
    }),
  }
}

/** The dimensions a rows request may select by (`ROWS_DIMENSIONS`). */
const ROWS_DIMENSIONS = [
  'day',
  'week',
  'project',
  'release',
  'suite',
  'status',
  'failure_category',
  'branch',
  'environment',
  'ingestion_source',
  'test',
  'error_signature',
]

/** `GET /analytics/chart-data/rows`: each selector must be one of the chart's own dimensions (`parse_rows_request`). */
function chartRows(request: ApiRequest) {
  const q = request.url.searchParams
  const metric = q.get('metric') ?? ''
  const groupBy = q.getAll('group_by')
  if (!(metric in ROWS_RECONCILIATION)) return respond(422, refusal('metric_enum', 'metric'))
  if (groupBy.length === 0) return respond(422, refusal('missing_parameter', 'group_by'))
  if (groupBy.length > 2) return respond(422, refusal('group_by_cap', 'group_by'))
  if (groupBy.some((d) => !ROWS_DIMENSIONS.includes(d))) return respond(422, refusal('dimension_enum', 'group_by'))
  const sel: Record<string, string> = {}
  for (const [name, value] of q.entries()) {
    if (!name.startsWith('bucket_')) continue
    const dimension = name.slice('bucket_'.length)
    if (!groupBy.includes(dimension)) return respond(422, refusal('selector_not_in_group_by', name))
    if (value === '' || value === '__other__') return respond(422, refusal('bucket_value', name))
    sel[dimension] = value
  }
  if (Object.keys(sel).length === 0) return respond(422, refusal('missing_parameter', 'bucket'))
  const page = intParam(request, 'page', 1)
  const size = intParam(request, 'size', 50)
  if (page < 1) return respond(422, refusal('page_range', 'page'))
  if (size < 1 || size > 200) return respond(422, refusal('size_range', 'size'))
  const scopeSuite = q.getAll('suite_name')
  return scopedTo(request, chartRowsPage(metric, sel, intParam(request, 'days', 30), page, size, scopeSuite.length === 1 ? scopeSuite[0] : undefined))
}

/** Every Wave 3 read, answered from the fixtures above. */
export const WAVE3: ApiHandlers = [
  [HEATMAP_PATH, heatmap],
  [COVERAGE_MAP_PATH, coverageMap],
  [FAILURE_GROUPS_PATH, failureGroups],
  [SYSTEMIC_CLUSTERS_PATH, systemicClusters],
  [TEST_SCATTER_PATH, testScatter],
  [CHART_ROWS_PATH, chartRows],
]

/** /coverage with the catalogue: + the coverage map and the environment / release heatmaps (and their rows). */
export const COVERAGE_ON: ApiHandlers = [...RELEASES_LIST, ...WAVE3, ...COVERAGE]

/** /failures with the catalogue: + failure groups, clusters, the ladder's chart-data, the project scatter, rows. */
export const FAILURES_ON: ApiHandlers = [...RELEASES_LIST, ...CHART_DATA, ...WAVE3, ...FAILURES]

/** A request with only a query (and the project), to build a fixture body outside a route. */
function fixtureRequest(query: string): ApiRequest {
  const url = new URL(`http://127.0.0.1/fixture?${query}&project_id=${PROJECT_ID}`)
  return { url, path: url.pathname, method: 'GET', route: undefined as never }
}

export interface FixtureBody {
  name: string
  body: Record<string, unknown>
  /** `chart`: C3 + C2 (the catalogue's sources); `rows` / `clusters`: C2 `meta` beside the route's own list. */
  kind: 'chart' | 'rows' | 'clusters'
}

/** Every body the Wave 3 specs draw, built by the handlers' own builders, for the contract check. */
export function wave3Bodies(): FixtureBody[] {
  const chart = (name: string, body: unknown): FixtureBody => ({ name, body: body as Record<string, unknown>, kind: 'chart' })
  const ladder = (query: string) => ladderChartData(fixtureRequest(query))
  return [
    chart('heatmap suite_day 14 d', suiteDayMatrix(14)),
    chart('heatmap suite_day 30 d', suiteDayMatrix(30)),
    chart('heatmap suite_day 90 d', suiteDayMatrix(90)),
    chart('heatmap suite_environment', suiteEnvironmentMatrix(30)),
    chart('heatmap suite_release', suiteReleaseMatrix(30)),
    chart('heatmap test_run', testRunMatrix(30)),
    chart('coverage-map depth 1', coverageMapLevel(30, 1, null, null)),
    ...COVERAGE_MAP_SUITES.map((s) => chart(`coverage-map depth 2 ${s.label}`, coverageMapLevel(30, 2, suiteKey(s.label), null))),
    ...COVERAGE_MAP_SUITES.flatMap((s) =>
      s.classes.map((c) => chart(`coverage-map depth 3 ${s.label} / ${c.key}`, coverageMapLevel(30, 3, suiteKey(s.label), c.key))),
    ),
    chart('coverage-map unknown suite (no children)', coverageMapLevel(30, 2, 'no such suite', null)),
    chart('failure-groups', failureGroupsBody(30)),
    chart('failure-groups include=edges', failureGroupsBody(30, { edges: true })),
    chart('failure-groups 120 d (weekly trend)', failureGroupsBody(120, { edges: true })),
    chart('failure-groups empty', failureGroupsBody(30, { empty: true })),
    chart('test-scatter suite', testScatterBody(30, SCATTER_SUITE_TESTS)),
    chart('test-scatter project', testScatterBody(30, SCATTER_PROJECT_TESTS)),
    chart('test-scatter all excluded', testScatterBody(30, SCATTER_SUITE_TESTS, { allExcluded: true })),
    chart('ladder L0', ladder('metric=executions&group_by=suite&group_by=status&days=30')),
    chart('ladder suite', ladder('metric=executions&group_by=status&suite_name=payments&days=30')),
    chart('ladder status', ladder('metric=failed&group_by=suite&days=30')),
    chart('ladder leaf', ladder('metric=failed&group_by=test&top_n=20&suite_name=payments&days=30')),
    chart('chart-data suites (wire)', onTheWire(suiteSeries(14))),
    chart('chart-data p95 (wire)', onTheWire(durationSeries(14, 'duration_p95'))),
    chart('chart-data releases (wire)', onTheWire(releaseSeries(90, [RELEASE_ID.current, RELEASE_ID.hostile]))),
    { name: 'systemic-clusters', body: systemicClustersBody(30), kind: 'clusters' },
    { name: 'systemic-clusters empty', body: systemicClustersBody(30, { empty: true }), kind: 'clusters' },
    { name: 'rows heatmap cell', body: chartRowsPage('executions', { suite: 'payments', day: daysAgo(1) }, 14, 1, 50), kind: 'rows' },
    { name: 'rows group', body: chartRowsPage('failures', { error_signature: FAILURE_GROUP_SPECS[0].signature }, 30, 1, 50), kind: 'rows' },
    { name: 'rows point', body: chartRowsPage('failure_rate', { test: SCATTER_SUITE_TESTS[3].id }, 30, 1, 50), kind: 'rows' },
    { name: 'rows page 2', body: chartRowsPage('executions', { suite: 'auth' }, 30, 2, 50), kind: 'rows' },
    { name: 'rows empty', body: chartRowsPage('executions', { suite: 'no such suite', day: daysAgo(1) }, 14, 1, 50), kind: 'rows' },
  ]
}

/**
 * /trends with the catalogue: + chart-data (suites, p50, p95); the probe is a
 * `/runs` read. Wave 3: the suite x day heatmap asks
 * `/analytics/heatmap?kind=suite_day` itself (and a cell's rows).
 */
export const TRENDS_ON: ApiHandlers = [...RELEASES_LIST, ...CHART_DATA, ...WAVE3, ...TRENDS]

/**
 * /coverage/suite with the catalogue: no new request (the overlays read the
 * page's points). Wave 3: the test x run heatmap, the scatter, their rows,
 * and the sections' unfiltered "ever had a run?" probe (`/runs?page=1&size=1`).
 */
export const SUITE_DETAIL_ON: ApiHandlers = [...RELEASES_LIST, ...WAVE3, ...RUNS, ...SUITE_DETAIL]

/** The two Object members the hostile fixtures use as names (`constructor`, `__proto__`). */
export const OBJECT_MEMBER_NAMES: readonly string[] = ['constructor', '__proto__']

/**
 * `handlers` with every matrix row and tree node NAMED like an Object member
 * left out of the answer. For the specs that test something other than
 * hostile names (axe, the visual regions): a canvas label `__proto__`
 * pollutes `Object.prototype` through zrender's text cache (B0.md), and a
 * polluted page breaks axe itself; the hostile specs keep the names and
 * assert the pollution away.
 */
export function withoutObjectMemberLabels(handlers: ApiHandlers): ApiHandlers {
  const drop = (label: unknown) => typeof label === 'string' && OBJECT_MEMBER_NAMES.includes(label)
  const clean = (answer: unknown): unknown => {
    if (!answer || typeof answer !== 'object' || Array.isArray(answer)) return answer
    const body = answer as Record<string, unknown>
    if (body.kind === 'matrix' && Array.isArray(body.y_labels)) {
      const labels = body.y_labels as string[]
      const keep = labels.map((label, y) => (drop(label) ? -1 : y)).filter((y) => y >= 0)
      const index = new Map(keep.map((y, i) => [y, i]))
      const cells = (body.cells as { y: number }[]).filter((c) => index.has(c.y)).map((c) => ({ ...c, y: index.get(c.y) }))
      return {
        ...body,
        y_labels: keep.map((y) => labels[y]),
        y_keys: keep.map((y) => (body.y_keys as string[])[y]),
        cells,
      }
    }
    if (body.kind === 'tree' && Array.isArray(body.nodes)) {
      const nodes = body.nodes as { parent_id: string | null; label: string }[]
      return { ...body, nodes: nodes.filter((node) => node.parent_id === null || !drop(node.label)) }
    }
    return answer
  }
  return handlers.map(([matcher, handler, method]) => {
    const wrapped: ApiHandler = (request) => {
      const answer = handler(request)
      return answer instanceof Promise ? answer.then(clean) : clean(answer)
    }
    return method ? ([matcher, wrapped, method] as const) : ([matcher, wrapped] as const)
  })
}
