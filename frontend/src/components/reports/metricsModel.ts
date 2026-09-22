/**
 * The metrics strip's content (VIZ-302), as pure functions.
 *
 * `metricsFromSummary` maps the ONE `/metrics/summary` response onto the
 * strip's metric set; `buildStripMetrics` turns that into formatted tiles.
 *
 * The figures come from the response's `report_metrics` block (contract C6):
 * per-status counts, durations and the previous period, with a delta drawn
 * only when that period is `comparable`. Honest about gaps: a value the block
 * reports as `null` is "—" with the block's reason — never 0 — and a response
 * without a valid block shows "—" with `BLOCK_UNAVAILABLE_REASON` rather than
 * guessing. When `meta.measured` is false the window-scoped values the KPI
 * fields fill with zeros are "—" with `meta.reason`.
 */
import { validateContract, type EnvelopeMeta, type ReportMetricKey, type ReportMetrics } from '@/lib/viz/contracts'
import type { ReportSummary } from '@/hooks/useReportMetrics'
import {
  FLAKY_SOURCE,
  formatDelta,
  formatMetric,
  passRateBasisLabel,
  shouldShowUnknown,
  type FormattedMetric,
  type MetricKind,
} from '@/utils/formatMetric'

export type MetricId =
  | 'runs'
  | 'total_tests'
  | 'passed'
  | 'failed'
  | 'broken'
  | 'skipped'
  | 'unknown'
  | 'flaky'
  | 'pass_rate'
  | 'total_duration'
  | 'avg_duration'

/** Visual (and tab) order. */
export const METRIC_ORDER: readonly MetricId[] = [
  'runs',
  'total_tests',
  'passed',
  'failed',
  'broken',
  'skipped',
  'unknown',
  'flaky',
  'pass_rate',
  'total_duration',
  'avg_duration',
]

export type MetricTone = 'passed' | 'failed' | 'broken' | 'skipped' | 'unknown' | 'flaky' | 'neutral'

interface MetricSpec {
  title: string
  kind: MetricKind
  tone: MetricTone
  /** Which trend direction is good. */
  positive: 'up' | 'down'
}

export const METRIC_SPECS: Record<MetricId, MetricSpec> = {
  runs: { title: 'Runs', kind: 'count', tone: 'neutral', positive: 'up' },
  total_tests: { title: 'Total tests', kind: 'count', tone: 'neutral', positive: 'up' },
  passed: { title: 'Passed', kind: 'count', tone: 'passed', positive: 'up' },
  failed: { title: 'Failed', kind: 'count', tone: 'failed', positive: 'down' },
  broken: { title: 'Broken', kind: 'count', tone: 'broken', positive: 'down' },
  skipped: { title: 'Skipped', kind: 'count', tone: 'skipped', positive: 'down' },
  unknown: { title: 'Unknown', kind: 'count', tone: 'unknown', positive: 'down' },
  flaky: { title: 'Flaky', kind: 'count', tone: 'flaky', positive: 'down' },
  pass_rate: { title: 'Pass rate', kind: 'percent', tone: 'neutral', positive: 'up' },
  total_duration: { title: 'Total duration', kind: 'duration', tone: 'neutral', positive: 'down' },
  avg_duration: { title: 'Avg duration', kind: 'duration', tone: 'neutral', positive: 'down' },
}

export interface ReportMetricsInput {
  values: Partial<Record<MetricId, number | null>>
  /** Why a value is missing, per metric. */
  reasons?: Partial<Record<MetricId, string>>
  passRateBasis?: string | null
  /**
   * Change vs the previous period, per metric, in the metric's own terms:
   * PERCENTAGE POINTS for a percent metric (pass rate 80 → 82.9 is +2.9 pp —
   * a relative +3.6 % would overstate it), relative percent for counts and
   * durations.
   */
  trends?: Partial<Record<MetricId, number | null>>
  /**
   * Why a comparable metric has no number (`trends` null): "New: 0 in the
   * previous period", "Previous period not measured". Shown on the tile,
   * never a silent blank.
   */
  trendNotes?: Partial<Record<MetricId, string>>
  /** The previous period is comparable; without it no delta is drawn. */
  comparable?: boolean
  /** Why the periods are not compared (C6 `previous.reason`), shown under the strip. */
  comparisonReason?: string | null
}

