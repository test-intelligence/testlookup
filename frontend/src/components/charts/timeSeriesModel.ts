/**
 * The pure model behind `TimeSeriesChart` (VIZ-403).
 *
 * Nothing here touches React or a chart engine, so every rule the story sets
 * is table-testable — and the renderer, the summary and the table view all
 * read the SAME model rather than each deriving their own.
 *
 * The rules it encodes, in one place:
 *
 *   - A day with no runs is a GAP in the rate line, never a zero. `/metrics/
 *     trends` zero-fills such a day and reports `pass_rate: 0`, which is the
 *     single most misleading number this chart could draw: "no runs" and "every
 *     test failed" would look identical. A rate is `null` unless something was
 *     actually evaluated (skips and unknowns are outside the denominator, so a
 *     day of nothing but skips has no rate either). The EXECUTION bar keeps its
 *     genuine zero — only the rate is unknown.
 *   - `measured: false` from VIZ-203 is the same gap, carrying the server's
 *     reason so the tooltip can say why rather than show "—" with no cause.
 *   - Buckets are UTC days while the rest of the UI is local time. The axis
 *     caption says so, and the tooltip carries the local instants the day
 *     covers — computed with `Intl` and a real time zone, so a UTC day that
 *     spans a DST transition reports both offsets instead of assuming one.
 *   - Release markers sit on the bucket that CONTAINS the release instant; a
 *     release exactly on midnight UTC opens that bucket rather than closing the
 *     previous one. Releases outside the window are dropped AND counted.
 *   - A zoomed rate axis is allowed, but then `AXIS_NOT_ZERO_LABEL` must be
 *     shown: a 92–98 % axis makes a 3-point dip look like a collapse.
 */
import type { EnvelopeMeta, SeriesChart, SeriesPoint } from '@/lib/viz/contracts'
import type { TrendPoint } from '@/types/metrics'
import { alignedZeroBasedScale, niceScale } from './niceScale'

/** Points per series Recharts (SVG) draws before the ECharts renderer takes over. */
export const SVG_POINT_LIMIT = 366

/** Shown under the x axis: the buckets are UTC days, the rest of the UI is local. */
export const UTC_AXIS_CAPTION = 'Days are UTC buckets; hover a day for your local equivalent'

/** Shown whenever the rate axis is zoomed away from zero. */
export const AXIS_NOT_ZERO_LABEL = 'Axis does not start at 0'

/** The y axis titles. The second axis gets its OWN title, never a bare number scale. */
export const RATE_AXIS_TITLE = 'Pass rate %'
export const EXECUTIONS_AXIS_TITLE = 'Executions'

/**
 * Why a rate is missing when the source gave no reason of its own. Mirrors the
 * backend's `_NO_SAMPLE_REASON`: a rate over nothing is not 0 %.
 */
export const NO_RATE_REASON =
  'no evaluated executions in this bucket: every test was skipped or had no verdict, and a rate over nothing is not 0%'

export interface TimeSeriesPoint {
  /** The bucket key, a UTC day `YYYY-MM-DD` for a day-grained chart. */
  x: string
  /** Pass rate 0..100, or `null` — a GAP. Never 0 for "no runs". */
  rate: number | null
  /** Why `rate` is null. `null` when the rate is measured. */
  rateReason: string | null
  /** Executions in the bucket. `0` is a real zero; `null` is "not reported". */
  executions: number | null
  /** Sample size behind `rate`. */
  n: number
  /** The bucket `meta.partial_day` names: still filling, drawn partial. */
  partial: boolean
}

export interface ReleaseInput {
  id: string
  name: string
  /** An ISO instant or a `YYYY-MM-DD` day. `null` = not released; ignored. */
  date: string | null | undefined
}

export interface ReleaseMarker {
  /** The bucket the marker sits on. */
  x: string
  /** Every release on that bucket, in the order given. */
  names: string[]
  ids: string[]
}

// ── Adapters ─────────────────────────────────────────────────────────────────

const DAY_MS = 86_400_000
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/

/** The UTC day a `YYYY-MM-DD` names, as an epoch. `NaN` for anything else. */
function dayStart(day: string): number {
  return DAY_PATTERN.test(day) ? Date.parse(`${day}T00:00:00Z`) : Number.NaN
}

const toDay = (at: number): string => new Date(at).toISOString().slice(0, 10)

