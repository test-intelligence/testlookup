/**
 * Test-scatter bodies (VIZ-506) for the chart gallery: C3 `points` series in
 * the shape `/api/v1/analytics/test-scatter` answers, wrapped as the
 * `{meta, series}` the scatter section reads.
 *
 * Deterministic: no clock, no `Math.random`. The many-point fixtures come from
 * a fixed-seed linear congruential generator (the one FK4's browser spec
 * used), so the same call always gives the same points, and so the same
 * baseline. Ids are fingerprints, never written with a leading `#`.
 */
import type { EnvelopeMeta, PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import type { ChartResponse } from '../chartStateCore'

/**
 * A name that executes if anything renders it as HTML. The gallery's own copy
 * is `HOSTILE_LABEL` (`pages/dev/chartGalleryFixtures`, which may not import
 * app code); a unit test holds the two equal.
 */
export const SCATTER_HOSTILE_NAME = '<img src=x onerror="window.__xss=1">'

const LONG_HEAD = 'tests/api/payments/test_refund_'
const LONG_TAIL = '_settles_in_the_original_currency.py'
/** A 250-character test name, its two ends distinct: every label channel must survive it. */
export const SCATTER_LONG_NAME = `${LONG_HEAD}${'x'.repeat(250 - LONG_HEAD.length - LONG_TAIL.length)}${LONG_TAIL}`

/** How many points the default and the dense fixture draw (the dense one is past `SCATTER_DENSE_POINTS`). */
export const SCATTER_DEFAULT_POINTS = 300
export const SCATTER_DENSE_COUNT = 2400

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: '11111111-1111-4111-8111-111111111111', name: 'Checkout' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-30', days: 30, timezone: 'UTC' },
  },
  totals: { matched_runs: 60, total_runs: 60, matched_executions: 48210, total_executions: 48210 },
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
}

/** A fixed-seed generator in [0, 1): the same seed gives the same sequence on every machine. */
function lcg(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 4294967296
  }
}

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

type Exclusions = NonNullable<PointsChart['excluded']>

/** The chart around `points`: the axes the endpoint names, and the medians it computes (absent with no point). */
function scatterChart(points: PointsChartPoint[], excluded: Exclusions): PointsChart {
  return {
    kind: 'points',
    x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
    y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
    size: { key: 'executions', label: 'Executions' },
    points,
    ...(points.length > 0 ? { medians: { x: median(points.map((p) => p.x)), y: median(points.map((p) => p.y)) } } : {}),
    excluded,
  }
}

/**
 * `count` tests: p95 from 1 ms to ~30 s on a log spread (some on the 1 ms
 * floor), four in ten flaky with a failure rate up to 60 %, the rest at 0 %
 * (a healthy suite's median failure rate is 0), 5 to 404 executions each.
 */
function generatedPoints(count: number, seed: number): PointsChartPoint[] {
  const rand = lcg(seed)
  return Array.from({ length: count }, (_, i) => {
    const x = Math.max(1, Math.round(10 ** (rand() * 4.5)))
    const flaky = rand() < 0.4
    const y = flaky ? Math.round(rand() * 600) / 10 : 0
    const size = 5 + Math.floor(rand() * 400)
    return { id: `fp-${seed}-${i}`, label: `tests/api/test_case_${i}.py::test_${i}`, x, y, size, n: size }
  })
}

const respond = (series: PointsChart): ChartResponse<PointsChart> => ({ meta: META, series })

/** The default: 300 tests, with some left out for each reason. */
export const scatterDefault: ChartResponse<PointsChart> = respond(
  scatterChart(generatedPoints(SCATTER_DEFAULT_POINTS, 7), { below_min_executions: 14, no_duration: 2, no_evaluated: 1 }),
)

/** Past the dense threshold: plain points (never ECharts' `large` mode) and the footer's "Dense" line. */
export const scatterDense: ChartResponse<PointsChart> = respond(
  scatterChart(generatedPoints(SCATTER_DENSE_COUNT, 11), { below_min_executions: 120, no_duration: 0, no_evaluated: 0 }),
)

/**
 * Hostile names in every label: markup, `Object.prototype` members (as ids
 * too: an id is what a rows request sends back, and a keyed lookup must not
 * read an inherited member), a 250-character name. The markup one is the
 * FASTEST test, so it is the first point the keyboard reaches.
 */
export const scatterHostile: ChartResponse<PointsChart> = respond(
  scatterChart(
    [
      { id: 'fp-hostile', label: SCATTER_HOSTILE_NAME, x: 2, y: 25, size: 40, n: 40 },
      { id: 'constructor', label: 'constructor', x: 1800, y: 12.5, size: 120, n: 120 },
      { id: '__proto__', label: '__proto__', x: 640, y: 0, size: 64, n: 64 },
      { id: 'toString', label: 'toString', x: 95, y: 3.3, size: 30, n: 30 },
      { id: 'fp-long', label: SCATTER_LONG_NAME, x: 12500, y: 41.7, size: 24, n: 24 },
      { id: 'fp-bold', label: '<b>suite</b>', x: 310, y: 8, size: 75, n: 75 },
      { id: 'fp-a', label: 'tests/api/test_cart.py::test_add_item', x: 48, y: 0, size: 200, n: 200 },
      { id: 'fp-b', label: 'tests/api/test_cart.py::test_remove_item', x: 52, y: 1.5, size: 200, n: 200 },
      { id: 'fp-c', label: 'tests/e2e/test_checkout.py::test_pay_by_card', x: 9200, y: 18, size: 50, n: 50 },
      { id: 'fp-d', label: 'tests/e2e/test_checkout.py::test_pay_by_wallet', x: 7400, y: 0, size: 50, n: 50 },
    ],
    { below_min_executions: 3, no_duration: 0, no_evaluated: 0 },
  ),
)

/** Every test left out: no point, no medians, and the counts that are the whole answer. */
export const scatterAllExcluded: ChartResponse<PointsChart> = respond(
  scatterChart([], { below_min_executions: 3, no_duration: 1, no_evaluated: 2 }),
)
