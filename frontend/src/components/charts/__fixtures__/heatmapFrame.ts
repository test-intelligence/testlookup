/**
 * Deterministic fixtures for `HeatmapChartFrame` (K5, Wave 2.6; VIZ-501, Wave
 * 3): the unit tests read them, and the chart gallery (wired by the
 * integrator) draws them for the visual baselines.
 *
 * Each one is a `/analytics/heatmap` response exactly as the endpoint answers
 * it (BE1), already in the `{meta, series}` shape the chart pipeline reads:
 * a pass rate in PERCENTAGE POINTS with `unit: "percent"`, `x_keys` = UTC
 * days, `y_keys` = lower-cased suite keys, the five status `counts` behind
 * every cell (adding up to `n`), and the rows in the SERVER's order — most
 * failures first, then executions, then key — not worst-rate first. The
 * frame's adapter does the rest; a fixture already in 0..1 or already sorted
 * would hide the two bugs the adapter exists to prevent.
 *
 * Every value is computed from literals (no clock, no random): a fixture that
 * changes between runs makes a screenshot diff that is not a regression. The
 * writing rule of `wave2Fixtures.ts` holds here too — no id is written with a
 * leading hash, which `check:theme` would read as a colour literal.
 */
import type { EnvelopeMeta, MatrixCell, MatrixChart, StatusCounts, VizStatus } from '@/lib/viz/contracts'
import type { ChartResponse } from '../chartState'

/** `count` UTC days from `start` (YYYY-MM-DD), as day keys. */
function dayKeys(start: string, count: number): string[] {
  const first = Date.parse(`${start}T00:00:00Z`)
  return Array.from({ length: count }, (_, i) => new Date(first + i * 86_400_000).toISOString().slice(0, 10))
}

/** A repeating wobble around a suite's base rate, so rows are distinguishable but not noisy. */
const WOBBLE = [0, -1.5, 1, -3, 2, 0.5, -2, 1.5, -0.5, 2.5, -1, 0, 1, -2.5]

interface SuiteSpec {
  key: string
  label: string
  base: number
  swing: number
  executions: number
}

const NO_COUNTS: StatusCounts = { passed: 0, failed: 0, broken: 0, skipped: 0, unknown: 0 }

/**
 * One cell from a target rate and a volume: the counts are built first and
 * the value is the pass rate THEY give (skipped outside the denominator), so
 * the cell is consistent with itself, as the server's is.
 */
function rateCell(x: number, y: number, target: number, n: number, day: number): MatrixCell {
  const skipped = day % 4 === 0 ? 2 : 0
  const evaluated = n - skipped
  const passed = Math.round((Math.min(100, Math.max(0, target)) / 100) * evaluated)
  const bad = evaluated - passed
  const broken = Math.floor(bad / 3)
  const counts: StatusCounts = { passed, failed: bad - broken, broken, skipped, unknown: 0 }
  return { x, y, value: Math.round((passed / evaluated) * 1000) / 10, n, counts }
}

/**
 * A suite x day matrix in the server's row order. `gaps` = `key@day` cells
 * nobody ran (`null`, `n: 0`); `skippedOnly` = cells that ran and evaluated
 * nothing (`null`, `n > 0`, every execution skipped).
 */