/** A tile's change line. `none`: no number, only `text` saying why. */
export interface MetricDelta {
  direction: 'up' | 'down' | 'flat' | 'none'
  text: string
}

export interface StripMetric {
  id: MetricId
  title: string
  tone: MetricTone
  positive: 'up' | 'down'
  formatted: FormattedMetric
  /** "of executions", the Flaky source: printed under the value. */
  note?: string
  delta: MetricDelta | null
  /** The delta as a number at one decimal place, `null` when no number is drawn. */
  trend: number | null
}

/** Reasons a comparable pair of periods still yields no number for one metric. */
export const TREND_NOTES = {
  newFromZero: 'New: 0 in the previous period',
  previousUnmeasured: 'No change shown: the previous period has no value',
} as const

/**
 * The reason on the tiles only `report_metrics` carries (the status counts and
 * the total duration) when a response has no valid block — a server older than
 * the block, or one whose block failed the C6 guard. Never shown as 0.
 */
export const BLOCK_UNAVAILABLE_REASON = 'Not in this response: the server sent no valid report_metrics'

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** `report_metrics` period key → strip metric id. */
const PERIOD_KEYS: readonly (readonly [ReportMetricKey, MetricId])[] = [
  ['runs', 'runs'],
  ['total_tests', 'total_tests'],
  ['passed', 'passed'],
  ['failed', 'failed'],
  ['broken', 'broken'],
  ['skipped', 'skipped'],
  ['unknown', 'unknown'],
  ['pass_rate', 'pass_rate'],
  ['total_duration_ms', 'total_duration'],
  ['avg_duration_ms', 'avg_duration'],
]

/** The C6 block of a summary response, validated; `null` when absent or invalid. */
export function readReportMetrics(summary: ReportSummary | null): ReportMetrics | null {
  if (!summary || summary.report_metrics === undefined) return null
  const result = validateContract('report_metrics', summary.report_metrics)
  return result.ok ? result.value : null
}

/**
 * The change from `previous` to `current` in the metric's terms: percentage
 * points for a percent metric, relative percent otherwise. `note` says why
 * there is no number when there is none but the reader would expect one.
 */
export function periodChange(
  kind: MetricKind,
  current: number | null,
  previous: number | null,
): { trend: number | null; note?: string } {
  if (current === null) return { trend: null } // the tile itself is "—" with its reason
  if (previous === null) return { trend: null, note: TREND_NOTES.previousUnmeasured }
  if (kind === 'percent') return { trend: current - previous }
  if (previous === 0) return current === 0 ? { trend: 0 } : { trend: null, note: TREND_NOTES.newFromZero }
  return { trend: ((current - previous) / previous) * 100 }
}

function fromBlock(block: ReportMetrics, summary: ReportSummary): ReportMetricsInput {
  const { current, previous } = block
  const values: Partial<Record<MetricId, number | null>> = { flaky: num(summary.flaky_test_count?.value) }
  const reasons: Partial<Record<MetricId, string>> = {}
  const trends: Partial<Record<MetricId, number | null>> = {}
  const trendNotes: Partial<Record<MetricId, string>> = {}
  for (const [key, id] of PERIOD_KEYS) {
    values[id] = current[key]
    const reason = current.reasons[key]
    if (current[key] === null && reason) reasons[id] = reason
    // A delta only for a COMPARABLE pair of periods.
    if (previous.comparable) {
      const change = periodChange(METRIC_SPECS[id].kind, current[key], previous[key])
      trends[id] = change.trend
      if (change.note) trendNotes[id] = change.note
    }
  }
  return {
    values,
    reasons,
    passRateBasis: block.pass_rate_basis,
    trends,
    trendNotes,
    comparable: previous.comparable,
    comparisonReason: previous.comparable ? null : previous.reason,
  }
}

/** Every tile "—" with one reason (the chrome's request failed): nothing compared. */
export function metricsUnavailable(reason: string): ReportMetricsInput {
  const reasons: Partial<Record<MetricId, string>> = {}
  const values: Partial<Record<MetricId, number | null>> = {}
  for (const id of METRIC_ORDER) {
    values[id] = null
    reasons[id] = reason
  }
  return { values, reasons, comparable: false }
}

