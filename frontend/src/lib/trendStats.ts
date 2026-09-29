/**
 * Trend analysis for a daily pass-rate series (VIZ-405).
 *
 * PURE and DETERMINISTIC: it reads only the series it is given (each day's
 * rate and `n`, the evaluated executions behind that rate) — no clock, no
 * server state, no randomness, no model fitting beyond ordinary least squares.
 * Every function is O(n) over at most `TREND_MAX_DAYS` points. The same module
 * produces the numbers the chart draws, the words the `ChartFrame` takeaway
 * uses and the explanation behind each number, so the three cannot disagree.
 *
 * THE RULES, in one place — each one is also stated to the reader:
 *
 *   Input      One point per UTC day (`YYYY-MM-DD`), each exactly one day after
 *              the one before — a weekly or otherwise sparse axis is refused,
 *              because every rule below counts calendar days. A day "with
 *              runs" has a finite `rate` AND `n > 0`. A `rate: null` day is a
 *              GAP — never a 0 % day — even when it carries an execution count
 *              (a day of nothing but skips reports executions and no rate).
 *              The still-filling day (`partial: true`) is LEFT OUT of every
 *              statistic, and of every day count a sentence states: its rate
 *              moves until the day closes.
 *   Minimum    Fewer than `TREND_MIN_DAYS_WITH_RUNS` (7) days with runs → no
 *              overlays at all, reason `INSUFFICIENT_DATA_REASON`.
 *   Moving     Trailing 7 calendar days, POOLED: Σ(rate·n) / Σn over the days
 *   average    with runs in the window, so each day counts by its executions.
 *              Gap days are skipped (not zeros). A value is produced only on a
 *              day that itself has runs, whose whole window lies inside the
 *              series, and whose window holds at least 4 days with runs.
 *   Trend      Weighted least squares of rate on calendar day (a gap still
 *              takes up a day on the x axis), weight = `n`. A 3-test day has a
 *              hundredth of the pull of a 300-test day. Slope is reported per
 *              week. It is "flat" — no direction claimed — when it is under
 *              `FLAT_SLOPE_PTS_PER_WEEK` (0.25) either way, OR no larger than
 *              its own standard error: an 80/90 alternating series fits
 *              -0.27 pts/week at a standard error of 0.85, and "falling" would
 *              state noise as a direction. A FITTED value is shown clamped to
 *              0..100 and marked "(fit)" — a line through a step change runs
 *              past 100 % at its end, and no pass rate does.
 *   Anomaly    A day is judged against the SAME WEEKDAY in the previous 4
 *              weeks (7, 14, 21 and 28 days before it), needing at least 2 of
 *              those days with runs. It is flagged when its rate is MORE THAN
 *              3 median absolute deviations below their median (MAD floored at
 *              1 pt, so a perfectly steady weekday does not flag every 0.1-pt
 *              dip and nothing is divided by 0) AND lower than every one of
 *              them. Days under `ANOMALY_MIN_EXECUTIONS` (50) executions are
 *              neither judged nor used in a baseline. Why this rule, in
 *              numbers — see `anomaliesOf`.
 *   Period     "Last 7 days vs previous 7", pooled like the moving average,
 *              ending on the last COMPLETE day. Each week needs at least
 *              `PERIOD_MIN_EXECUTIONS` (100) executions on
 *              `PERIOD_MIN_DAYS_WITH_RUNS` (3) days with runs, and the
 *              sentence states both weeks' executions. Computed even when the
 *              long-window overlays are unavailable, because a long window
 *              hides a recent incident.
 *
 * Wording is descriptive ("rising", "falling", "flat", "flagged as unusual"),
 * never causal.
 */
import { formatNumber } from '@/utils/formatters'

// ── Constants (every one of them appears in a sentence a reader sees) ──────

export const TREND_MIN_DAYS_WITH_RUNS = 7
export const TREND_MAX_DAYS = 366
export const MOVING_AVERAGE_WINDOW_DAYS = 7
export const MOVING_AVERAGE_MIN_DAYS = 4
/** Weeks of the same weekday a day is compared with. */
export const ANOMALY_BASELINE_WEEKS = 4
/** Same-weekday days with runs a day needs behind it to be judged at all. */
export const ANOMALY_MIN_BASELINE_DAYS = 2
export const ANOMALY_MAD_THRESHOLD = 3
export const ANOMALY_MAD_FLOOR_PTS = 1
/**
 * Executions a day needs to be judged, or to be part of a baseline. At 50,
 * one execution moves the day's rate by at most 2 pts — less than the
 * smallest dip the rule can flag (3 MADs of at least 1 pt), so a single
 * failing execution can never, by itself, make a day "unusual".
 */
