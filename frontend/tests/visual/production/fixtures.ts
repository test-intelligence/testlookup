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
import { respond, type ApiHandlers, type ApiRequest } from '../../lib/production-pages'

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

export const SUITE_DETAIL: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/analytics/suite-detail', suiteDetail],
  [/^\/api\/v1\/test-management\/suites\/[^/]+\/trend$/, suiteTrend],
  [
    '/api/v1/suites',
    () =>
      page([
        {
          id: '66666666-6666-4666-8666-666666666666',
          project_id: PROJECT_ID,
          name: SUITE,
          description: null,
          created_at: '2026-02-01T00:00:00Z',
        },
      ]),
  ],
]

// ── Value metrics (/value-metrics) ─────────────────────────────────────────

export const VALUE_METRICS: ApiHandlers = [...LAYOUT, ['/api/v1/value-metrics', valueMetrics]]

// ── Runs (/runs) ───────────────────────────────────────────────────────────

export const RUNS_PAGE: ApiHandlers = [...LAYOUT, ...RUNS]

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

const CLUSTERED_DECISION = { ...FLOORED_DECISION, cluster_insights: GATE_CLUSTERS }

/** `/release-gate/<run>` answered with the clustered decision. */
export const RELEASE_GATE_CLUSTERED: ApiHandlers = [
  [/^\/api\/v1\/release-readiness\/[^/]+$/, () => CLUSTERED_DECISION],
  ...RELEASE_GATE,
]

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

/** `/reports/summary`: the report is the page's only page-owned request. */
export const SUMMARY_REPORT: ApiHandlers = [...LAYOUT, ['/api/v1/reports/summary', summaryReport]]

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
function suiteSeries(days: number) {
  return {
    meta: chartMeta(days, {
      truncated: true,
      truncated_total: 11,
      truncated_axes: { series: { dimension: 'suite', kept: 7, total: 11 } },
    }),
    series: {
      kind: 'series',
      dimensions: ['day', 'suite'],
      x_type: 'time',
      series: SERIES_SUITES.map((suite) => ({
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

/** Every `chart-data` request the catalogue makes, answered by its parameters. */
function chartData(request: ApiRequest, releaseRate?: number) {
  const q = request.url.searchParams
  const days = intParam(request, 'days', 30)
  const metric = q.get('metric')
  const groupBy = q.getAll('group_by').join(',')
  if (metric === 'pass_rate' && groupBy === 'day,suite') return suiteSeries(days)
  if ((metric === 'duration_p50' || metric === 'duration_p95') && groupBy === 'day') return durationSeries(days, metric)
  if (metric === 'pass_rate' && groupBy === 'day,release') return releaseSeries(days, q.getAll('release_id'), releaseRate)
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

/** Flags the flag-on specs turn on (`MockOptions.flags`). */
export const CATALOGUE_ON = { viz_chart_data_api: true } as const
export const HEATMAP_ON = { viz_chart_data_api: true, viz_advanced_charts: true } as const

/** /overview with the catalogue: + top failing (Failure categories draws the page's own read). */
export const OVERVIEW_ON: ApiHandlers = [...RELEASES_LIST, ...TOP_FAILING_HANDLER, ...OVERVIEW]

/** /trends with the catalogue: + chart-data (suites, p50, p95); the probe is a `/runs` read. */
export const TRENDS_ON: ApiHandlers = [...RELEASES_LIST, ...CHART_DATA, ...TRENDS]

/** /reports/summary with the catalogue: + the trend (`/metrics/trends`). */
export const SUMMARY_REPORT_ON: ApiHandlers = [...RELEASES_LIST, ...SUMMARY_REPORT, ['/api/v1/metrics/trends', trends]]

/** /coverage/suite with the catalogue: no new request (the overlays read the page's points). */
export const SUITE_DETAIL_ON: ApiHandlers = [...RELEASES_LIST, ...SUITE_DETAIL]

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
