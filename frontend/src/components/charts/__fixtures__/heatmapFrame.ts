/**
 * Deterministic fixtures for `HeatmapChartFrame` (Wave 2.6, K5): the unit
 * tests read them, and the chart gallery (wired by the integrator) draws them
 * for the visual baselines.
 *
 * Each one is a `chart-data` response exactly as the Trends page receives it
 * — `metric=pass_rate&group_by=day&group_by=suite&top_n=7` — so values are
 * PERCENTAGE POINTS (0..100), the series arrive in the server's order (by
 * volume, not worst first) and the remainder is the server's `__other__`
 * series. The frame's adapter does the rest; a fixture already in 0..1 or
 * already sorted would hide the two bugs the adapter exists to prevent.
 *
 * Every value is computed from literals (no clock, no random): a fixture that
 * changes between runs makes a screenshot diff that is not a regression. The
 * writing rule of `wave2Fixtures.ts` holds here too — no id is written with a
 * leading hash, which `check:theme` would read as a colour literal.
 */
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import type { ChartResponse } from '../chartState'
import { OTHER_KEY } from '../multiSeriesModel'

/** `count` UTC days from `start` (YYYY-MM-DD), as day keys. */
function dayKeys(start: string, count: number): string[] {
  const first = Date.parse(`${start}T00:00:00Z`)
  return Array.from({ length: count }, (_, i) => new Date(first + i * 86_400_000).toISOString().slice(0, 10))
}

/** A repeating wobble around a suite's base rate, so rows are distinguishable but not noisy. */
const WOBBLE = [0, -1.5, 1, -3, 2, 0.5, -2, 1.5, -0.5, 2.5, -1, 0, 1, -2.5]

function rateAt(base: number, day: number, swing: number): number {
  const value = base + WOBBLE[day % WOBBLE.length] * swing
  return Math.round(Math.min(100, Math.max(0, value)) * 10) / 10
}

interface SuiteSpec {
  key: string
  label: string
  base: number
  swing: number
  executions: number
}

function seriesChart(
  suites: readonly SuiteSpec[],
  days: readonly string[],
  gaps: ReadonlySet<string> = new Set(),
): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['day', 'suite'],
    x_type: 'time',
    series: suites.map((suite) => ({
      key: suite.key,
      label: suite.label,
      points: days.map((x, i) =>
        gaps.has(`${suite.key}@${x}`)
          ? { x, y: null, n: 0 }
          : { x, y: rateAt(suite.base, i, suite.swing), n: suite.executions + (i % 5) },
      ),
    })),
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

/** In the server's order: most executions first. The worst suite (legacy-import) is sixth. */
const TOP_SUITES: readonly SuiteSpec[] = [
  { key: 'checkout', label: 'checkout', base: 97, swing: 0.6, executions: 420 },
  { key: 'search', label: 'search', base: 92, swing: 1.2, executions: 380 },
  { key: 'auth', label: 'auth', base: 99, swing: 0.3, executions: 350 },
  { key: 'billing', label: 'billing', base: 84, swing: 1.8, executions: 300 },
  { key: 'reports', label: 'reports', base: 95, swing: 0.8, executions: 260 },
  { key: 'legacy-import', label: 'legacy-import', base: 61, swing: 2.5, executions: 180 },
  { key: 'admin', label: 'admin', base: 88, swing: 1.5, executions: 150 },
]

const OTHER: SuiteSpec = { key: OTHER_KEY, label: 'Other', base: 90, swing: 1, executions: 240 }

const FORTNIGHT = dayKeys('2026-09-01', 14)

/** The Trends default window: 14 days, top 7 of 12 suites plus Other. */
export const heatmapFrameMeta: EnvelopeMeta = META(FORTNIGHT, {
  truncated: true,
  truncated_total: 12,
  truncated_axes: { series: { dimension: 'suite', kept: 7, total: 12 } },
})

/**
 * 7 suites + Other over 14 days, in volume order; admin did not run on
 * 2026-09-04 (a null cell, drawn hatched) and billing had a day with no
 * executions on 2026-09-09. The frame must draw legacy-import first and
 * Other last, and say "top 7 of 12 suites".
 */
export const heatmapFrameWorstFirst: ChartResponse = {
  meta: heatmapFrameMeta,
  series: seriesChart([...TOP_SUITES, OTHER], FORTNIGHT, new Set(['admin@2026-09-04', 'billing@2026-09-09'])),
}

const QUARTER = dayKeys('2026-06-16', 90)

/** The largest window a page can ask for: 8 rows x 90 days = 720 cells (cap 5,400). */
export const heatmapFrameDense: ChartResponse = {
  meta: META(QUARTER, {
    truncated: true,
    truncated_total: 31,
    truncated_axes: { series: { dimension: 'suite', kept: 7, total: 31 } },
  }),
  series: seriesChart([...TOP_SUITES, OTHER], QUARTER),
}

/** A label that executes if anything renders it as HTML (suite names come from ingested CI files). */
export const HEATMAP_FRAME_HOSTILE_NAME = '<img src=x onerror="window.__xss=1">'

const WEEK = dayKeys('2026-09-08', 7)

/** Three suites, no truncation, no Other; one suite's name is markup that must render as text. */
export const heatmapFrameHostile: ChartResponse = {
  meta: META(WEEK, {}),
  series: seriesChart(
    [
      { key: 'payments', label: 'payments', base: 96, swing: 0.5, executions: 90 },
      { key: HEATMAP_FRAME_HOSTILE_NAME, label: HEATMAP_FRAME_HOSTILE_NAME, base: 72, swing: 2, executions: 40 },
      { key: '<b>suite</b>', label: '<b>suite</b>', base: 88, swing: 1, executions: 60 },
    ],
    WEEK,
  ),
}
