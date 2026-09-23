/**
 * Deterministic fixtures for the VIZ-403 / VIZ-406 catalogue entries.
 *
 * Kept in this module (not in the gallery's own fixture file) so the chart
 * gallery can import them without this story and VIZ-401/402 editing the same
 * file. Every value is a literal — no `Date.now()`, no random — because these
 * back a visual baseline: a fixture that changes with the clock makes a
 * screenshot diff that is not a regression.
 *
 * Each exported model already carries the edge case the story names, so the
 * gallery renders the interesting case rather than the happy one.
 *
 * ONE WRITING RULE, and it is not cosmetic: a run, build or test id in this
 * file is written "run 4181" — never with a leading hash.
 * `scripts/check-theme-tokens.mjs` forbids colour literals anywhere under
 * `src/components/charts/**`, and to it a colour literal is a hash followed by
 * three, four, six or eight hex digits. An id like 4181 or 9902 is four hex
 * digits, and 412 is three, so prefixing one with a hash makes it a valid CSS
 * colour as far as that matcher is concerned and fails `npm run check:theme`
 * with a message about chart tokens that has nothing to do with the real
 * problem. The guard is right to stay blunt — an all-numeric three-digit hex
 * IS a colour, and it has to scan comments as well as code — so this file
 * avoids the shape rather than the guard being loosened for it. That applies
 * to prose here as much as to the fixture strings: this paragraph deliberately
 * spells the examples out in words.
 */
import type { EnvelopeMeta } from '@/lib/viz/contracts'
import {
  buildDurationHistogram,
  durationBandPoints,
  rankSlowestTests,
  type DurationBandModel,
  type DurationHistogramModel,
  type SlowestTestsModel,
} from '../durationBuckets'
import { buildTimeSeriesModel, timeSeriesFromTrends, type ReleaseInput, type TimeSeriesModel } from '../timeSeriesModel'
import type { TrendPoint } from '@/types/metrics'

const day = (date: string, passed: number, failed: number, skipped = 0, broken = 0): TrendPoint => ({
  date,
  passed,
  failed,
  skipped,
  broken,
  total: passed + failed + skipped + broken,
  pass_rate:
    passed + failed + broken > 0 ? Math.round((passed / (passed + failed + broken)) * 1000) / 10 : 0,
})

const META = (over: Partial<EnvelopeMeta>): EnvelopeMeta =>
  ({
    schema_version: 1,
    scope: {
      projects: [{ id: 'p1', name: 'payments' }],
      releases: [],
      suites: [],
      window: { from: '2026-03-01', to: '2026-03-10', days: 10, timezone: 'UTC' },
    },
    totals: { matched_runs: 24, total_runs: 24, matched_executions: 1840, total_executions: 1840 },
    pass_rate_basis: 'executions',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: null,
    generated_at: '2026-03-10T09:00:00Z',
    as_of: '2026-03-10T09:00:00Z',
    ...over,
  }) as EnvelopeMeta

/**
 * The headline trend's envelope meta and releases, exported so a variant built
 * from its days (the gallery's hostile-release item) keeps the still-filling
 * last day and every release. `buildTimeSeriesModel` marks the partial day from
 * the META, not from the points it is handed, so a variant that passes only
 * `points` draws 03-10 as a finished, suspiciously low bar.
 */
export const trendWithReleasesMeta: EnvelopeMeta = META({ partial_day: '2026-03-10', includes_in_progress: 2 })
export const trendWithReleasesReleases: readonly ReleaseInput[] = [
  { id: 'r0', name: '1.3.0', date: '2026-02-24T10:00:00Z' },
  { id: 'r1', name: '1.4.0', date: '2026-03-03T00:00:00Z' },
  { id: 'r2', name: '1.4.1', date: '2026-03-08T16:30:00Z' },
]

/**
 * The headline trend: ten UTC days, TWO of them with no runs (a weekend), two
 * releases inside the window and one dated before it, and the last day still
 * filling. Exercises gaps, markers, the outside-window notice and the partial
 * day in one frame.
 */
export const trendWithReleasesFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends(
    [
      day('2026-03-01', 180, 12),
      day('2026-03-02', 176, 18, 4),
      day('2026-03-03', 190, 6),
      day('2026-03-06', 150, 40, 2, 3),
      day('2026-03-07', 172, 20),
      day('2026-03-08', 181, 9),
      day('2026-03-09', 168, 24, 1),
      day('2026-03-10', 90, 6),
    ],
    { from: '2026-03-01', to: '2026-03-10' },
  ),
  meta: trendWithReleasesMeta,
  releases: trendWithReleasesReleases,
})

