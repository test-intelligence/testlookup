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
 */

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
/** The fixture keys for the VIZ-406 duration histogram. */
export type GalleryHistogramFixture = 'duration-histogram' | 'duration-histogram-empty'

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
  comparability?: { comparable: boolean; reason: string | null }
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

/** Two branches whose suites differ: the envelope says `comparable: false`. */
export const GALLERY_BRANCHES: GalleryComparisonSeries[] = [
  rateSeries('main', [95.1, 95.4, 94.8, 95.9, 96.2, 95.7, 96.4, 96.0, 96.8, 97.1, 96.6, 97.0, 97.4, 97.2], 520),
  rateSeries('release/2.4', [91.2, 90.4, 92.1, 91.7, 90.9, 92.8, 93.0, 92.2, 93.5, 94.1, 93.6, 94.4, 94.0, 94.9], 380),
]
export const GALLERY_NOT_COMPARABLE_REASON = 'release/2.4 ran 2 suites that main did not (ledger, audit)'

/**
 * Two releases on the CALENDAR: R1 from 2 Feb, R2 from 20 Feb. Aligned on
 * "days since release start", both begin at day 0.
 */
export const GALLERY_RELEASES: GalleryComparisonSeries[] = [
  { ...rateSeries('R1', [71.0, 76.4, 80.2, 83.9, 86.1, 88.0, 89.4, 90.6, 91.1, 92.3, 92.0, 93.1], 300, '2026-02-02'), key: 'r1' },
  { ...rateSeries('R2', [79.5, 84.2, 87.9, 90.1, 91.8, 92.6, 93.9, 94.2, 95.0, 95.3], 340, '2026-02-20'), key: 'r2' },
]

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
    })
  | (GalleryItemBase & { chart: 'duration-histogram'; fixture: GalleryHistogramFixture })
  | (GalleryItemBase & { chart: 'duration-band' })
  | (GalleryItemBase & { chart: 'slowest-tests' })
  // Wave 2 (VIZ-404): a whole chart again, inside a real ChartFrame.
  | (GalleryItemBase & {
      chart: 'multi-series'
      comparison: GalleryComparison
      /** MEASURED, like the other framed canvases: too short and the frame spills over the next item. */
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
  if (item.chart === 'heatmap') return 'echarts'
  if (item.chart === 'slowest-tests') return 'dom'
  return 'recharts'
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
/** The plot height inside a framed item. */
export const GALLERY_FRAME_PLOT_HEIGHT = 260

/** The box a gallery item is drawn in. */
export function galleryCanvasSize(item: GalleryItem): { width: number; height: number } {
  switch (item.chart) {
    case 'slowest-tests':
      return GALLERY_LIST_CANVAS
    case 'time-series':
      return item.canvasHeight ? { width: GALLERY_TALL_FRAME_CANVAS.width, height: item.canvasHeight } : GALLERY_TALL_FRAME_CANVAS
    case 'duration-histogram':
    case 'duration-band':
      return GALLERY_TALL_FRAME_CANVAS
    case 'bars':
      return item.canvasHeight ? { width: GALLERY_FRAME_CANVAS.width, height: item.canvasHeight } : GALLERY_FRAME_CANVAS
    case 'multi-series':
      return { width: GALLERY_FRAME_CANVAS.width, height: item.canvasHeight }
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
    // 0 because there are no SVG marks to count: this one renders `<div>` bars.
    // Its geometry is asserted separately, on the ranked bars themselves.
    minMarks: 0,
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
      comparability: { comparable: false, reason: GALLERY_NOT_COMPARABLE_REASON },
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
