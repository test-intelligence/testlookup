/**
 * The pure half of the VIZ-407 zoom: slicing an already-built chart model to a
 * day range, and every sentence the zoom says. Nothing here touches React.
 *
 * The rules it encodes, in one place:
 *
 *   - A zoom SLICES the model the chart was going to draw; it never rebuilds
 *     one from the sliced data. Rebuilding would re-run decisions that belong
 *     to the whole window: the multi-series "top 7 + Other" fold would re-rank
 *     on ten days and hand the lines different colours, and the VIZ-405 trend
 *     analysis would lose the four weeks of history its anomaly rule looks
 *     back over and the six days its first moving average needs. So the
 *     analysis is computed on the FULL model and only its per-day values are
 *     cut to the range (`sliceTrendAnalysis`).
 *   - The y axes are kept from the full model. A zoom changes which DAYS are
 *     shown, not the scale they are read on: a rescaled axis would make a
 *     three-point dip in the zoomed week look like a collapse — exactly what
 *     `AXIS_NOT_ZERO_LABEL` exists to warn about, arriving without the warning.
 *   - What is derived from the drawn points IS re-derived on the slice: which
 *     points are isolated (a measured day whose neighbour is now off-screen
 *     must be drawn as a dot or it is invisible), the single-point rule, the
 *     gap count the summary states, and whether the still-filling day is in
 *     view.
 *   - The first visible day keeps its predecessor (`precedingPoint`), so a
 *     tooltip can still state "change vs the previous day" on it.
 *   - A zoom is kept as the two day KEYS it covers, not as indexes: when the
 *     data changes, a range whose days are gone is dropped rather than
 *     silently re-pointed at whatever days now sit at those positions.
 */
import type { TrendAnalysis } from '@/lib/trendStats'
import { formatNumber } from '@/utils/formatters'
import { pickRenderer, type ReleaseMarker, type TimeSeriesModel } from '../timeSeriesModel'
import { windowWords } from './windowWords'

/** An inclusive range of positions on a chart's x axis. */
export interface ZoomRange {
  start: number
  end: number
}

/** A zoom as it is REMEMBERED: the first and last day key it covers. */
export interface ZoomKeys {
  from: string
  to: string
}

// ── Ranges ───────────────────────────────────────────────────────────────────

/** `range` clamped to an axis of `count` positions, with `start <= end`. `null` when the axis is empty. */
export function clampRange(range: ZoomRange, count: number): ZoomRange | null {
  if (count <= 0) return null
  const clamp = (value: number) => Math.min(count - 1, Math.max(0, Math.round(Number.isFinite(value) ? value : 0)))
  const a = clamp(range.start)
  const b = clamp(range.end)
  return { start: Math.min(a, b), end: Math.max(a, b) }
}

/** True when `range` covers every position — which is no zoom at all. */
export function isFullRange(range: ZoomRange, count: number): boolean {
  return range.start <= 0 && range.end >= count - 1
}

/**
 * The remembered zoom on the CURRENT axis, or `null` — unzoomed — when there is
 * none, when either day is no longer on the axis (the data moved on), or when
 * the range would cover the whole axis.
 */
export function resolveZoom(xs: readonly string[], keys: ZoomKeys | null | undefined): ZoomRange | null {
  if (!keys) return null
  const start = xs.indexOf(keys.from)
  const end = xs.indexOf(keys.to)
  if (start < 0 || end < 0 || end < start) return null
  const range = { start, end }
  return isFullRange(range, xs.length) ? null : range
}

/** The keys to remember for `range` on `xs`, or `null` for "not zoomed". */
export function zoomKeysFor(xs: readonly string[], range: ZoomRange | null): ZoomKeys | null {
  if (range === null) return null
  const clamped = clampRange(range, xs.length)
  if (clamped === null || isFullRange(clamped, xs.length)) return null
  return { from: xs[clamped.start], to: xs[clamped.end] }
}

// ── Slicing the single-series model ─────────────────────────────────────────

/**
 * `model` cut to `range`. `null` range → the model itself, untouched (so an
 * unzoomed frame is byte-for-byte the unzoomed chart).
 */