/** The single-point case: a dot, never a line. */
export const trendSinglePointFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends([day('2026-03-05', 42, 3)]),
})

/** The zoomed-axis case: a narrow band, so the "does not start at 0" indicator shows. */
export const trendZoomedAxisFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends([
    day('2026-03-01', 194, 6),
    day('2026-03-02', 191, 9),
    day('2026-03-03', 196, 4),
    day('2026-03-04', 190, 10),
  ]),
  zoomRateAxis: true,
})

/**
 * VIZ-405, trend overlays: 30 UTC days (2026-03-01 to 2026-03-30), three of
 * the four Saturdays without runs (27 days with runs), a slow slide of roughly
 * 0.8 pts a week, one 3-test day at 100 % that the execution weighting must
 * keep from swinging the trend and that is under the anomaly rule's 50-execution
 * minimum (2026-03-10), and one day far below the Mondays before it
 * (2026-03-23, 79 % against 94.4, 94.1 and 93.4 %) for the anomaly marker.
 */
const ANALYSIS_DAYS: TrendPoint[] = [
  day('2026-03-01', 203, 9),
  day('2026-03-02', 187, 11),
  day('2026-03-03', 215, 11),
  day('2026-03-04', 193, 12),
  day('2026-03-05', 220, 11),
  day('2026-03-06', 180, 10),
  day('2026-03-08', 208, 11),
  day('2026-03-09', 192, 12),
  day('2026-03-10', 3, 0),
  day('2026-03-11', 213, 15),
  day('2026-03-12', 190, 11),
  day('2026-03-13', 202, 13),
  day('2026-03-15', 210, 12),
  day('2026-03-16', 183, 13),
  day('2026-03-17', 198, 12),
  day('2026-03-18', 217, 16),
  day('2026-03-19', 195, 12),
  day('2026-03-20', 176, 12),
  day('2026-03-22', 211, 13),
  day('2026-03-23', 169, 45),
  day('2026-03-24', 186, 13),
  day('2026-03-25', 213, 17),
  day('2026-03-26', 196, 13),
  day('2026-03-27', 202, 15),
  day('2026-03-28', 180, 13),
  day('2026-03-29', 212, 14),
  day('2026-03-30', 195, 16),
]
/** The VIZ-405 overlay model over those days (the VIZ-407 fixture below reuses them). */
export const trendAnalysisFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends(ANALYSIS_DAYS, { from: '2026-03-01', to: '2026-03-30' }),
  // Zoomed: on a 0-100 axis the rate, the average and the trend line all sit
  // in the top tenth of the plot and cannot be told apart. The axis then says
  // it does not start at 0.
  zoomRateAxis: true,
})

/**
 * VIZ-407, a window long enough to zoom into: 42 UTC days (2026-02-17 to
 * 2026-03-30). The last 30 are `trendAnalysisFixture`'s own days — its
 * run-free Saturdays and its one flagged day (2026-03-23) included — and the
 * twelve before them repeat its first week's numbers, so no new day stands
 * out. Three releases: one before any zoom the gallery opens on (2026-02-24),
 * one inside it (2026-03-18) and one after it (2026-03-29), so a zoomed view
 * has markers on both sides that it must still list in its table.
 */
const FIRST_WEEK: readonly (readonly [number, number])[] = [
  [203, 9],
  [187, 11],
  [215, 11],
  [193, 12],
  [220, 11],
  [180, 10],
  [208, 11],
]
export const trendZoomReleasesFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends(
    [
      ...Array.from({ length: 12 }, (_, i) => {
        const [passed, failed] = FIRST_WEEK[i % FIRST_WEEK.length]
        return day(`2026-02-${String(17 + i).padStart(2, '0')}`, passed, failed)
      }),
      ...ANALYSIS_DAYS,
    ],
    { from: '2026-02-17', to: '2026-03-30' },
  ),
  zoomRateAxis: true,
  releases: [
    { id: 'z1', name: '2.0.0', date: '2026-02-24T09:00:00Z' },
    { id: 'z2', name: '2.1.0', date: '2026-03-18T09:00:00Z' },
    { id: 'z3', name: '2.1.1', date: '2026-03-29T09:00:00Z' },
  ],
})

/**
 * The envelope `meta` behind that window, so the gallery item carries a real
 * SCOPE: its frame footer states "N of M", and its exports (VIZ-606) stamp
 * the project, suites, window, totals and generated-at — plus the zoom note —
 * instead of "Scope unavailable". Every number is derived from the fixture:
 * the executions are the model's own, and the runs are two per day with runs.
 * `generated_at` is fixed, so the export's file name is too.
 */
