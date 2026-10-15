/**
 * EXEC-04: the run's waterfall (`ExecutionTimeline`), as data. Pure: no React,
 * no DOM, no colours.
 *
 * Input is the `timeline` block of `GET /api/v1/runs/{run_id}/timing`
 * (EXEC-03 contract §1): every timed test with its start, finish, offset from
 * the earliest start and, when the report named it, its worker.
 *
 * Lanes:
 *   - by WORKER when the report named one: one lane per worker, ordered by
 *     worker id (natural order, so `gw2` comes before `gw10`). A worker that
 *     ran overlapping tests (async tests, nested fixtures) gets extra rows for
 *     the overlap rather than bars drawn on top of each other;
 *   - otherwise GREEDY-PACKED: bars sorted by start, each placed on the first
 *     row that is free by then. First-fit by start time uses the fewest rows
 *     any packing can (interval graphs are perfect), and the rows are NOT
 *     workers — the chart says so;
 *   - mixed (some tests named a worker, some did not): the worker lanes, then
 *     packed "No worker" rows.
 *
 * A bar is start → finish from the timestamps. The reported `duration_ms` is
 * kept for the tooltip (it can differ: a duration may leave fixtures out).
 * Nothing is invented: a test whose timestamps cannot be read is not drawn and
 * counted, never placed at 0.
 */
import type { VizStatus } from '@/lib/viz/contracts'
import type { RunTimeline } from '@/types/executionMetrics'
import { formatDuration, formatNumber } from '@/utils/formatters'
import { tipContent, type TooltipContent, type TooltipRow } from './tooltip'

/** The most rows the chart draws; the contract caps worker lanes at 64 too. */
export const MAX_TIMELINE_LANES = 64

/** What a row with no worker id is called in a mixed timeline. */
export const NO_WORKER_LABEL = 'No worker'

export type TimelineLaneMode = 'worker' | 'packed' | 'mixed'

export interface TimelineBar {
  testId: string
  name: string
  suite: string | null
  status: VizStatus
  /** The stored status, as sent (`FAILED`). */
  rawStatus: string
  workerId: string | null
  /** Milliseconds after the timeline's origin (the earliest start). */
  start: number
  end: number
  /** `end - start`: what the bar's length means. */
  spanMs: number
  /** The reported duration; `null` = not reported. */
  durationMs: number | null
  /** The row the bar is drawn on (index into `lanes`). */
  lane: number
}

export interface TimelineLane {
  index: number
  key: string
  label: string
  workerId: string | null
  /** True for a packed row: a place to draw, not a worker. */
  packed: boolean
  bars: TimelineBar[]
}

export interface ExecutionTimelineModel {
  lanes: TimelineLane[]
  /** Every drawn bar, in start order. */
  bars: TimelineBar[]
  /** From the origin to the latest finish, ms. `0` when every bar is instantaneous. */
  spanMs: number
  mode: TimelineLaneMode
  /** Bars drawn. */
  shown: number
  /** Timed tests in the run (the server's `total`, before its cut). */
  total: number
  /** The server kept only the longest `limit` tests. */
  truncated: boolean
  /** Items whose timestamps could not be read. */
  invalid: number
  /** Bars not drawn because more than `MAX_TIMELINE_LANES` rows would be needed. */
  overflow: number
  /** Sentences the chart shows under the plot, in order. */
  notes: string[]
}

const STATUS_OF: Readonly<Record<string, VizStatus>> = {
  PASSED: 'passed',
  FAILED: 'failed',
  BROKEN: 'broken',
  SKIPPED: 'skipped',
}

/** A stored status (`FAILED`, `passed`) as the kit's status; anything else is `unknown`. */
export function vizStatusOf(status: string | null | undefined): VizStatus {
  return (status && STATUS_OF[status.toUpperCase()]) || 'unknown'
}

const parseInstant = (value: string | null | undefined): number | null => {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms : null
}

const finiteNonNegative = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0

/** Natural order of worker ids: `0, 1, 2, 10`, `gw2` before `gw10`. */
export function compareWorkerIds(a: string, b: string): number {
  return a.localeCompare(b, 'en', { numeric: true, sensitivity: 'base' }) || (a < b ? -1 : a > b ? 1 : 0)
}

export interface Interval {
  start: number
  end: number
}