export function sliceTimeSeriesModel(model: TimeSeriesModel, range: ZoomRange | null): TimeSeriesModel {
  if (range === null) return model
  const clamped = clampRange(range, model.points.length)
  if (clamped === null) return model
  const points = model.points.slice(clamped.start, clamped.end + 1)
  const inView = new Set(points.map((point) => point.x))

  // The same neighbour rule `buildTimeSeriesModel` applies, over what is drawn.
  const isolated: string[] = []
  let gaps = 0
  let measured = 0
  for (let i = 0; i < points.length; i++) {
    if (points[i].rate === null) {
      gaps += 1
      continue
    }
    measured += 1
    const before = i > 0 ? points[i - 1].rate : null
    const after = i < points.length - 1 ? points[i + 1].rate : null
    if (before === null && after === null) isolated.push(points[i].x)
  }

  const partialInView = model.partialDay !== null && inView.has(model.partialDay)
  return {
    ...model,
    points,
    // Axes kept from the full model: see the module comment.
    markers: model.markers.filter((marker) => inView.has(marker.x)),
    // Still only the releases that could not be placed on the WINDOW. A marker
    // outside the zoom is a different fact, stated by the frame and the table.
    markersOutsideWindow: model.markersOutsideWindow,
    partialDay: partialInView ? model.partialDay : null,
    inProgressCount: partialInView ? model.inProgressCount : 0,
    isolated,
    singlePoint: measured === 1,
    gaps,
    renderer: pickRenderer(points.length),
    precedingPoint: clamped.start > 0 ? model.points[clamped.start - 1] : null,
  }
}

/** The release markers of the full model that the zoom takes off the plot. */
export function markersOutsideRange(markers: readonly ReleaseMarker[], xs: readonly string[], range: ZoomRange | null): ReleaseMarker[] {
  if (range === null) return []
  const inView = new Set(xs.slice(range.start, range.end + 1))
  return markers.filter((marker) => !inView.has(marker.x))
}

/**
 * The FULL-window analysis with its per-day values cut to the days in view.
 *
 * Everything else — the fit's slope and its window, the explanations, the
 * period comparison, `judgedDays` — is the whole window's, unchanged: those
 * sentences describe the long window, and the frame says so while zoomed. A
 * value for a day in view is the SAME object the unzoomed chart shows, never a
 * recomputation on the slice.
 */
export function sliceTrendAnalysis(analysis: TrendAnalysis, days: readonly string[]): ZoomedTrendAnalysis {
  if (!analysis.available) return analysis
  const inView = new Set(days)
  return {
    ...analysis,
    movingAverage: analysis.movingAverage.filter((point) => inView.has(point.x)),
    fit: { ...analysis.fit, line: analysis.fit.line.filter((point) => inView.has(point.x)) },
    anomalies: analysis.anomalies.filter((anomaly) => inView.has(anomaly.x)),
    zoomed: { anomaliesInWindow: analysis.anomalies.length },
  }
}

/**
 * The analysis a ZOOMED chart is given: the window's, cut to the days in view,
 * still knowing how many days the WHOLE window flagged. Without that, a
 * statistic reading "2 days flagged" sat beside an explanation saying "5
 * flagged" — both true, of different ranges, and neither saying which.
 * Absent on an unzoomed analysis, which is every analysis `analyzeTrend` makes.
 */
export type ZoomedTrendAnalysis = TrendAnalysis & { zoomed?: { anomaliesInWindow: number } }

// ── Words ────────────────────────────────────────────────────────────────────

const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

interface DayParts {
  year: number
  month: number
  day: number
}

/**
 * A UTC day's parts, read from its key rather than through `Date` — the key IS
 * the UTC day, and a `Date` rendered in the viewer's zone would move it.
 */
function partsOf(x: string): DayParts | null {
  const match = DAY_PATTERN.exec(x)
  if (!match) return null
  const month = Number(match[2])
  const day = Number(match[3])
  if (month < 1 || month > 12 || day < 1 || day > 31) return null
  return { year: Number(match[1]), month, day }
}

const shortMonth = (month: number) => MONTHS[month - 1].slice(0, 3)

/** "Sep 3" — the brush's own axis labels. Anything that is not a day is returned as it is. */
export function dayShort(x: string): string {
  const parts = partsOf(x)
  return parts ? `${shortMonth(parts.month)} ${parts.day}` : x
}

