/**
 * Coverage-map levels (VIZ-502) for the chart gallery: C3 `tree` bodies in the
 * shape `/api/v1/analytics/coverage-map` answers (BE2: one level per request,
 * the parent as the one root, its children largest first, every node with all
 * eight `stats` keys, `value` = test count, `measure` = pass rate), each with
 * the level it answers.
 *
 * Deterministic literals: no clock, no `Math.random`. Ids carry the prefixes
 * the endpoint issues (`all`, `s:`, `c:<suite>␟<class>`, `other:`), never a
 * leading `#`.
 */
import type { EnvelopeMeta, TreeChart, TreeNode, TreeNodeStats } from '@/lib/viz/contracts'
import type { ChartResponse } from '../chartStateCore'
import { CLASS_KEY_SEPARATOR, NO_CLASS_KEY, NO_CLASS_LABEL, ROOT_ID, TOP_LEVEL, type CoverageLevel } from '../coverageMap.model'
import { HEATMAP_FRAME_LONG_NAME } from './heatmapFrame'

/**
 * A name that executes if anything renders it as HTML. The gallery's own copy
 * is `HOSTILE_LABEL` (`pages/dev/chartGalleryFixtures`, which may not import
 * app code); a unit test holds the two equal.
 */
export const COVERAGE_HOSTILE_NAME = '<img src=x onerror="window.__xss=1">'

/** A ~250-character suite name, its two ends distinct: the heatmap frame's own, so the specs name one string. */
export const COVERAGE_LONG_NAME = HEATMAP_FRAME_LONG_NAME

/** One level of the map and the request it answers. */
export interface CoverageMapFixture {
  response: ChartResponse<TreeChart>
  level: CoverageLevel
}

const meta = (over: Partial<EnvelopeMeta> = {}): EnvelopeMeta =>
  ({
    schema_version: 1,
    scope: {
      projects: [{ id: '11111111-1111-4111-8111-111111111111', name: 'Checkout' }],
      releases: [],
      suites: [],
      window: { from: '2026-09-01', to: '2026-09-30', days: 30, timezone: 'UTC' },
    },
    totals: { matched_runs: 60, total_runs: 60, matched_executions: 9440, total_executions: 9440 },
    pass_rate_basis: 'executions',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: null,
    generated_at: '2026-09-30T09:00:00Z',
    as_of: '2026-09-30T09:00:00Z',
    // The endpoint's definitions: the footer says what the numbers were counted in.
    definitions: { grain: 'execution_row', coverage: 'test_execution' },
    ...over,
  }) as EnvelopeMeta

/** Run in the window: a pass rate, executions, flaky tests, a last run `staleness` days before 2026-09-30. */
function seen(tests: number, passRate: number | null, executions: number, flaky: number, lastDay: string, staleness: number): TreeNodeStats {
  return {
    test_count: tests,
    executions,
    pass_rate: passRate,
    flaky_count: flaky,
    flaky_share: tests > 0 ? flaky / tests : null,
    last_executed_at: `${lastDay}T08:00:00Z`,
    staleness_days: staleness,
    recency: 'seen',
  }
}

/** Not run in this window: seen before it, so a last run and a staleness, but no pass rate. */
const idle = (tests: number, lastDay: string, staleness: number): TreeNodeStats => seen(tests, null, 0, 0, lastDay, staleness)

/** Its last run's record was deleted: we do not know when it ran, which is not "never". */
const lost = (tests: number): TreeNodeStats => ({
  test_count: tests,
  executions: 0,
  pass_rate: null,
  flaky_count: 0,
  flaky_share: 0,
  last_executed_at: null,
  staleness_days: null,
  recency: 'unknown',
})

const never = (tests: number): TreeNodeStats => ({ ...lost(tests), recency: 'never' })

const node = (id: string, parent: string | null, label: string, stats: TreeNodeStats): TreeNode => ({
  id,
  parent_id: parent,
  label,
  value: stats.test_count,
  measure: stats.pass_rate,
  stats,
})

const suiteId = (key: string) => `s:${key}`
const classId = (suite: string, key: string) => `c:${suite}${CLASS_KEY_SEPARATOR}${key}`
const tree = (nodes: TreeNode[], over: Partial<EnvelopeMeta> = {}): ChartResponse<TreeChart> => ({
  meta: meta(over),
  series: { kind: 'tree', nodes },
})

/**
 * Level 1, every suite of a project: each pass-rate bin, the three gaps (not
 * run in this window, last run unknown, never run) and the combined
 * `Other (6)` node. The server folds only past 499 children; the fixture
 * folds at gallery scale, with `truncated_total` saying how many there were.
 */