/**
 * First-fit packing by start time: each interval goes on the lowest row that
 * is free when it starts (touching is not overlapping). Returns each
 * interval's row, in INPUT order, or `null` when it would need a row past
 * `maxRows`. The input need not be sorted.
 */
export function packIntervals(intervals: readonly Interval[], maxRows = Number.POSITIVE_INFINITY): (number | null)[] {
  const order = intervals
    .map((interval, index) => ({ interval, index }))
    .sort(
      (a, b) =>
        a.interval.start - b.interval.start ||
        b.interval.end - b.interval.start - (a.interval.end - a.interval.start) ||
        a.index - b.index,
    )
  const rowEnds: number[] = []
  const rows: (number | null)[] = intervals.map(() => null)
  for (const { interval, index } of order) {
    let row = rowEnds.findIndex((end) => end <= interval.start)
    if (row === -1) {
      if (rowEnds.length >= maxRows) continue
      row = rowEnds.length
      rowEnds.push(interval.end)
    } else {
      rowEnds[row] = interval.end
    }
    rows[index] = row
  }
  return rows
}

type Draft = Omit<TimelineBar, 'lane'>

function draftBars(timeline: RunTimeline): { drafts: Draft[]; invalid: number } {
  const items = Array.isArray(timeline.items) ? timeline.items : []
  const starts = items.map((item) => parseInstant(item.started_at))
  const origin =
    parseInstant(timeline.origin) ??
    starts.reduce<number | null>((min, s) => (s === null ? min : min === null ? s : Math.min(min, s)), null)
  let invalid = 0
  const drafts: Draft[] = []
  items.forEach((item, i) => {
    const started = starts[i]
    const finished = parseInstant(item.finished_at)
    const start = finiteNonNegative(item.offset_ms)
      ? item.offset_ms
      : started !== null && origin !== null && started >= origin
        ? started - origin
        : null
    if (start === null || !item.test_id) {
      invalid += 1
      return
    }
    // The bar is the clock's account (finish - start); a finish before the
    // start (clock skew) is an instant, never a negative bar. Without a
    // readable finish, the reported duration is the best length there is.
    const fromClock = started !== null && finished !== null ? Math.max(0, finished - started) : null
    const durationMs = finiteNonNegative(item.duration_ms) ? item.duration_ms : null
    const spanMs = fromClock ?? durationMs ?? 0
    drafts.push({
      testId: item.test_id,
      name: item.test_name || item.test_id,
      suite: item.suite_name ?? null,
      status: vizStatusOf(item.status),
      rawStatus: item.status ?? 'UNKNOWN',
      workerId: item.worker_id ?? null,
      start,
      end: start + spanMs,
      spanMs,
      durationMs,
    })
  })
  drafts.sort((a, b) => a.start - b.start || a.testId.localeCompare(b.testId))
  return { drafts, invalid }
}