/** "September 3, 2026" — a handle's `aria-valuetext`. */
export function dayInWords(x: string): string {
  const parts = partsOf(x)
  return parts ? `${MONTHS[parts.month - 1]} ${parts.day}, ${parts.year}` : x
}

/**
 * A range as a reader says it: "Sep 3–12, 2026", "Aug 28 – Sep 12, 2026",
 * "Dec 28, 2025 – Jan 3, 2026", or one day, "Sep 3, 2026".
 */
export function rangeInWords(from: string, to: string): string {
  const a = partsOf(from)
  const b = partsOf(to)
  if (!a || !b) return from === to ? from : `${from}–${to}`
  if (from === to) return `${shortMonth(a.month)} ${a.day}, ${a.year}`
  if (a.year !== b.year) return `${shortMonth(a.month)} ${a.day}, ${a.year} – ${shortMonth(b.month)} ${b.day}, ${b.year}`
  if (a.month !== b.month) return `${shortMonth(a.month)} ${a.day} – ${shortMonth(b.month)} ${b.day}, ${b.year}`
  return `${shortMonth(a.month)} ${a.day}–${b.day}, ${b.year}`
}

const days = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'day' : 'days'}`

/** How an axis position is named. Calendar days by default; an aligned axis names relative days. */
export interface AxisWords {
  /** Short, for the brush's own labels. */
  short: (x: string) => string
  /** In full, for a handle's `aria-valuetext`. */
  full: (x: string) => string
  /** A range, for the footer, the summary and the announcement. */
  range: (from: string, to: string) => string
}

export const CALENDAR_WORDS: AxisWords = { short: dayShort, full: dayInWords, range: rangeInWords }

/** A release-aligned axis: positions are "days since release start", not dates. */
export const RELATIVE_DAY_WORDS: AxisWords = {
  short: (x) => `Day ${x}`,
  full: (x) => `Day ${x} since release start`,
  range: (from, to) => (from === to ? `day ${from} since release start` : `days ${from}–${to} since release start`),
}

/**
 * The trend strip's flagged-days count. Unzoomed: "3 days flagged as unusual",
 * exactly as before. Zoomed, BOTH counts — the days in view the chart marks,
 * and the window's count its explanation ("… judged, 5 flagged") is about:
 * "2 days flagged as unusual in view (5 in the window)".
 */
export function flaggedDaysText(label: string, inView: number, inWindow?: number): string {
  const what = label.toLowerCase()
  const shown = inView === 0 ? `No day ${what}` : `${formatNumber(inView)} ${inView === 1 ? 'day' : 'days'} ${what}`
  return inWindow === undefined ? shown : `${shown} in view (${formatNumber(inWindow)} in the window)`
}

/** What the page's announcer says when a zoom is made or changed. */
export function zoomChangeLabel(words: AxisWords, from: string, to: string): string {
  return `zoomed to ${words.range(from, to)}`
}

/** …and when it is reset. */
export function zoomResetLabel(total: number): string {
  return `zoom reset, all ${days(total)} shown`
}

/** The scope sentence the frame's summary gains while zoomed. */
export function zoomScopeLabel(words: AxisWords, from: string, to: string, scopeLabel: string | undefined): string {
  const zoom = `zoomed to ${words.range(from, to)}`
  return scopeLabel ? `${scopeLabel}; ${zoom}` : zoom
}

export interface ZoomNoteInput {
  words: AxisWords
  from: string
  to: string
  /** Days in view. */
  shown: number
  /** Days on the whole axis. */
  total: number
  /** A trend analysis is on: its statistics still describe the whole window. */
  trendAnalysis?: boolean
  /** Release markers the zoom took off the plot. */
  markersOutside?: number
}

/**
 * The frame's zoom note — visible in its footer and stamped on an export, so a
 * zoomed picture never passes for the whole window. It says what is shown,
 * that the summary, table and export follow the zoom, and what does NOT
 * follow it.
 */
export function zoomNote({ words, from, to, shown, total, trendAnalysis = false, markersOutside = 0 }: ZoomNoteInput): string {
  const parts = [
    `Zoomed to ${words.range(from, to)}: ${shown.toLocaleString('en-US')} of ${days(total)}, not the page window. The summary, table and export show these days only.`,
  ]
  if (trendAnalysis) parts.push(`Trend statistics are computed over all ${days(total)}.`)
  if (markersOutside > 0) {
    parts.push(
      `${markersOutside.toLocaleString('en-US')} ${markersOutside === 1 ? 'release marker is' : 'release markers are'} outside the zoom and listed in the table.`,
    )
  }
  return parts.join(' ')
}

// ── Promoting the zoom to the page window ────────────────────────────────────

/** The reason shown when the range does not end on the latest day. */
export const PROMOTE_NOT_LATEST_REASON = 'Only a range that ends on the latest day can become the page window'
/** The reason shown on an axis of relative days. */
export const PROMOTE_RELATIVE_REASON = 'Days since release start cannot become the page window'

export type PromoteDecision = { enabled: true; days: number } | { enabled: false; reason: string }

/** "1, 7, 14, 30 or 90". */
function orList(values: readonly number[]): string {
  const sorted = [...values].sort((a, b) => a - b).map((v) => v.toLocaleString('en-US'))
  if (sorted.length <= 1) return sorted.join('')
  return `${sorted.slice(0, -1).join(', ')} or ${sorted[sorted.length - 1]}`
}

/**
 * Can this zoom become the page's time window — and as exactly how many days?
 *
 * The global window is "the last N days" and nothing else: the store holds
 * `days`, the URL `?window=N`, and the API takes `days` with no `from`/`to`.
 * So only a range that ENDS on the latest day of the data is a window at all.
 *
 * And not every N survives. Each page SNAPS the stored window to its own
 * option set (`snapToAllowed`): storing 12 on a page that offers 1, 7, 14, 30
 * and 90 would show 14 days under a filter the reader set to 12. So a length
 * outside `windowOptions` is refused with the lengths that would work, rather
 * than applied as a different window than the one the reader chose.
 */
export function promoteDecision({
  xs,
  range,
  windowOptions,
  currentWindowDays,
  relative = false,
}: {
  xs: readonly string[]
  range: ZoomRange
  /** The lengths the page snaps its window to. */
  windowOptions: readonly number[]
  /** The window in force now. */
  currentWindowDays: number
  /** The axis is relative days (release-aligned), not dates. */
  relative?: boolean
}): PromoteDecision {
  if (relative) return { enabled: false, reason: PROMOTE_RELATIVE_REASON }
  if (range.end !== xs.length - 1) return { enabled: false, reason: PROMOTE_NOT_LATEST_REASON }
  const length = range.end - range.start + 1
  if (!windowOptions.includes(length)) {
    return {
      enabled: false,
      reason: `The page window can be ${orList(windowOptions)} days; this range is ${days(length)}`,
    }
  }
  if (length === currentWindowDays) {
    // In the words the filter bar and its chip use for that window ("last 24
    // hours" for one day), as the button and the announcement do (`windowWords`).
    return { enabled: false, reason: `The page window is already the ${windowWords(length)}` }
  }
  return { enabled: true, days: length }
}

// ── The frames' zoom option ─────────────────────────────────────────────────

/** What a time-chart frame's `zoom` prop may carry (`true` = zoomable, nothing else). */
export interface ChartZoomOptions {
  /** Start zoomed to these days — a zoomed state a page or the gallery wants to open on. */
  initial?: ZoomKeys
  /**
   * Offer "Apply as time filter". `windowOptions` is the set of lengths the
   * PAGE snaps the global window to (its own option list, e.g.
   * `REPORT_WINDOW_OPTIONS`), so only a length that survives the snap exactly
   * is offered. Leave it out where the chart's data is not the global window.
   */
  applyAsWindow?: { windowOptions: readonly number[] }
}

/** The `zoom` prop, normalised: `null` = not zoomable. */
export function zoomOptionsOf(zoom: boolean | ChartZoomOptions | undefined): ChartZoomOptions | null {
  if (zoom === undefined || zoom === false) return null
  return zoom === true ? {} : zoom
}

/** Two zooms are the same zoom (both unzoomed, or the same positions). */
export function sameRange(a: ZoomRange | null, b: ZoomRange | null): boolean {
  if (a === null || b === null) return a === b
  return a.start === b.start && a.end === b.end
}
