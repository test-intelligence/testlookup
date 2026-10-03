/**
 * Fixed data for the DEV-only chart gallery (`/__charts`).
 *
 * The gallery is the SUBJECT of the visual-regression harness, so everything a
 * pixel depends on is a literal in this file: no `Date.now()`, no
 * `Math.random()`, no fetch, no locale-formatted value. Changing a number here
 * is supposed to fail `npm run test:visual` — that is how the harness is
 * mutation-checked — so re-baseline deliberately (`npm run test:visual:update`).
 *
 * Kept free of app imports (no `@/…`, no CSS, no React): the Playwright specs
 * import the ids and canvas size from here, and they run in plain Node.
 * `import type` is erased, so the kit's prop types are allowed.
 */
import type { CoverageColorBy } from '@/components/charts/coverageMap.model'
import type { FailureGroupsFixtureOptions } from '@/components/charts/failureGroups/failureGroups.fixtures'
import type { FailureGroupsView } from '@/components/charts/failureGroups/failureGroups.model'
import type { GaugeBarProps } from '@/components/charts/GaugeBar'
import type { HeatmapNouns } from '@/components/charts/heatmapFromMatrix'
import type { SparklineTone } from '@/components/charts/Sparkline.model'
import type { TimeSeriesRateTarget } from '@/components/charts/TimeSeriesChart'

/** Same shape `TrendChart` takes; spelt out so a field rename there is a type error here. */
export interface GalleryTrendPoint {
  date: string
  passed: number
  failed: number
  skipped: number
  broken: number
  total: number
  pass_rate: number
}

/** One week; every status is non-zero every day so each stacked-bar segment draws. */
export const GALLERY_TREND_DATA: GalleryTrendPoint[] = [
  { date: '2026-09-01', passed: 182, failed: 12, skipped: 6, broken: 4, total: 204, pass_rate: 89.2 },
  { date: '2026-09-02', passed: 190, failed: 9, skipped: 5, broken: 3, total: 207, pass_rate: 91.8 },
  { date: '2026-09-03', passed: 171, failed: 24, skipped: 8, broken: 6, total: 209, pass_rate: 81.8 },
  { date: '2026-09-04', passed: 188, failed: 14, skipped: 7, broken: 2, total: 211, pass_rate: 89.1 },
  { date: '2026-09-05', passed: 201, failed: 6, skipped: 4, broken: 1, total: 212, pass_rate: 94.8 },
  { date: '2026-09-06', passed: 196, failed: 10, skipped: 9, broken: 5, total: 220, pass_rate: 89.1 },
  { date: '2026-09-07', passed: 207, failed: 5, skipped: 6, broken: 2, total: 220, pass_rate: 94.1 },
]

/** `[P1, P2, P3, P4]` — all four non-zero so all four sectors draw. */
export const GALLERY_DEFECT_DATA: number[] = [3, 7, 12, 5]

/** ≥ 95, so the gauge takes its "passed" colour. */
export const GALLERY_PASS_RATE = 96.4

/** Same shape as the heatmap's `NumericMatrix` (contracts C3 `matrix`), spelt out for the same reason. */
export interface GalleryMatrix {
  kind: 'matrix'
  value_type: 'rate' | 'count'
  /** C3 (OD-7): what a `rate` is measured in. These fixtures are 0..1 ratios, so they say so. */
  unit?: 'ratio'
  x_labels: string[]
  y_labels: string[]
  cells: { x: number; y: number; value: number | null; n: number }[]
}

/** A status matrix (contracts C3 `matrix`, `value_type: 'status'`): cells are statuses, drawn with their decals. */
export interface GalleryStatusMatrix {
  kind: 'matrix'
  value_type: 'status'
  x_labels: string[]
  y_labels: string[]
  cells: { x: number; y: number; value: 'passed' | 'failed' | 'broken' | 'skipped' | 'unknown' | null; n: number }[]
}

const HEATMAP_DAYS =['09-01', '09-02', '09-03', '09-04', '09-05', '09-06', '09-07']
const HEATMAP_SUITES = ['auth', 'billing', 'checkout', 'search', 'reports', 'admin']
/** Pass rate per suite per day; one `null` (no run that day) so an empty cell is baselined too. */
const HEATMAP_RATES: (number | null)[][] = [
  [0.98, 0.97, 0.99, 1.0, 0.96, 0.98, 0.99],
  [0.91, 0.88, 0.72, 0.85, 0.9, 0.93, 0.95],
  [0.99, 0.99, 0.98, null, 0.97, 0.99, 1.0],
  [0.8, 0.76, 0.64, 0.7, 0.82, 0.85, 0.88],
  [0.95, 0.94, 0.9, 0.92, 0.96, 0.97, 0.94],
  [0.6, 0.55, 0.42, 0.5, 0.58, 0.66, 0.71],
]

export const GALLERY_HEATMAP_DATA: GalleryMatrix = {
  kind: 'matrix',
  value_type: 'rate',
  unit: 'ratio',
  x_labels: HEATMAP_DAYS,
  y_labels: HEATMAP_SUITES,
  cells: HEATMAP_RATES.flatMap((row, y) => row.map((value, x) => ({ x, y, value, n: 40 + x + y }))),
}

const STATUS_ROWS: GalleryStatusMatrix['cells'][number]['value'][][] = [
  ['passed', 'passed', 'failed', 'passed', 'broken', 'passed'],
  ['skipped', 'passed', 'passed', 'unknown', 'passed', null],
  ['failed', 'failed', 'broken', 'passed', 'skipped', 'passed'],
]

/** Every status (and one no-data cell), so each decal and the hatch are baselined. */
export const GALLERY_STATUS_MATRIX_DATA: GalleryStatusMatrix = {
  kind: 'matrix',
  value_type: 'status',
  x_labels: ['run 1', 'run 2', 'run 3', 'run 4', 'run 5', 'run 6'],
  y_labels: ['auth', 'billing', 'checkout'],
  cells: STATUS_ROWS.flatMap((row, y) => row.map((value, x) => ({ x, y, value, n: 1 }))),
}

/**
 * A label that executes if anything renders it as HTML. Test names come from
 * ingested CI files, so this is the shape an attack takes. The e2e spec hovers
 * its cell and asserts the tooltip shows it as literal text and `window.__xss`
 * stays unset.
 */
export const HOSTILE_LABEL = '<img src=x onerror="window.__xss=1">'

/** Two cells side by side: the hostile one on the left (x = 0), a benign one on the right. */
export const GALLERY_HOSTILE_HEATMAP_DATA: GalleryMatrix = {
  kind: 'matrix',
  value_type: 'count',
  x_labels: [HOSTILE_LABEL, 'benign'],
  y_labels: ['<b>suite</b>'],
  cells: [
    { x: 0, y: 0, value: 7, n: 7 },
    { x: 1, y: 0, value: 3, n: 3 },
  ],
}

/** Every chart is drawn inside a box of exactly this many CSS px. */
export const GALLERY_CANVAS = { width: 640, height: 320 } as const

/** A status heatmap draws its patterned legend under the canvas, inside the same box. */
export const GALLERY_STATUS_LEGEND_HEIGHT = 40

/** The canvas height a gallery item's chart is given. */
export function galleryChartHeight(item: GalleryItem): number {
  // Wave 2.6: the heatmap frame's plot, at the Trends section's height.
  if (item.chart === 'heatmap-frame') return GALLERY_HEATMAP_FRAME_PLOT_HEIGHT
  // Wave 3: each plot at its section's height.
  if (item.chart === 'coverage-map') return GALLERY_COVERAGE_MAP_PLOT_HEIGHT
  if (item.chart === 'scatter') return GALLERY_SCATTER_PLOT_HEIGHT
  if (item.chart === 'failure-groups') return GALLERY_FAILURE_GROUPS_PLOT_HEIGHT
  if (item.chart === 'systemic-clusters') return GALLERY_CLUSTERS_PLOT_HEIGHT
  return item.chart === 'heatmap' && item.data.value_type === 'status'
    ? GALLERY_CANVAS.height - GALLERY_STATUS_LEGEND_HEIGHT
    : GALLERY_CANVAS.height
}

// -- Wave 2 - the VIZ-401 donut and the VIZ-402 bars -------------------------
//
// These items mount the REAL `ChartFrame`, so each one is a whole chart: the
// plot, its legend, its own notes and the table view. One item per edge case
// in the stories, because these are what the Playwright and visual specs
// exercise. Contract C3 is spelt out here, as everything else in this file is,
// so a rename in `lib/viz/contracts` is a type error here rather than a
// silently mis-shaped fixture.

export interface GalleryCategorySeries {
  kind: 'series'
  dimensions: string[]
  x_type: 'category'
  series: { key: string; label: string; points: { x: string; y: number | null; n: number }[] }[]
  x_labels?: Record<string, string>
}

export type GalleryStatus = 'passed' | 'failed' | 'broken' | 'skipped' | 'unknown'
export type GalleryStatusCounts = Partial<Record<GalleryStatus, number>>

/** The fixed status order, as `VIZ_STATUSES` has it. */
const GALLERY_STATUS_ORDER: GalleryStatus[] = ['passed', 'failed', 'broken', 'skipped', 'unknown']

/** One series over a category axis: `[label, value]` pairs, in the order given. */
export function gallerySeries(
  pairs: readonly (readonly [string, number])[],
  dimension: string,
  key = 'value',
  label = 'Value',
): GalleryCategorySeries {
  return {
    kind: 'series',
    dimensions: [dimension],
    x_type: 'category',
    series: [{ key, label, points: pairs.map(([x, y]) => ({ x, y, n: Math.abs(y) })) }],
  }
}

/** `chart-data` with `group_by=status`: the statuses ARE the x axis. */
export function galleryStatusSeries(counts: GalleryStatusCounts): GalleryCategorySeries {
  return gallerySeries(
    GALLERY_STATUS_ORDER.filter((status) => counts[status] !== undefined).map(
      (status) => [status, counts[status] as number] as const,
    ),
    'status',
    'executions',
    'Executions',
  )
}

/** `chart-data` with `group_by=(suite, status)`: one series per status. */
export function galleryStatusRowsSeries(
  rows: readonly (readonly [string, GalleryStatusCounts])[],
): GalleryCategorySeries {
  const present = GALLERY_STATUS_ORDER.filter((status) => rows.some(([, counts]) => (counts[status] ?? 0) > 0))
  return {
    kind: 'series',
    dimensions: ['suite', 'status'],
    x_type: 'category',
    series: present.map((status) => ({
      key: status,
      label: status,
      points: rows.map(([suite, counts]) => ({ x: suite, y: counts[status] ?? 0, n: counts[status] ?? 0 })),
    })),
  }
}

/** The story's own numbers: 880 / 60 / 20 / 40, total 1 000. */
export const GALLERY_DONUT_STATUS: GalleryStatusCounts = { passed: 880, failed: 60, broken: 20, skipped: 40 }
/** `unknown` is the fifth slice - and the slice limit. */
export const GALLERY_DONUT_UNKNOWN: GalleryStatusCounts = { ...GALLERY_DONUT_STATUS, unknown: 12 }
/** One status only: a full, still-labelled ring. */
export const GALLERY_DONUT_SINGLE: GalleryStatusCounts = { passed: 412 }
/** 0.5%: the label moves to the legend and the arc keeps its minimum. */
export const GALLERY_DONUT_TINY: GalleryStatusCounts = { passed: 9_950, failed: 50 }
/** All zero: the frame shows filtered-empty instead of a ring of nothing. */
export const GALLERY_DONUT_ZERO: GalleryStatusCounts = { passed: 0, failed: 0, broken: 0, skipped: 0 }

export const GALLERY_TOP_FAILING: (readonly [string, number])[] = [
  ['tests.api.test_login_with_an_expired_token', 41],
  ['tests.ui.test_checkout_completes', 33],
  ['tests.api.test_refund_is_idempotent', 28],
  ['tests.ui.test_search_ranks_exact_matches', 23],
  ['tests.api.test_webhook_retries', 19],
  ['tests.ui.test_cart_persists', 16],
  ['tests.api.test_rate_limit_headers', 12],
  ['tests.ui.test_address_validation', 9],
  ['tests.api.test_currency_rounding', 7],
  ['tests.ui.test_empty_state', 4],
]

/** Nine clear leaders, then four tests tied on the tenth place. */
export const GALLERY_TIED_BARS: (readonly [string, number])[] = [
  ...GALLERY_TOP_FAILING.slice(0, 9),
  ['tests.api.test_tied_alpha', 7],
  ['tests.api.test_tied_bravo', 7],
  ['tests.api.test_tied_charlie', 7],
  ['tests.api.test_tied_delta', 7],
  ['tests.api.test_below_the_line', 2],
]
export const GALLERY_TIE_TOP_N = 10