/** Every UTC day from `from` to `to` inclusive. Empty when either is unparseable. */
export function utcDayRange(from: string, to: string): string[] {
  const start = dayStart(from)
  const end = dayStart(to)
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return []
  const out: string[] = []
  for (let at = start; at <= end; at += DAY_MS) out.push(toDay(at))
  return out
}

export interface TrendAdapterOptions {
  /** Fill the calendar between these UTC days; a day the payload never sent is still a bucket. */
  from?: string
  to?: string
}

/**
 * `/metrics/trends` → model points.
 *
 * `pass_rate` is not recomputed — the endpoint's own number is used — but it is
 * GATED on the status counts: the endpoint reports `0` for a day it zero-filled
 * AND for a day whose every test was skipped, and only the counts can tell
 * those apart from a genuine 0 %. So the rate is kept when `evaluated > 0`
 * (passed + failed + broken) and is `null` otherwise, which is the gap.
 */
export function timeSeriesFromTrends(points: TrendPoint[], options: TrendAdapterOptions = {}): TimeSeriesPoint[] {
  const byDay = new Map<string, TimeSeriesPoint>()
  for (const point of points) {
    const passed = point.passed ?? 0
    const failed = point.failed ?? 0
    const broken = point.broken ?? 0
    const skipped = point.skipped ?? 0
    // The pass-rate denominator: skipped and unknown are outside it.
    const evaluated = passed + failed + broken
    const executions = point.total ?? passed + failed + broken + skipped
    byDay.set(point.date, {
      x: point.date,
      rate: evaluated > 0 ? point.pass_rate : null,
      rateReason: evaluated > 0 ? null : NO_RATE_REASON,
      executions,
      n: evaluated,
      partial: false,
    })
  }
  const axis =
    options.from && options.to
      ? utcDayRange(options.from, options.to)
      : [...byDay.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  return axis.map(
    (day) =>
      byDay.get(day) ?? {
        x: day,
        // A day the payload never mentioned: nothing is known, not even the volume.
        rate: null,
        rateReason: NO_RATE_REASON,
        executions: null,
        n: 0,
        partial: false,
      },
  )
}

export interface ChartDataAdapterInput {
  /** The `pass_rate` (or any 0..100 rate) series from `/analytics/chart-data`. */
  rate?: SeriesChart | null
  /** The `executions` (or any volume) series from the same endpoint. */
  executions?: SeriesChart | null
}

const firstPoints = (chart: SeriesChart | null | undefined): SeriesPoint[] => chart?.series[0]?.points ?? []

/**
 * Two C3 series (one metric each, as `chart-data` returns them) → model points.
 * The x axes are UNIONED: a bucket only one of the two reported is still a
 * bucket, with the other side a gap, so a missing metric never shortens the axis.
 */
export function timeSeriesFromChartData({ rate, executions }: ChartDataAdapterInput): TimeSeriesPoint[] {
  const ratePoints = firstPoints(rate)
  const executionPoints = firstPoints(executions)
  const byRate = new Map(ratePoints.map((p) => [p.x, p]))
  const byExecutions = new Map(executionPoints.map((p) => [p.x, p]))
  const axis: string[] = []
  const seen = new Set<string>()
  for (const point of [...ratePoints, ...executionPoints]) {
    if (seen.has(point.x)) continue
    seen.add(point.x)
    axis.push(point.x)
  }
  axis.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  return axis.map((x) => {
    const r = byRate.get(x)
    const e = byExecutions.get(x)
    const measured = r !== undefined && r.y !== null && r.measured !== false
    return {
      x,
      rate: measured ? (r.y as number) : null,
      rateReason: measured ? null : (r?.reason ?? NO_RATE_REASON),
      executions: e === undefined || e.y === null || e.measured === false ? null : e.y,
      n: r?.n ?? e?.n ?? 0,
      partial: false,
    }
  })
}

// ── Rate axis ────────────────────────────────────────────────────────────────

export interface RateAxis {
  domain: [number, number]
  /**
   * The ticks, handed to the renderer rather than left to it. Given only a
   * zoomed domain of 90-100, Recharts drew ticks at 90, 93, 96 and 100 — a
   * last interval of 4 after two of 3 — so the grid read as unevenly spaced
   * and no label sat on a round value. These are a nice step apart
   * (`niceScale`: 2.5 for a 10-point span), each printed at the precision
   * its step needs (92.5, not 93).
   */
  ticks: number[]
  startsAtZero: boolean
  /** `AXIS_NOT_ZERO_LABEL` when the axis is zoomed off zero, else `null`. */
  label: string | null
}

/** Headroom around a zoomed rate domain, in percentage points, snapped to this step. */
const ZOOM_STEP = 5
/** The rate axis aims for this many intervals; the executions axis then uses the same count. */
const RATE_AXIS_INTERVALS = 4

/** The rate axis over `[low, high]`, snapped OUTWARD to a nice step. Every ladder step divides 100. */
function rateScale(low: number, high: number): Pick<RateAxis, 'domain' | 'ticks'> {
  const { domain, ticks } = niceScale(low, high, { intervals: RATE_AXIS_INTERVALS })
  return { domain: [Math.max(0, domain[0]), Math.min(100, domain[1])], ticks: ticks.filter((t) => t >= 0 && t <= 100) }
}

/**
 * The rate axis domain. Zooming is allowed — a 92–98 % band is unreadable on a
 * 0–100 axis — but a zoomed axis MUST announce itself, because the same dip
 * drawn on a 90–100 axis looks ten times worse than on a 0–100 one.
 */
export function rateAxisDomain(
  points: readonly { rate: number | null }[],
  { zoom }: { zoom: boolean },
): RateAxis {
  const full: RateAxis = { ...rateScale(0, 100), startsAtZero: true, label: null }
  if (!zoom) return full
  let min = Number.POSITIVE_INFINITY
  let max = Number.NEGATIVE_INFINITY
  for (const point of points) {
    if (point.rate === null || !Number.isFinite(point.rate)) continue
    if (point.rate < min) min = point.rate
    if (point.rate > max) max = point.rate
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return full
  const low = Math.max(0, Math.floor((min - ZOOM_STEP) / ZOOM_STEP) * ZOOM_STEP)
  const high = Math.min(100, Math.ceil((max + ZOOM_STEP) / ZOOM_STEP) * ZOOM_STEP)
  const scale = rateScale(low, high)
  const startsAtZero = scale.domain[0] === 0
  return { ...scale, startsAtZero, label: startsAtZero ? null : AXIS_NOT_ZERO_LABEL }
}

// ── Executions axis ──────────────────────────────────────────────────────────

export interface ExecutionsAxis {
  /** `[0, max]`, `max` >= every drawn execution count: no bar is clipped. */
  domain: [number, number]
  /** As many intervals as the rate axis, so both axes' ticks sit on one grid. */
  ticks: number[]
  /** The largest execution count drawn (0 when none is). */
  largest: number
}

/**
 * The right-hand axis. Zero-based (a bar is a length), whole-number ticks,
 * and the SAME interval count as the rate axis: the grid is drawn from the
 * rate axis, and a second scale whose ticks fall between its lines reads as a
 * second, misaligned grid.
 */
export function executionsAxisDomain(
  points: readonly { executions: number | null }[],
  rateAxis: Pick<RateAxis, 'ticks'>,
): ExecutionsAxis {
  let largest = 0
  for (const point of points) {
    if (point.executions !== null && Number.isFinite(point.executions) && point.executions > largest) {
      largest = point.executions
    }
  }
  const intervals = Math.max(1, rateAxis.ticks.length - 1)
  const { domain, ticks } = alignedZeroBasedScale(largest, intervals, { integer: true })
  return { domain, ticks, largest }
}

// ── Release markers ──────────────────────────────────────────────────────────

/**
 * The bucket a release instant belongs to.
 *
 * Bucket boundaries are half-open `[day, day+1)`: a release at exactly
 * `2026-03-04T00:00:00Z` OPENS the 4th's bucket. Closing the 3rd's with it
 * would attribute the release to the day before it shipped — and a release
 * stamped at midnight is the common case, not a corner one.
 */
export function bucketForInstant(at: number, buckets: readonly string[]): string | null {
  if (!Number.isFinite(at) || buckets.length === 0) return null
  if (buckets.every((bucket) => DAY_PATTERN.test(bucket))) {
    for (const bucket of buckets) {
      const start = dayStart(bucket)
      if (at >= start && at < start + DAY_MS) return bucket
    }
    return null
  }
  // A non-day axis (hourly, weekly): the last bucket that opens at or before `at`.
  let found: string | null = null
  for (const bucket of buckets) {
    const start = Date.parse(bucket)
    if (Number.isNaN(start)) continue
    if (at >= start) found = bucket
  }
  return found
}

export interface ReleaseMarkerResult {
  markers: ReleaseMarker[]
  /**
   * Releases that COULD NOT BE PLACED on the drawn axis: dated outside the
   * window, or carrying a date nothing can parse. Both are dropped from the
   * plot, and both are counted here — a release with a malformed date used to
   * vanish without a trace, which reads as "there were no releases" rather than
   * "one release could not be drawn".
   *
   * `date: null` is NOT counted: that is "not released yet", a different fact.
   */
  outsideWindow: number
}

export function releaseMarkers(
  releases: readonly ReleaseInput[],
  buckets: readonly string[],
): ReleaseMarkerResult {
  const byBucket = new Map<string, ReleaseMarker>()
  let outsideWindow = 0
  for (const release of releases) {
    if (!release.date) continue
    const at = Date.parse(DAY_PATTERN.test(release.date) ? `${release.date}T00:00:00Z` : release.date)
    if (Number.isNaN(at)) {
      // A date nothing can parse is still a release the reader will not see.
      outsideWindow += 1
      continue
    }
    const bucket = bucketForInstant(at, buckets)
    if (bucket === null) {
      outsideWindow += 1
      continue
    }
    const existing = byBucket.get(bucket)
    if (existing) {
      existing.names.push(release.name)
      existing.ids.push(release.id)
    } else {
      byBucket.set(bucket, { x: bucket, names: [release.name], ids: [release.id] })
    }
  }
  // Axis order, so the markers read left to right as they are drawn.
  const markers = buckets.filter((b) => byBucket.has(b)).map((b) => byBucket.get(b) as ReleaseMarker)
  return { markers, outsideWindow }
}

/** The markers as table rows — the same markers the plot draws, for the table view. */
export function releaseMarkerRows(markers: readonly ReleaseMarker[]): { day: string; names: string }[] {
  return markers.map((marker) => ({ day: marker.x, names: marker.names.join(', ') }))
}

// ── UTC buckets vs the viewer's clock ────────────────────────────────────────

export interface LocalDayRange {
  /** The local instant the UTC day opens, e.g. "Mar 7, 19:00". */
  start: string
  /** The local instant its last minute begins, e.g. "Mar 8, 19:59". */
  end: string
  /**
   * True when the zone's offset differs between the two ends — a UTC day that
   * spans a DST transition. Callers say so rather than implying one offset.
   */
  offsetChanged: boolean
}

function offsetMinutes(at: number, timeZone: string | undefined): number | null {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'longOffset' }).formatToParts(
      new Date(at),
    )
    const name = parts.find((part) => part.type === 'timeZoneName')?.value ?? ''
    const match = /GMT([+-])(\d{2}):?(\d{2})?/.exec(name)
    if (!match) return name === 'GMT' ? 0 : null
    const sign = match[1] === '-' ? -1 : 1
    return sign * (Number(match[2]) * 60 + Number(match[3] ?? 0))
  } catch {
    return null
  }
}

