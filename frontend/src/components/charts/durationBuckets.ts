/**
 * The pure model behind duration analysis (VIZ-406).
 *
 * Three rules, all of them about not lying with a chart:
 *
 *   1. LOG-SPACED BUCKETS. Test durations span six orders of magnitude — a
 *      0.4 ms assertion and a 40-minute browser suite sit in the same dataset.
 *      Linear buckets put 99 % of the mass in the first bar and tell the reader
 *      nothing. The edges are the 1-2-5 ladder, so every edge is a round
 *      duration a human recognises, and they are LABELLED rather than implied.
 *   2. AN OVERFLOW BUCKET — and, when it is needed, an UNDERFLOW one. The
 *      ladder stops just above the bulk (the 99th percentile), and everything
 *      beyond it is one "≥ X" bar. One pathological test cannot stretch the
 *      axis until the rest is a single pixel — and the outlier is still visibly
 *      there, not quietly dropped. At the other end the ladder has a floor
 *      (1 µs); a duration under it gets a "< 0.001ms" bar rather than being
 *      counted in a bucket whose label excludes it.
 *   3. EXCLUSIONS ARE COUNTED, NOT HIDDEN. A missing duration is not a zero and
 *      a zero duration is not a measurement of "instant" worth bucketing on a
 *      log scale (log 0 does not exist). Both are excluded and both are
 *      STATED — "214 executions without duration" — because a histogram over an
 *      unknown fraction of the data is a histogram of nothing in particular.
 *
 * Durations are milliseconds throughout and are formatted with `formatDuration`
 * — the same formatter `TimingCell` uses, so a bucket edge and a run's duration
 * read identically.
 */
import type { SeriesChart, SeriesPoint } from '@/lib/viz/contracts'
import { formatDuration, formatNumber } from '@/utils/formatters'

/** The mantissas of the bucket ladder: three buckets per decade. */
export const BUCKET_STEPS = [1, 2, 5] as const
/** The ladder's ends, as powers of ten in milliseconds: 1 µs … ~58 days. */
export const MIN_EXPONENT = -3
export const MAX_EXPONENT = 9
/** The quantile the ladder's top edge answers for; above it is the overflow bucket. */
export const OVERFLOW_QUANTILE = 0.99
/** Bars are unreadable below this, and uncountable above it. */
export const MIN_FINITE_BUCKETS = 3
export const MAX_FINITE_BUCKETS = 15

/** The 1-2-5 ladder, built from decimal literals so every edge is the exact round value. */
const LADDER: number[] = (() => {
  const out: number[] = []
  for (let e = MIN_EXPONENT; e <= MAX_EXPONENT; e++) {
    for (const step of BUCKET_STEPS) out.push(Number(`${step}e${e}`))
  }
  return out
})()

/** A duration we can put on a log axis: finite and strictly positive. */
const isMeasurable = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value > 0

/** Nearest-rank quantile of an ASCENDING array. */
function quantile(sorted: readonly number[], q: number): number {
  if (sorted.length === 0) return Number.NaN
  const rank = Math.ceil(q * sorted.length)
  return sorted[Math.min(Math.max(rank, 1), sorted.length) - 1]
}

/**
 * The bucket edges for `values`: `edges[i]`..`edges[i+1]` is a finite bucket,
 * and everything at or above the LAST edge is the overflow bucket. Empty when
 * no value can be placed on a log axis at all.
 */
export function logBucketEdges(values: readonly (number | null | undefined)[]): number[] {
  const positives = values.filter(isMeasurable).sort((a, b) => a - b)
  if (positives.length === 0) return []
  const lo = positives[0]
  const bulkTop = quantile(positives, OVERFLOW_QUANTILE)

  // The last edge at or below the smallest value: no value can fall off the bottom.
  let start = 0
  for (let i = 0; i < LADDER.length; i++) {
    if (LADDER[i] <= lo) start = i
    else break
  }
  // The first edge strictly above the bulk: the outliers past it are the overflow.
  let end = LADDER.length - 1
  for (let i = start; i < LADDER.length; i++) {
    if (LADDER[i] > bulkTop) {
      end = i
      break
    }
  }
  const ceiling = LADDER.length - 1
  if (end - start < MIN_FINITE_BUCKETS) end = Math.min(start + MIN_FINITE_BUCKETS, ceiling)
  if (end - start > MAX_FINITE_BUCKETS) end = start + MAX_FINITE_BUCKETS
  return LADDER.slice(start, end + 1)
}