export const ANOMALY_MIN_EXECUTIONS = 50
export const FLAT_SLOPE_PTS_PER_WEEK = 0.25
export const PERIOD_DAYS = 7
/**
 * What each week of "last 7 vs previous 7" needs. At 100 executions one
 * execution moves the week's rate by at most 1 pt, and on 3 days no single
 * day can BE the week. The real API reports a rate from a single execution
 * (`MIN_RATE_SAMPLE = 1`), so without this one 1-execution day at 0 % became
 * the chart's headline: "down 95.0 pts".
 */
export const PERIOD_MIN_EXECUTIONS = 100
export const PERIOD_MIN_DAYS_WITH_RUNS = 3

export const INSUFFICIENT_DATA_REASON = `Needs at least ${TREND_MIN_DAYS_WITH_RUNS} days with runs`
export const TOO_LONG_REASON = `Trend overlays cover up to ${TREND_MAX_DAYS} days`
export const NOT_DAILY_REASON = 'Trend overlays need one bucket per UTC day, with no day missing'

export const MOVING_AVERAGE_LABEL = '7-day moving average'
export const TREND_LINE_LABEL = 'Trend line'
export const ANOMALY_LABEL = 'Flagged as unusual'
/**
 * A number and its unit never part at a line break: the takeaway wrapped as
 * "(up 1.8" / "pts; 1,485 vs …" once the toolbar narrowed its column
 * (baseline review B). A no-break space binds them — and binds a rate of
 * change to its whole unit, "0.6 pts per week", which reads as one quantity.
 */
export const NBSP = String.fromCharCode(0xa0)
export const ANOMALY_RULE =
  `Rule: more than ${ANOMALY_MAD_THRESHOLD}${NBSP}MADs below the median of the same weekday in the previous ` +
  `${ANOMALY_BASELINE_WEEKS} weeks, and below each of them; days under ${ANOMALY_MIN_EXECUTIONS} executions are not judged.`

// ── Types ──────────────────────────────────────────────────────────────────

export interface TrendInputPoint {
  /** A UTC day, `YYYY-MM-DD`. */
  x: string
  /** Pass rate 0..100, or `null` for a gap. */
  rate: number | null
  /** Evaluated executions behind `rate` — the weight. */
  n: number
  /** The still-filling day: left out of every statistic. */
  partial?: boolean
}

export interface MovingAveragePoint {
  x: string
  value: number
  /** Days with runs in the window. */
  days: number
  /** Executions pooled. */
  executions: number
}

export type TrendDirection = 'rising' | 'falling' | 'flat'

/** Why a slope is reported as flat: inside the fixed band, or no larger than its own standard error. */
export type FlatReason = 'under-threshold' | 'within-standard-error'

export interface TrendFit {
  slopePerDay: number
  /** `slopePerDay × 7`; exactly 0 for a constant series. */
  slopePerWeek: number
  /**
   * The slope's standard error, per week: days are the observations, their
   * executions the (relative) weights, so it does not shrink just because a
   * suite runs more tests. `null` with fewer than 3 days with runs.
   */
  slopeStandardErrorPerWeek: number | null
  /** Execution-weighted mean rate — the line passes through it. */
  mean: number
  direction: TrendDirection
  /** Set exactly when `direction` is `'flat'`. */
  flatBecause: FlatReason | null
  /**
   * The fitted value on every series day from the first to the last day with
   * runs — UNCLAMPED, for drawing a straight line; `formatTrendFit` is what a
   * reader is shown.
   */
  line: { x: string; value: number }[]
  daysWithRuns: number
  executions: number
}

export interface Anomaly {
  x: string
  rate: number
  n: number
  /** The day's UTC weekday, e.g. "Monday" — its baseline is the same weekday. */
  weekday: string
  /** Median of the baseline days. */
  median: number
  /** Raw median absolute deviation of the baseline. */
  mad: number
  /** `max(mad, ANOMALY_MAD_FLOOR_PTS)` — what the rule divides by. */
  effectiveMad: number
  /** How many effective MADs below the median the day sits. */
  deviations: number
  /** Same-weekday days with runs in the previous `ANOMALY_BASELINE_WEEKS` weeks. */
  baselineDays: number
}