export const coverageSuites: CoverageMapFixture = {
  level: TOP_LEVEL,
  response: tree(
    [
      node(ROOT_ID, null, 'All suites', seen(551, 86.4, 9440, 46, '2026-09-29', 1)),
      node(suiteId('checkout'), ROOT_ID, 'checkout', seen(120, 96.5, 2400, 3, '2026-09-29', 1)),
      node(suiteId('payments'), ROOT_ID, 'payments', seen(95, 82.1, 1900, 11, '2026-09-28', 2)),
      node(suiteId('search'), ROOT_ID, 'search', seen(80, 91.3, 1600, 2, '2026-09-25', 5)),
      node(suiteId('auth'), ROOT_ID, 'auth', seen(64, 99.2, 1280, 0, '2026-09-29', 1)),
      node(suiteId('reports'), ROOT_ID, 'reports', seen(48, 64, 960, 7, '2026-09-18', 12)),
      node(suiteId('admin'), ROOT_ID, 'admin', seen(40, 45.5, 400, 10, '2026-08-26', 35)),
      node(suiteId('notifications'), ROOT_ID, 'notifications', seen(30, 18, 600, 12, '2026-09-27', 3)),
      node(suiteId('legacy-import'), ROOT_ID, 'legacy-import', idle(24, '2026-05-13', 140)),
      node(suiteId('archived'), ROOT_ID, 'archived', lost(18)),
      node(suiteId('experimental'), ROOT_ID, 'experimental', never(12)),
      // Counts summed, the pass rate left out: a rate over unrelated leftovers answers nothing.
      node(`other:${ROOT_ID}`, ROOT_ID, 'Other (6)', seen(20, null, 300, 1, '2026-09-27', 3)),
    ],
    { truncated: true, truncated_total: 16 },
  ),
}

/** Level 2, the classes or files of `payments`: each flaky-share bin, a gap, and the tests with no class. */
export const coveragePaymentsClasses: CoverageMapFixture = {
  level: { depth: 2, suite: 'payments', classKey: null },
  response: tree([
    node(suiteId('payments'), null, 'payments', seen(95, 82.1, 1900, 11, '2026-09-28', 2)),
    node(classId('payments', 'tests/api/test_refunds.py'), suiteId('payments'), 'tests/api/test_refunds.py', seen(30, 78.4, 600, 6, '2026-09-28', 2)),
    node(
      classId('payments', 'com.acme.payments.CardAuthorizationTest'),
      suiteId('payments'),
      'com.acme.payments.CardAuthorizationTest',
      seen(22, 95, 440, 1, '2026-09-28', 2),
    ),
    node(classId('payments', 'tests/api/test_checkout_totals.py'), suiteId('payments'), 'tests/api/test_checkout_totals.py', seen(18, 99.1, 360, 0, '2026-09-27', 3)),
    node(classId('payments', NO_CLASS_KEY), suiteId('payments'), NO_CLASS_LABEL, seen(12, 88, 240, 1, '2026-09-28', 2)),
    node(classId('payments', 'tests/e2e/test_wallet_topup.py'), suiteId('payments'), 'tests/e2e/test_wallet_topup.py', idle(8, '2026-09-10', 20)),
    node(classId('payments', 'PaymentsSmokeSuite.Retries'), suiteId('payments'), 'PaymentsSmokeSuite.Retries', seen(5, 61, 260, 3, '2026-09-26', 4)),
  ]),
}

/** Level 1 with hostile suite names in every label channel; the markup one is the largest, so the keyboard reaches it first. */
export const coverageHostile: CoverageMapFixture = {
  level: TOP_LEVEL,
  response: tree([
    node(ROOT_ID, null, 'All suites', seen(185, 84.2, 3700, 9, '2026-09-29', 1)),
    node(suiteId(COVERAGE_HOSTILE_NAME), ROOT_ID, COVERAGE_HOSTILE_NAME, seen(60, 72.5, 1200, 5, '2026-09-29', 1)),
    node(suiteId('constructor'), ROOT_ID, 'constructor', seen(45, 93, 900, 1, '2026-09-28', 2)),
    node(suiteId('__proto__'), ROOT_ID, '__proto__', seen(30, 88.8, 600, 2, '2026-09-27', 3)),
    node(suiteId(COVERAGE_LONG_NAME), ROOT_ID, COVERAGE_LONG_NAME, seen(25, 97.1, 500, 0, '2026-09-29', 1)),
    node(suiteId('toString'), ROOT_ID, 'toString', seen(15, 55, 300, 1, '2026-09-20', 10)),
    node(suiteId('<b>suite</b>'), ROOT_ID, '<b>suite</b>', idle(10, '2026-08-01', 60)),
  ]),
}

/** A project whose one suite holds one test: one rectangle, and every sentence in the singular. */
export const coverageOneTest: CoverageMapFixture = {
  level: TOP_LEVEL,
  response: tree([
    node(ROOT_ID, null, 'All suites', seen(1, 100, 30, 0, '2026-09-29', 1)),
    node(suiteId('smoke'), ROOT_ID, 'smoke', seen(1, 100, 30, 0, '2026-09-29', 1)),
  ]),
}

/** A level with no tests at all: the endpoint answers `nodes: []` (never a zero root). */
export const coverageEmpty: CoverageMapFixture = {
  level: TOP_LEVEL,
  response: tree([]),
}