/**
 * The local instants a UTC day covers. The end is the day's LAST MINUTE, not
 * the next day's midnight, so the range a reader sees is closed on both ends.
 */
export function localDayRange(
  utcDay: string,
  { timeZone, locale = 'en-US' }: { timeZone?: string; locale?: string } = {},
): LocalDayRange {
  const start = dayStart(utcDay)
  if (Number.isNaN(start)) return { start: utcDay, end: utcDay, offsetChanged: false }
  const end = start + DAY_MS - 60_000
  const format = new Intl.DateTimeFormat(locale, {
    timeZone,
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  const startOffset = offsetMinutes(start, timeZone)
  const endOffset = offsetMinutes(end, timeZone)
  return {
    start: format.format(new Date(start)),
    end: format.format(new Date(end)),
    offsetChanged: startOffset !== null && endOffset !== null && startOffset !== endOffset,
  }
}

/** The viewer's calendar date, in their zone, for an instant. */
function localDay(at: Date, timeZone: string | undefined): string {
  // `en-CA` formats as YYYY-MM-DD, which is the shape we compare.
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(at)
}

/**
 * The sentence a viewer whose calendar disagrees with UTC needs when the newest
 * UTC bucket is empty.
 *
 * At UTC+13, "today" in the chart opened 13 hours ago local and may hold no
 * runs at all while the reader's own calendar has already turned over. Without
 * this the chart looks broken rather than early.
 *
 * The direction MATTERS. A reader at UTC-11 has not reached the UTC day yet —
 * their date is EARLIER, not later — and telling them it is "already" the day
 * before is a plain falsehood about their own clock. The two cases get the two
 * words they deserve ("already" / "still"), and a reader whose calendar agrees
 * with UTC is told nothing, because there is nothing surprising to explain.
 */
export function utcTodayNote({
  latest,
  now,
  timeZone,
}: {
  latest: { x: string; rate: number | null } | null | undefined
  now: Date
  timeZone?: string
}): string | null {
  if (!latest || latest.rate !== null) return null
  const utcToday = now.toISOString().slice(0, 10)
  if (latest.x !== utcToday) return null
  const viewerDay = localDay(now, timeZone)
  if (viewerDay === utcToday) return null
  // Both are YYYY-MM-DD, so a string comparison IS a date comparison.
  const when = viewerDay > utcToday ? 'already' : 'still'
  return `The newest bucket is today in UTC (${utcToday}) and has no runs yet; your local date is ${when} ${viewerDay}.`
}

// ── The model ────────────────────────────────────────────────────────────────

export type TimeSeriesRenderer = 'svg' | 'echarts'

/** SVG up to `SVG_POINT_LIMIT` points per series; ECharts (with `sampling`) above it. */
export function pickRenderer(pointsPerSeries: number): TimeSeriesRenderer {
  return pointsPerSeries > SVG_POINT_LIMIT ? 'echarts' : 'svg'
}

export interface TimeSeriesModel {
  points: TimeSeriesPoint[]
  rateAxis: RateAxis
  executionsAxis: ExecutionsAxis
  markers: ReleaseMarker[]
  markersOutsideWindow: number
  partialDay: string | null
  inProgressCount: number
  /** x values whose rate is measured but has no measured neighbour: draw a dot or it is invisible. */
  isolated: string[]
  /** Exactly one measured rate in the whole series: a dot, never a line. */
  singlePoint: boolean
  /** Buckets with no rate. */
  gaps: number
  renderer: TimeSeriesRenderer
  caption: string
}

export interface BuildTimeSeriesInput {
  points: TimeSeriesPoint[]
  meta?: EnvelopeMeta | null
  releases?: readonly ReleaseInput[]
  /** Zoom the rate axis to the data. Off by default: a 0–100 axis needs no caveat. */
  zoomRateAxis?: boolean
}

export function buildTimeSeriesModel({
  points,
  meta,
  releases = [],
  zoomRateAxis = false,
}: BuildTimeSeriesInput): TimeSeriesModel {
  const partialDay = meta?.partial_day ?? null
  const marked = points.map((point) => ({ ...point, partial: point.x === partialDay }))
  const buckets = marked.map((point) => point.x)
  const placed = releaseMarkers(releases, buckets)

  const isolated: string[] = []
  let gaps = 0
  let measured = 0
  for (let i = 0; i < marked.length; i++) {
    if (marked[i].rate === null) {
      gaps += 1
      continue
    }
    measured += 1
    const before = i > 0 ? marked[i - 1].rate : null
    const after = i < marked.length - 1 ? marked[i + 1].rate : null
    if (before === null && after === null) isolated.push(marked[i].x)
  }

  const rateAxis = rateAxisDomain(marked, { zoom: zoomRateAxis })
  return {
    points: marked,
    rateAxis,
    executionsAxis: executionsAxisDomain(marked, rateAxis),
    markers: placed.markers,
    markersOutsideWindow: placed.outsideWindow,
    partialDay,
    inProgressCount: meta?.includes_in_progress ?? 0,
    isolated,
    singlePoint: measured === 1,
    gaps,
    renderer: pickRenderer(marked.length),
    caption: UTC_AXIS_CAPTION,
  }
}

/**
 * The model as a C3 `SeriesChart`, for `ChartFrame`'s summary and table view.
 * Built from the model the renderer draws, so the three cannot disagree — a
 * gap in the plot is a "—" in the table and "no data" in the summary.
 */
export function timeSeriesToChartSeries(model: TimeSeriesModel): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [
      {
        key: 'pass_rate',
        label: RATE_AXIS_TITLE,
        points: model.points.map((point) =>
          point.rate === null
            ? { x: point.x, y: null, n: point.n, measured: false, reason: point.rateReason ?? NO_RATE_REASON }
            : { x: point.x, y: point.rate, n: point.n },
        ),
      },
      {
        key: 'executions',
        label: EXECUTIONS_AXIS_TITLE,
        points: model.points.map((point) =>
          point.executions === null
            ? { x: point.x, y: null, n: point.n, measured: false, reason: 'this bucket reported no executions' }
            : { x: point.x, y: point.executions, n: point.n },
        ),
      },
    ],
  }
}
