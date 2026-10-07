/**
 * K4 (Wave 2.6): data a report page ALREADY holds, as the canonical
 * `ChartResponse` the catalogue frames read.
 *
 * Plan 2.1, principle 1: where the page's own payload counts the same
 * population, a chart is drawn from it rather than from a second request, so
 * the chart and the number beside it cannot disagree (a donut that says 81%
 * beside a KPI that says 83% costs more trust than a request costs time). These
 * are the thin adapters that make that possible; the page wraps the result in
 * `readyState(...)` and hands it to the frame.
 *
 * Three rules every adapter keeps, each pinned by a test:
 *
 *   - **A missing count is `null`, never 0.** "This suite had no broken
 *     tests" is a measurement; a payload without the field is the absence of
 *     one. A null point carries `measured: false` and the reason, so the table
 *     prints "—" and the plot draws nothing, instead of a confident zero.
 *   - **Names are text.** Suite, test and cluster names come from ingested CI
 *     files and are hostile. They are kept verbatim (React renders them as
 *     text), never used as an object key by themselves (a suite called
 *     `__proto__` would read `Object.prototype` back as its label), and a lone
 *     UTF-16 surrogate is replaced so the contract's well-formed-string rule
 *     cannot refuse the whole chart over one bad name.
 *   - **The output is a C3 series that `validateChartResponse` accepts.**
 *     `meta` is `null`: the data came through an existing endpoint, not a
 *     `chart-data` envelope, and an export then says "Scope unavailable"
 *     rather than stamping a scope nobody measured.
 */
import { type SeriesChart, type SeriesPoint, type VizStatus } from '@/lib/viz/contracts'
import type { ChartResponse } from '@/components/charts/chartState'

/** Why a count point is empty: the payload did not carry a usable number. */
export const MISSING_COUNT_REASON = 'the server sent no usable count for this value'
/** Why `unknown` is empty although a total was sent. */
export const UNKNOWN_NOT_DERIVABLE_REASON = 'not derivable: a status count is missing, so the remainder of the total is unknown'
/** Why `unknown` is empty when the statuses exceed the total (inconsistent payload). */
export const UNKNOWN_OVER_TOTAL_REASON = 'not derivable: the status counts add up to more than the total'

/** The four statuses every payload here names; `unknown` is derived. */
const NAMED_STATUSES = ['passed', 'failed', 'broken', 'skipped'] as const

type Named = (typeof NAMED_STATUSES)[number]

// ── Text and numbers from an untrusted payload ───────────────────────────────

/**
 * A name as display text: the string itself, with any lone surrogate replaced
 * by U+FFFD (what `String.prototype.toWellFormed` does; not in every browser
 * this app supports). A number is written out; anything else, or a blank
 * string, is `fallback` — never "undefined" or "[object Object]".
 */
export function displayText(raw: unknown, fallback: string): string {
  const text = typeof raw === 'string' ? raw : typeof raw === 'number' && Number.isFinite(raw) ? String(raw) : ''
  if (text.trim() === '') return fallback
  return wellFormed(text)
}

/**
 * Lone surrogates replaced by U+FFFD. A loop, not a regex: the regex needs a
 * lookbehind, which is a parse error (for the whole chunk) in older Safari.
 */
function wellFormed(text: string): string {
  let out = ''
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i)
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(i + 1)
      if (next >= 0xdc00 && next <= 0xdfff) {
        out += text[i] + text[i + 1]
        i += 1
        continue
      }
      out += '�'
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      out += '�'
    } else {
      out += text[i]
    }
  }
  return out
}

/** A count: a finite number >= 0, else `null` (a string "12", NaN, a negative are not counts). */
function readCount(raw: unknown): number | null {
  return typeof raw === 'number' && Number.isFinite(raw) && raw >= 0 ? raw : null
}

/** A C3 point for a count, `null` with its reason when there is none. */
function countPoint(x: string, value: number | null, reason = MISSING_COUNT_REASON): SeriesPoint {
  if (value === null) return { x, y: null, n: 0, measured: false, reason }
  return { x, y: value, n: Math.round(value) }
}

/**
 * A key for the i-th row that no name can collide with or turn into a
 * prototype lookup: the name (made well formed) plus its position. Two rows
 * with one name stay two bars.
 */
const rowKey = (name: string, index: number) => `${name}#${index}`

function categoryResponse(
  dimensions: string[],
  series: SeriesChart['series'],
  xLabels: Record<string, string>,
): ChartResponse {
  return { meta: null, series: { kind: 'series', dimensions, x_type: 'category', series, x_labels: xLabels } }
}

// ── Status counts (Overview and Summary donuts) ──────────────────────────────

/** A status breakdown as a page holds it. Any field may be absent. */
export interface StatusCounts {
  passed?: number | null
  failed?: number | null
  broken?: number | null
  skipped?: number | null
  /** Sent by a payload that counts it; otherwise derived from `total`. */
  unknown?: number | null
  /** Every execution (or test) in the population, whatever its status. */
  total?: number | null
}