export interface PeriodStat {
  from: string
  to: string
  rate: number
  executions: number
  daysWithRuns: number
}

export type PeriodComparison =
  | { measurable: true; last: PeriodStat; previous: PeriodStat; deltaPts: number; explain: string }
  | { measurable: false; reason: string; explain: string }

export interface TrendOverlayState {
  movingAverage: boolean
  trendLine: boolean
}

export interface TrendExplanations {
  movingAverage: string
  trendLine: string
  anomalies: string
  period: string
}

interface TrendCommon {
  /** Calendar days from the first to the last point, inclusive (the still-filling day too). */
  spanDays: number
  daysWithRuns: number
  period: PeriodComparison
}

export type TrendAnalysis =
  | (TrendCommon & {
      available: true
      from: string
      /** The last COMPLETE day: the still-filling day is not in the window the sentences describe. */
      to: string
      /** Calendar days `from`..`to`, inclusive — the "N days" every sentence states. */
      windowDays: number
      executions: number
      /** The still-filling day that was left out, if any. */
      excludedPartial: string | null
      movingAverage: MovingAveragePoint[]
      fit: TrendFit
      anomalies: Anomaly[]
      judgedDays: number
      explain: TrendExplanations
    })
  | (TrendCommon & { available: false; reason: string })

// ── Preparing the series ───────────────────────────────────────────────────

const DAY_MS = 86_400_000
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/

interface Usable {
  /** Calendar-day offset from the series' first point. */
  t: number
  x: string
  rate: number
  n: number
}

interface Prepared {
  ok: boolean
  reason: string | null
  /** Epoch of the first point's day. */
  start: number
  spanDays: number
  usable: Usable[]
  /** Offset of the last point that is not still filling, or -1. */
  lastComplete: number
  excludedPartial: string | null
}

const dayOf = (start: number, t: number) => new Date(start + t * DAY_MS).toISOString().slice(0, 10)

function prepare(points: readonly TrendInputPoint[]): Prepared {
  const empty: Prepared = { ok: false, reason: INSUFFICIENT_DATA_REASON, start: 0, spanDays: 0, usable: [], lastComplete: -1, excludedPartial: null }
  if (points.length === 0) return empty
  const start = DAY_PATTERN.test(points[0].x) ? Date.parse(`${points[0].x}T00:00:00Z`) : Number.NaN
  if (Number.isNaN(start)) return { ...empty, reason: NOT_DAILY_REASON }
  const usable: Usable[] = []
  let previous = -1
  let lastComplete = -1
  let excludedPartial: string | null = null
  for (const point of points) {
    const at = DAY_PATTERN.test(point.x) ? Date.parse(`${point.x}T00:00:00Z`) : Number.NaN
    if (Number.isNaN(at)) return { ...empty, reason: NOT_DAILY_REASON }
    const t = Math.round((at - start) / DAY_MS)
    // Each point exactly one day after the one before. Ascending alone let
    // weekly buckets (every 7 days) through as "daily": the 7-day average then
    // pooled one bucket, and the period comparison compared single weeks.
    if (t !== previous + 1) return { ...empty, reason: NOT_DAILY_REASON }
    previous = t
    if (point.partial) {
      excludedPartial = point.x
      continue
    }
    lastComplete = t
    if (point.rate !== null && Number.isFinite(point.rate) && Number.isFinite(point.n) && point.n > 0) {
      usable.push({ t, x: point.x, rate: point.rate, n: point.n })
    }
  }
  return { ok: true, reason: null, start, spanDays: previous + 1, usable, lastComplete, excludedPartial }
}

// ── Moving average ─────────────────────────────────────────────────────────

function movingAverageOf(usable: readonly Usable[]): MovingAveragePoint[] {
  const out: MovingAveragePoint[] = []
  let lo = 0
  let sumW = 0
  let sumWY = 0
  for (let i = 0; i < usable.length; i++) {
    const day = usable[i]
    sumW += day.n
    sumWY += day.n * day.rate
    const windowStart = day.t - (MOVING_AVERAGE_WINDOW_DAYS - 1)
    while (usable[lo].t < windowStart) {
      sumW -= usable[lo].n
      sumWY -= usable[lo].n * usable[lo].rate
      lo += 1
    }
    const days = i - lo + 1
    // The whole window must lie inside the series, and hold enough days with runs.
    if (windowStart < 0 || days < MOVING_AVERAGE_MIN_DAYS) continue
    out.push({ x: day.x, value: sumWY / sumW, days, executions: sumW })
  }
  return out
}

