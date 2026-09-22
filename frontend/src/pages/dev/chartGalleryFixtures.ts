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

/** Which engine draws a gallery item: Recharts → SVG, ECharts → canvas. */
export function galleryEngine(item: GalleryItem): 'recharts' | 'echarts' {
  return item.chart === 'heatmap' ? 'echarts' : 'recharts'
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
]

export const GALLERY_ITEM_IDS: string[] = GALLERY_ITEMS.map((item) => item.id)

/** The `data-gallery-item` values of the items that must actually draw something. */
export const GALLERY_DRAWN_ITEMS: GalleryItem[] = GALLERY_ITEMS.filter((item) => !item.empty)

/** Drawn items per engine — the specs wait on an svg for one and a painted canvas for the other. */
export const GALLERY_DRAWN_SVG_ITEMS: GalleryItem[] = GALLERY_DRAWN_ITEMS.filter((item) => galleryEngine(item) === 'recharts')
export const GALLERY_DRAWN_CANVAS_ITEMS: GalleryItem[] = GALLERY_DRAWN_ITEMS.filter((item) => galleryEngine(item) === 'echarts')
