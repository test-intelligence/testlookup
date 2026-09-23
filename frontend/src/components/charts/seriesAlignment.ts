/**
 * Release-over-release alignment (VIZ-404) — a CLIENT transform over the
 * absolute UTC days the API returns.
 *
 * `chart-data` answers "pass rate by day × release" on the calendar: R1's
 * first day is 2 March, R2's is 10 April. Compared on that axis the two lines
 * never overlap, so the question the reader asked ("is R2 settling faster than
 * R1 did?") has no answer on screen. Aligned, x is "days since release start"
 * and BOTH lines start at day 0.
 *
 * The rules:
 *
 *   - A series' start is the day the caller names (the release's own start
 *     date), else its first day with anything evaluated (`n > 0` or a measured
 *     value). A release with no such day starts on its first returned day and
 *     is all gaps — never zeros.
 *   - Day d of a series is its start + d UTC days. A day the API did not
 *     return for that series (past the window's end, or before its start) is a
 *     GAP with a reason, never a zero and never a borrowed neighbour.
 *   - Days before a series' start are dropped: "day -3" is not a day of that
 *     release.
 *   - Each relative day keeps the ABSOLUTE date it stands for, per series, so
 *     the table and the tooltip can say both ("Day 3 · R1 2026-03-05").
 *
 * Pure, and free of `@/` value imports: the Playwright specs import it in
 * plain Node.
 */
import type { SeriesPoint } from '@/lib/viz/contracts'

/** The x-axis title of an aligned comparison. */
export const ALIGNED_X_TITLE = 'Days since release start'

const DAY_MS = 86_400_000
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/

/** The epoch of a `YYYY-MM-DD` UTC day, or `NaN`. */
export function utcDayEpoch(day: string): number {
  return DAY_PATTERN.test(day) ? Date.parse(`${day}T00:00:00Z`) : Number.NaN
}

const toDay = (at: number): string => new Date(at).toISOString().slice(0, 10)

/** `day` plus `days` UTC days. */
export function addUtcDays(day: string, days: number): string {
  const at = utcDayEpoch(day)
  return Number.isNaN(at) ? day : toDay(at + days * DAY_MS)
}

/** Whole UTC days from `from` to `to` (negative when `to` is earlier). `NaN` if either is not a day. */
export function utcDaysBetween(from: string, to: string): number {
  const a = utcDayEpoch(from)
  const b = utcDayEpoch(to)
  return Number.isNaN(a) || Number.isNaN(b) ? Number.NaN : Math.round((b - a) / DAY_MS)
}

export interface AlignableSeries {
  key: string
  label: string
  /** `x` is a `YYYY-MM-DD` UTC day, as `chart-data` returns a day axis. */
  points: readonly SeriesPoint[]
}

export interface AlignedSeries {
  key: string
  label: string
  /** Day 0 of this series, `YYYY-MM-DD`. */
  start: string
  /**
   * Where day 0 came from: the caller's start date (`named`), the first day
   * with anything evaluated (`first-active`), or neither — a series with
   * nothing evaluated, all gaps (`none`). The caption says which, because
   * "day 0" means a different thing in each.
   */
  startSource: AlignmentStartSource
  /** `x` is the relative day as a string: `'0'`, `'1'`, … — one per axis day. */
  points: SeriesPoint[]
  /** The absolute UTC day each relative day stands for, index-aligned with `points`. */
  dates: string[]
}

export type AlignmentStartSource = 'named' | 'first-active' | 'none'

export interface AlignmentResult {
  /** `'0' … 'N'`, the relative days on the axis. */
  xs: string[]
  series: AlignedSeries[]
  /** Points whose `x` is not a day, which no alignment can place. Counted, not hidden. */
  unplaced: number
}

export interface AlignmentOptions {
  /** A series' day 0, by series key — the release's own start date. */
  starts?: Readonly<Record<string, string>>
  /** Never more relative days than this (the SVG point budget). */
  maxDays?: number
}

/** Why a relative day of one release has no value. */
export function missingDayReason(label: string, day: number, date: string): string {
  return `${label} has no data for day ${day} (${date}): that day is outside the returned window`
}