/** The pooled, trailing 7-day moving average — see the module comment for the rules. */
export function movingAverage(points: readonly TrendInputPoint[]): MovingAveragePoint[] {
  const prepared = prepare(points)
  return prepared.ok ? movingAverageOf(prepared.usable) : []
}

// ── Weighted least squares ─────────────────────────────────────────────────

/** Below this, a slope is floating-point noise around a constant series. */
const SLOPE_EPSILON = 1e-9

function flatReasonOf(slopePerWeek: number, standardErrorPerWeek: number | null): FlatReason | null {
  if (Math.abs(slopePerWeek) < FLAT_SLOPE_PTS_PER_WEEK) return 'under-threshold'
  if (standardErrorPerWeek !== null && Math.abs(slopePerWeek) <= standardErrorPerWeek) return 'within-standard-error'
  return null
}

function fitOf(prepared: Prepared): TrendFit | null {
  const { usable, start } = prepared
  if (usable.length < 2) return null
  let sumW = 0
  let sumWT = 0
  let sumWY = 0
  for (const day of usable) {
    sumW += day.n
    sumWT += day.n * day.t
    sumWY += day.n * day.rate
  }
  const tBar = sumWT / sumW
  const mean = sumWY / sumW
  let sxy = 0
  let sxx = 0
  for (const day of usable) {
    const dt = day.t - tBar
    sxy += day.n * dt * (day.rate - mean)
    sxx += day.n * dt * dt
  }
  if (sxx === 0) return null
  let slopePerDay = sxy / sxx
  if (Math.abs(slopePerDay * 7) < SLOPE_EPSILON) slopePerDay = 0
  const slopePerWeek = slopePerDay === 0 ? 0 : slopePerDay * 7
  // Standard error with the executions as RELATIVE weights and the days as the
  // observations: s² = Σ n·r² / (k − 2), Var(slope) = s² / Σ n·(t − t̄)². Both
  // sums scale with the weights, so doubling every day's executions leaves it
  // alone — a big suite is not thereby a certain one.
  let standardErrorPerWeek: number | null = null
  if (usable.length > 2) {
    let ssr = 0
    for (const day of usable) {
      const residual = day.rate - (mean + slopePerDay * (day.t - tBar))
      ssr += day.n * residual * residual
    }
    standardErrorPerWeek = Math.sqrt(ssr / (usable.length - 2) / sxx) * 7
  }
  const flatBecause = flatReasonOf(slopePerWeek, standardErrorPerWeek)
  const first = usable[0].t
  const last = usable[usable.length - 1].t
  const line: { x: string; value: number }[] = []
  for (let t = first; t <= last; t++) line.push({ x: dayOf(start, t), value: mean + slopePerDay * (t - tBar) })
  return {
    slopePerDay,
    slopePerWeek,
    slopeStandardErrorPerWeek: standardErrorPerWeek,
    mean,
    direction: flatBecause ? 'flat' : slopePerWeek > 0 ? 'rising' : 'falling',
    flatBecause,
    line,
    daysWithRuns: usable.length,
    executions: sumW,
  }
}

/** The execution-weighted least-squares trend, or `null` with fewer than two days with runs. */
export function weightedTrend(points: readonly TrendInputPoint[]): TrendFit | null {
  const prepared = prepare(points)
  return prepared.ok ? fitOf(prepared) : null
}

// ── Anomalies ──────────────────────────────────────────────────────────────