export interface DurationBucket {
  from: number
  /** `null` marks the overflow bucket: everything at or above `from`. */
  to: number | null
  count: number
  /** "20ms – 50ms", "≥ 1.0s" for the overflow bucket, "< 0.001ms" for the underflow one. */
  label: string
  overflow: boolean
  /**
   * The UNDERFLOW bucket: `0`..the ladder's first edge. Present only when a
   * duration actually fell below the ladder's floor (1 µs), because an
   * always-empty first bar is a bar that says nothing.
   */
  underflow: boolean
}

export interface DurationHistogramModel {
  buckets: DurationBucket[]
  /** Durations the chart could place. */
  counted: number
  /** Durations that were missing, not a number, or negative. */
  missing: number
  /** Durations recorded as exactly zero. */
  zero: number
  /** `missing + zero`. */
  excluded: number
  total: number
  /** The sentence the chart shows, or `null` when nothing was excluded. */
  excludedStatement: string | null
}

const OVERFLOW_PREFIX = '≥ '
/** The underflow bucket's label prefix: everything strictly below the ladder's floor. */
export const UNDERFLOW_PREFIX = '< '
const RANGE_SEPARATOR = ' – '

export function bucketLabel(from: number, to: number | null): string {
  return to === null ? `${OVERFLOW_PREFIX}${formatDuration(from)}` : `${formatDuration(from)}${RANGE_SEPARATOR}${formatDuration(to)}`
}

/** "< 0.001ms" — the bucket for a duration under the ladder's first edge. */
export const underflowLabel = (edge: number): string => `${UNDERFLOW_PREFIX}${formatDuration(edge)}`

/**
 * The excluded-duration sentence. Zeroes are broken out because they are
 * excluded for a DIFFERENT reason than a missing value — a zero is a recorded
 * measurement that a log axis cannot place — and folding them together would
 * let a reporting bug (every duration written as 0) read as a missing-data bug.
 */
export function excludedStatement({ missing, zero }: { missing: number; zero: number }): string | null {
  const total = missing + zero
  if (total <= 0) return null
  const head = `${formatNumber(total)} ${total === 1 ? 'execution' : 'executions'} without duration`
  if (zero <= 0) return head
  return `${head}, including ${formatNumber(zero)} recorded as ${formatDuration(0)}`
}

export function buildDurationHistogram(values: readonly (number | null | undefined)[]): DurationHistogramModel {
  let missing = 0
  let zero = 0
  const measurable: number[] = []
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value) && value === 0) {
      zero += 1
      continue
    }
    if (isMeasurable(value)) measurable.push(value)
    else missing += 1
  }
  const edges = logBucketEdges(measurable)
  const buckets: DurationBucket[] = []
  // The UNDERFLOW bucket, and only when something is actually in it.
  //
  // `logBucketEdges` starts at the last ladder edge AT OR BELOW the smallest
  // value — but when the smallest value is under the ladder's own floor (1 µs)
  // there is no such edge, so `edges[0]` is the floor and sits ABOVE it. The
  // placement loop below then clamps that value into `buckets[0]`, a bucket
  // whose label says "0.001ms – 0.002ms" and excludes it. An explicit bucket
  // named "< 0.001ms" is the only honest place for it.
  const floor = edges[0]
  const underflowed = edges.length > 0 && measurable.some((value) => value < floor)
  if (underflowed) {
    buckets.push({ from: 0, to: floor, count: 0, label: underflowLabel(floor), overflow: false, underflow: true })
  }
  for (let i = 0; i < edges.length - 1; i++) {
    buckets.push({
      from: edges[i],
      to: edges[i + 1],
      count: 0,
      label: bucketLabel(edges[i], edges[i + 1]),
      overflow: false,
      underflow: false,
    })
  }
  if (edges.length > 0) {
    const last = edges[edges.length - 1]
    buckets.push({ from: last, to: null, count: 0, label: bucketLabel(last, null), overflow: true, underflow: false })
  }
  for (const value of measurable) {
    // The last bucket whose lower edge the value reaches. `buckets[0].from` is
    // `edges[0]`, or `0` when an underflow bucket was added — so with the
    // bucket above in place, every value lies inside the range its bucket names.
    let index = 0
    for (let i = 0; i < buckets.length; i++) {
      if (value >= buckets[i].from) index = i
      else break
    }
    buckets[index].count += 1
  }
  return {
    buckets,
    counted: measurable.length,
    missing,
    zero,
    excluded: missing + zero,
    total: values.length,
    excludedStatement: excludedStatement({ missing, zero }),
  }
}