/** The tile's change line: "2.9 pp vs previous period", or why there is none. */
function deltaFor(id: MetricId, input: ReportMetricsInput): MetricDelta | null {
  if (input.comparable !== true) return null
  const raw = input.trends?.[id]
  const base = formatDelta(raw, true)
  if (base === null || typeof raw !== 'number') {
    const note = input.trendNotes?.[id]
    return note ? { direction: 'none', text: note } : null
  }
  if (base.direction === 'flat') return { direction: 'flat', text: 'vs previous period' }
  if (METRIC_SPECS[id].kind === 'percent') {
    // Points, not the relative change formatDelta states for counts.
    return { direction: base.direction, text: `${Math.abs(raw).toFixed(1)} pp vs previous period` }
  }
  return base
}

/**
 * The strip's input from ONE `/metrics/summary` response.
 *
 * With a valid `report_metrics` block (contract C6) every tile comes from it:
 * the counts, the rate, the durations and — only when `previous.comparable` —
 * the deltas; a `null` carries the block's own reason. Flaky stays the KPI
 * field (it is not window-scoped).
 *
 * Without one (an older server, a block that failed the guard, or a scope the
 * caller may not read, whose payload is `{meta}` alone), the tiles the KPI
 * fields cover come from them, the rest are "—" with
 * `BLOCK_UNAVAILABLE_REASON`, and nothing is compared. When `meta.measured` is
 * false every window-scoped tile is "—" with `meta.reason`: an empty scope is
 * not a 0 % pass rate.
 */
export function metricsFromSummary(summary: ReportSummary | null, meta: EnvelopeMeta | null): ReportMetricsInput {
  const block = readReportMetrics(summary)
  if (block && summary) return fromBlock(block, summary)
  const unmeasured = meta !== null && !meta.measured
  const scopeReason = unmeasured ? (meta?.reason ?? undefined) : undefined
  const windowed = (value: number | null) => (unmeasured ? null : value)
  const reasons: Partial<Record<MetricId, string>> = {}
  for (const id of ['passed', 'failed', 'broken', 'skipped', 'total_duration'] as const) {
    reasons[id] = scopeReason ?? BLOCK_UNAVAILABLE_REASON
  }
  if (scopeReason) {
    for (const id of ['runs', 'total_tests', 'pass_rate', 'avg_duration'] as const) reasons[id] = scopeReason
  }
  return {
    values: {
      runs: windowed(meta ? meta.totals.matched_runs : null),
      total_tests: windowed(num(summary?.total_executions_7d?.value)),
      flaky: num(summary?.flaky_test_count?.value),
      pass_rate: windowed(num(summary?.avg_pass_rate_7d?.value)),
      avg_duration: windowed(num(summary?.avg_duration_ms?.value)),
    },
    reasons,
    passRateBasis: meta?.pass_rate_basis ?? summary?.avg_pass_rate_7d?.basis ?? null,
    // Without the block nothing says whether the previous period is
    // comparable, so no delta is drawn.
    comparable: false,
  }
}

export function buildStripMetrics(input: ReportMetricsInput, include: readonly MetricId[] = METRIC_ORDER): StripMetric[] {
  const wanted = new Set(include)
  const out: StripMetric[] = []
  for (const id of METRIC_ORDER) {
    if (!wanted.has(id)) continue
    const value = input.values[id]
    if (id === 'unknown' && !shouldShowUnknown(value)) continue
    const spec = METRIC_SPECS[id]
    const formatted = formatMetric(value, spec.kind, { reason: input.reasons?.[id] })
    let note: string | undefined
    if (id === 'pass_rate') note = passRateBasisLabel(input.passRateBasis) ?? 'basis not stated'
    if (id === 'flaky') note = FLAKY_SOURCE
    if (id === 'total_tests') note = 'test executions in the window'
    const raw = input.trends?.[id]
    const delta = formatted.measured ? deltaFor(id, input) : null
    out.push({
      id,
      title: spec.title,
      tone: spec.tone,
      positive: spec.positive,
      formatted,
      note,
      delta,
      trend: delta && delta.direction !== 'none' && typeof raw === 'number' ? Math.round(raw * 10) / 10 : null,
    })
  }
  return out
}