/** The model for `ExecutionTimeline`, or `null` when there is nothing to draw. */
export function buildExecutionTimelineModel(
  timeline: RunTimeline | null | undefined,
  { maxLanes = MAX_TIMELINE_LANES }: { maxLanes?: number } = {},
): ExecutionTimelineModel | null {
  if (!timeline || timeline.measured === false) return null
  const { drafts, invalid } = draftBars(timeline)
  if (drafts.length === 0) return null

  const named = drafts.filter((bar) => bar.workerId !== null)
  const unnamed = drafts.filter((bar) => bar.workerId === null)
  const mode: TimelineLaneMode = unnamed.length === 0 ? 'worker' : named.length === 0 ? 'packed' : 'mixed'

  const lanes: TimelineLane[] = []
  const bars: TimelineBar[] = []
  let overflow = 0
  let overlappingWorkers = 0

  const place = (group: Draft[], label: (row: number) => string, key: string, workerId: string | null, packed: boolean) => {
    const room = Math.max(0, maxLanes - lanes.length)
    const rows = packIntervals(group, room)
    const first = lanes.length
    const used = rows.reduce<number>((max, row) => (row === null ? max : Math.max(max, row + 1)), 0)
    for (let row = 0; row < used; row++) {
      lanes.push({ index: first + row, key: `${key}:${row}`, label: label(row), workerId, packed, bars: [] })
    }
    group.forEach((draft, i) => {
      const row = rows[i]
      if (row === null) {
        overflow += 1
        return
      }
      const bar: TimelineBar = { ...draft, lane: first + row }
      lanes[first + row].bars.push(bar)
      bars.push(bar)
    })
    return used
  }

  if (named.length > 0) {
    const byWorker = new Map<string, Draft[]>()
    for (const bar of named) {
      const id = bar.workerId as string
      const list = byWorker.get(id)
      if (list) list.push(bar)
      else byWorker.set(id, [bar])
    }
    for (const id of [...byWorker.keys()].sort(compareWorkerIds)) {
      const used = place(
        byWorker.get(id) as Draft[],
        (row) => (row === 0 ? `Worker ${id}` : `Worker ${id} · ${row + 1}`),
        `w:${id}`,
        id,
        false,
      )
      if (used > 1) overlappingWorkers += 1
    }
  }
  if (unnamed.length > 0) {
    place(
      unnamed,
      (row) => (mode === 'packed' ? `Lane ${row + 1}` : `${NO_WORKER_LABEL} · ${row + 1}`),
      'packed',
      null,
      true,
    )
  }
  bars.sort((a, b) => a.start - b.start || a.testId.localeCompare(b.testId))

  const ends = bars.map((bar) => bar.end)
  const originMs = parseInstant(timeline.origin)
  const endMs = parseInstant(timeline.end)
  const reportedSpan = originMs !== null && endMs !== null && endMs >= originMs ? endMs - originMs : 0
  const spanMs = Math.max(reportedSpan, ...ends, 0)

  const total = finiteNonNegative(timeline.total) ? timeline.total : drafts.length + invalid
  const truncated = Boolean(timeline.truncated)
  const notes: string[] = []
  if (mode === 'packed') {
    notes.push('The report did not say which worker ran each test: tests are packed onto rows so no two bars overlap. The rows are not workers.')
  } else if (mode === 'mixed') {
    notes.push(`Some tests did not name their worker: they are packed onto the "${NO_WORKER_LABEL}" rows.`)
  }
  if (overlappingWorkers > 0) {
    notes.push(
      `${formatNumber(overlappingWorkers)} ${overlappingWorkers === 1 ? 'worker' : 'workers'} ran overlapping tests; the overlap is drawn on extra rows of that worker.`,
    )
  }
  if (truncated) {
    notes.push(`Showing the ${formatNumber(drafts.length + invalid)} longest of ${formatNumber(total)} timed tests.`)
  }
  if (overflow > 0) {
    notes.push(`${formatNumber(overflow)} ${overflow === 1 ? 'test is' : 'tests are'} not drawn: more than ${formatNumber(maxLanes)} rows would be needed.`)
  }
  if (invalid > 0) {
    notes.push(`${formatNumber(invalid)} ${invalid === 1 ? 'test has' : 'tests have'} unreadable timestamps and ${invalid === 1 ? 'is' : 'are'} not drawn.`)
  }

  return { lanes, bars, spanMs, mode, shown: bars.length, total, truncated, invalid, overflow, notes }
}

/** `+1.3s` after the start of the run, for the tooltip. */
const offsetText = (ms: number) => `+${formatDuration(Math.round(ms))}`

/**
 * One bar's tooltip (the kit's content model, VIZ-601): the test, its suite,
 * status, when it started and finished relative to the run, how long the
 * clock says it took and the reported duration when that differs. Every value
 * is plain text; names are untrusted and are never parsed.
 */
export function timelineBarTipContent(bar: TimelineBar, lane?: TimelineLane): TooltipContent {
  const rows: (TooltipRow | null)[] = [
    bar.suite ? { kind: 'dimension', key: 'suite', label: 'Suite', value: bar.suite } : null,
    { kind: 'dimension', key: 'status', label: 'Status', value: bar.rawStatus },
    lane ? { kind: 'dimension', key: 'lane', label: lane.packed ? 'Row' : 'Worker', value: lane.packed ? lane.label : (bar.workerId ?? lane.label) } : null,
    { kind: 'value', key: 'span', label: 'Took', value: formatDuration(Math.round(bar.spanMs)) },
    { kind: 'value', key: 'window', label: 'Ran', value: `${offsetText(bar.start)} → ${offsetText(bar.end)}` },
    bar.durationMs !== null && Math.round(bar.durationMs) !== Math.round(bar.spanMs)
      ? { kind: 'value', key: 'duration', label: 'Reported duration', value: formatDuration(Math.round(bar.durationMs)) }
      : null,
  ]
  return tipContent(bar.name, rows)
}
