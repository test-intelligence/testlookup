/**
 * The pure half of Trends › Execution time and Suite › "Suite execution time"
 * (EXEC-04b, TRND-09): the execution-metrics API's shapes turned into the
 * kit's chart models and states, and the words the sections print.
 *
 * The rule throughout is the contract's (`EXEC-03-contract.md`): `null` is
 * NOT MEASURED. A wall clock, a percentile or a change the server did not
 * measure is printed "not reported" (or "—" for a change), never 0, and a
 * block with `measured: false` is a not-measured state carrying the server's
 * own reason, as plain text.
 */
import { classifyChartError } from '@/components/charts/chartState'
import type { ChartState } from '@/components/charts/chartStateCore'
import {
  bucketLabel,
  rankSlowestTests,
  type DurationBucket,
  type DurationHistogramModel,
  type SlowestTestsModel,
} from '@/components/charts/durationBuckets'
import { formatMs, NOT_REPORTED } from '@/components/charts/executionTimeModel'
import type {
  DurationHistogram,
  ExecutionPercentiles,
  Measured,
  SlowestTestRow,
} from '@/types/executionMetrics'
import { formatDuration, formatNumber } from '@/utils/formatters'

// ── Requests → states ───────────────────────────────────────────────────────

export interface ExecutionRead<T> {
  data: T | undefined
  error: unknown
  isLoading: boolean
  retry?: () => void
}

/**
 * One read's state for a frame: loading, the classified error (Retry asks
 * again), 403 as forbidden, a `measured: false` answer as not-measured with
 * the server's reason, else drawn. `select` picks the block a frame draws, so
 * a block of its own with `measured: false` speaks for that frame only.
 */
export function executionState<T, B extends Measured = Measured>(
  read: ExecutionRead<T>,
  select?: (data: T) => B | null | undefined,
): ChartState<T> {
  if (read.error) {
    const classified = classifyChartError(read.error)
    if (classified.type === 'forbidden') return { status: 'forbidden', requestId: classified.requestId }
    if (classified.type === 'error') return { status: 'error', error: classified.error, retry: read.retry }
    // A superseded request or a 401 the token refresh owns: still loading.
    return { status: 'loading' }
  }
  if (read.data === undefined) return { status: 'loading' }
  const top = read.data as unknown as Partial<Measured>
  if (top.measured === false) return notMeasured(top.reason)
  if (select) {
    const block = select(read.data)
    if (!block) return notMeasured(SERVER_TOO_OLD_REASON)
    if (block.measured === false) return notMeasured(block.reason)
  }
  return { status: 'ready', data: read.data, meta: null, revalidating: false }
}

/** A block the server did not send at all (an API older than the field). */
export const SERVER_TOO_OLD_REASON = 'this server does not report this figure yet'
const notMeasured = <T,>(reason: string | null | undefined): ChartState<T> => ({
  status: 'not-measured',
  reason: reason && reason.trim() ? reason : 'not measured in this scope',
  meta: null,
})

// ── Figures ──────────────────────────────────────────────────────────────────

/** "5.0s / 6.1s over 30 runs", or "not reported" (with why) when the window has none. */
export function percentilePair(block: ExecutionPercentiles | null | undefined): string {
  if (!block || block.measured === false || block.n <= 0) return block?.reason ? `${NOT_REPORTED} (${block.reason})` : NOT_REPORTED
  return `${formatMs(block.p50_ms)} / ${formatMs(block.p95_ms)} over ${formatNumber(block.n)} ${block.n === 1 ? 'run' : 'runs'}`
}

/** A percentile block's value: "not reported" when it was not measured (never 0). */
export function percentileText(block: ExecutionPercentiles | null | undefined, which: 'p50_ms' | 'p95_ms'): string {
  if (!block || block.measured === false) return NOT_REPORTED
  return formatMs(block[which])
}

/** "+12.5%" / "−3.0%" / "no change"; "—" when the server could not compare (no previous figure). */
export function changeText(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return '—'
  if (Math.abs(pct) < 0.05) return 'no change'
  return `${pct > 0 ? '+' : '−'}${Math.abs(pct).toFixed(1)}%`
}

/** Slower is worse: +% reads as the failed hue, −% as the passed hue; no comparison is muted. */
export function changeTone(pct: number | null | undefined): 'worse' | 'better' | 'none' {
  if (pct == null || !Number.isFinite(pct) || Math.abs(pct) < 0.05) return 'none'
  return pct > 0 ? 'worse' : 'better'
}

/** The change's words for its tooltip: what it compares, in full. */
export function changeTitle(pct: number | null | undefined, previousWindow: string): string {
  if (pct == null || !Number.isFinite(pct)) return `Not compared: no figure for the ${previousWindow}`
  if (Math.abs(pct) < 0.05) return `No change against the ${previousWindow}`
  return `${Math.abs(pct).toFixed(1)}% ${pct > 0 ? 'slower' : 'faster'} than the ${previousWindow}`
}

