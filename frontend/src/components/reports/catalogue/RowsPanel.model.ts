/**
 * The rows panel's model (VIZ-208 / 602): the executions behind one chart
 * mark, from `GET /api/v1/analytics/chart-data/rows`. Pure: no React, no DOM.
 *
 * The panel is shared by every Wave-3 host (a heatmap cell, a treemap leaf, a
 * failure group, a scatter point, a drill level), so its contract is pinned
 * here and each host only says WHICH rows: the chart they come from (its
 * metric, group_by and top_n decide which executions count, and which
 * selectors are legal) and the selectors (the open panel's C5 `rows` levels).
 *
 * "The total equals the mark" is stated precisely by the server
 * (`reconciliation`: a count's `y`, a rate's `n`); when the panel's number is
 * not the one the chart drew, the panel SAYS why (the chart counted run
 * totals; data arrived since the chart loaded) instead of showing a bare
 * mismatch (plan R14).
 */
import {
  validateEnvelopeMeta,
  type DrillLevel,
  type EnvelopeMeta,
  type ValidationResult,
  type VizDimension,
} from '@/lib/viz/contracts'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { ChartAccessors } from '@/components/charts/chartState'
import { formatNumber } from '@/utils/formatters'

export const ROWS_URL = '/api/v1/analytics/chart-data/rows'

/** One page of rows; the server's default and the panel's. */
export const ROWS_PAGE_SIZE = 50

/** The chart the rows are drilled from. The rows endpoint validates it as chart-data does. */
export interface RowsChartSpec {
  /** chart-data metric (`executions`, `failures`, `failed`, `pass_rate`, ...). */
  metric: string
  /** The chart's own dimensions (at most two, plus `error_signature` for a failure group). */
  groupBy: readonly VizDimension[]
  /** The chart's `top_n`, when it had one. */
  topN?: number
}

/** What the activated mark showed, so the panel can explain a different total instead of hiding it. */
export interface RowsExpectation {
  /** The mark's drawn value (a count's `y`). */
  y: number | null
  /** The sample behind it (a rate's `n`). */
  n: number | null
  /** `meta.as_of` of the chart's response: when the chart counted. */
  asOf: string | null
}

export interface RowsPanelProps {
  /** The open panel's selectors (`useDrillPath().rows`). Empty = closed: nothing rendered, nothing requested. */
  selectors: readonly DrillLevel[]
  chart: RowsChartSpec
  /**
   * The page scope, exactly as the chart's own request had it (the output of
   * `catalogueParams` WITHOUT the chart's `extra`: project, days, releases,
   * suites), so the rows population is the chart's. `null` while unresolved.
   */
  scope: CatalogParams | null
  /** What the rows are of, for the heading ("payments, failed"). Untrusted text. */
  title: string
  /** The mark's figures when the host still has them (not after a reload from a shared URL). */
  expected?: RowsExpectation | null
  /** Close the panel (the host calls `useDrillPath().closeRows()`); focus returns to the opener. */
  onClose: () => void
}

/** One execution, as `chart_rows_service.row_item` sends it. Every string is untrusted text. */
export interface RowsItem {
  id: string
  test_name: string
  test_fingerprint: string
  suite: string | null
  status: string
  duration_ms: number | null
  run_id: string
  release: { id: string; name: string | null } | null
  created_at: string
  failure_category: string | null
  error_line: string | null
}

/** The page body (with `meta` beside it on the wire). */
export interface RowsPage {
  items: RowsItem[]
  total: number
  page: number
  size: number
  pages: number
  /** Which mark field these rows add up to (`y` for a count, `n` for a rate), and the number to compare. */
  reconciliation: { mark_field: 'y' | 'n'; measure: string; value: number }
}

/** A validated rows response: the page and its C2 envelope. */
export interface RowsResponse extends RowsPage {
  meta: EnvelopeMeta | null
}

// ── Request ───────────────────────────────────────────────────────────────────

/** A stable identity for a selector list (SWR key, React key). */
export function selectorsKey(selectors: readonly DrillLevel[]): string {
  return JSON.stringify(selectors.map(({ dimension, value }) => [dimension, value]))
}

/**
 * The query parameters of one page, or `null` while there is nothing to ask:
 * the panel is closed, or the scope has not resolved. The scope comes first
 * and the chart's keys after it, so a scope cannot override `metric` or a
 * selector; `group_by` is repeated, one `bucket_<dimension>` per selector.
 */
export function rowsRequestParams(
  { selectors, chart, scope }: Pick<RowsPanelProps, 'selectors' | 'chart' | 'scope'>,
  page: number,
): CatalogParams | null {
  if (selectors.length === 0 || scope === null) return null
  const params: CatalogParams = { ...scope, metric: chart.metric, group_by: [...chart.groupBy] }
  if (chart.topN !== undefined) params.top_n = chart.topN
  for (const { dimension, value } of selectors) params[`bucket_${dimension}`] = value
  params.page = Math.max(1, Math.trunc(page))
  params.size = ROWS_PAGE_SIZE
  return params
}

// ── Validation ────────────────────────────────────────────────────────────────

type Dict = Record<string, unknown>
const isDict = (v: unknown): v is Dict => typeof v === 'object' && v !== null && !Array.isArray(v)
const isCount = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const isText = (v: unknown): v is string => typeof v === 'string'
const isTextOrNull = (v: unknown) => v === null || isText(v)