/** Names far longer than the axis: middle-truncated, full in tooltip and table. */
export const GALLERY_LONG_NAMES: (readonly [string, number])[] = [
  ['tests.integration.checkout.test_payment_gateway_declines_an_expired_card_and_retries_once', 31],
  ['tests.integration.checkout.test_payment_gateway_declines_an_expired_card_and_gives_up', 24],
  ['tests.integration.accounts.test_password_reset_email_is_sent_once_per_request_window', 18],
  ['tests.integration.accounts.test_password_reset_token_expires_after_thirty_minutes', 11],
  ['tests.integration.reporting.test_summary_pdf_carries_the_release_and_suite_context', 6],
]

/** Every suite has every status, so each stacked segment draws. */
export const GALLERY_SUITE_STATUS: (readonly [string, GalleryStatusCounts])[] = [
  ['checkout', { passed: 180, failed: 22, broken: 8, skipped: 10 }],
  ['auth', { passed: 140, failed: 6, broken: 3, skipped: 4 }],
  ['search', { passed: 96, failed: 14, broken: 5, skipped: 12 }],
  ['billing', { passed: 74, failed: 31, broken: 11, skipped: 6 }],
  ['reports', { passed: 52, failed: 4, broken: 2, skipped: 18 }],
]

/** A change chart: negatives make it diverge around zero. */
export const GALLERY_CHANGE_BARS: (readonly [string, number])[] = [
  ['payments', 12],
  ['reports', 7],
  ['search', 4],
  ['cart', -3],
  ['auth', -9],
]

/** Sixty bars: fifty are drawn and the chart states the page and the total. */
export const GALLERY_MANY_BARS: (readonly [string, number])[] = Array.from(
  { length: 60 },
  (_, i) => [`suite-${String(i).padStart(3, '0')}`, 300 - i * 2] as const,
)

/** Six categories: past the pie limit, so the registry ranks them. */
export const GALLERY_SIX_CATEGORIES: (readonly [string, number])[] = [
  ['PRODUCT_BUG', 46],
  ['INFRASTRUCTURE', 31],
  ['TEST_CODE', 22],
  ['DATA', 14],
  ['TIMEOUT', 9],
  ['UNKNOWN', 5],
]
/** Four: at the limit, so the registry draws the donut. */
export const GALLERY_FOUR_CATEGORIES = GALLERY_SIX_CATEGORIES.slice(0, 4)

/** A bar whose category name is markup: it must be drawn as literal text. */
export const GALLERY_HOSTILE_BARS: (readonly [string, number])[] = [
  [HOSTILE_LABEL, 9],
  ['benign', 4],
]

/**
 * VIZ-606: test names a spreadsheet would EVALUATE — one per character OWASP
 * lists (`=`, `+`, `-`, `@`). The chart draws them as text like any name; the
 * exported CSV must hand each one over inert (a leading `'`), while the
 * negative-looking VALUES stay numbers. Ranked by value, so their order is fixed.
 */
export const GALLERY_FORMULA_BARS: (readonly [string, number])[] = [
  ['=HYPERLINK("https://example.test","open")', 12],
  ['+1+1', 9],
  ['-2+3', 6],
  ['@SUM(A1:A2)', 3],
]

/**
 * Wave 2.4 review A6/F9 and A11: test names that get past a FIRST-character
 * check. One hides a formula after a `;` — the list separator Excel splits a
 * double-clicked `.csv` on in de-DE, fr-FR and most of continental Europe, so
 * `=1+2` becomes a cell of its own there; one starts with a FULL-WIDTH `＝`
 * (U+FF1D), which input normalisation folds to `=`. The exported CSV must
 * hand both over inert: the first as `x;'=1+2` (quoted), the second as
 * `'＝SUM(A1:A2)`. Ranked by value, so their order is fixed.
 */
export const GALLERY_CSV_LOCALE_BARS: (readonly [string, number])[] = [
  ['x;=1+2', 8],
  ['＝SUM(A1:A2)', 5],
]

/** A stacked bar whose SUITE is markup (VIZ-601 scenario 3): two statuses, so each bar has two segments. */
export const GALLERY_HOSTILE_SUITE_STATUS: (readonly [string, GalleryStatusCounts])[] = [
  [HOSTILE_LABEL, { passed: 12, failed: 3 }],
  ['benign', { passed: 9, failed: 1 }],
]

// -- Wave 2 - the VIZ-403 time series and the VIZ-406 duration charts --------
//
// Their MODELS live in `src/components/charts/__fixtures__/wave2Fixtures`, that
// story's own fixture module, and are deliberately NOT imported here: this file
// is imported by the Playwright specs, which run in plain Node with no `@/`
// alias, and `durationBuckets` value-imports `@/utils/formatters`. So an item
// names its fixture with a `fixture` key and `ChartGalleryPage` resolves it in
// an exhaustive switch — a renamed or deleted fixture is a type error there,
// not a blank box in the gallery.

/**
 * The fixture keys `ChartGalleryPage` maps to the VIZ-403 time-series models,
 * and to the two VIZ-405 trend-overlay ones (`trend-analysis*`).
 */
export type GalleryTimeSeriesFixture =
  | 'trend-with-releases'
  | 'trend-single-point'
  | 'trend-zoomed-axis'
  | 'trend-analysis'
  | 'trend-analysis-sparse'
  // VIZ-407: 42 days with trend statistics and releases either side of a zoom.
  | 'trend-zoom-releases'
  // VIZ-601: the headline trend, its release renamed to `HOSTILE_LABEL`.
  | 'trend-hostile-release'
/** The fixture keys for the VIZ-406 duration histogram. */
export type GalleryHistogramFixture = 'duration-histogram' | 'duration-histogram-empty'

// -- Wave 2.5 (VIZ-104) - the kit pieces the production pages moved onto -------
//
// `StackedColumnChart` and `DayStrip` keep their fixtures beside the component
// (`components/charts/__fixtures__/stackedColumn.ts` and `dayStrip.ts`, read by
// their unit tests too); those modules import app code, so an item names its
// fixture by key and `ChartGalleryPage` resolves it, as for the time series.
// `Sparkline`, `GaugeBar` and `RingGauge` take a handful of literals, spelt out
// here. A format or a tone RULE is named by key for the same reason: the
// formatter and `bandsTone` are app code.

/** The fixture keys `ChartGalleryPage` maps to `__fixtures__/stackedColumn.ts`. */
export type GalleryStackedFixture =
  | 'stacked-status-daily'
  | 'stacked-series-monthly'
  | 'stacked-hostile-labels'
  | 'stacked-single-bucket'
  | 'stacked-long-window'
  | 'stacked-many-categories'

/** The fixture keys `ChartGalleryPage` maps to `DAY_STRIP_FIXTURES` (`__fixtures__/dayStrip.ts`). */
export type GalleryDayStripFixture =
  | 'day-strip-presence'
  | 'day-strip-intensity'
  | 'day-strip-severity'
  | 'day-strip-compact'
  | 'day-strip-builds'
  | 'day-strip-dense'
  | 'day-strip-hostile'

// -- Wave 2.6 (VIZ-408) - the heatmap frame and prototype-member names --------
//
// `HeatmapChartFrame`'s fixtures live beside it (`__fixtures__/heatmapFrame.ts`,
// read by its unit tests too) and import app code, so an item names its
// fixture by key and `ChartGalleryPage` resolves it, as for the stacked
// columns. Each one is an `/analytics/heatmap` matrix exactly as the page
// receives it (percent points with their `unit`, keys, counts, the server's
// failures order); there is no `__other__` row.

/** The fixture keys `ChartGalleryPage` maps to `__fixtures__/heatmapFrame.ts`. */
export type GalleryHeatmapFrameFixture =
  | 'heatmap-frame'
  | 'heatmap-frame-90d'
  | 'heatmap-frame-hostile'
  // Wave 3 (FK1): a test x run status matrix, the edge cases, and fit-to-data colour.
  | 'heatmap-frame-status'
  | 'heatmap-frame-edges'
  | 'heatmap-frame-fit'

/** The heatmap frame's plot height: the Trends section's own (`TrendsCatalogue`), so the gallery shows what the page draws. */
export const GALLERY_HEATMAP_FRAME_PLOT_HEIGHT = 320
/** The heatmap frame's box (see the items): measured 432 px under Linux fonts, plus two wrapped lines. */
const GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT = 480

// -- Wave 3 (PR-B) - the coverage treemap, the test scatter, failure groups --
//
// Their fixtures live beside the kit too (`__fixtures__/coverageMap.ts`,
// `__fixtures__/testScatter.ts`, `failureGroups/failureGroups.fixtures.ts`)
// and import app code, so an item names its fixture by key (or, for failure
// groups, by the options of the fixture builder) and `ChartGalleryPage`
// resolves it. Each item draws its frame the way its catalogue section does,
// from a settled state and with none of the section's data hooks.

/** The fixture keys `ChartGalleryPage` maps to `__fixtures__/coverageMap.ts`. */
export type GalleryCoverageMapFixture =
  | 'coverage-suites'
  | 'coverage-payments-classes'
  | 'coverage-hostile'
  | 'coverage-one-test'
  | 'coverage-empty'

/** The fixture keys `ChartGalleryPage` maps to `__fixtures__/testScatter.ts`. */
export type GalleryScatterFixture = 'scatter-default' | 'scatter-dense' | 'scatter-hostile' | 'scatter-all-excluded'

/**
 * The plot heights the sections draw at (`COVERAGE_MAP_HEIGHT`,
 * `SCATTER_HEIGHT`, `FailureGroupsFrame`'s default), spelt out because this
 * file cannot import them; a unit test holds each equal to the section's.
 */
export const GALLERY_COVERAGE_MAP_PLOT_HEIGHT = 360
export const GALLERY_SCATTER_PLOT_HEIGHT = 320
export const GALLERY_FAILURE_GROUPS_PLOT_HEIGHT = 360

// The boxes, MEASURED at 640 px wide in Chromium (docs/viz-work/w3/i-g, `measure.spec.ts`), twice: with
// the machine's fonts and with every text forced to a DejaVu-wide face (Verdana stands in on Windows; it
// reproduces the Linux 432 px of the Wave 2.6 heatmap frame). The box is the wide-font height plus room
// for one more wrapped line (a footer sentence or a long title), so CI's fonts never spill into the next
// item. Wide-font frame heights: treemap 598-638 (empty level 485), scatter 559-615 (every test left out
// 448), failure groups 908-927 with 8 short names, 1,059 hostile and 1,071 with 200 groups (the table's
// long names wrap).
const GALLERY_COVERAGE_MAP_CANVAS_HEIGHT = 680
// The empty states draw one sentence in a 120 px body since the R2-B fixes (X2/X3/X4, F-14): re-measured by I,
// final round, at 245 (coverage level), 212 (every test left out) and 248 px (no cluster), both font set-ups.
const GALLERY_COVERAGE_MAP_EMPTY_CANVAS_HEIGHT = 320
const GALLERY_SCATTER_CANVAS_HEIGHT = 660
const GALLERY_SCATTER_EXCLUDED_CANVAS_HEIGHT = 290
const GALLERY_FAILURE_GROUPS_CANVAS_HEIGHT = 970
const GALLERY_FAILURE_GROUPS_TALL_CANVAS_HEIGHT = 1110
/**
 * The clusters tab's frame (plot, the two-cluster list, the identity note): 585 px with both font set-ups;
 * the empty answer's frame 248 px since X3. MEASURED as above (I, `i-g/measure.spec.ts`), plus room for a wrapped line.
 */
const GALLERY_CLUSTERS_CANVAS_HEIGHT = 660
const GALLERY_CLUSTERS_EMPTY_CANVAS_HEIGHT = 320

/** The plot height `SystemicClusters` draws at (its default), held equal to the component's by a unit test. */
export const GALLERY_CLUSTERS_PLOT_HEIGHT = 300

/** The clusters bodies `ChartGalleryPage` builds from `clustersBody` (FK3's fixtures). */
export type GalleryClustersFixture = 'clusters' | 'clusters-empty'

/**
 * Suite names that are also `Object.prototype` members. Names come from
 * ingested CI files, so these are real names, and a plain-object label lookup
 * (`labels[x] ?? x`) once read the inherited member instead: a bar labelled
 * "function Object() { [native code] }". Spelt out here (the kit's own copy,
 * `__fixtures__/protoKeyNames.ts`, is app code); a unit test holds the two equal.
 */
