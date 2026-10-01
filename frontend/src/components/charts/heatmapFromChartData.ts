/**
 * K5 (Wave 2.6): a `chart-data` day x series pass-rate response as the
 * heatmap's matrix — the suite x day heatmap on Trends, drawn from the SAME
 * response the multi-series chart above it reads (one request, two views).
 *
 * The server-side heatmap (VIZ-205 / VIZ-501, Wave 3) will order rows, cap
 * them and cover other axes itself; until then this adapter is the whole
 * pipeline, and when it lands only this function is replaced.
 *
 *   - **Units.** `chart-data` sends a pass rate in percentage POINTS (0..100);
 *     the heatmap reads a rate matrix as a RATIO (0..1: its ramp ends, tooltip
 *     and table all format it with `formatRateValue`). The contract validator
 *     accepts either, so a missing `/ 100` is not an error anywhere — it is a
 *     silent "9,640.0%". Divided here, once.
 *   - **Rows worst first**, by the n-weighted rate over the window (a 50% day
 *     with one execution does not outweigh a 90% day with fifty). A row with
 *     nothing measured is not "worst", it is unknown: it goes after the
 *     measured rows. The server's `__other__` remainder is always last, and
 *     labelled "Other".
 *   - **`null` stays `null`**: a day a suite did not run (or has no point for)
 *     is a no-data cell, drawn hatched — never the ramp's lowest colour.
 */
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import type { NumericMatrix } from './engines/echarts/heatmapOption'
import { OTHER_KEY, OTHER_LABEL } from './multiSeriesModel'

/** What the server's remainder row is called on the heatmap. */
export const OTHER_ROW_LABEL = OTHER_LABEL

export interface HeatmapRows {
  /** Named rows drawn (the server's top N; "Other" not counted). */
  shown: number
  /** How many there were in all (`meta.truncated_axes.series.total`), else `shown`. */
  total: number
  /** Whether an "Other" row is drawn. */
  other: boolean
}

export interface HeatmapFromChartData {
  matrix: NumericMatrix
  rows: HeatmapRows
}

interface Row {
  key: string
  label: string
  other: boolean
  /** n-weighted rate over the window, 0..100; `null` when nothing was measured. */
  rate: number | null
  byX: Map<string, { y: number | null; n: number }>
}

const isMeasured = (y: number | null): y is number => typeof y === 'number' && Number.isFinite(y)

/** The window's rate for one row: sum(y * n) / sum(n), or the plain mean if no point carries a sample size. */
function windowRate(points: readonly { y: number | null; n: number }[]): number | null {
  let weighted = 0
  let samples = 0
  let plain = 0
  let measured = 0
  for (const point of points) {
    if (!isMeasured(point.y)) continue
    measured += 1
    plain += point.y
    if (point.n > 0) {
      weighted += point.y * point.n
      samples += point.n
    }
  }
  if (measured === 0) return null
  return samples > 0 ? weighted / samples : plain / measured
}

const byLabel = (a: Row, b: Row) => (a.label < b.label ? -1 : a.label > b.label ? 1 : a.key < b.key ? -1 : a.key > b.key ? 1 : 0)

/** Worst (lowest rate) first; unmeasured rows after every measured one; ties alphabetical. */
function worstFirst(a: Row, b: Row): number {
  if (a.rate === null || b.rate === null) {
    if (a.rate === b.rate) return byLabel(a, b)
    return a.rate === null ? 1 : -1
  }
  return a.rate - b.rate || byLabel(a, b)
}

/** A C3 count (`n`): a whole number >= 0. */
const sampleSize = (n: number) => (Number.isFinite(n) ? Math.max(0, Math.round(n)) : 0)

export function heatmapFromChartData(chart: SeriesChart, meta: EnvelopeMeta | null): HeatmapFromChartData {
  // Columns: every x any series has, in calendar order for a time axis.
  const seen = new Set<string>()
  for (const line of chart.series) for (const point of line.points) seen.add(point.x)
  const xs = [...seen]
  if (chart.x_type === 'time') xs.sort()

  const rows: Row[] = chart.series.map((line) => {
    const other = line.key === OTHER_KEY
    return {
      key: line.key,
      label: other ? OTHER_ROW_LABEL : line.label || line.key,
      other,
      rate: windowRate(line.points),
      byX: new Map(line.points.map((point) => [point.x, { y: point.y, n: point.n }])),
    }
  })
  const named = rows.filter((row) => !row.other).sort(worstFirst)
  const ordered = [...named, ...rows.filter((row) => row.other)]

  const cells: NumericMatrix['cells'] = []
  ordered.forEach((row, y) => {
    xs.forEach((x, column) => {
      const point = row.byX.get(x)
      cells.push({
        x: column,
        y,
        // Percentage points -> ratio. Anything unmeasured stays null.
        value: point && isMeasured(point.y) ? point.y / 100 : null,
        n: point ? sampleSize(point.n) : 0,
      })
    })
  })

  const total = meta?.truncated_axes?.series?.total
  return {
    matrix: {
      kind: 'matrix',
      value_type: 'rate',
      x_labels: xs,
      y_labels: ordered.map((row) => row.label),
      cells,
    },
    rows: {
      shown: named.length,
      total: typeof total === 'number' && total > named.length ? total : named.length,
      other: ordered.length > named.length,
    },
  }
}

/** The row truncation in words, for the frame's footer: "Top 7 of 12 suites by executions; ...". */
export function heatmapRowsNote(rows: HeatmapRows, rowNoun: string): string {
  if (rows.total > rows.shown) {
    return `Top ${formatNumber(rows.shown)} of ${formatNumber(rows.total)} ${rowNoun} by executions; the rest are combined in "${OTHER_ROW_LABEL}".`
  }
  if (rows.other) return `The remaining ${rowNoun} are combined in "${OTHER_ROW_LABEL}".`
  return ''
}

/** One sentence for assistive tech, read by the heatmap wrapper. */
export function heatmapDescription(title: string, matrix: NumericMatrix, rows: HeatmapRows, rowNoun: string): string {
  const named = `${formatNumber(rows.shown)} ${rowNoun}${rows.other ? ` and ${OTHER_ROW_LABEL}` : ''}`
  const columns = matrix.x_labels.length
  return `${title}: pass rate for ${named} over ${formatNumber(columns)} ${columns === 1 ? 'day' : 'days'}, lowest first.`
}