const ZOOM_EXECUTIONS = trendZoomReleasesFixture.points.reduce((sum, point) => sum + (point.executions ?? 0), 0)
const ZOOM_RUNS = 2 * trendZoomReleasesFixture.points.filter((point) => (point.executions ?? 0) > 0).length
export const trendZoomReleasesMeta: EnvelopeMeta = META({
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: ['checkout', 'payments-api'],
    window: { from: '2026-02-17', to: '2026-03-30', days: 42, timezone: 'UTC' },
  },
  totals: {
    matched_runs: ZOOM_RUNS,
    total_runs: ZOOM_RUNS,
    matched_executions: ZOOM_EXECUTIONS,
    total_executions: ZOOM_EXECUTIONS,
  },
  generated_at: '2026-03-30T09:00:00Z',
  as_of: '2026-03-30T09:00:00Z',
})

/**
 * VIZ-405, too little data: 14 days with runs on only 6 of them — below the
 * 7 the overlays need, so their toggles are disabled with the reason. The
 * last week against the previous one IS still measurable, and is still shown:
 * a thin long window must not hide a recent drop.
 */
export const trendAnalysisSparseFixture: TimeSeriesModel = buildTimeSeriesModel({
  points: timeSeriesFromTrends(
    [
      day('2026-03-01', 190, 10),
      day('2026-03-03', 188, 12),
      day('2026-03-05', 191, 9),
      day('2026-03-08', 146, 54),
      day('2026-03-10', 142, 58),
      day('2026-03-12', 150, 50),
    ],
    { from: '2026-03-01', to: '2026-03-14' },
  ),
})

/** Runs still executing on the partial day, named in its tooltip. */
export const inProgressRunsFixture = [{ x: '2026-03-10', names: ['nightly-regression run 4181', 'smoke run 9902'] }]

/**
 * A duration histogram whose bulk sits between 2 ms and 900 ms with one
 * 42-minute outlier — the case the overflow bucket exists for — plus 214
 * executions with no duration at all.
 */
export const durationHistogramFixture: DurationHistogramModel = buildDurationHistogram([
  ...Array.from({ length: 40 }, (_, i) => 0.4 + i * 0.05),
  ...Array.from({ length: 260 }, (_, i) => 2 + (i % 18) * 3),
  ...Array.from({ length: 140 }, (_, i) => 60 + (i % 40) * 21),
  ...Array.from({ length: 30 }, (_, i) => 900 + i * 40),
  2_520_000,
  ...Array.from<number | null>({ length: 214 }).fill(null),
  0,
  0,
])

/**
 * Nothing timed at all: two executions with no duration and one recorded as
 * exactly 0 ms. No value can go on a log axis, so the histogram draws no bars
 * and says so — and still states the three it excluded, broken out by reason.
 */
export const durationHistogramEmptyFixture: DurationHistogramModel = buildDurationHistogram([null, null, 0])

/** p50/p95 over a week, with one unmeasured day and one day the data inverts. */
export const durationBandFixture: DurationBandModel = durationBandPoints({
  p50: {
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [
      {
        key: 'duration_p50',
        label: 'p50',
        points: [
          { x: '2026-03-04', y: 118, n: 260 },
          { x: '2026-03-05', y: 124, n: 254 },
          { x: '2026-03-06', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' },
          { x: '2026-03-07', y: 131, n: 248 },
          { x: '2026-03-08', y: 410, n: 12 },
          { x: '2026-03-09', y: 140, n: 266 },
        ],
      },
    ],
  },
  p95: {
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [
      {
        key: 'duration_p95',
        label: 'p95',
        points: [
          { x: '2026-03-04', y: 940, n: 260 },
          { x: '2026-03-05', y: 1020, n: 254 },
          { x: '2026-03-06', y: null, n: 0, measured: false, reason: 'no execution in this bucket carries a duration' },
          { x: '2026-03-07', y: 1180, n: 248 },
          // Deliberately BELOW p50: the disagreement case the chart must report.
          { x: '2026-03-08', y: 180, n: 12 },
          { x: '2026-03-09', y: 1240, n: 266 },
        ],
      },
    ],
  },
})

/** 24 tests, so the frame shows the top 20 and states that there were more. */
export const slowestTestsFixture: SlowestTestsModel = rankSlowestTests([
  { name: 'checkout completes a purchase with a saved card and a coupon', p95: 2_520_000, runs: 12 },
  { name: 'checkout retries a declined card', p95: 184_000, runs: 12 },
  ...Array.from({ length: 21 }, (_, i) => ({
    name: `payments suite case ${i + 1}`,
    p95: 90_000 - i * 3_500,
    runs: 8 + (i % 5),
  })),
  { name: 'search returns nothing for an unknown sku', p95: null, runs: 3 },
])