export const GALLERY_PROTOTYPE_NAMES = ['constructor', '__proto__', 'toString', 'hasOwnProperty'] as const

/** Failures by suite, one ranked bar per name, with NO `x_labels`: every label lookup falls through to the name. */
export const GALLERY_PROTOTYPE_BARS: GalleryCategorySeries = gallerySeries(
  GALLERY_PROTOTYPE_NAMES.map((name, i) => [name, 12 - i * 3] as const),
  'suite',
  'failures',
  'Failures',
)

/** A value format the page resolves: `percent` is `formatPercent`, `number` is `formatGaugeNumber`. */
export type GalleryFormat = 'percent' | 'number'

/** What a `Sparkline` item draws: the component's props, and the cell widths it is drawn at. */
export interface GallerySparkline {
  series: (number | null)[]
  label: string
  tone?: SparklineTone
  domain?: [number, number]
  area?: boolean
  format?: GalleryFormat
  /** One cell per width (px), side by side: the stretch and the end-dot inset at two sizes. */
  widths: number[]
}

/** A `GaugeBar`'s props, less the functions (named by key instead). */
export interface GalleryGaugeBar extends Omit<GaugeBarProps, 'format' | 'className'> {
  format?: GalleryFormat
  /** The box the bar is drawn in (px): its measured tick row lays out against this width. */
  box: number
}

/** A `RingGauge`: its value, its caption, and its tone as bands (`bandsTone`). */
export interface GalleryRingGauge {
  value: number | null
  caption: string
  bands: { direction: 'higher-is-better' | 'lower-is-better'; thresholds: readonly [number, number] }
  format: GalleryFormat
}

/** The pass-rate thresholds of the IntelligenceHub meter and the risk bands of the release gate. */
const HUB_PASS_RATE_BANDS = { direction: 'higher-is-better', thresholds: [60, 80] } as const
const GATE_RISK_BANDS = { direction: 'lower-is-better', thresholds: [40, 70] } as const
/** The health scale the verdict meters share (Runs, Defects, Trends, Coverage, FailureAnalysis). */
const HEALTH_TICKS = [{ value: 0, label: 'Blocked' }, { value: 33, label: 'At risk' }, { value: 66, label: 'Stable' }, { value: 100 }]
/** RunIntelligence's risk scale. */
const RISK_TICKS = [{ value: 0, label: 'Safe' }, { value: 30, label: 'Conditional' }, { value: 70, label: 'Block' }, { value: 100 }]
/** The md bar's box and the sm bar's (a KPI cell). */
const GAUGE_MD_BOX = 320
const GAUGE_SM_BOX = 160
/**
 * A stacked-column frame's box. MEASURED under Linux fonts: the tallest of
 * the five frames (the daily one, whose gap note adds a line under the
 * legend) is 348 px at 640 wide; 440 leaves room for a title or note that
 * wraps onto more lines on another runner, as the zoom items' do.
 */
const STACKED_CANVAS_HEIGHT = 440
/**
 * The bar form of a stacked chart grows with its rows (`BAR_ROW_HEIGHT` each):
 * 16 suites are a 456 px plot, plus the frame's title, toolbar and notes.
 */
const STACKED_BARS_CANVAS_HEIGHT = 640
/** A DayStrip across the plot canvas, and across a phone (SC 1.4.10: its legend wraps, it never overflows). */
const DAY_STRIP_BOX = GALLERY_CANVAS.width
const DAY_STRIP_NARROW_BOX = 320

/**
 * The instant the gallery tells `TimeSeriesChart` "now" is, and the zone it
 * resolves UTC buckets into.
 *
 * Both are FIXED, and both have to be: `utcTodayNote` compares the newest
 * bucket against the real clock, and the tooltip's "your local equivalent" row
 * is computed in the viewer's own zone. Left to the machine, the same item
 * would render one way on a CI runner in UTC and another on a laptop in IST,
 * and the note would appear or vanish depending on the DAY the baseline was
 * taken. Neither is a regression, and both would flap the screenshots.
 */
export const GALLERY_NOW = new Date('2026-03-10T09:00:00Z')
export const GALLERY_TIME_ZONE = 'UTC'
/** Fixed too: an unset locale would take the machine's, and month names differ. */
export const GALLERY_LOCALE = 'en-US'

// -- Wave 2 - the VIZ-404 multi-series comparison -----------------------------
//
// C3 series spelt out, as everywhere else in this file, and built by
// `ChartGalleryPage` into the model the chart draws. The days are LITERALS
// (UTC, before `GALLERY_NOW`): the chart reads no clock and no zone of its own,
// so nothing here can move with the machine or the day the baseline ran.

/** One C3 point (`SeriesPoint`), spelt out. */
export interface GallerySeriesPoint {
  x: string
  y: number | null
  n: number
  measured?: boolean
  reason?: string | null
}

export interface GalleryComparisonSeries {
  key: string
  label: string
  points: GallerySeriesPoint[]
}

/** What a VIZ-404 gallery item hands `buildMultiSeriesModel`. */
export interface GalleryComparison {
  series: GalleryComparisonSeries[]
  metric: { kind: 'rate' | 'count'; title: string }
  alignment?: 'absolute' | 'release-start'
  /**
   * The envelope's `meta.comparability`, in the API's own wire shape — the
   * gallery hands it to the model exactly as `chart-data` would.
   */
  comparability?: GalleryComparability
  seriesNoun: string
  /** Series hidden when the item first draws (the legend-toggle edge case). */
  initialHidden?: string[]
}

/** `YYYY-MM-DD`, `offset` UTC days after `from`. Pure arithmetic on a literal: no clock. */
export function galleryDay(from: string, offset: number): string {
  const at = Date.parse(`${from}T00:00:00Z`) + offset * 86_400_000
  return new Date(at).toISOString().slice(0, 10)
}

const COMPARISON_START = '2026-02-24'
const COMPARISON_DAYS = 14

/** A rate series from literal values; `n` is the executions behind each day. */
function rateSeries(key: string, values: readonly (number | null)[], n: number, from = COMPARISON_START): GalleryComparisonSeries {
  return {
    key,
    label: key,
    points: values.map((y, i) =>
      y === null
        ? {
            x: galleryDay(from, i),
            y: null,
            n: 0,
            measured: false,
            reason: 'no evaluated executions in this bucket: every test was skipped',
          }
        : { x: galleryDay(from, i), y, n },
    ),
  }
}

export const GALLERY_RATE_METRIC = { kind: 'rate', title: 'Pass rate %' } as const
export const GALLERY_COUNT_METRIC = { kind: 'count', title: 'Executions' } as const

/**
 * The story's own three suites. payments and cart END within half a point of
 * each other, so their direct labels have to be nudged apart to be read.
 */
export const GALLERY_THREE_SUITES: GalleryComparisonSeries[] = [
  rateSeries('payments', [96.2, 97.0, 95.8, 96.4, 97.3, 96.9, 95.1, 96.0, 96.8, 97.2, 96.5, 95.9, 94.6, 93.8], 420),
  rateSeries('cart', [88.4, 89.9, 90.3, 88.7, 91.2, 92.0, 91.5, 90.8, 92.4, 93.1, 92.7, 93.5, 94.0, 94.3], 310),
  rateSeries('search', [78.1, 80.4, 79.2, 82.6, 81.0, 83.3, 84.9, 83.8, 85.2, 84.1, 86.0, 85.4, 86.9, 87.3], 260),
]

/**
 * Twelve suites of daily executions: past the eight-line limit, so the 7 with
 * the most executions are drawn and the other 5 are folded into "Other".
 * Deterministic arithmetic, no randomness.
 */
export const GALLERY_TWELVE_SUITES: GalleryComparisonSeries[] = [
  'checkout',
  'auth',
  'search',
  'billing',
  'reports',
  'admin',
  'catalog',
  'profile',
  'ledger',
  'audit',
  'exports',
  'webhooks',
].map((key, s) => ({
  key,
  label: key,
  points: Array.from({ length: COMPARISON_DAYS }, (_, d) => {
    const y = 60 + (11 - s) * 22 + ((s * 7 + d * 13) % 17)
    return { x: galleryDay(COMPARISON_START, d), y, n: y }
  }),
}))

/**
 * search is not measured on four days: three the server sent as
 * `measured: false` with its reason, and one it never sent at all. Both are
 * gaps, never zeros — and day 7 is an ISOLATED measured day, drawn as a dot.
 */
const GAPPY_SEARCH = rateSeries('search', [78.1, 80.4, 79.2, null, null, 83.3, null, 83.8, 0, 84.1, 86.0, 85.4, 86.9, 87.3], 260)
export const GALLERY_GAPPY_SUITES: GalleryComparisonSeries[] = [
  GALLERY_THREE_SUITES[0],
  GALLERY_THREE_SUITES[1],
  // Day 8 is missing from the payload altogether (its placeholder is dropped here).
  { ...GAPPY_SEARCH, points: GAPPY_SEARCH.points.filter((point) => point.x !== galleryDay(COMPARISON_START, 8)) },
]

/** C2 `meta.comparability`, spelt out (the `Comparability` contract, without an app import). */
export interface GalleryComparability {
  comparable: boolean
  reason: string | null
  reason_code: 'different_suites' | 'partial_coverage' | null
}

/** Two branches whose suites differ: the envelope says `comparable: false`. */
export const GALLERY_BRANCHES: GalleryComparisonSeries[] = [
  rateSeries('main', [95.1, 95.4, 94.8, 95.9, 96.2, 95.7, 96.4, 96.0, 96.8, 97.1, 96.6, 97.0, 97.4, 97.2], 520),
  rateSeries('release/2.4', [91.2, 90.4, 92.1, 91.7, 90.9, 92.8, 93.0, 92.2, 93.5, 94.1, 93.6, 94.4, 94.0, 94.9], 380),
]
/**
 * What `chart-data` says about them, word for word as `judge_comparability`
 * (backend/app/services/chart_data_service.py) builds it for two branches
 * whose suites differ: COUNTS only, never a suite or branch name — a name in
 * the banner would be text the caller's scope did not produce. 14 suites ran
 * on one branch or the other, 12 on both: release/2.4 ran 2 that main did not.
 */
export const GALLERY_NOT_COMPARABLE: GalleryComparability = {
  comparable: false,
  reason:
    'The 2 series compared by branch did not run the same suites in this scope: 14 suites ran in at least one of them, 12 in all of them.',
  reason_code: 'different_suites',
}

/**
 * Two releases on the CALENDAR: R1 from 2 Feb, R2 from 20 Feb. Aligned on
 * "days since release start", both begin at day 0.
 */
export const GALLERY_RELEASES: GalleryComparisonSeries[] = [
  { ...rateSeries('R1', [71.0, 76.4, 80.2, 83.9, 86.1, 88.0, 89.4, 90.6, 91.1, 92.3, 92.0, 93.1], 300, '2026-02-02'), key: 'r1' },
  { ...rateSeries('R2', [79.5, 84.2, 87.9, 90.1, 91.8, 92.6, 93.9, 94.2, 95.0, 95.3], 340, '2026-02-20'), key: 'r2' },
]

/**
 * VIZ-601 scenario 3 on a comparison: a SERIES named with markup, beside a
 * benign one. The key stays plain (it is an id, not a name); the label is what
 * the legend, the direct label, the tooltip and the table all print.
 */
export const GALLERY_HOSTILE_SUITES: GalleryComparisonSeries[] = [
  { ...rateSeries('hostile', GALLERY_THREE_SUITES[0].points.map((point) => point.y), 420), label: HOSTILE_LABEL },
  GALLERY_THREE_SUITES[2],
]

/**
 * VIZ-407: the `zoom` a gallery item hands its frame — `ChartZoomOptions`,
 * spelt out (that module is not importable from plain Node). `initial` opens
 * the item ALREADY zoomed, so its baseline is the zoomed state and needs no
 * interaction; the days are literals, like every other day in this file.
 */
export interface GalleryZoom {
  initial?: { from: string; to: string }
  applyAsWindow?: { windowOptions: readonly number[] }
}

/** The report pages' window options (`REPORT_WINDOW_OPTIONS`), spelt out for the same reason. */
export const GALLERY_REPORT_WINDOW_OPTIONS: readonly number[] = [1, 7, 14, 30, 90]

interface GalleryItemBase {
  /** `data-gallery-item` value, and the screenshot's file name. */
  id: string
  title: string
  /** An explicit no-data item: baselines what "nothing to show" looks like. */
  empty: boolean
  /**
   * Fewest drawn marks (`path` / `rect` / `circle`) the functional spec accepts.
   * Conservative on purpose — a floor that catches a dropped series without
   * pinning Recharts' exact DOM. `0` for the empty items, which are not checked.
   */
  minMarks: number
}

