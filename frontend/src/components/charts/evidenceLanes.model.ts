/**
 * W39 STRAT-07: what the "Observability evidence" card draws, from the run's stored evidence
 * (`RunObservabilityTimeline`, contract §1.2). Pure — no React, no DOM — so every rule is unit-tested.
 *
 * Nothing is invented. An item whose instant cannot be read here is never placed at 0, at the axis start or at "now":
 * it joins its lane's "Without a time" list (`time_unreadable`), next to the ones the server already listed there. A
 * Prometheus sample whose time is not a finite number is dropped; a sample whose value is `null` is a gap in its line,
 * never a zero. The axis comes from the items' own times and the run window, never from a recomputed fetch window,
 * and there is no axis at all when nothing in a lane with evidence is timed.
 */
import { formatClock, formatMonthDay, formatNumber } from '@/utils/formatters'
import {
  LANE_SOURCES,
  ZONE_ASSUMED_SENTENCE,
  type ObservabilityEvent,
  type ObservabilityLane,
  type ObservabilitySeries,
  type ObservabilityUntimed,
  type ObservabilityWindow,
  type RunObservabilityTimeline,
} from '@/types/observabilityTimeline'

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
/** Tick spacings, smallest first; the axis takes the first that needs at most `AXIS_TICKS_MAX` ticks. */
const TICK_STEPS = [
  SECOND, 2 * SECOND, 5 * SECOND, 10 * SECOND, 15 * SECOND, 30 * SECOND,
  MINUTE, 2 * MINUTE, 5 * MINUTE, 10 * MINUTE, 15 * MINUTE, 30 * MINUTE,
  HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 30 * DAY,
]
export const AXIS_TICKS_MAX = 6
/** A span of one instant is drawn on this much room either side (axis padding, never an item's time). */
export const SINGLE_INSTANT_PAD_MS = 30 * SECOND
/** OCP first → last bars are packed into at most this many rows; the rest share the last row. */
export const SPAN_ROWS_MAX = 4

export type AxisState = 'timed' | 'untimed' | 'none'

export interface EvidenceAxis {
  startMs: number
  endMs: number
  spanMs: number
  ticks: number[]
}

/** One timed item: an instant, or a first → last span (OCP), on a packed row. */
export interface EvidenceMark {
  kind: 'instant' | 'span'
  startMs: number
  endMs: number
  row: number
  event: ObservabilityEvent
}

/** One Prometheus series: its drawable runs of `[ms, value]` (split at `null` values) and its value range. */
export interface EvidenceSeries {
  series: ObservabilitySeries
  segments: [number, number][][]
  min: number
  max: number
  /** Samples with a finite time and value. */
  drawn: number
}

export interface EvidenceLaneView {
  lane: ObservabilityLane
  marks: EvidenceMark[]
  rows: number
  series: EvidenceSeries[]
  /** The server's untimed items, then the events whose instant could not be read here. */
  untimed: ObservabilityUntimed[]
  /** Anything to place on the axis. */
  timed: boolean
}

export interface EvidenceView {
  axis: EvidenceAxis | null
  axisState: AxisState
  runWindow: { startMs: number; endMs: number } | null
  lanes: EvidenceLaneView[]
}

/** A date-time with no zone (`2026-10-20T10:00:05`, `…05.250`). */
const ZONELESS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/

/**
 * An ISO instant → epoch ms, or null (missing, empty, unparseable). The answer's instants are UTC (contract §1.2), so
 * one that arrives without a zone is read as UTC — never as this browser's local time.
 */
export function parseInstant(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const text = value.trim()
  const ms = Date.parse(ZONELESS.test(text) ? `${text}Z` : text)
  return Number.isFinite(ms) ? ms : null
}

/** Ticks on whole multiples of a step, inside [start, end]. */
export function axisFrom(startMs: number, endMs: number): EvidenceAxis {
  let start = startMs
  let end = endMs
  if (end <= start) {
    start -= SINGLE_INSTANT_PAD_MS
    end += SINGLE_INSTANT_PAD_MS
  }
  const spanMs = end - start
  // n intervals can carry n + 1 ticks (both ends on a whole step).
  const step = TICK_STEPS.find((s) => spanMs / s <= AXIS_TICKS_MAX - 1) ?? TICK_STEPS[TICK_STEPS.length - 1]
  const ticks: number[] = []
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) ticks.push(t)
  return { startMs: start, endMs: end, spanMs, ticks }
}

/** An instant's x on a plot `width` px wide. */
export function xOf(axis: EvidenceAxis, ms: number, width: number): number {
  return ((ms - axis.startMs) / axis.spanMs) * width
}

function untimedFrom(event: ObservabilityEvent): ObservabilityUntimed {
  return { title: event.title, detail: event.detail, test_case_id: event.test_case_id, test_name: event.test_name, reason: 'time_unreadable' }
}

