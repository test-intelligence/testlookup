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

/** Every chart is drawn inside a box of exactly this many CSS px. */
export const GALLERY_CANVAS = { width: 640, height: 320 } as const

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
]

export const GALLERY_ITEM_IDS: string[] = GALLERY_ITEMS.map((item) => item.id)

/** The `data-gallery-item` values of the items that must actually draw something. */
export const GALLERY_DRAWN_ITEMS: GalleryItem[] = GALLERY_ITEMS.filter((item) => !item.empty)