function suiteDayChart(
  suites: readonly SuiteSpec[],
  days: readonly string[],
  { gaps = new Set<string>(), skippedOnly = new Set<string>() }: { gaps?: ReadonlySet<string>; skippedOnly?: ReadonlySet<string> } = {},
): MatrixChart {
  const grid = suites.map((suite) =>
    days.map((day, i): Omit<MatrixCell, 'y'> => {
      const at = `${suite.key}@${day}`
      if (gaps.has(at)) return { x: i, value: null, n: 0, counts: NO_COUNTS }
      if (skippedOnly.has(at)) return { x: i, value: null, n: 4, counts: { ...NO_COUNTS, skipped: 4 } }
      const { y: _y, ...cell } = rateCell(i, 0, suite.base + WOBBLE[i % WOBBLE.length] * suite.swing, suite.executions + (i % 5), i)
      return cell
    }),
  )
  // The server's order: failed + broken over the window desc, executions desc, key asc.
  const totals = suites.map((suite, index) => {
    let bad = 0
    let n = 0
    for (const cell of grid[index]) {
      bad += (cell.counts?.failed ?? 0) + (cell.counts?.broken ?? 0)
      n += cell.n
    }
    return { index, key: suite.key, bad, n }
  })
  totals.sort((a, b) => b.bad - a.bad || b.n - a.n || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
  return {
    kind: 'matrix',
    value_type: 'rate',
    unit: 'percent',
    x_labels: [...days],
    x_keys: [...days],
    y_labels: totals.map((row) => suites[row.index].label),
    y_keys: totals.map((row) => suites[row.index].key),
    cells: totals.flatMap((row, y) => grid[row.index].map((cell) => ({ ...cell, y }))),
  }
}

const META = (days: readonly string[], over: Partial<EnvelopeMeta>): EnvelopeMeta => ({
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: [],
    window: { from: days[0], to: days[days.length - 1], days: days.length, timezone: 'UTC' },
  },
  totals: { matched_runs: 96, total_runs: 96, matched_executions: 18_420, total_executions: 18_420 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-14T09:00:00Z',
  as_of: '2026-09-14T09:00:00Z',
  ...over,
})

/** Seven suites; the worst RATE (legacy-import) is not the most failures (billing, by volume). */
const TOP_SUITES: readonly SuiteSpec[] = [
  { key: 'checkout', label: 'checkout', base: 97, swing: 0.6, executions: 420 },
  { key: 'search', label: 'search', base: 92, swing: 1.2, executions: 380 },
  { key: 'auth', label: 'auth', base: 99, swing: 0.3, executions: 350 },
  { key: 'billing', label: 'billing', base: 84, swing: 1.8, executions: 300 },
  { key: 'reports', label: 'reports', base: 95, swing: 0.8, executions: 260 },
  { key: 'legacy-import', label: 'legacy-import', base: 61, swing: 2.5, executions: 60 },
  { key: 'admin', label: 'admin', base: 88, swing: 1.5, executions: 150 },
]

const FORTNIGHT = dayKeys('2026-09-01', 14)

/** The Trends default window: 14 days, the top 7 of 12 suites (the rest dropped and counted). */
export const heatmapFrameMeta: EnvelopeMeta = META(FORTNIGHT, {
  truncated: true,
  truncated_total: 12,
  truncated_axes: { series: { dimension: 'suite', kept: 7, total: 12 } },
})

/**
 * 7 suites over 14 days, in the server's (failures) order; admin did not run
 * on 2026-09-04 and billing had a day with no executions on 2026-09-09 (two
 * null cells, drawn hatched). The frame must draw legacy-import first and say
 * "Top 7 of 12 suites by failures".
 */
export const heatmapFrameWorstFirst: ChartResponse<MatrixChart> = {
  meta: heatmapFrameMeta,
  series: suiteDayChart(TOP_SUITES, FORTNIGHT, { gaps: new Set(['admin@2026-09-04', 'billing@2026-09-09']) }),
}

const QUARTER = dayKeys('2026-06-16', 90)

/** The largest window a page can ask for: 7 rows x 90 days = 630 cells (cap 5,400). */
export const heatmapFrameDense: ChartResponse<MatrixChart> = {
  meta: META(QUARTER, {
    truncated: true,
    truncated_total: 31,
    truncated_axes: { series: { dimension: 'suite', kept: 7, total: 31 } },
  }),
  series: suiteDayChart(TOP_SUITES, QUARTER),
}

/** A label that executes if anything renders it as HTML (suite names come from ingested CI files). */
export const HEATMAP_FRAME_HOSTILE_NAME = '<img src=x onerror="window.__xss=1">'

const WEEK = dayKeys('2026-09-08', 7)

/**
 * Three suites, no truncation; one suite's name is markup that must render as
 * text, and it has the lowest rate, so it is the TOP row worst first.
 */
export const heatmapFrameHostile: ChartResponse<MatrixChart> = {
  meta: META(WEEK, {}),
  series: suiteDayChart(
    [
      { key: 'payments', label: 'payments', base: 96, swing: 0.5, executions: 90 },
      { key: HEATMAP_FRAME_HOSTILE_NAME, label: HEATMAP_FRAME_HOSTILE_NAME, base: 72, swing: 2, executions: 40 },
      { key: '<b>suite</b>', label: '<b>suite</b>', base: 88, swing: 1, executions: 60 },
    ],
    WEEK,
  ),
}

/** Object-member and very long names: every label channel must print them literally. */
export const HEATMAP_FRAME_LONG_NAME = `payments-integration-${'x'.repeat(220)}-eu-west`

/**
 * The edge cases in one week (Wave 3): an all-null row (every cell hatched:
 * the renderer, the sort and the table must survive it), a skipped-only cell
 * (`null` with `n > 0`: "Nothing evaluated"), today's partial column, and
 * suites named `constructor`, `__proto__` and a 250-character name.
 */
export const heatmapFrameEdges: ChartResponse<MatrixChart> = {
  meta: META(WEEK, { partial_day: WEEK[WEEK.length - 1], includes_in_progress: 1 }),
  series: suiteDayChart(
    [
      { key: 'constructor', label: 'constructor', base: 93, swing: 1, executions: 50 },
      { key: '__proto__', label: '__proto__', base: 81, swing: 2, executions: 45 },
      { key: HEATMAP_FRAME_LONG_NAME, label: HEATMAP_FRAME_LONG_NAME, base: 97, swing: 0.5, executions: 30 },
      { key: 'dormant', label: 'dormant', base: 90, swing: 0, executions: 0 },
    ],
    WEEK,
    {
      gaps: new Set(WEEK.map((day) => `dormant@${day}`)),
      skippedOnly: new Set([`__proto__@${WEEK[2]}`]),
    },
  ),
}

/** Run ids and build labels for the test x run fixture (oldest to newest, as the server sends them). */
const RUNS = Array.from({ length: 8 }, (_, i) => ({ id: `run-${String(i + 1).padStart(3, '0')}`, label: `Build ${1200 + i}` }))

const TEST_STATUSES: readonly (readonly (VizStatus | null)[])[] = [
  ['failed', 'failed', 'broken', 'failed', 'passed', 'failed', 'failed', 'broken'],
  ['passed', 'failed', 'passed', 'failed', 'passed', 'failed', 'passed', 'failed'],
  ['passed', 'passed', null, 'broken', 'passed', 'passed', 'skipped', 'failed'],
  ['unknown', 'passed', 'passed', 'passed', 'failed', 'passed', 'passed', 'passed'],
]

/** Test names as ingested: a hostile one and an Object member among them. */
const TESTS = [
  { key: 'fp-0001', label: 'test_checkout_total_rounds_half_up' },
  { key: 'fp-0002', label: HEATMAP_FRAME_HOSTILE_NAME },
  { key: 'fp-0003', label: 'constructor' },
  { key: 'fp-0004', label: 'tests/api/test_auth.py::test_token_refresh_after_expiry' },
]

/**
 * `kind=test_run` (Suite detail): a STATUS matrix, the last 8 runs oldest to
 * newest, the 4 tests with the most failures in the window; one test did not
 * run in run 3 (`null`, `n: 0`). No `unit`, no `counts` (BE1 note 5).
 */
export const heatmapFrameStatus: ChartResponse<MatrixChart> = {
  meta: META(FORTNIGHT, { pass_rate_basis: null }),
  series: {
    kind: 'matrix',
    value_type: 'status',
    // Build labels repeat across branches in real CI: the key is the run id.
    x_labels: RUNS.map((run) => run.label),
    x_keys: RUNS.map((run) => run.id),
    y_labels: TESTS.map((test) => test.label),
    y_keys: TESTS.map((test) => test.key),
    cells: TEST_STATUSES.flatMap((row, y) => row.map((value, x) => ({ x, y, value, n: value === null ? 0 : 1 }))),
  },
}