/** Why a relative day INSIDE a release's returned window has no value. */
export function unreportedDayReason(label: string, day: number, date: string): string {
  return `${label} reported nothing for day ${day} (${date})`
}

/** The reason carried by a series with nothing evaluated anywhere. */
export const NO_START_REASON = 'no day of this release had anything evaluated, so it has no start to align on'

/** The first day with anything evaluated: a sample, or a measured value. */
function firstActiveDay(points: readonly SeriesPoint[]): string | null {
  let first: string | null = null
  for (const point of points) {
    if (!DAY_PATTERN.test(point.x)) continue
    const active = point.n > 0 || (point.y !== null && point.measured !== false)
    if (active && (first === null || point.x < first)) first = point.x
  }
  return first
}

function earliestDay(points: readonly SeriesPoint[]): string | null {
  let first: string | null = null
  for (const point of points) if (DAY_PATTERN.test(point.x) && (first === null || point.x < first)) first = point.x
  return first
}

/**
 * Align every series on "days since its own start". Both lines begin at day 0;
 * the axis runs to the longest series' last returned day.
 */
export function alignByReleaseStart(
  input: readonly AlignableSeries[],
  { starts = {}, maxDays = Number.POSITIVE_INFINITY }: AlignmentOptions = {},
): AlignmentResult {
  let unplaced = 0
  const prepared = input.map((series) => {
    const byDay = new Map<string, SeriesPoint>()
    let last: string | null = null
    for (const point of series.points) {
      if (!DAY_PATTERN.test(point.x)) {
        unplaced += 1
        continue
      }
      byDay.set(point.x, point)
      if (last === null || point.x > last) last = point.x
    }
    const named = starts[series.key]
    const namedStart = named && DAY_PATTERN.test(named) ? named : null
    const firstActive = firstActiveDay(series.points)
    const start = namedStart ?? firstActive ?? earliestDay(series.points)
    const startSource: AlignmentStartSource = namedStart ? 'named' : firstActive ? 'first-active' : 'none'
    const span = start !== null && last !== null ? utcDaysBetween(start, last) : -1
    return { series, byDay, start, startSource, last, span, active: firstActive !== null }
  })

  const longest = prepared.reduce((max, entry) => Math.max(max, entry.span), -1)
  const days = Math.min(longest + 1, Math.max(0, Math.floor(maxDays)))
  const xs = Array.from({ length: Math.max(0, days) }, (_, day) => String(day))

  const series = prepared.map(({ series: source, byDay, start, startSource, last, active }) => {
    const origin = start ?? ''
    const dates = xs.map((_, day) => (start === null ? '' : addUtcDays(origin, day)))
    const points = xs.map((x, day): SeriesPoint => {
      const date = dates[day]
      const found = date ? byDay.get(date) : undefined
      if (!active) {
        return { x, y: null, n: found?.n ?? 0, measured: false, reason: NO_START_REASON }
      }
      if (!found) {
        // Inside the window the API returned for this series, a missing day is
        // one it reported nothing for — not one outside the window.
        const inside = date !== '' && last !== null && date <= last
        return {
          x,
          y: null,
          n: 0,
          measured: false,
          reason: inside ? unreportedDayReason(source.label, day, date) : missingDayReason(source.label, day, date || NO_DATE),
        }
      }
      // The point as the API sent it, re-keyed: a gap stays a gap with its own reason.
      return found.y === null || found.measured === false
        ? { x, y: null, n: found.n, measured: false, reason: found.reason ?? missingDayReason(source.label, day, date) }
        : { x, y: found.y, n: found.n }
    })
    return { key: source.key, label: source.label, start: origin, startSource, points, dates }
  })

  return { xs, series, unplaced }
}

const NO_DATE = 'no date'

/**
 * The row header of one relative day in the table view: the relative day AND
 * the absolute day it is for every series — "Day 3 (R1 2026-03-05; R2 2026-04-13)".
 */
export function alignedRowLabel(day: number, series: readonly Pick<AlignedSeries, 'label' | 'dates'>[]): string {
  const dates = series.map((s) => `${s.label} ${s.dates[day] || NO_DATE}`).join('; ')
  return dates ? `Day ${day} (${dates})` : `Day ${day}`
}