function medianOf(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 === 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'] as const
const weekdayOf = (x: string) => WEEKDAYS[new Date(`${x}T00:00:00Z`).getUTCDay()]

/**
 * WHY THE SAME WEEKDAY. The first rule compared each day with the 7 calendar
 * days before it. A suite at 96 % on weekdays and 88 % at weekends has, for
 * every Saturday and Sunday, a baseline of five 96s and two 88s: median 96,
 * MAD 0 — and with MAD floored at 1 pt every weekend day sat 8 MADs down. It
 * flagged 16 of 52 judged days of a suite doing exactly what it always does;
 * an 80/90 alternating series flagged 12 of 24. No robust scale can fix that
 * inside one week: two lows in seven ARE outliers by a 50 %-breakdown
 * estimator's own definition. The weekly pattern has to be compared like for
 * like, so the baseline is the same weekday in the previous 4 weeks.
 *
 * WHY "AND BELOW EACH OF THEM". Lag 7 is odd, so an alternating series gives
 * an 80 day the baseline [90, 80, 90]: median 90, MAD 0, and 80 is 10 floored
 * MADs down although 80 is in the baseline. A value this weekday has already
 * reached inside the window is a level, not an anomaly. (It is more robust
 * than "skip a MAD of 0 over unequal values", which a ±0.5-pt jitter defeats.)
 *
 * WHAT IT COSTS. A day needs 2 same-weekday days with runs behind it, so
 * nothing in a window's first 14 days is judged; a 10-day history before a
 * 6-day incident flags its 5th and 6th days (the first to have 2 Mondays and
 * Tuesdays behind them), where the old rule flagged the first four. With four
 * weeks of history every day of an incident's first week is flagged — each is
 * compared with normal days, never with the incident's own earlier days.
 *
 * On the gallery fixture: 2026-03-23 (79.0 %) against Mondays 94.4, 94.1 and
 * 93.4 — median 94.1, MAD 0.3 floored to 1 — is 15.1 MADs down (the 7-day rule
 * said 14.9), and 13 days are judged (23 before: the first two weeks are not).
 */
function anomaliesOf(usable: readonly Usable[]): { anomalies: Anomaly[]; judgedDays: number } {
  // Only days with enough executions are judged OR stand in a baseline.
  const byDay = new Map<number, Usable>()
  for (const day of usable) if (day.n >= ANOMALY_MIN_EXECUTIONS) byDay.set(day.t, day)
  const anomalies: Anomaly[] = []
  let judgedDays = 0
  for (const day of byDay.values()) {
    // At most 4 values, so the sorts are O(1).
    const baseline: number[] = []
    for (let week = 1; week <= ANOMALY_BASELINE_WEEKS; week++) {
      const earlier = byDay.get(day.t - 7 * week)
      if (earlier) baseline.push(earlier.rate)
    }
    if (baseline.length < ANOMALY_MIN_BASELINE_DAYS) continue
    judgedDays += 1
    const median = medianOf(baseline)
    const mad = medianOf(baseline.map((v) => Math.abs(v - median)))
    const effectiveMad = Math.max(mad, ANOMALY_MAD_FLOOR_PTS)
    const below = median - day.rate
    if (below > ANOMALY_MAD_THRESHOLD * effectiveMad && day.rate < Math.min(...baseline)) {
      anomalies.push({
        x: day.x,
        rate: day.rate,
        n: day.n,
        weekday: weekdayOf(day.x),
        median,
        mad,
        effectiveMad,
        deviations: below / effectiveMad,
        baselineDays: baseline.length,
      })
    }
  }
  return { anomalies, judgedDays }
}

/** Days more than 3 (floored) MADs below, and below each of, the same weekday in the 4 weeks before them. */
export function detectAnomalies(points: readonly TrendInputPoint[]): { anomalies: Anomaly[]; judgedDays: number } {
  const prepared = prepare(points)
  return prepared.ok ? anomaliesOf(prepared.usable) : { anomalies: [], judgedDays: 0 }
}

// ── Period over period ─────────────────────────────────────────────────────

const PERIOD_METHOD = 'Pooled over executions, ending on the last complete day'
const PERIOD_MINIMUM = `each week needs at least ${PERIOD_MIN_EXECUTIONS} executions on ${PERIOD_MIN_DAYS_WITH_RUNS} days with runs`

function periodOf(prepared: Prepared): PeriodComparison {
  const notMeasurable = (reason: string): PeriodComparison => ({ measurable: false, reason, explain: `${reason}. ${PERIOD_METHOD}.` })
  if (!prepared.ok) return notMeasurable(prepared.reason ?? NOT_DAILY_REASON)
  const end = prepared.lastComplete
  const firstPrevious = end - 2 * PERIOD_DAYS + 1
  if (end < 0 || firstPrevious < 0) {
    return notMeasurable(`Needs ${2 * PERIOD_DAYS} days to compare the last ${PERIOD_DAYS} with the previous ${PERIOD_DAYS}`)
  }
  const pool = (from: number, to: number): PeriodStat => {
    let w = 0
    let wy = 0
    let days = 0
    for (const day of prepared.usable) {
      if (day.t < from || day.t > to) continue
      w += day.n
      wy += day.n * day.rate
      days += 1
    }
    return { from: dayOf(prepared.start, from), to: dayOf(prepared.start, to), rate: w > 0 ? wy / w : Number.NaN, executions: w, daysWithRuns: days }
  }
  const last = pool(end - PERIOD_DAYS + 1, end)
  const previous = pool(firstPrevious, end - PERIOD_DAYS)
  // Checked in reading order, so the reason names the week a reader looks at first.
  for (const [name, stat] of [[`last ${PERIOD_DAYS} days`, last], [`previous ${PERIOD_DAYS} days`, previous]] as const) {
    if (stat.executions < PERIOD_MIN_EXECUTIONS || stat.daysWithRuns < PERIOD_MIN_DAYS_WITH_RUNS) {
      return notMeasurable(
        `The ${name} have ${plural(stat.executions, 'execution')} on ${plural(stat.daysWithRuns, 'day')} with runs; ${PERIOD_MINIMUM}`,
      )
    }
  }
  const describe = (stat: PeriodStat) =>
    `${stat.from} to ${stat.to}: ${pct(stat.rate)}, ${plural(stat.executions, 'execution')} on ${plural(stat.daysWithRuns, 'day')}.`
  const partial = prepared.excludedPartial ? ` ${partialNote(prepared.excludedPartial)}` : ''
  return {
    measurable: true,
    last,
    previous,
    deltaPts: last.rate - previous.rate,
    explain: `${PERIOD_METHOD}; ${PERIOD_MINIMUM}. ${describe(last)} ${describe(previous)}${partial}`,
  }
}

/** "Last 7 days vs previous 7", pooled over executions. */
export function periodOverPeriod(points: readonly TrendInputPoint[]): PeriodComparison {
  return periodOf(prepare(points))
}

// ── Words ──────────────────────────────────────────────────────────────────

/** One decimal, and never "-0.0". */
function fixed1(value: number): string {
  const rounded = Math.round(value * 10) / 10
  return (rounded === 0 ? 0 : rounded).toFixed(1)
}
const pct = (value: number) => `${fixed1(value)}%`
/** The number–unit helpers below bind with `NBSP` (see there). */
const points = (value: string | number) => `${value}${NBSP}pts`
const pointsPerWeek = (value: string | number) => `${points(value)}${NBSP}per${NBSP}week`
const mads = (value: string) => `${value}${NBSP}MADs`
const count = (value: number) => formatNumber(value)
const plural = (value: number, noun: string) => `${count(value)} ${value === 1 ? noun : `${noun}s`}`
const partialNote = (day: string) => `${day} is still filling and is left out.`
/** A FITTED value as a reader sees it: clamped to 0..100 — a line can run past 100 %, a pass rate cannot. */
const fit = (value: number) => `${pct(Math.min(100, Math.max(0, value)))} (fit)`

/** The ChartFrame takeaway for the long window, e.g. "Pass rate is falling 0.8 pts per week (30 days, 27 days with runs)". */
export function trendTakeaway(analysis: TrendAnalysis): string | null {
  if (!analysis.available) return null
  // The days the FIT used: a still-filling last day is in neither count.
  const sample = `(${plural(analysis.windowDays, 'day')}, ${plural(analysis.daysWithRuns, 'day')} with runs)`
  const { fit: line } = analysis
  if (line.flatBecause === 'under-threshold') {
    return `Pass rate is flat: under ${pointsPerWeek(FLAT_SLOPE_PTS_PER_WEEK)} either way ${sample}`
  }
  if (line.flatBecause === 'within-standard-error') {
    return (
      `Pass rate is flat: its slope, ${pointsPerWeek(fixed1(Math.abs(line.slopePerWeek)))}, is within its standard error ` +
      `of ${fixed1(line.slopeStandardErrorPerWeek ?? 0)} ${sample}`
    )
  }
  return `Pass rate is ${line.direction} ${pointsPerWeek(fixed1(Math.abs(line.slopePerWeek)))} ${sample}`
}

/**
 * "Last 7 days 81.5% vs 90.0% the previous 7 (down 8.5 pts; 650 vs 600
 * executions)", or `null` when not measurable — including when either week is
 * under the minimum sample.
 */
export function periodTakeaway(period: PeriodComparison): string | null {
  if (!period.measurable) return null
  // The change is taken between the ROUNDED rates shown, so the sentence adds up.
  const delta = Math.round((Number(fixed1(period.last.rate)) - Number(fixed1(period.previous.rate))) * 10) / 10
  const change = delta === 0 ? 'no change' : `${delta > 0 ? 'up' : 'down'} ${points(fixed1(Math.abs(delta)))}`
  const sample = `${count(period.last.executions)} vs ${plural(period.previous.executions, 'execution')}`
  return `Last ${PERIOD_DAYS} days ${pct(period.last.rate)} vs ${pct(period.previous.rate)} the previous ${PERIOD_DAYS} (${change}; ${sample})`
}

/**
 * The `ChartFrame` takeaway for a chart with trend analysis on: the module's
 * trend sentence while an overlay is SHOWN (otherwise the caller's own
 * takeaway), and the period-over-period line beside it whenever it is
 * measurable — the recent week is never hidden behind the long window.
 */
export function trendFrameTakeaway(
  analysis: TrendAnalysis,
  shown: TrendOverlayState,
  fallback: string | undefined,
): string | undefined {
  const overlayOn = analysis.available && (shown.movingAverage || shown.trendLine)
  const parts = [overlayOn ? trendTakeaway(analysis) : fallback, periodTakeaway(analysis.period)].filter(
    (part): part is string => Boolean(part),
  )
  return parts.length > 0 ? parts.join(' · ') : undefined
}

/** What an anomaly's tooltip says: the numbers, then the rule that flagged it. */
export function anomalyRuleText(anomaly: Anomaly): string {
  const floored = anomaly.mad < ANOMALY_MAD_FLOOR_PTS ? `, floored at ${ANOMALY_MAD_FLOOR_PTS}${NBSP}pt` : ''
  return (
    `${pct(anomaly.rate)} is ${mads(fixed1(anomaly.deviations))} below ${pct(anomaly.median)}, the median of the ` +
    `${count(anomaly.baselineDays)} previous ${anomaly.weekday}s (MAD ${points(fixed1(anomaly.mad))}${floored}; ` +
    `${plural(anomaly.n, 'execution')}). ${ANOMALY_RULE}`
  )
}

/**
 * The explanation behind each statistic: method, window, sample — each kept
 * under 240 characters, because it is also the control's accessible
 * DESCRIPTION and a screen reader reads all of it on every focus.
 */
function explanations(
  prepared: Prepared,
  windowDays: number,
  ma: MovingAveragePoint[],
  line: TrendFit,
  judgedDays: number,
  flagged: number,
  period: PeriodComparison,
): TrendExplanations {
  const from = dayOf(prepared.start, 0)
  const to = dayOf(prepared.start, windowDays - 1)
  const days = prepared.usable.length
  const partial = prepared.excludedPartial ? ` ${partialNote(prepared.excludedPartial)}` : ''
  const signed = (value: number) => (value === 0 ? '0.0' : `${value > 0 ? '+' : '-'}${fixed1(Math.abs(value))}`)
  const error = line.slopeStandardErrorPerWeek === null ? '' : ` ± ${fixed1(line.slopeStandardErrorPerWeek)}`
  return {
    movingAverage:
      `Method: each day pooled with the ${MOVING_AVERAGE_WINDOW_DAYS - 1} before it, weighted by executions; days without ` +
      `runs are skipped, never 0%. Window: ${MOVING_AVERAGE_WINDOW_DAYS} calendar days, ${MOVING_AVERAGE_MIN_DAYS} or more ` +
      `with runs. Sample: ${plural(ma.length, 'day')} averaged from ${plural(days, 'day')} with runs ` +
      `(${plural(line.executions, 'execution')}).${partial}`,
    trendLine:
      `Method: least-squares line, each day weighted by its executions. Window: ${from} to ${to} ` +
      `(${plural(windowDays, 'day')}, ${count(days)} with runs, ${plural(line.executions, 'execution')}). ` +
      `Slope: ${pointsPerWeek(`${signed(line.slopePerWeek)}${error}`)}; flat if under ${FLAT_SLOPE_PTS_PER_WEEK} or within its ` +
      `standard error.${partial}`,
    anomalies:
      `Rule: more than ${mads(String(ANOMALY_MAD_THRESHOLD))} (floored at ${ANOMALY_MAD_FLOOR_PTS}${NBSP}pt) below the median of the same ` +
      `weekday in the previous ${ANOMALY_BASELINE_WEEKS} weeks, and below each of them. Needs ${ANOMALY_MIN_BASELINE_DAYS} ` +
      `such days; days under ${ANOMALY_MIN_EXECUTIONS} executions are not judged. Sample: ${plural(judgedDays, 'day')} ` +
      `judged, ${count(flagged)} flagged.${partial}`,
    period: period.explain,
  }
}

// ── The whole analysis ─────────────────────────────────────────────────────

export function analyzeTrend(points: readonly TrendInputPoint[]): TrendAnalysis {
  const prepared = prepare(points)
  const period = periodOf(prepared)
  const common = { spanDays: prepared.spanDays, daysWithRuns: prepared.usable.length, period }
  if (!prepared.ok) return { ...common, available: false, reason: prepared.reason ?? NOT_DAILY_REASON }
  if (points.length > TREND_MAX_DAYS || prepared.spanDays > TREND_MAX_DAYS) {
    return { ...common, available: false, reason: TOO_LONG_REASON }
  }
  const fit = prepared.usable.length >= TREND_MIN_DAYS_WITH_RUNS ? fitOf(prepared) : null
  if (!fit) return { ...common, available: false, reason: INSUFFICIENT_DATA_REASON }
  const ma = movingAverageOf(prepared.usable)
  const { anomalies, judgedDays } = anomaliesOf(prepared.usable)
  // The window the sentences describe ends on the last COMPLETE day.
  const windowDays = prepared.lastComplete + 1
  return {
    ...common,
    available: true,
    from: dayOf(prepared.start, 0),
    to: dayOf(prepared.start, windowDays - 1),
    windowDays,
    executions: fit.executions,
    excludedPartial: prepared.excludedPartial,
    movingAverage: ma,
    fit,
    anomalies,
    judgedDays,
    explain: explanations(prepared, windowDays, ma, fit, judgedDays, anomalies.length, period),
  }
}

// ── Per-day rows, for the tooltip, the keyboard cursor and the table ───────

interface DayIndex {
  movingAverage: Map<string, MovingAveragePoint>
  trendLine: Map<string, { x: string; value: number }>
  anomalies: Map<string, Anomaly>
}

/** Built once per analysis, so asking about every day of a year stays O(n). */
const dayIndexes = new WeakMap<object, DayIndex>()

function dayIndex(analysis: Extract<TrendAnalysis, { available: true }>): DayIndex {
  let index = dayIndexes.get(analysis)
  if (!index) {
    index = {
      movingAverage: new Map(analysis.movingAverage.map((p) => [p.x, p])),
      trendLine: new Map(analysis.fit.line.map((p) => [p.x, p])),
      anomalies: new Map(analysis.anomalies.map((a) => [a.x, a])),
    }
    dayIndexes.set(analysis, index)
  }
  return index
}

export interface TrendDayRow {
  key: 'movingAverage' | 'trendLine' | 'anomaly'
  label: string
  value: string
}

/**
 * What the analysis says about one day, in the order a tooltip lists it. An
 * overlay that is OFF contributes nothing; a flagged day is ALWAYS stated,
 * with the rule that flagged it.
 */
export function trendRowsForDay(analysis: TrendAnalysis, x: string, shown: TrendOverlayState): TrendDayRow[] {
  if (!analysis.available) return []
  const index = dayIndex(analysis)
  const rows: TrendDayRow[] = []
  if (shown.movingAverage) {
    const ma = index.movingAverage.get(x)
    if (ma) {
      rows.push({
        key: 'movingAverage',
        label: MOVING_AVERAGE_LABEL,
        value: `${pct(ma.value)} (${plural(ma.days, 'day')} with runs, ${plural(ma.executions, 'execution')})`,
      })
    }
  }
  if (shown.trendLine) {
    const fitted = index.trendLine.get(x)
    if (fitted) rows.push({ key: 'trendLine', label: TREND_LINE_LABEL, value: fit(fitted.value) })
  }
  const anomaly = index.anomalies.get(x)
  if (anomaly) rows.push({ key: 'anomaly', label: ANOMALY_LABEL, value: anomalyRuleText(anomaly) })
  return rows
}

/** Formatting shared with the chart, so a number reads the same everywhere. */
export const formatTrendPercent = pct
/** A fitted (trend-line) value: clamped to 0..100 and marked "(fit)". */
export const formatTrendFit = fit
export const formatTrendSlope = (analysis: TrendAnalysis): string | null =>
  analysis.available ? pointsPerWeek(fixed1(Math.abs(analysis.fit.slopePerWeek))) : null