function itemErrors(item: unknown, i: number): string[] {
  const where = `items[${i}]`
  if (!isDict(item)) return [`${where} must be an object`]
  const errors: string[] = []
  for (const key of ['id', 'test_name', 'test_fingerprint', 'status', 'run_id', 'created_at'] as const) {
    if (!isText(item[key])) errors.push(`${where}.${key} must be a string`)
  }
  for (const key of ['suite', 'failure_category', 'error_line'] as const) {
    if (!isTextOrNull(item[key])) errors.push(`${where}.${key} must be a string or null`)
  }
  const duration = item.duration_ms
  if (!(duration === null || (typeof duration === 'number' && Number.isFinite(duration) && duration >= 0))) {
    errors.push(`${where}.duration_ms must be a non-negative number or null`)
  }
  const release = item.release
  if (!(release === null || (isDict(release) && isText(release.id) && isTextOrNull(release.name)))) {
    errors.push(`${where}.release must be {id, name} or null`)
  }
  return errors
}

/** The rows body as the panel reads it. A body that fails is never half-drawn. */
export function validateRowsResponse(input: unknown): ValidationResult<RowsResponse> {
  if (!isDict(input)) return { ok: false, errors: ['invalid_type: a rows response must be an object'] }
  const errors: string[] = []
  let meta: EnvelopeMeta | null = null
  if (input.meta !== undefined && input.meta !== null) {
    const checked = validateEnvelopeMeta(input.meta)
    if (checked.ok) meta = checked.value
    else errors.push(...checked.errors.map((e) => `meta: ${e}`))
  }
  if (!Array.isArray(input.items)) errors.push('items must be an array')
  else input.items.forEach((item, i) => errors.push(...itemErrors(item, i)))
  for (const key of ['total', 'page', 'size', 'pages'] as const) {
    if (!isCount(input[key])) errors.push(`${key} must be a non-negative integer`)
  }
  const rec = input.reconciliation
  if (!(isDict(rec) && (rec.mark_field === 'y' || rec.mark_field === 'n') && isText(rec.measure) && isCount(rec.value))) {
    errors.push('reconciliation must be {mark_field: "y"|"n", measure, value}')
  }
  if (errors.length) return { ok: false, errors }
  return { ok: true, value: { ...(input as unknown as RowsPage), meta } }
}

/** `useChartData` accessors: empty = no execution at all. */
export const ROWS_ACCESSORS: ChartAccessors<RowsResponse> = {
  meta: (value) => value.meta,
  isEmpty: (value) => value.total === 0,
  shown: (value) => value.items.length,
}

// ── Words ─────────────────────────────────────────────────────────────────────

const plural = (n: number, one: string, many = `${one}s`) => `${formatNumber(n)} ${n === 1 ? one : many}`

const MEASURE_NOUN: Record<string, [string, string]> = {
  rows: ['execution', 'executions'],
  distinct_tests: ['test', 'tests'],
  distinct_runs: ['run', 'runs'],
}

/** "12 tests" for the reconciliation's measure (an unknown measure reads as a plain count). */
export function measureText(measure: string, value: number): string {
  const noun = Object.prototype.hasOwnProperty.call(MEASURE_NOUN, measure) ? MEASURE_NOUN[measure] : null
  return noun ? plural(value, noun[0], noun[1]) : formatNumber(value)
}

/** The panel's heading. */
export function rowsHeading(title: string): string {
  return `Executions in ${title}`
}

/** The count line above the table: "42 executions", plus the measure when it is not the rows themselves. */
export function rowsCountText(page: Pick<RowsPage, 'total' | 'reconciliation'>): string {
  const rows = plural(page.total, 'execution')
  const { measure, value } = page.reconciliation
  return measure === 'rows' ? rows : `${rows} (${measureText(measure, value)})`
}

/** The one page announcement when the rows arrive. */
export function rowsAnnouncement(title: string, total: number): string {
  return `${plural(total, 'execution')} in ${title}`
}

/** `meta.definitions.chart_grain`, when the server sent it. */
export function chartGrain(meta: EnvelopeMeta | null): string | null {
  const definitions = (meta as { definitions?: unknown } | null)?.definitions
  const grain = isDict(definitions) ? definitions.chart_grain : undefined
  return typeof grain === 'string' ? grain : null
}

/**
 * Why the rows do not add up to what the chart drew, or `null` when they do
 * (or when the host no longer knows what it drew). Never a bare mismatch.
 */
export function reconciliationNotice(
  page: Pick<RowsResponse, 'reconciliation' | 'meta'>,
  expected: RowsExpectation | null | undefined,
): string | null {
  if (!expected) return null
  const drew = page.reconciliation.mark_field === 'y' ? expected.y : expected.n
  const now = page.reconciliation.value
  if (drew === null || drew === now) return null
  const measure = page.reconciliation.measure
  if (chartGrain(page.meta) === 'run_aggregate') {
    return `The chart counted ${measureText(measure, drew)} from run totals, which include runs whose per-test results have not arrived; ${measureText(measure, now)} can be listed here.`
  }
  const when = expected.asOf ? ` at ${expected.asOf.replace('T', ' ').replace(/\.\d+/, '').replace('Z', ' UTC')}` : ''
  return `${measureText(measure, now)} now; the chart counted ${formatNumber(drew)}${when}. Results have changed since the chart loaded.`
}