// ── p50 / p95 band ───────────────────────────────────────────────────────────

export interface DurationBandPoint {
  x: string
  /** Milliseconds, or `null` — a gap, never a zero. */
  p50: number | null
  p95: number | null
  /** The shaded band's ends: `min`/`max` of the two, so it is drawable either way. */
  low: number | null
  high: number | null
  /** True when the DATA says p95 < p50. Reported, never corrected. */
  inverted: boolean
  /** Why a percentile is missing, from the server. */
  reason: string | null
  /**
   * The sample sizes behind `p50` and `p95`, from the source series. `null`
   * when that bucket reported no point at all. They are carried because a p95
   * over 2 executions is not the claim a p95 over 200 is, and the C3 series
   * `bandToChartSeries` builds has to state the real one rather than `0`.
   */
  n50: number | null
  n95: number | null
}

export interface DurationBandModel {
  points: DurationBandPoint[]
  /** How many buckets had p95 below p50. */
  inverted: number
  /** The sentence to show when any bucket is inverted, else `null`. */
  notice: string | null
  /**
   * When the band is a zoomed slice (VIZ-407), the day just before the
   * visible range, so the first visible day can still state its change vs the
   * previous day (as `TimeSeriesModel.precedingPoint` does). `null` when the
   * slice starts on the first day; absent on an unzoomed band.
   */
  precedingPoint?: DurationBandPoint | null
}

const measuredY = (point: SeriesPoint | undefined): number | null =>
  point === undefined || point.y === null || point.measured === false ? null : point.y

/**
 * The p50/p95 band.
 *
 * When the data says p95 is BELOW p50 — which a cache split across two queries,
 * or two different samples, can genuinely produce — nothing is swapped and
 * nothing is clamped. Both lines keep their reported values, the band is drawn
 * between the min and the max so it is still a band, and the chart SAYS the
 * percentiles disagree. Silently reordering them would hide the only evidence
 * that the numbers cannot both be right.
 */
export function durationBandPoints({
  p50,
  p95,
}: {
  p50?: SeriesChart | null
  p95?: SeriesChart | null
}): DurationBandModel {
  const p50Points = p50?.series[0]?.points ?? []
  const p95Points = p95?.series[0]?.points ?? []
  const by50 = new Map(p50Points.map((point) => [point.x, point]))
  const by95 = new Map(p95Points.map((point) => [point.x, point]))
  const axis: string[] = []
  const seen = new Set<string>()
  for (const point of [...p50Points, ...p95Points]) {
    if (seen.has(point.x)) continue
    seen.add(point.x)
    axis.push(point.x)
  }
  axis.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))

  let inverted = 0
  const points = axis.map((x) => {
    const lower = measuredY(by50.get(x))
    const upper = measuredY(by95.get(x))
    const both = lower !== null && upper !== null
    const isInverted = both && (upper as number) < (lower as number)
    if (isInverted) inverted += 1
    return {
      x,
      p50: lower,
      p95: upper,
      low: both ? Math.min(lower as number, upper as number) : null,
      high: both ? Math.max(lower as number, upper as number) : null,
      inverted: isInverted,
      reason: by50.get(x)?.reason ?? by95.get(x)?.reason ?? null,
      n50: by50.get(x)?.n ?? null,
      n95: by95.get(x)?.n ?? null,
    }
  })

  return { points, inverted, notice: bandNotice(inverted) }
}