/**
 * A status donut's data: one series, x = status, in the fixed status order.
 *
 * `unknown` is the RESIDUAL: `total - (passed + failed + broken + skipped)`,
 * when the payload sends a total. It is left out when there is no total (the
 * payload does not count it, and a zero would claim it does), and `null` with
 * the reason when a status is missing or the statuses exceed the total.
 */
export function chartResponseFromStatusCounts(counts: StatusCounts): ChartResponse {
  const named = NAMED_STATUSES.map((status) => [status, readCount(counts[status])] as const)
  const points = named.map(([status, value]) => countPoint(status, value))

  const unknown = readCount(counts.unknown)
  const total = readCount(counts.total)
  if (unknown !== null) {
    points.push(countPoint('unknown', unknown))
  } else if (total !== null) {
    if (named.some(([, value]) => value === null)) {
      points.push(countPoint('unknown', null, UNKNOWN_NOT_DERIVABLE_REASON))
    } else {
      const residual = total - named.reduce((sum, [, value]) => sum + (value as number), 0)
      points.push(residual < 0 ? countPoint('unknown', null, UNKNOWN_OVER_TOTAL_REASON) : countPoint('unknown', residual))
    }
  }

  return {
    meta: null,
    series: {
      kind: 'series',
      dimensions: ['status'],
      x_type: 'category',
      series: [{ key: 'count', label: 'Count', points }],
    },
  }
}

/** A `/metrics/trends` point, as far as the status sums need it. */
export interface TrendCountsPoint {
  passed?: number | null
  failed?: number | null
  broken?: number | null
  skipped?: number | null
  total?: number | null
}

/**
 * The window's status totals from a page's own day series (Overview's
 * `useTrendData`), for `chartResponseFromStatusCounts`.
 *
 * A status is summed only if EVERY day reported it (one day without `broken`
 * would otherwise undercount the window and look like a real, smaller number);
 * otherwise it is `null`. `total` is carried only when every day has one, so
 * `unknown` is derived only from a whole-window total.
 */
export function statusCountsFromTrendPoints(points: readonly TrendCountsPoint[]): StatusCounts {
  const sum = (field: Named | 'total'): number | null => {
    let acc = 0
    for (const point of points) {
      const value = readCount(point[field])
      if (value === null) return null
      acc += value
    }
    return acc
  }
  const counts: StatusCounts = {}
  for (const status of NAMED_STATUSES) counts[status] = sum(status)
  const total = sum('total')
  if (total !== null) counts.total = total
  return counts
}

// ── Per-suite status rows (Summary: results by suite) ────────────────────────

/** A `/reports/summary` suite row, as far as the stacked bars need it. */
export interface SuiteStatusRow {
  suite_name?: unknown
  passed?: number | null
  failed?: number | null
  broken?: number | null
  skipped?: number | null
}

/**
 * Stacked bars by suite: statuses as series, suites as x (`x_labels` holds the
 * display name), ordered worst first — failed + broken, descending, then by
 * name, then by position. A composition chart keeps its input order
 * (`statusBarModel`), so the order is decided here.
 */
export function chartResponseFromSuiteRows(rows: readonly SuiteStatusRow[]): ChartResponse {
  const entries = rows.map((row, index) => {
    const name = displayText(row.suite_name, '(no suite)')
    const counts = Object.fromEntries(NAMED_STATUSES.map((status) => [status, readCount(row[status])])) as Record<
      Named,
      number | null
    >
    return { key: rowKey(name, index), name, index, counts, bad: (counts.failed ?? 0) + (counts.broken ?? 0) }
  })
  entries.sort((a, b) => b.bad - a.bad || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0) || a.index - b.index)

  const xLabels: Record<string, string> = {}
  for (const entry of entries) xLabels[entry.key] = entry.name
  const series = NAMED_STATUSES.map((status) => ({
    key: status as VizStatus,
    label: STATUS_LABEL[status],
    points: entries.map((entry) => countPoint(entry.key, entry.counts[status])),
  }))
  return categoryResponse(['suite', 'status'], series, xLabels)
}

const STATUS_LABEL: Record<Named, string> = { passed: 'Passed', failed: 'Failed', broken: 'Broken', skipped: 'Skipped' }

// ── Failure clusters (Release gate) ──────────────────────────────────────────

/** A stored decision's `cluster_insights` entry, as far as the breakdown needs it. */
export interface ClusterRow {
  cluster_id?: unknown
  id?: unknown
  label?: unknown
  size?: number | null
}

/**
 * One slice (or bar) per failure cluster, sized by its member count. Read from
 * the STORED decision, so the chart explains the verdict's own input and never
 * a recomputed one.
 */
export function chartResponseFromClusters(clusters: readonly ClusterRow[]): ChartResponse {
  const xLabels: Record<string, string> = {}
  const points = clusters.map((cluster, index) => {
    const id = displayText(cluster.cluster_id ?? cluster.id, 'cluster')
    const key = rowKey(id, index)
    xLabels[key] = displayText(cluster.label, '(unlabelled cluster)')
    return countPoint(key, readCount(cluster.size))
  })
  return categoryResponse(['failure_cluster'], [{ key: 'tests', label: 'Failed tests', points }], xLabels)
}