export type GalleryItem =
  | (GalleryItemBase & { chart: 'trend'; variant: 'line' | 'area' | 'bar'; data: GalleryTrendPoint[] })
  | (GalleryItemBase & { chart: 'donut'; data: number[] })
  | (GalleryItemBase & { chart: 'gauge'; value: number })
  | (GalleryItemBase & { chart: 'heatmap'; data: GalleryMatrix | GalleryStatusMatrix; description: string })
  // Wave 2 (VIZ-401 / VIZ-402): whole charts, inside a real ChartFrame.
  | (GalleryItemBase & { chart: 'status-donut'; counts: GalleryStatusCounts; caption?: string })
  | (GalleryItemBase & {
      chart: 'bars'
      variant: 'ranked' | 'grouped' | 'stacked'
      data: GalleryCategorySeries
      dimension: string
      topN?: number
      initialMode?: 'absolute' | 'percent'
      /**
       * A taller box than `GALLERY_FRAME_CANVAS`, for a bar chart whose plot
       * GROWS with its rows (a 50-bar page, grouped bars). MEASURED, like the
       * other canvases: too short and the frame spills over the next item.
       */
      canvasHeight?: number
    })
  | (GalleryItemBase & {
      chart: 'breakdown'
      data: GalleryCategorySeries
      dimension: string
      /** What the CALLER would like. The registry still decides. */
      preferred?: 'donut'
    })
  // Wave 2 (VIZ-403 / VIZ-406): whole charts again, inside a real ChartFrame.
  | (GalleryItemBase & {
      chart: 'time-series'
      fixture: GalleryTimeSeriesFixture
      /** Name the runs still executing on the partial day, in its tooltip. */
      inProgress?: boolean
      /**
       * VIZ-405: offer the trend overlays, with both the moving average and
       * the trend line starting ON. Left out, the frame is the VIZ-403 frame.
       */
      trendOverlays?: boolean
      /**
       * A taller box than `GALLERY_TALL_FRAME_CANVAS`, for a frame that carries
       * the trend controls above its plot and the statistics strip under it.
       * MEASURED, like the other canvases.
       */
      canvasHeight?: number
      /** VIZ-407: make the frame zoomable (and, with `initial`, open it zoomed). */
      zoom?: GalleryZoom
      /**
       * Hand the frame its fixture's envelope `meta` (project, suites, window,
       * totals, a fixed generated-at), so its footer and its exports (VIZ-606)
       * carry a real scope. Left out, the frame gets `meta: null` like every
       * other item, and exports say "Scope unavailable".
       */
      scoped?: boolean
      /** VIZ-104 K2: a dashed target line on the rate axis. Left out, the frame draws none. */
      rateTarget?: TimeSeriesRateTarget
    })
  | (GalleryItemBase & { chart: 'duration-histogram'; fixture: GalleryHistogramFixture })
  | (GalleryItemBase & { chart: 'duration-band'; zoom?: GalleryZoom; canvasHeight?: number })
  | (GalleryItemBase & { chart: 'slowest-tests' })
  // Wave 2 (VIZ-404): a whole chart again, inside a real ChartFrame.
  | (GalleryItemBase & {
      chart: 'multi-series'
      comparison: GalleryComparison
      /** MEASURED, like the other framed canvases: too short and the frame spills over the next item. */
      canvasHeight: number
      /** VIZ-407: make the frame zoomable (and, with `initial`, open it zoomed). */
      zoom?: GalleryZoom
    })
  // Wave 2.5 (VIZ-104): the kit pieces the production pages moved onto.
  | (GalleryItemBase & {
      chart: 'stacked-column'
      fixture: GalleryStackedFixture
      /** What one column is ("day", "month"): the frame's summary and keyboard speak it. */
      bucketNoun?: string
      /** MEASURED, like the other framed canvases. */
      canvasHeight: number
    })
  | (GalleryItemBase & { chart: 'sparkline'; sparkline: GallerySparkline })
  | (GalleryItemBase & { chart: 'gauge-bar'; gauge: GalleryGaugeBar })
  | (GalleryItemBase & { chart: 'ring-gauge'; ring: GalleryRingGauge })
  | (GalleryItemBase & {
      chart: 'day-strip'
      fixture: GalleryDayStripFixture
      /** The strip's box (px): a card's width, or a phone's. */
      box: number
    })
  // Wave 2.6 (VIZ-408 K5): the suite x day heatmap inside a real ChartFrame.
  | (GalleryItemBase & {
      chart: 'heatmap-frame'
      fixture: GalleryHeatmapFrameFixture
      /** MEASURED, like the other framed canvases. */
      canvasHeight: number
      /** Wave 3: start with the colour scale fitted to the data. Left out, the fixed scale. */
      fit?: boolean
      /** Wave 3: what a row and a column are. Left out, suites and days. */
      nouns?: HeatmapNouns
      /** Wave 3: the axis titles. Left out, the frame's own. */
      rowAxis?: string
      columnAxis?: string
    })
  // Wave 3 (VIZ-502): one level of the coverage map, as `CoverageMapSection` draws it.
  | (GalleryItemBase & {
      chart: 'coverage-map'
      fixture: GalleryCoverageMapFixture
      /** The measure the map starts coloured by (the toolbar can change it). */
      colorBy: CoverageColorBy
      /** MEASURED, like the other framed canvases. */
      canvasHeight: number
    })
  // Wave 3 (VIZ-506): the test scatter, as `ScatterSection` draws it.
  | (GalleryItemBase & {
      chart: 'scatter'
      fixture: GalleryScatterFixture
      /** MEASURED, like the other framed canvases. */
      canvasHeight: number
    })
  // Wave 3 (VIZ-504): failure groups, as `FailureGroupsSection`'s groups tab draws them.
  | (GalleryItemBase & {
      chart: 'failure-groups'
      /** The options `failureGroupsResponse` builds the body from. */
      groups: FailureGroupsFixtureOptions
      /** The first view. Left out, the bubbles. */
      view?: FailureGroupsView
      /** MEASURED, like the other framed canvases. */
      canvasHeight: number
    })
  // Wave 3 (VIZ-504 / VIZ-207): the "Systemic flake clusters" tab, from a settled state (no fetch).
  | (GalleryItemBase & {
      chart: 'systemic-clusters'
      fixture: GalleryClustersFixture
      /** MEASURED, like the other framed canvases. */
      canvasHeight: number
    })

/**
 * Which engine draws a gallery item, and therefore what a spec may assert on:
 * Recharts → an SVG with countable marks, ECharts → a painted canvas, and
 * `dom` → plain elements.
 *
 * `slowest-tests` is the `dom` one: it is a ranked LIST whose bars are `<span>`s
 * with a percentage width, not a chart engine at all. Counting `path|rect|circle`
 * inside a `.recharts-surface` would find no svg and fail on it, so it is
 * checked by its own rows instead (see the chart-gallery spec).
 */
export function galleryEngine(item: GalleryItem): 'recharts' | 'echarts' | 'dom' {
  switch (item.chart) {
    case 'heatmap':
    case 'heatmap-frame':
      return 'echarts'
    // Wave 3: canvases too, unless the item has nothing to draw. An empty
    // level and a scatter whose every test was left out mount NO engine (the
    // frame says why in words), so there is no canvas to wait for.
    case 'coverage-map':
    case 'scatter':
      return item.empty ? 'dom' : 'echarts'
    // Wave 2.5: the sparkline is a hand-drawn svg (no Recharts surface), and
    // the gauge bar and the day strip are plain elements. Wave 3: failure
    // groups are React SVG circles, laid out by d3 but drawn by no engine.
    case 'slowest-tests':
    case 'sparkline':
    case 'gauge-bar':
    case 'day-strip':
    case 'failure-groups':
    case 'systemic-clusters':
      return 'dom'
    default:
      return 'recharts'
  }
}

/**
 * The drawn marks of a `dom` item, as a selector a spec can count and measure:
 * what `path, rect, circle` inside a Recharts surface is for the other items.
 * A mark counts when it has a box (a flat sparkline's line has no height, and
 * is still a drawn line). Its `minMarks` is how many of them must be drawn.
 */
export function galleryDomMarks(item: GalleryItem): string {
  switch (item.chart) {
    case 'slowest-tests':
      return '[data-testid="ranked-bar"]'
    case 'sparkline':
      return '[data-testid="sparkline"] [data-part]'
    case 'gauge-bar':
      // The reading, not the track: a track is drawn for an unmeasured value too.
      return '[data-gauge-fill], [data-gauge-segment], [data-gauge-marker], [data-gauge-target]'
    case 'day-strip':
      return '[data-day-cell]'
    case 'failure-groups':
    case 'systemic-clusters':
      // A group's (or cluster's) own circle, not the selection or focus rings drawn after it.
      return '[data-group-plot] [data-group-id] > circle:first-child'
    default:
      throw new Error(`${item.id} is drawn by ${galleryEngine(item)}, not the DOM`)
  }
}

/** Wave-2 items draw a whole `ChartFrame`, so they get a taller box. */
export function galleryFramed(item: GalleryItem): boolean {
  switch (item.chart) {
    case 'status-donut':
    case 'bars':
    case 'breakdown':
    case 'time-series':
    case 'duration-histogram':
    case 'duration-band':
    case 'slowest-tests':
    case 'multi-series':
    case 'stacked-column':
    case 'heatmap-frame':
    case 'coverage-map':
    case 'scatter':
    case 'failure-groups':
    case 'systemic-clusters':
      return true
    default:
      return false
  }
}

/**
 * `?canvas=<this>` on the gallery releases the pinned 640 px canvas, so a spec
 * can watch the SAME charts reflow inside a real 320 px frame. The pinned
 * canvas is what keeps the visual baselines comparable; it is also what hides
 * SC 1.4.10 from every spec that does not pass this.
 */
export const GALLERY_FLUID_CANVAS_PARAM = 'fluid'

/**
 * A framed item's heading is the FRAME's own — the gallery adds none of its
 * own above it, so a chart is not named twice. Level 2, under the page's h1.
 */
export const GALLERY_FRAME_HEADING_LEVEL = 2

/** A framed chart needs room for the header, the plot, the legend and the footer. */
export const GALLERY_FRAME_CANVAS = { width: 640, height: 460 } as const
/**
 * …and a VIZ-403 / VIZ-406 chart frame needs more of it: the time series
 * carries up to four notes under its plot (the UTC caption, the partial day,
 * the gap count and the releases outside the window) and the histogram carries
 * its excluded and placed counts.
 *
 * MEASURED on the rendered gallery at 640 px wide, not guessed — the box is a
 * fixed-height div with the item's border around it, so content taller than it
 * spills across the next item and into its screenshot. The tallest of the three
 * is the histogram at 412 px (the time series is 408); 520 leaves room for a
 * note that wraps onto a second line where a CI runner's font metrics differ.
 */
export const GALLERY_TALL_FRAME_CANVAS = { width: 640, height: 520 } as const
/**
 * `slowest-tests` is not a plot at all but 20 ranked rows, which measure 751 px
 * with the frame around them — nothing a plot height governs.
 */
export const GALLERY_LIST_CANVAS = { width: 640, height: 820 } as const
/** The Wave 2.5 kit pieces' canvases: see `galleryCanvasSize`. */
export const GALLERY_SPARKLINE_CANVAS_HEIGHT = 80
export const GALLERY_GAUGE_CANVAS_HEIGHT = 120
export const GALLERY_DAY_STRIP_CANVAS_HEIGHT = 160
/** The plot height inside a framed item. */
export const GALLERY_FRAME_PLOT_HEIGHT = 260

/** The box a gallery item is drawn in. */
export function galleryCanvasSize(item: GalleryItem): { width: number; height: number } {
  switch (item.chart) {
    case 'slowest-tests':
      return GALLERY_LIST_CANVAS
    case 'time-series':
      return item.canvasHeight ? { width: GALLERY_TALL_FRAME_CANVAS.width, height: item.canvasHeight } : GALLERY_TALL_FRAME_CANVAS
    case 'duration-band':
      return item.canvasHeight ? { width: GALLERY_TALL_FRAME_CANVAS.width, height: item.canvasHeight } : GALLERY_TALL_FRAME_CANVAS
    case 'duration-histogram':
      return GALLERY_TALL_FRAME_CANVAS
    case 'bars':
      return item.canvasHeight ? { width: GALLERY_FRAME_CANVAS.width, height: item.canvasHeight } : GALLERY_FRAME_CANVAS
    case 'multi-series':
    case 'stacked-column':
    case 'heatmap-frame':
    case 'coverage-map':
    case 'scatter':
    case 'failure-groups':
    case 'systemic-clusters':
      return { width: GALLERY_FRAME_CANVAS.width, height: item.canvasHeight }
    // Wave 2.5: a KPI-sized piece in a box of its own size, not the 320 px
    // plot canvas (MEASURED, like the rest: the tick row, the legend and a
    // wrapped legend line on a narrow strip all fit).
    case 'sparkline':
      return { width: GALLERY_CANVAS.width, height: GALLERY_SPARKLINE_CANVAS_HEIGHT }
    case 'gauge-bar':
      return { width: GALLERY_CANVAS.width, height: GALLERY_GAUGE_CANVAS_HEIGHT }
    case 'day-strip':
      return { width: item.box, height: GALLERY_DAY_STRIP_CANVAS_HEIGHT }
    default:
      return galleryFramed(item) ? GALLERY_FRAME_CANVAS : GALLERY_CANVAS
  }
}