/**
 * The sentence for `inverted` buckets whose p95 is below their p50, or `null`
 * when there are none. Exported so a band SLICED to a zoomed range (VIZ-407)
 * states its own count in the same words, rather than a copy of them.
 */
export function bandNotice(inverted: number): string | null {
  return inverted > 0
    ? `p95 was below p50 on ${formatNumber(inverted)} ${inverted === 1 ? 'day' : 'days'}; both are drawn as reported.`
    : null
}

// ── Slowest tests ────────────────────────────────────────────────────────────

export interface SlowTestRow {
  name: string
  /** p95 duration in ms, or `null` when it was not measured. */
  p95: number | null
  runs: number
}

export interface SlowestTestsModel {
  rows: SlowTestRow[]
  shown: number
  total: number
  truncated: boolean
}

export const SLOWEST_TESTS_LIMIT = 20

/**
 * The slowest tests by p95. A test whose p95 was not measured sinks to the
 * BOTTOM rather than sorting as zero — it is not fast, it is unknown, and
 * ranking it as the fastest test in the project would be the more confident lie.
 */
export function rankSlowestTests(
  rows: readonly SlowTestRow[],
  { limit = SLOWEST_TESTS_LIMIT }: { limit?: number } = {},
): SlowestTestsModel {
  const ranked = [...rows].sort((a, b) => {
    if (a.p95 === null && b.p95 === null) return 0
    if (a.p95 === null) return 1
    if (b.p95 === null) return -1
    return b.p95 - a.p95
  })
  const shown = ranked.slice(0, limit)
  return { rows: shown, shown: shown.length, total: rows.length, truncated: rows.length > shown.length }
}

// ── The C3 series each chart hands its frame ─────────────────────────────────
//
// Built FROM the drawn model rather than fetched again, so `ChartFrame`'s
// summary and table view cannot disagree with the plot. A value the chart draws
// as a gap is `y: null` here with the reason, which the table prints as "—".

export function histogramToChartSeries(model: DurationHistogramModel): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['duration_bucket'],
    x_type: 'category',
    series: [
      {
        key: 'executions',
        label: 'Executions',
        points: model.buckets.map((bucket) => ({ x: bucket.label, y: bucket.count, n: bucket.count })),
      },
    ],
  }
}

export function bandToChartSeries(model: DurationBandModel): SeriesChart {
  // `n` is the sample size the SOURCE reported for that percentile, never a
  // flat 0: a declared `n: 0` beside a measured value says "this number came
  // from nothing", which is a stronger — and false — claim than saying nothing.
  const point = (x: string, value: number | null, n: number | null, reason: string | null) =>
    value === null
      ? { x, y: null, n: n ?? 0, measured: false as const, reason: reason ?? 'this percentile was not measured in this bucket' }
      : { x, y: value, n: n ?? 0 }
  return {
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [
      { key: 'p50', label: 'p50 (ms)', points: model.points.map((p) => point(p.x, p.p50, p.n50, p.reason)) },
      { key: 'p95', label: 'p95 (ms)', points: model.points.map((p) => point(p.x, p.p95, p.n95, p.reason)) },
    ],
  }
}

export function slowestToChartSeries(model: SlowestTestsModel): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['test'],
    x_type: 'category',
    series: [
      {
        key: 'p95',
        label: 'p95 (ms)',
        points: model.rows.map((row) =>
          row.p95 === null
            ? { x: row.name, y: null, n: row.runs, measured: false as const, reason: 'no run of this test carries a duration' }
            : { x: row.name, y: row.p95, n: row.runs },
        ),
      },
      {
        key: 'runs',
        label: 'Runs',
        points: model.rows.map((row) => ({ x: row.name, y: row.runs, n: row.runs })),
      },
    ],
  }
}