/** Greedy packing: each mark takes the first row whose last mark ended before it starts (capped). */
function pack(marks: Omit<EvidenceMark, 'row'>[]): { marks: EvidenceMark[]; rows: number } {
  const ends: number[] = []
  const sorted = [...marks].sort((a, b) => a.startMs - b.startMs)
  const packed = sorted.map((mark) => {
    let row = ends.findIndex((end) => end < mark.startMs)
    if (row === -1) row = ends.length < SPAN_ROWS_MAX ? ends.length : SPAN_ROWS_MAX - 1
    ends[row] = Math.max(ends[row] ?? -Infinity, mark.endMs)
    return { ...mark, row }
  })
  return { marks: packed, rows: Math.max(1, ends.length) }
}

function seriesView(series: ObservabilitySeries): EvidenceSeries {
  const segments: [number, number][][] = []
  let current: [number, number][] = []
  let min = Infinity
  let max = -Infinity
  let drawn = 0
  for (const point of Array.isArray(series.points) ? series.points : []) {
    const [at, value] = Array.isArray(point) ? point : []
    if (typeof at !== 'number' || !Number.isFinite(at)) continue
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      if (current.length) segments.push(current)
      current = []
      continue
    }
    current.push([at * 1000, value])
    min = Math.min(min, value)
    max = Math.max(max, value)
    drawn += 1
  }
  if (current.length) segments.push(current)
  return { series, segments, min, max, drawn }
}

export function laneView(lane: ObservabilityLane): EvidenceLaneView {
  const untimed = [...(lane.untimed ?? [])]
  const placed: Omit<EvidenceMark, 'row'>[] = []
  for (const event of lane.events ?? []) {
    const startMs = parseInstant(event.at)
    if (startMs === null) {
      untimed.push(untimedFrom(event))
      continue
    }
    const endMs = parseInstant(event.end)
    if (endMs !== null && endMs > startMs) placed.push({ kind: 'span', startMs, endMs, event })
    else placed.push({ kind: 'instant', startMs, endMs: startMs, event })
  }
  const { marks, rows } = pack(placed)
  const series = (lane.series ?? []).map(seriesView)
  const timed = marks.length > 0 || series.some((s) => s.drawn > 0)
  return { lane, marks, rows, series, untimed, timed }
}

function windowMs(window: ObservabilityWindow | null | undefined): { startMs: number; endMs: number } | null {
  const startMs = parseInstant(window?.start)
  const endMs = parseInstant(window?.end)
  return startMs !== null && endMs !== null && endMs >= startMs ? { startMs, endMs } : null
}

/** The lanes in `LANE_SOURCES` order (a source the vocabulary does not name is never drawn), the axis and its state. */
export function buildEvidenceView(data: RunObservabilityTimeline): EvidenceView {
  const given = Array.isArray(data.lanes) ? data.lanes : []
  const lanes = LANE_SOURCES.flatMap((source) => given.filter((lane) => lane.source === source).slice(0, 1)).map(laneView)
  const runWindow = windowMs(data.run_window)
  const shown = lanes.filter((view) => view.lane.state === 'evidence')
  let axis: EvidenceAxis | null = null
  if (shown.some((view) => view.timed)) {
    // The server's axis, widened (never narrowed) to every drawn item, so no mark falls off the plot.
    const instants: number[] = []
    const server = windowMs(data.axis)
    if (server) instants.push(server.startMs, server.endMs)
    if (runWindow) instants.push(runWindow.startMs, runWindow.endMs)
    for (const view of shown) {
      for (const mark of view.marks) instants.push(mark.startMs, mark.endMs)
      for (const s of view.series) for (const segment of s.segments) for (const [at] of segment) instants.push(at)
    }
    axis = axisFrom(Math.min(...instants), Math.max(...instants))
  }
  const anyUntimed = shown.some((view) => view.untimed.length > 0)
  return { axis, axisState: axis ? 'timed' : anyUntimed ? 'untimed' : 'none', runWindow, lanes }
}

// ── Text (React text children only: titles and details come from stored tool output) ──

/** A tick's or an item's clock time; the day too when the axis spans more than one. */
export function instantLabel(ms: number, spanMs: number): string {
  return spanMs >= DAY ? `${formatMonthDay(ms)} ${formatClock(ms)}` : formatClock(ms)
}

/** A mark's hover / focus title: when, the level, the item, its detail, its test and its count. */
export function markText(mark: EvidenceMark, spanMs: number): string {
  const { event } = mark
  const when = mark.kind === 'span'
    ? `${instantLabel(mark.startMs, spanMs)} → ${instantLabel(mark.endMs, spanMs)}`
    : instantLabel(mark.startMs, spanMs)
  const parts = [
    event.level ? `${when} [${event.level}]` : when,
    event.title,
    event.detail,
    event.test_name ? `Test: ${event.test_name}` : null,
    typeof event.count === 'number' && Number.isFinite(event.count) ? `Seen ${formatNumber(event.count)}×` : null,
    event.zone_assumed ? ZONE_ASSUMED_SENTENCE : null,
  ]
  return parts.filter((part): part is string => typeof part === 'string' && part !== '').join(' — ')
}