export const GALLERY_ITEMS: GalleryItem[] = [
  {
    id: 'trend-line',
    title: 'TrendChart · line',
    chart: 'trend',
    variant: 'line',
    data: GALLERY_TREND_DATA,
    empty: false,
    minMarks: 3, // passed, failed, skipped (pass_rate is hidden)
  },
  {
    id: 'trend-area',
    title: 'TrendChart · area',
    chart: 'trend',
    variant: 'area',
    data: GALLERY_TREND_DATA,
    empty: false,
    minMarks: 2, // total, passed
  },
  {
    id: 'trend-bar',
    title: 'TrendChart · bar',
    chart: 'trend',
    variant: 'bar',
    data: GALLERY_TREND_DATA,
    empty: false,
    minMarks: GALLERY_TREND_DATA.length, // at least one segment per day
  },
  {
    id: 'defect-donut',
    title: 'DefectDonut',
    chart: 'donut',
    data: GALLERY_DEFECT_DATA,
    empty: false,
    minMarks: GALLERY_DEFECT_DATA.length, // one sector per priority
  },
  {
    id: 'pass-rate-gauge',
    title: 'PassRateGauge',
    chart: 'gauge',
    value: GALLERY_PASS_RATE,
    empty: false,
    minMarks: 2, // track + value arc
  },
  {
    id: 'heatmap',
    title: 'HeatmapChart · pass rate by suite and day',
    chart: 'heatmap',
    data: GALLERY_HEATMAP_DATA,
    description: 'Pass rate for 6 suites over 7 days; admin is lowest, auth highest.',
    empty: false,
    minMarks: GALLERY_HEATMAP_DATA.cells.length, // canvas: checked by pixels, not DOM marks
  },
  {
    id: 'heatmap-hostile-label',
    title: 'HeatmapChart · hostile label',
    chart: 'heatmap',
    data: GALLERY_HOSTILE_HEATMAP_DATA,
    description: 'Two cells whose labels contain markup; the markup must render as text.',
    empty: false,
    minMarks: GALLERY_HOSTILE_HEATMAP_DATA.cells.length,
  },
  {
    id: 'heatmap-status',
    title: 'HeatmapChart · status by suite and run',
    chart: 'heatmap',
    data: GALLERY_STATUS_MATRIX_DATA,
    description: 'Status of 3 suites over 6 runs; each status has its own pattern.',
    empty: false,
    minMarks: GALLERY_STATUS_MATRIX_DATA.cells.length,
  },
  {
    id: 'trend-empty',
    title: 'TrendChart · no data',
    chart: 'trend',
    variant: 'line',
    data: [],
    empty: true,
    minMarks: 0,
  },
  {
    id: 'defect-donut-empty',
    title: 'DefectDonut · no data',
    chart: 'donut',
    data: [0, 0, 0, 0],
    empty: true,
    minMarks: 0,
  },
  {
    id: 'pass-rate-gauge-empty',
    title: 'PassRateGauge · zero',
    chart: 'gauge',
    value: 0,
    empty: true,
    minMarks: 0,
  },
  {
    id: 'heatmap-empty',
    title: 'HeatmapChart · no data',
    chart: 'heatmap',
    data: { ...GALLERY_HEATMAP_DATA, cells: GALLERY_HEATMAP_DATA.cells.map((cell) => ({ ...cell, value: null })) },
    description: 'No measured cells.',
    empty: true,
    minMarks: 0,
  },
  // -- Wave 2 - VIZ-401: one item per donut edge case --------------------------
  {
    id: 'donut-status',
    title: 'DonutChart - status distribution',
    chart: 'status-donut',
    counts: GALLERY_DONUT_STATUS,
    caption: 'executions',
    empty: false,
    minMarks: 4, // one sector per status
  },
  {
    id: 'donut-status-unknown',
    title: 'DonutChart - unknown is the fifth slice',
    chart: 'status-donut',
    counts: GALLERY_DONUT_UNKNOWN,
    caption: 'executions',
    empty: false,
    minMarks: 5,
  },
  {
    id: 'donut-single-status',
    title: 'DonutChart - one status, a full ring',
    chart: 'status-donut',
    counts: GALLERY_DONUT_SINGLE,
    caption: 'executions',
    empty: false,
    minMarks: 1,
  },
  {
    id: 'donut-tiny-slice',
    title: 'DonutChart - a slice under 2%',
    chart: 'status-donut',
    counts: GALLERY_DONUT_TINY,
    caption: 'executions',
    empty: false,
    minMarks: 2, // the tiny slice keeps a visible arc
  },
  {
    id: 'donut-all-zero',
    title: 'DonutChart - all zero (filtered empty)',
    chart: 'status-donut',
    counts: GALLERY_DONUT_ZERO,
    empty: true,
    minMarks: 0,
  },

  // -- Wave 2 - VIZ-402: one item per bar edge case ----------------------------
  {
    id: 'bar-ranked',
    title: 'BarChart - top failing tests, ranked',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Test',
    data: gallerySeries(GALLERY_TOP_FAILING, 'test', 'failures', 'Failures'),
    empty: false,
    minMarks: GALLERY_TOP_FAILING.length,
  },
  {
    id: 'bar-ranked-ties',
    title: 'BarChart - ties at the top-10 boundary',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Test',
    data: gallerySeries(GALLERY_TIED_BARS, 'test', 'failures', 'Failures'),
    topN: GALLERY_TIE_TOP_N,
    empty: false,
    minMarks: 13, // 9 leaders + the four tied on the tenth place
  },
  {
    id: 'bar-long-names',
    title: 'BarChart - long test names, middle-truncated',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Test',
    data: gallerySeries(GALLERY_LONG_NAMES, 'test', 'failures', 'Failures'),
    empty: false,
    minMarks: GALLERY_LONG_NAMES.length,
  },
  {
    id: 'bar-diverging',
    title: 'BarChart - change, diverging around zero',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Suite',
    data: gallerySeries(GALLERY_CHANGE_BARS, 'suite', 'change', 'Change'),
    empty: false,
    minMarks: GALLERY_CHANGE_BARS.length,
  },
  {
    id: 'bar-paginated',
    title: 'BarChart - 60 bars, page 1 of 2',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Suite',
    data: gallerySeries(GALLERY_MANY_BARS, 'suite', 'failures', 'Failures'),
    // 50 rows at `MIN_BAR_ROW_HEIGHT` (20 px) make a 1 068 px plot; the frame
    // around it measures 1 170 px at 640 px wide.
    canvasHeight: 1220,
    empty: false,
    minMarks: 50, // the page cap
  },
  {
    id: 'bar-hostile-label',
    title: 'BarChart - a hostile category name',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Test',
    data: gallerySeries(GALLERY_HOSTILE_BARS, 'test', 'failures', 'Failures'),
    empty: false,
    minMarks: GALLERY_HOSTILE_BARS.length,
  },
  {
    id: 'bar-stacked',
    title: 'BarChart - results by suite, stacked',
    chart: 'bars',
    variant: 'stacked',
    dimension: 'Suite',
    data: galleryStatusRowsSeries(GALLERY_SUITE_STATUS),
    empty: false,
    minMarks: GALLERY_SUITE_STATUS.length * 4,
  },
  {
    id: 'bar-stacked-100',
    title: 'BarChart - results by suite, 100% stacked',
    chart: 'bars',
    variant: 'stacked',
    dimension: 'Suite',
    data: galleryStatusRowsSeries(GALLERY_SUITE_STATUS),
    initialMode: 'percent',
    empty: false,
    minMarks: GALLERY_SUITE_STATUS.length * 4,
  },
  {
    id: 'bar-grouped',
    title: 'BarChart - results by suite, grouped',
    chart: 'bars',
    variant: 'grouped',
    dimension: 'Suite',
    data: galleryStatusRowsSeries(GALLERY_SUITE_STATUS),
    // Five rows of four status bars, each thick enough for its pattern: a
    // 400 px plot in a frame measured at 476 px.
    canvasHeight: 520,
    empty: false,
    minMarks: GALLERY_SUITE_STATUS.length * 4,
  },

  // -- Wave 2 - the registry picks the chart type ------------------------------
  {
    id: 'breakdown-four-categories',
    title: 'Registry - 4 categories, a donut',
    chart: 'breakdown',
    dimension: 'Category',
    data: gallerySeries(GALLERY_FOUR_CATEGORIES, 'failure_category', 'failures', 'Failures'),
    empty: false,
    minMarks: GALLERY_FOUR_CATEGORIES.length,
  },
  {
    id: 'breakdown-six-categories',
    title: 'Registry - 6 categories, a ranked bar (no pie offered)',
    chart: 'breakdown',
    dimension: 'Category',
    data: gallerySeries(GALLERY_SIX_CATEGORIES, 'failure_category', 'failures', 'Failures'),
    // The caller asked for a donut; the registry overrules it.
    preferred: 'donut',
    empty: false,
    minMarks: GALLERY_SIX_CATEGORIES.length,
  },

  // -- Wave 2 - VIZ-403: the time series ---------------------------------------
  {
    id: 'timeseries-trend-releases',
    title: 'TimeSeriesChart · releases + partial day',
    chart: 'time-series',
    fixture: 'trend-with-releases',
    inProgress: true,
    empty: false,
    // The fixture is 10 UTC days, 2 of which had no runs at all (a weekend): 8
    // execution bars, and the rate line as at least one path. The 2 no-run days
    // draw no bar and break the line rather than bridging it, so 8 + 1 = 9.
    minMarks: 9,
  },
  {
    id: 'timeseries-single-point',
    title: 'TimeSeriesChart · one day',
    chart: 'time-series',
    fixture: 'trend-single-point',
    empty: false,
    // One bucket: its execution bar, plus the DOT the single-point case forces.
    // A line renderer draws no path at all for one point, so without the dot
    // this chart would be blank while holding data — which is what this floor
    // is here to catch.
    minMarks: 2,
  },
  {
    id: 'timeseries-zoomed-axis',
    title: 'TimeSeriesChart · zoomed rate axis',
    chart: 'time-series',
    fixture: 'trend-zoomed-axis',
    empty: false,
    // Four measured days: 4 execution bars (the rate line is on top of that).
    minMarks: 4,
  },

  // -- Wave 2 - VIZ-406: the duration charts -----------------------------------
  {
    id: 'duration-histogram',
    title: 'DurationHistogram · log buckets + overflow',
    chart: 'duration-histogram',
    fixture: 'duration-histogram',
    empty: false,
    // The fixture spans 0.4 ms to 42 minutes, so the 1-2-5 ladder runs 0.2ms …
    // 2s (12 finite buckets) and the 42-minute outlier lands in the overflow
    // bucket: 13 bars, every one of them non-empty. Floored at 6 — half of
    // them — so a narrowed ladder or a dropped overflow bucket fails while a
    // one-bucket shift in the fixture does not.
    minMarks: 6,
  },
  {
    id: 'duration-histogram-empty',
    title: 'DurationHistogram · nothing timed',
    chart: 'duration-histogram',
    fixture: 'duration-histogram-empty',
    empty: true,
    minMarks: 0,
  },
  {
    id: 'duration-band',
    title: 'DurationTrend · p50/p95 band',
    chart: 'duration-band',
    empty: false,
    // The band area, the p50 line and the p95 line. The unmeasured day splits
    // each of the three into two paths rather than bridging the gap, so 3 is a
    // floor that still fails if the band or either line stops being drawn.
    minMarks: 3,
  },
  {
    id: 'slowest-tests',
    title: 'SlowestTests · top 20 by p95',
    chart: 'slowest-tests',
    empty: false,
    // Its marks are `<div>` bars, not SVG (`galleryDomMarks`): one per row
    // of the top 20 the list shows (24 tests, capped at SLOWEST_TESTS_LIMIT).
    // Its ranking is asserted separately, on the bars themselves.
    minMarks: 20,
  },

  // -- Wave 2 - VIZ-404: one item per multi-series edge case --------------------
  //
  // Each `minMarks` is the number of LINES drawn (one path each); dots on an
  // isolated measured day come on top of that.
  {
    id: 'multi-series-three-suites',
    title: 'MultiSeriesChart · three suites',
    chart: 'multi-series',
    comparison: { series: GALLERY_THREE_SUITES, metric: GALLERY_RATE_METRIC, seriesNoun: 'suites' },
    canvasHeight: 490,
    empty: false,
    minMarks: 3,
  },
  {
    id: 'multi-series-folded',
    title: 'MultiSeriesChart · 12 suites, top 7 + Other',
    chart: 'multi-series',
    comparison: { series: GALLERY_TWELVE_SUITES, metric: GALLERY_COUNT_METRIC, seriesNoun: 'suites' },
    canvasHeight: 530,
    empty: false,
    minMarks: 8, // 7 kept + "Other"
  },
  {
    id: 'multi-series-gaps',
    title: 'MultiSeriesChart · a suite with unmeasured days',
    chart: 'multi-series',
    comparison: { series: GALLERY_GAPPY_SUITES, metric: GALLERY_RATE_METRIC, seriesNoun: 'suites' },
    canvasHeight: 510,
    empty: false,
    minMarks: 3,
  },
  {
    id: 'multi-series-not-comparable',
    title: 'MultiSeriesChart · not comparable',
    chart: 'multi-series',
    comparison: {
      series: GALLERY_BRANCHES,
      metric: GALLERY_RATE_METRIC,
      seriesNoun: 'branches',
      comparability: GALLERY_NOT_COMPARABLE,
    },
    canvasHeight: 530,
    empty: false,
    minMarks: 2,
  },
  {
    id: 'multi-series-release-aligned',
    title: 'MultiSeriesChart · release over release',
    chart: 'multi-series',
    comparison: { series: GALLERY_RELEASES, metric: GALLERY_RATE_METRIC, seriesNoun: 'releases', alignment: 'release-start' },
    canvasHeight: 510,
    empty: false,
    minMarks: 2,
  },
  {
    id: 'multi-series-hidden',
    title: 'MultiSeriesChart · one series hidden',
    chart: 'multi-series',
    comparison: { series: GALLERY_THREE_SUITES, metric: GALLERY_RATE_METRIC, seriesNoun: 'suites', initialHidden: ['cart'] },
    canvasHeight: 490,
    empty: false,
    minMarks: 2, // cart is hidden: two lines
  },

  // -- Wave 2 - VIZ-405: the trend overlays -------------------------------------
  //
  // The SAME time-series chart, with the trend controls above the plot, the
  // overlays drawn on it and the statistics strip under it — which is why each
  // has a measured `canvasHeight` of its own rather than the VIZ-403 box.
  {
    id: 'timeseries-trend-analysis',
    title: 'TimeSeriesChart · trend overlays + anomaly',
    chart: 'time-series',
    fixture: 'trend-analysis',
    trendOverlays: true,
    // Frame measured at 506 px (Chromium, 640 px, every theme); 600 leaves
    // room for the strip or a note to wrap onto another line under CI fonts.
    canvasHeight: 600,
    empty: false,
    // 30 UTC days, 3 of them run-free Saturdays: 27 execution bars, the rate
    // line, the two overlays and the card-coloured halo under each (one path
    // each), and the anomaly triangle = 33. The floor is the exact count, so a
    // dropped overlay OR a dropped halo fails it (at 30 it did not: an overlay
    // could go missing and the triangle kept the total up).
    minMarks: 33,
  },
  {
    id: 'timeseries-trend-insufficient',
    title: 'TimeSeriesChart · trend overlays unavailable',
    chart: 'time-series',
    fixture: 'trend-analysis-sparse',
    trendOverlays: true,
    // Frame measured at 435 px; the same headroom as above.
    canvasHeight: 520,
    empty: false,
    // 6 days with runs, none next to another: 6 execution bars, 6 rate DOTS
    // (an isolated day is a dot, since a line needs two neighbours) and the
    // rate line's own path = 13, measured. No overlay is drawn — below 7 days
    // with runs there is none to draw — so the floor is the exact count.
    minMarks: 13,
  },

  // -- Wave 2.4 - VIZ-601 scenario 3: a hostile name in every kind of chart ----
  //
  // `HOSTILE_LABEL` executes if anything renders it as markup. The heatmap and
  // the ranked bar already carry it; these four put it where the other charts
  // print a NAME — a donut slice, a stacked bar's category, a release marker and
  // a comparison series — for the spec that hovers, keys, tables and exports
  // each one (`tests/ci-e2e/chart-hostile-names.spec.ts`).
  {
    id: 'donut-hostile-label',
    title: 'Registry - a donut with a hostile category name',
    chart: 'breakdown',
    dimension: 'Category',
    data: gallerySeries(GALLERY_HOSTILE_BARS, 'failure_category', 'failures', 'Failures'),
    preferred: 'donut',
    empty: false,
    // Two categories, under the pie limit: the registry draws a donut, one sector each.
    minMarks: GALLERY_HOSTILE_BARS.length,
  },
  {
    id: 'bar-stacked-hostile-label',
    title: 'BarChart - a stacked bar with a hostile suite name',
    chart: 'bars',
    variant: 'stacked',
    dimension: 'Suite',
    data: galleryStatusRowsSeries(GALLERY_HOSTILE_SUITE_STATUS),
    empty: false,
    // Two suites × the two statuses they have (passed, failed): 4 segments.
    minMarks: GALLERY_HOSTILE_SUITE_STATUS.length * 2,
  },
  {
    id: 'timeseries-hostile-release',
    title: 'TimeSeriesChart · a hostile release name',
    chart: 'time-series',
    fixture: 'trend-hostile-release',
    empty: false,
    // `trend-with-releases`' own days: 8 execution bars and the rate line = 9.
    minMarks: 9,
  },
  {
    id: 'multi-series-hostile-label',
    title: 'MultiSeriesChart · a hostile series name',
    chart: 'multi-series',
    comparison: { series: GALLERY_HOSTILE_SUITES, metric: GALLERY_RATE_METRIC, seriesNoun: 'suites' },
    canvasHeight: 490,
    empty: false,
    minMarks: GALLERY_HOSTILE_SUITES.length, // one line per series
  },

  {
    id: 'bar-formula-names',
    title: 'BarChart - test names a spreadsheet would evaluate',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Test',
    data: gallerySeries(GALLERY_FORMULA_BARS, 'test', 'failures', 'Failures'),
    empty: false,
    minMarks: GALLERY_FORMULA_BARS.length, // one bar per name
  },
  {
    id: 'bar-csv-locale-names',
    // Plain text: a frame title is not Markdown, so code-span backticks were
    // drawn as literal characters (baseline review A, D3).
    title: 'BarChart - names a first-character check misses (; and full-width ＝)',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Test',
    data: gallerySeries(GALLERY_CSV_LOCALE_BARS, 'test', 'failures', 'Failures'),
    empty: false,
    minMarks: GALLERY_CSV_LOCALE_BARS.length, // one bar per name
  },

  // -- Wave 2.4 - VIZ-407: zoomable frames, opened ALREADY zoomed ---------------
  //
  // Each opens on `zoom.initial`, so its baseline is the zoomed state with no
  // interaction: the brush, Reset zoom, the footer's zoom note and — where a
  // release falls outside the view — the table's "(outside the zoomed view)".
  //
  // Their canvases are measured under the CI runner's fonts, not a
  // developer's: text there is WIDER (DejaVu Sans, what `system-ui` resolves
  // to on Ubuntu), so titles, the takeaway, the brush hint and the zoom note
  // wrap onto more lines. `timeseries-zoom-trend` measured 728 px on Windows
  // and 779 px on Linux, in a 760 px canvas sized from the Windows number: its
  // footer was scrolled out of the baseline (baseline review B). Each height
  // below is the Linux measurement plus 75-90 px, room for about four more
  // wrapped lines. The chart-gallery spec fails any item whose frame does not
  // fit its canvas.
  {
    id: 'timeseries-zoom-trend',
    title: 'TimeSeriesChart · zoomed, with trend statistics and releases',
    chart: 'time-series',
    fixture: 'trend-zoom-releases',
    trendOverlays: true,
    // 2026-03-15 to 2026-03-28 of the 42 days: the flagged 2026-03-23 and the
    // 2.1.0 release are in view; 2.0.0 (02-24) and 2.1.1 (03-29) are not.
    zoom: { initial: { from: '2026-03-15', to: '2026-03-28' } },
    // A real scope: the footer's "N of M", and exports stamped with the project,
    // the window and the zoom note (VIZ-606's "local zoom is reflected and stated").
    scoped: true,
    // Frame measured at 779 px under Linux fonts (728 on Windows): the trend
    // controls, the plot, the statistics strip, the brush, the window totals
    // and a three-line zoom note.
    canvasHeight: 860,
    empty: false,
    // 14 days in view, one of them a run-free Saturday (03-21): 13 execution
    // bars, the rate line, the moving average and the trend line over their
    // card-coloured halos (4), and the anomaly triangle = 13 + 1 + 4 + 1 = 19.
    minMarks: 19,
  },
  {
    id: 'timeseries-zoom-apply-disabled',
    title: 'TimeSeriesChart · zoomed off the latest day (Apply unavailable)',
    chart: 'time-series',
    fixture: 'trend-with-releases',
    // Seven days, but NOT ending on the latest (03-10): "Apply as time filter"
    // is offered disabled, with its reason on screen.
    zoom: {
      initial: { from: '2026-03-02', to: '2026-03-08' },
      applyAsWindow: { windowOptions: GALLERY_REPORT_WINDOW_OPTIONS },
    },
    // Frame measured at 591 px under Linux fonts (540 on Windows): the plot, the
    // brush with its disabled Apply and the reason under it, and the zoom note.
    canvasHeight: 680,
    empty: false,
    // 03-02..03-08 has runs on 03-02, 03-03, 03-06, 03-07 and 03-08 (03-04 and
    // 03-05 are the weekend with none): 5 execution bars and the rate line = 6.
    minMarks: 6,
  },
  {
    id: 'multi-series-zoom-hidden',
    title: 'MultiSeriesChart · zoomed, one series hidden',
    chart: 'multi-series',
    comparison: { series: GALLERY_THREE_SUITES, metric: GALLERY_RATE_METRIC, seriesNoun: 'suites', initialHidden: ['cart'] },
    zoom: { initial: { from: galleryDay(COMPARISON_START, 3), to: galleryDay(COMPARISON_START, 10) } },
    canvasHeight: 660, // frame measured at 584 px under Linux fonts (568 on Windows)
    empty: false,
    minMarks: 2, // cart is hidden: two lines, over the eight days in view
  },
  {
    id: 'duration-band-zoomed',
    title: 'DurationTrend · zoomed onto the inverted day',
    chart: 'duration-band',
    // 03-07..03-09 of the band's six days: the inverted 03-08 is in view (the
    // figure says "1 day"), the unmeasured 03-06 is not.
    zoom: { initial: { from: '2026-03-07', to: '2026-03-09' } },
    canvasHeight: 640, // frame measured at 552 px under Linux fonts (536 on Windows)
    empty: false,
    // No gap in view, so nothing is split: the range area draws its fill and
    // its two edges (3 paths — counted in Chromium, the unzoomed item's floor
    // of 3 predates that count) and each percentile line is one path = 5.
    minMarks: 5,
  },

  // -- Wave 2.5 - VIZ-104: the kit pieces the production pages moved onto ------
  //
  // Appended AFTER every earlier item, never inserted among them: an unframed
  // item's gallery heading is a fractional 18.56 px tall, so 24 of them placed
  // earlier moved every later item by a sub-pixel, and the zoom items' Linux
  // baselines re-antialiased (a 1 px shift, 17 433 pixels). The framed charts
  // come last, so `chart-fullscreen.spec.ts` still finds a framed chart as the
  // last item. Each piece gets the edge cases its builder named, one
  // hostile-name item for every piece that prints a name, and a strip at phone
  // width.
  //
  // Sparkline (K3), each drawn at two cell widths. A mark is the line, the
  // end dot, and the area when there is one, per cell.
  {
    id: 'sparkline-pass-rate',
    title: 'Sparkline · pass rate with an area, 0-100',
    chart: 'sparkline',
    sparkline: {
      series: [91.2, 93.5, 92.8, 95.1, 94.0, 96.3, 95.8, 97.2],
      label: 'Pass rate',
      tone: 'good',
      domain: [0, 100],
      area: true,
      format: 'percent',
      widths: [160, 96],
    },
    empty: false,
    minMarks: 6, // (area + line + end dot) x 2 cells
  },
  {
    id: 'sparkline-gaps',
    title: 'Sparkline · days without runs break the line',
    chart: 'sparkline',
    sparkline: {
      series: [88, 90, null, null, 86, 91, null, 93],
      label: 'Pass rate (days without runs)',
      tone: 'warn',
      domain: [0, 100],
      format: 'percent',
      widths: [160, 96],
    },
    empty: false,
    // (line + end dot) x 2 cells. The line is ONE path of three runs: two
    // points, two points, and the lone 93 drawn as a dot by its round cap.
    minMarks: 4,
  },
  {
    id: 'sparkline-flat',
    title: 'Sparkline · a flat series, drawn through the middle',
    chart: 'sparkline',
    sparkline: { series: [120, 120, 120, 120, 120], label: 'Executions', tone: 'neutral', widths: [160, 96] },
    empty: false,
    minMarks: 4, // (line + end dot) x 2 cells; the line has a width and no height
  },
  {
    id: 'sparkline-executions',
    title: 'Sparkline · executions, a measured 0 is a point',
    chart: 'sparkline',
    sparkline: {
      series: [410, 380, 512, 0, 455, 1284, 640],
      label: 'Executions',
      tone: 'accent',
      area: true,
      widths: [160, 96],
    },
    empty: false,
    minMarks: 6, // (area + line + end dot) x 2 cells
  },
  {
    id: 'sparkline-too-few',
    title: 'Sparkline · one point draws nothing (the caller keeps its caption)',
    chart: 'sparkline',
    sparkline: { series: [42], label: 'Executions', widths: [160] },
    empty: true,
    minMarks: 0,
  },

  // GaugeBar (K4). A mark is the reading: the fill, each segment, the marker,
  // the target. The track is drawn for an unmeasured value too, so it is not one.
  {
    id: 'gauge-bar-marker',
    title: 'GaugeBar · marker on the health gradient',
    chart: 'gauge-bar',
    gauge: {
      value: 72,
      label: 'Pipeline health',
      variant: 'marker',
      gradient: 'health',
      tone: 'good',
      ticks: HEALTH_TICKS,
      valueText: '72 of 100, Stable',
      box: GAUGE_MD_BOX,
    },
    empty: false,
    minMarks: 1, // the marker
  },
  {
    id: 'gauge-bar-fill-risk',
    title: 'GaugeBar · fill on the risk gradient',
    chart: 'gauge-bar',
    gauge: { value: 24, label: 'Composite risk score', gradient: 'risk', ticks: RISK_TICKS, box: GAUGE_MD_BOX },
    empty: false,
    minMarks: 1, // the fill
  },
  {
    id: 'gauge-bar-not-measured',
    title: 'GaugeBar · not measured (an empty track, never 0)',
    chart: 'gauge-bar',
    gauge: { value: null, label: 'Coverage health score', gradient: 'health', ticks: HEALTH_TICKS, box: GAUGE_MD_BOX },
    empty: true,
    minMarks: 0,
  },
  {
    id: 'gauge-bar-segments',
    title: 'GaugeBar · segments by severity',
    chart: 'gauge-bar',
    gauge: {
      value: 10,
      label: 'Proposed clusters by severity',
      size: 'sm',
      domain: [0, 10],
      segments: [
        { value: 2, label: 'P0', tone: 'bad' },
        { value: 3, label: 'P1', tone: 'warn' },
        { value: 4, label: 'P2', tone: 'accent' },
        { value: 1, label: 'P3', tone: 'neutral' },
      ],
      box: GAUGE_SM_BOX,
    },
    empty: false,
    minMarks: 4, // one per segment; the domain is their total, so all four are drawn
  },
  {
    id: 'gauge-bar-target',
    title: 'GaugeBar · a reading short of its target',
    chart: 'gauge-bar',
    gauge: {
      value: 55,
      label: 'Avg cluster confidence',
      size: 'sm',
      tone: { direction: 'higher-is-better', thresholds: [70, 80] },
      target: { value: 70, label: 'Target' },
      box: GAUGE_SM_BOX,
    },
    empty: false,
    minMarks: 2, // the fill and the target
  },
  {
    id: 'gauge-bar-clamped',
    title: 'GaugeBar · 130 on a 0-100 scale stops at the end',
    chart: 'gauge-bar',
    gauge: { value: 130, label: 'Monthly budget', tone: 'bad', box: GAUGE_MD_BOX },
    empty: false,
    minMarks: 1, // the fill, clamped to the track
  },
  {
    id: 'gauge-bar-inline',
    title: 'GaugeBar · a table-cell meter with its value',
    chart: 'gauge-bar',
    gauge: {
      value: 87,
      label: 'Pass rate',
      size: 'sm',
      thickness: 5,
      outlined: true,
      showValue: true,
      format: 'percent',
      tone: HUB_PASS_RATE_BANDS,
      box: GAUGE_SM_BOX,
    },
    empty: false,
    minMarks: 1, // the fill
  },
  {
    id: 'gauge-bar-hostile-label',
    title: 'GaugeBar · a hostile name in the label, a tick and the target',
    chart: 'gauge-bar',
    gauge: {
      value: 40,
      label: HOSTILE_LABEL,
      showLabel: true,
      tone: 'warn',
      ticks: [{ value: 0 }, { value: 50, label: HOSTILE_LABEL }, { value: 100 }],
      target: { value: 80, label: HOSTILE_LABEL },
      box: GAUGE_MD_BOX,
    },
    empty: false,
    minMarks: 2, // the fill and the target
  },

  // RingGauge (K4b), at the plot canvas's height as `PassRateGauge` is: a
  // mark is the ring's track and its value arc.
  {
    id: 'ring-gauge-risk',
    title: 'RingGauge · risk score, lower is better',
    chart: 'ring-gauge',
    ring: { value: 37, caption: 'Risk Score', bands: GATE_RISK_BANDS, format: 'number' },
    empty: false,
    minMarks: 2, // track + value arc
  },
  {
    id: 'ring-gauge-not-measured',
    title: 'RingGauge · not measured',
    chart: 'ring-gauge',
    ring: { value: null, caption: 'Risk Score', bands: GATE_RISK_BANDS, format: 'number' },
    empty: true,
    minMarks: 0,
  },
  {
    id: 'ring-gauge-hostile-label',
    title: 'RingGauge · a hostile caption',
    chart: 'ring-gauge',
    ring: { value: 62, caption: HOSTILE_LABEL, bands: GATE_RISK_BANDS, format: 'number' },
    empty: false,
    minMarks: 2, // track + value arc
  },

  // DayStrip (K5): a mark is a cell, one per day (or build) of the fixture.
  {
    id: 'day-strip-presence',
    title: 'DayStrip · presence (Trends run cadence)',
    chart: 'day-strip',
    fixture: 'day-strip-presence',
    box: DAY_STRIP_BOX,
    empty: false,
    minMarks: 30, // 30 days
  },
  {
    id: 'day-strip-presence-narrow',
    title: 'DayStrip · presence at phone width',
    chart: 'day-strip',
    fixture: 'day-strip-presence',
    box: DAY_STRIP_NARROW_BOX,
    empty: false,
    minMarks: 30, // the same 30 days, in narrower cells
  },
  {
    id: 'day-strip-intensity',
    title: 'DayStrip · intensity (Coverage run cadence)',
    chart: 'day-strip',
    fixture: 'day-strip-intensity',
    box: DAY_STRIP_BOX,
    empty: false,
    minMarks: 30, // 30 days
  },
  {
    id: 'day-strip-severity',
    title: 'DayStrip · status with severity (failure timeline)',
    chart: 'day-strip',
    fixture: 'day-strip-severity',
    box: DAY_STRIP_BOX,
    empty: false,
    minMarks: 30, // 30 days
  },
  {
    id: 'day-strip-compact',
    title: 'DayStrip · compact run strip, no legend',
    chart: 'day-strip',
    fixture: 'day-strip-compact',
    box: DAY_STRIP_BOX,
    empty: false,
    minMarks: 14, // the last 14 of the 30 days
  },
  {
    id: 'day-strip-builds',
    title: 'DayStrip · builds (build velocity)',
    chart: 'day-strip',
    fixture: 'day-strip-builds',
    box: DAY_STRIP_BOX,
    empty: false,
    minMarks: 14, // 9 builds, padded to 14 cells
  },
  {
    id: 'day-strip-dense',
    title: 'DayStrip · 90 days',
    chart: 'day-strip',
    fixture: 'day-strip-dense',
    box: DAY_STRIP_BOX,
    empty: false,
    minMarks: 90, // 90 days
  },
  {
    id: 'day-strip-hostile-label',
    title: 'DayStrip · hostile cell and strip names',
    chart: 'day-strip',
    fixture: 'day-strip-hostile',
    // Four square cells: across the 640 px canvas each would be 150 px tall.
    box: DAY_STRIP_NARROW_BOX,
    empty: false,
    minMarks: 4, // 4 builds, one per hostile name
  },

  // StackedColumnChart (K1): `minMarks` is the number of POSITIVE values in
  // the fixture. Recharts draws each stacked segment as one path; a 0 is a
  // path of no height, and a `null` (not measured) draws nothing at all.
  {
    id: 'stacked-status-daily',
    title: 'StackedColumnChart · executions by status, per day',
    chart: 'stacked-column',
    fixture: 'stacked-status-daily',
    bucketNoun: 'day',
    canvasHeight: STACKED_CANVAS_HEIGHT,
    empty: false,
    // 14 days x 4 statuses = 56 values, less 8 for Mar 4 (not measured at
    // all) and Mar 7 (ran, executed nothing: all four are 0), less 5 more
    // zeros (broken on Feb 26, Feb 28, Mar 6 and Mar 8; skipped on Mar 1)
    // and 1 missing value (skipped on Mar 9) = 42.
    minMarks: 42,
  },
  {
    id: 'stacked-series-monthly',
    title: 'StackedColumnChart · hours saved by model leg, per month',
    chart: 'stacked-column',
    fixture: 'stacked-series-monthly',
    bucketNoun: 'month',
    canvasHeight: STACKED_CANVAS_HEIGHT,
    empty: false,
    // 6 months x 3 legs = 18, less December's dedup leg (not measured) = 17.
    minMarks: 17,
  },
  {
    id: 'stacked-hostile-labels',
    title: 'StackedColumnChart · hostile, long, right-to-left and emoji names, every one on the axis',
    chart: 'stacked-column',
    fixture: 'stacked-hostile-labels',
    canvasHeight: STACKED_CANVAS_HEIGHT,
    empty: false,
    // 4 suites x 2 series = 8, less the 0 failed of the right-to-left suite = 7.
    // A category axis never drops a name: all four are drawn, slanted (the
    // long one cut in the middle), none only in the tooltip.
    minMarks: 7,
  },
  {
    id: 'stacked-single-bucket',
    title: 'StackedColumnChart · one day',
    chart: 'stacked-column',
    fixture: 'stacked-single-bucket',
    bucketNoun: 'day',
    canvasHeight: STACKED_CANVAS_HEIGHT,
    empty: false,
    // One day of 42 / 3 / 1 / 0: three segments (skipped is 0).
    minMarks: 3,
  },
  {
    id: 'stacked-long-window',
    title: 'StackedColumnChart · 60 days, thinned or slanted labels',
    chart: 'stacked-column',
    fixture: 'stacked-long-window',
    bucketNoun: 'day',
    canvasHeight: STACKED_CANVAS_HEIGHT,
    empty: false,
    // Day i of 0..59: passed (120 + ...) is always positive (60); failed
    // (7i mod 11) is 0 on the 6 days i = 0, 11, ... 55 (54); broken is 2 on
    // the 7 days i = 0, 9, ... 54 and 0 otherwise (7); skipped (3i mod 5)
    // is 0 on the 12 days i = 0, 5, ... 55 (48). 60 + 54 + 7 + 48 = 169.
    minMarks: 169,
  },
  // `rateTarget` (K2) on the VIZ-403 time series.
  {
    id: 'timeseries-rate-target',
    title: 'TimeSeriesChart · a 90% target on the rate axis',
    chart: 'time-series',
    fixture: 'trend-with-releases',
    rateTarget: { value: 90, label: 'Target 90%' },
    empty: false,
    // `trend-with-releases`' own days: 8 execution bars and the rate line = 9.
    // The target is a reference LINE (`<line>`), not a counted mark.
    minMarks: 9,
  },
  {
    id: 'timeseries-rate-target-off-axis',
    title: 'TimeSeriesChart · a target outside the zoomed rate axis',
    chart: 'time-series',
    fixture: 'trend-zoomed-axis',
    // The axis is zoomed onto 95-98 %: an 80 % target is not drawn, and a note says so.
    rateTarget: { value: 80, label: 'Target 80%' },
    empty: false,
    // Four measured days: 4 execution bars and the rate line = 5.
    minMarks: 5,
  },
  // Appended last, as every later wave's items are: an item inserted earlier
  // moves every item after it by a sub-pixel and breaks their baselines.
  {
    id: 'stacked-many-categories',
    title: 'StackedColumnChart · 16 suites, drawn as bars with every name',
    chart: 'stacked-column',
    fixture: 'stacked-many-categories',
    canvasHeight: STACKED_BARS_CANVAS_HEIGHT,
    empty: false,
    // 16 suites x 4 statuses = 64 values, of which 39 are positive: passed is
    // always positive (16); failed is 0 on suites 0, 4, 7, 8, 12 and 14 (10);
    // broken is 1 on suites 2, 7 and 12 (3); skipped ((2i) mod 3) is 0 on the
    // 6 suites i = 0, 3, ... 15 (10). 16 + 10 + 3 + 10 = 39.
    minMarks: 39,
  },
  // Wave 2.6 (VIZ-408): appended after every earlier item, for the reason above.
  // K5, the heatmap frame the Trends catalogue draws: the default 14-day
  // window, truncated to the top 7 of 12 suites and drawn worst first.
  // Box: MEASURED under Linux fonts, the 8-row frames are 432 px tall at 640
  // wide (the 3-row one 412); 480 leaves two lines for a footer that wraps.
  {
    id: 'heatmap-frame',
    title: 'HeatmapChartFrame - suite by day, worst first',
    chart: 'heatmap-frame',
    fixture: 'heatmap-frame',
    canvasHeight: GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT,
    empty: false,
    // 7 suites x 14 days = 98 cells (2 null, hatched);
    // canvas: checked by pixels, not DOM marks.
    minMarks: 98,
  },
  {
    id: 'heatmap-frame-90d',
    title: 'HeatmapChartFrame - 7 suites x 90 days',
    chart: 'heatmap-frame',
    fixture: 'heatmap-frame-90d',
    canvasHeight: GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT,
    empty: false,
    // The largest window a page can ask for: 7 rows x 90 days = 630 cells (cap 5,400).
    minMarks: 630,
  },
  {
    id: 'heatmap-frame-hostile',
    title: 'HeatmapChartFrame - hostile suite name',
    chart: 'heatmap-frame',
    fixture: 'heatmap-frame-hostile',
    canvasHeight: GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT,
    empty: false,
    // 3 suites x 7 days = 21 cells, one row named `HOSTILE_LABEL`.
    minMarks: 21,
  },
  // B1's own-property label fix: a suite named `constructor` is a bar named `constructor`.
  {
    id: 'bar-ranked-prototype-names',
    title: 'BarChart - suite names that are Object members',
    chart: 'bars',
    variant: 'ranked',
    dimension: 'Suite',
    data: GALLERY_PROTOTYPE_BARS,
    empty: false,
    // One bar per name.
    minMarks: GALLERY_PROTOTYPE_NAMES.length,
  },
  // Wave 3 (PR-B): appended after every earlier item, for the reason above.
  // FK1: the heatmap frame on the Suite detail page's test x run status
  // matrix, the edge cases in one week, and the colour scale fitted to the data.
  {
    id: 'heatmap-frame-status',
    title: 'HeatmapChartFrame - test by run, statuses',
    chart: 'heatmap-frame',
    fixture: 'heatmap-frame-status',
    nouns: { rows: ['test', 'tests'], columns: ['run', 'runs'] },
    rowAxis: 'Test',
    columnAxis: 'Build (oldest to newest)',
    canvasHeight: GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT,
    empty: false,
    // 4 tests x 8 runs = 32 cells (one did not run, hatched).
    minMarks: 32,
  },
  {
    id: 'heatmap-frame-edges',
    title: 'HeatmapChartFrame - all-null row, skipped-only cell, partial day, Object-member and long names',
    chart: 'heatmap-frame',
    fixture: 'heatmap-frame-edges',
    canvasHeight: GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT,
    empty: false,
    // 4 suites x 7 days = 28 cells, a whole row of them null.
    minMarks: 28,
  },
  {
    id: 'heatmap-frame-fit',
    title: 'HeatmapChartFrame - colour scale fitted to the data',
    chart: 'heatmap-frame',
    fixture: 'heatmap-frame-fit',
    fit: true,
    canvasHeight: GALLERY_HEATMAP_FRAME_CANVAS_HEIGHT,
    empty: false,
    // The 14-day default's 7 suites x 14 days = 98 cells.
    minMarks: 98,
  },
  // FK4 (VIZ-506): the test scatter, one point per test.
  {
    id: 'scatter',
    title: 'TestScatter - 300 tests, p95 duration by failure rate',
    chart: 'scatter',
    fixture: 'scatter-default',
    canvasHeight: GALLERY_SCATTER_CANVAS_HEIGHT,
    empty: false,
    // One point per test; canvas: checked by pixels.
    minMarks: 300,
  },
  {
    id: 'scatter-dense',
    title: 'TestScatter - 2,400 tests, past the dense threshold',
    chart: 'scatter',
    fixture: 'scatter-dense',
    canvasHeight: GALLERY_SCATTER_CANVAS_HEIGHT,
    empty: false,
    minMarks: 2400,
  },
  {
    id: 'scatter-hostile',
    title: 'TestScatter - hostile test names',
    chart: 'scatter',
    fixture: 'scatter-hostile',
    canvasHeight: GALLERY_SCATTER_CANVAS_HEIGHT,
    empty: false,
    minMarks: 10,
  },
  {
    // Not an empty response: every test was LEFT OUT, and the counts are the answer.
    id: 'scatter-all-excluded',
    title: 'TestScatter - every test left out',
    chart: 'scatter',
    fixture: 'scatter-all-excluded',
    canvasHeight: GALLERY_SCATTER_EXCLUDED_CANVAS_HEIGHT,
    empty: true,
    minMarks: 0,
  },
  // FK2 (VIZ-502): the coverage treemap, one level per item.
  {
    id: 'coverage-map-pass-rate',
    title: 'CoverageTreemap - pass rate, gaps and Other',
    chart: 'coverage-map',
    fixture: 'coverage-suites',
    colorBy: 'pass_rate',
    canvasHeight: GALLERY_COVERAGE_MAP_CANVAS_HEIGHT,
    empty: false,
    // 10 suites + Other = 11 rectangles.
    minMarks: 11,
  },
  {
    id: 'coverage-map-staleness',
    title: 'CoverageTreemap - days since last run',
    chart: 'coverage-map',
    fixture: 'coverage-suites',
    colorBy: 'staleness',
    canvasHeight: GALLERY_COVERAGE_MAP_CANVAS_HEIGHT,
    empty: false,
    minMarks: 11,
  },
  {
    id: 'coverage-map-flaky-share',
    title: 'CoverageTreemap - classes by flaky share',
    chart: 'coverage-map',
    fixture: 'coverage-payments-classes',
    colorBy: 'flaky_share',
    canvasHeight: GALLERY_COVERAGE_MAP_CANVAS_HEIGHT,
    empty: false,
    // 6 classes or files of one suite.
    minMarks: 6,
  },
  {
    id: 'coverage-map-hostile',
    title: 'CoverageTreemap - hostile suite names',
    chart: 'coverage-map',
    fixture: 'coverage-hostile',
    colorBy: 'pass_rate',
    canvasHeight: GALLERY_COVERAGE_MAP_CANVAS_HEIGHT,
    empty: false,
    minMarks: 6,
  },
  {
    id: 'coverage-map-empty',
    title: 'CoverageTreemap - an empty level',
    chart: 'coverage-map',
    fixture: 'coverage-empty',
    colorBy: 'pass_rate',
    canvasHeight: GALLERY_COVERAGE_MAP_EMPTY_CANVAS_HEIGHT,
    empty: true,
    minMarks: 0,
  },
  {
    id: 'coverage-map-one-test',
    title: 'CoverageTreemap - one suite, one test',
    chart: 'coverage-map',
    fixture: 'coverage-one-test',
    colorBy: 'pass_rate',
    canvasHeight: GALLERY_COVERAGE_MAP_CANVAS_HEIGHT,
    empty: false,
    minMarks: 1,
  },
  // FK3 (VIZ-504): failure groups, from `failureGroupsResponse`'s options.
  {
    id: 'failure-groups',
    title: 'FailureGroupsFrame - bubbles',
    chart: 'failure-groups',
    groups: {},
    canvasHeight: GALLERY_FAILURE_GROUPS_CANVAS_HEIGHT,
    empty: false,
    // The builder's default 8 groups, one circle each.
    minMarks: 8,
  },
  {
    id: 'failure-groups-related',
    title: 'FailureGroupsFrame - related groups',
    chart: 'failure-groups',
    groups: {},
    view: 'relations',
    canvasHeight: GALLERY_FAILURE_GROUPS_CANVAS_HEIGHT,
    empty: false,
    minMarks: 8,
  },
  {
    id: 'failure-groups-hostile',
    title: 'FailureGroupsFrame - hostile group names',
    chart: 'failure-groups',
    groups: { hostile: true },
    canvasHeight: GALLERY_FAILURE_GROUPS_TALL_CANVAS_HEIGHT,
    empty: false,
    minMarks: 8,
  },
  {
    id: 'failure-groups-giant',
    title: 'FailureGroupsFrame - one group holds most failures',
    chart: 'failure-groups',
    groups: { giant: true },
    canvasHeight: GALLERY_FAILURE_GROUPS_CANVAS_HEIGHT,
    empty: false,
    minMarks: 8,
  },
  {
    id: 'failure-groups-no-edges',
    title: 'FailureGroupsFrame - no related groups',
    chart: 'failure-groups',
    groups: { edges: false },
    canvasHeight: GALLERY_FAILURE_GROUPS_CANVAS_HEIGHT,
    empty: false,
    minMarks: 8,
  },
  {
    id: 'failure-groups-200',
    title: 'FailureGroupsFrame - 200 groups',
    chart: 'failure-groups',
    groups: { groups: 200 },
    canvasHeight: GALLERY_FAILURE_GROUPS_TALL_CANVAS_HEIGHT,
    empty: false,
    minMarks: 200,
  },
  // R6: the clusters tab, from `clustersBody` (two clusters, one named `constructor`, a hostile member name).
  {
    id: 'systemic-clusters',
    title: 'SystemicClusters - tests that fail together',
    chart: 'systemic-clusters',
    fixture: 'clusters',
    canvasHeight: GALLERY_CLUSTERS_CANVAS_HEIGHT,
    empty: false,
    // Two clusters, one circle each.
    minMarks: 2,
  },
  {
    id: 'systemic-clusters-empty',
    title: 'SystemicClusters - no clusters (an answer, not no data)',
    chart: 'systemic-clusters',
    fixture: 'clusters-empty',
    canvasHeight: GALLERY_CLUSTERS_EMPTY_CANVAS_HEIGHT,
    empty: true,
    minMarks: 0,
  },
]

export const GALLERY_ITEM_IDS: string[] = GALLERY_ITEMS.map((item) => item.id)

/** The `data-gallery-item` values of the items that must actually draw something. */
export const GALLERY_DRAWN_ITEMS: GalleryItem[] = GALLERY_ITEMS.filter((item) => !item.empty)

/**
 * Drawn items per engine — the specs wait on an svg for one, a painted canvas
 * for the next and plain elements for the last.
 *
 * Every drawn item is in exactly one of these three, and the spec ASSERTS that
 * (`GALLERY_DRAWN_ITEMS.length` against the three lengths). Without it, a new
 * engine value would put an item in none of them and it would be checked by
 * nothing while the suite stayed green.
 */
export const GALLERY_DRAWN_SVG_ITEMS: GalleryItem[] = GALLERY_DRAWN_ITEMS.filter((item) => galleryEngine(item) === 'recharts')
export const GALLERY_DRAWN_CANVAS_ITEMS: GalleryItem[] = GALLERY_DRAWN_ITEMS.filter((item) => galleryEngine(item) === 'echarts')
export const GALLERY_DRAWN_DOM_ITEMS: GalleryItem[] = GALLERY_DRAWN_ITEMS.filter((item) => galleryEngine(item) === 'dom')