/** "previous 14 days" — the window the server compares against (the same length, just before). */
export function previousWindowWords(days: number): string {
  return days === 1 ? 'previous day' : `previous ${days} days`
}

// ── Slowest tests (TRND-09) ──────────────────────────────────────────────────

/** "20 slowest tests (p95)": the frame's title, from the server's own limit. */
export const slowestTitle = (limit: number): string => `${limit} slowest tests (p95)`

/** A slow test's display name: the test, then its suite in parentheses. Untrusted text, printed as text. */
export function slowTestName(row: Pick<SlowestTestRow, 'test_name' | 'suite_name' | 'test_fingerprint'>): string {
  const name = row.test_name?.trim() || `Unnamed test ${row.test_fingerprint.slice(0, 8)}`
  return row.suite_name?.trim() ? `${name} (${row.suite_name.trim()})` : name
}

/** The server's slowest tests (p95 desc) as the kit's ranked model; `n` is the executions behind each p95. */
export function slowestFromServer(items: readonly SlowestTestRow[], limit: number): SlowestTestsModel {
  return rankSlowestTests(
    items.map((row) => ({ name: slowTestName(row), p95: row.p95_ms ?? null, runs: row.n })),
    { limit: Math.max(1, limit) },
  )
}

// ── Duration histogram (TRND-09) ─────────────────────────────────────────────

/**
 * The server's FIXED buckets (`lower_ms <= d < upper_ms`, all of them, zero
 * filled, the last open-ended) as the kit's histogram model. Nothing is
 * excluded: the first bucket starts at 0 ms, so a zero duration is placed, and
 * only executions carrying a duration are counted at all (`n`). The placed
 * count is the buckets' own sum, so "N of M placed" can never claim more than
 * the bars hold.
 */
export function histogramFromServer(histogram: DurationHistogram): DurationHistogramModel {
  const buckets: DurationBucket[] = histogram.buckets.map((bucket) => ({
    from: bucket.lower_ms,
    to: bucket.upper_ms,
    count: Math.max(0, Math.trunc(bucket.count)),
    label: bucketLabel(bucket.lower_ms, bucket.upper_ms),
    overflow: bucket.upper_ms === null,
    underflow: false,
  }))
  const counted = buckets.reduce((sum, bucket) => sum + bucket.count, 0)
  return {
    buckets: counted > 0 ? buckets : [],
    counted,
    missing: 0,
    zero: 0,
    excluded: 0,
    total: Math.max(counted, Math.trunc(histogram.n)),
    excludedStatement: null,
  }
}

/** What the server's buckets are, in place of the client ladder's "1-2-5 per decade". */
export function serverHistogramCaption(histogram: DurationHistogram): string {
  const top = histogram.buckets.find((bucket) => bucket.upper_ms === null)
  const tail = top ? `; the last bucket counts everything from ${formatDuration(top.lower_ms)} up` : ''
  return `Fixed, log-spaced buckets, the same for every scope so two scopes compare bucket for bucket${tail}.`
}

/** The histogram frame's one line: the typical and the slow end, over how many executions. */
export function histogramTakeaway(histogram: DurationHistogram): string | undefined {
  if (!histogram.measured || histogram.n <= 0) return undefined
  return `p50 ${formatMs(histogram.p50_ms)} · p95 ${formatMs(histogram.p95_ms)} over ${formatNumber(histogram.n)} test ${
    histogram.n === 1 ? 'execution' : 'executions'
  }`
}

// ── Regressions ──────────────────────────────────────────────────────────────

const num = (value: unknown, fallback: number): number => (typeof value === 'number' && Number.isFinite(value) ? value : fallback)

/** The suite rule in words, from the server's own constants (EXEC-03: 5 vs 20 runs, 1.5×, same branch). */
export function suiteRuleText(rule: Record<string, number | boolean> | null | undefined): string {
  const recent = num(rule?.recent_runs, 5)
  const baseline = num(rule?.baseline_runs, 20)
  const ratio = num(rule?.ratio, 1.5)
  const branch = rule?.same_branch === false ? '' : ', on the same branch'
  return `A suite is listed when the median of its last ${recent} runs is at least ${ratio}× the median of the ${baseline} runs before them${branch}.`
}

/** The test rule in words. */
export function testRuleText(rule: Record<string, number | boolean> | null | undefined): string {
  const ratio = num(rule?.test_ratio, 1.5)
  const samples = num(rule?.test_min_samples, 10)
  return `A test is listed when its median in the window is at least ${ratio}× its duration baseline (a baseline of ${samples} or more samples).`
}

/** "2.0×". */
export const ratioText = (ratio: number): string => `${ratio.toFixed(1)}×`
