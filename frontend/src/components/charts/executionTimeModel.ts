/**
 * The pure model behind `ExecutionTimeTrend` (EXEC-04b): WALL CLOCK beside
 * TEST TIME (SUMMED), per UTC day (Trends › Execution time) or per run (a
 * suite's "Suite execution time").
 *
 * The two figures are different measurements, and the chart exists to show
 * the distance between them:
 *
 *   - **Wall clock** is how long the run took on the clock, from its first
 *     test's start to its last test's finish (or the report's own run span).
 *   - **Test time (summed)** adds up every test's duration. Tests that ran in
 *     parallel are each counted in full, so it can be far longer than the
 *     wall clock; summed ÷ wall is the PARALLELISM.
 *
 * `null` is NOT MEASURED, on both lines: a run whose report carried no
 * timestamps has no wall clock (never the sum standing in for it), and a day
 * with no run has neither. Each such point is a GAP with its reason, never
 * 0 ms (the API contract's `null` rule, `EXEC-03-contract.md`).
 *
 * Free of React and of every engine: the plot, the tooltip, the keyboard
 * readout, the summary and the table view all read this one model.
 */
import type { SeriesChart } from '@/lib/viz/contracts'
import type { ExecutionDayPoint, SuiteHistoryPoint } from '@/types/executionMetrics'
import { formatDuration } from '@/utils/formatters'
import { utcDayLabel } from './chartText'

/** The two series, in drawing and legend order. */
export const EXECUTION_SERIES = ['wall', 'summed'] as const
export type ExecutionSeriesKey = (typeof EXECUTION_SERIES)[number]

/** The glossary's words for the two figures (concepts.md "Definitions"). */
export const WALL_CLOCK_LABEL = 'Wall clock'
export const SUMMED_LABEL = 'Test time (summed)'
export const PARALLELISM_LABEL = 'Parallelism'
/** How every surface prints a figure the report did not carry. */
export const NOT_REPORTED = 'not reported'

export const EXECUTION_SERIES_LABEL: Readonly<Record<ExecutionSeriesKey, string>> = {
  wall: WALL_CLOCK_LABEL,
  summed: SUMMED_LABEL,
}

/** A day with no run in scope. */
export const NO_RUN_DAY_REASON = 'no run in this scope on this day'
/** A day whose runs all lacked a wall clock (every one of them was summed time only). */
export const NO_WALL_DAY_REASON = 'no run on this day reported a wall clock (its report carried no timestamps)'
/** A run whose report carried no timestamps (`derived_sum`). */
export const NO_WALL_RUN_REASON = "this run's report carried no timestamps, so only its summed test time is known"
/** A run with no timed test at all. */
export const NO_SUMMED_RUN_REASON = 'no test in this run reported a duration'

export interface ExecutionTimePoint {
  /** Unique, in order: the UTC day, or the run id. The plot's and the table's x. */
  key: string
  /** The axis tick: "Oct 14", or "#" and the build number. */
  tick: string
  /** The tooltip's and the table's name for the point: "2026-10-14", or the build ("#" and its number) · "Oct 14, 10:00 UTC". */
  title: string
  /** ms, or `null` (a gap with `wallReason`). */
  wall: number | null
  summed: number | null
  wallReason: string | null
  summedReason: string | null
  /** summed ÷ wall over the SAME runs, or `null` when that is not known. */
  parallelism: number | null
  /** Day grain: runs that day, and how many of them carried a wall clock. */
  runs: number | null
  wallRuns: number | null
  /** Run grain: the run, for a link. */
  runId: string | null
}

export interface ExecutionTimeModel {
  points: ExecutionTimePoint[]
  /** `day`: a calendar axis (UTC days); `run`: one point per run, oldest first. */
  grain: 'day' | 'run'
  /** The largest measured value on either line, ms (`null`: nothing measured). */
  max: number | null
  /** Points where the summed time is known but the wall clock is not. */
  wallGaps: number
  /** Whether any point has any measured value. */
  measured: boolean
}

/** The parallelism ratio as the kit prints it: "1.9×". */
export function formatParallelism(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio) || ratio <= 0) return NOT_REPORTED
  return `${ratio.toFixed(1)}×`
}

/**
 * A duration in ms as the kit prints it, from a float the server computed (a
 * median, a p95): whole milliseconds under a second (`formatDuration` prints
 * raw ms, so 180.25 would read "180.25ms"), one decimal under 10 ms so a
 * sub-millisecond unit test is not "0ms".
 */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return NOT_REPORTED
  if (ms < 10 && !Number.isInteger(ms)) return `${ms.toFixed(1)}ms`
  return formatDuration(ms < 1000 ? Math.round(ms) : ms)
}

const finiteOrNull = (value: number | null | undefined): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null

function finish(points: ExecutionTimePoint[], grain: 'day' | 'run'): ExecutionTimeModel {
  let max: number | null = null
  let wallGaps = 0
  for (const point of points) {
    for (const value of [point.wall, point.summed]) {
      if (value !== null && (max === null || value > max)) max = value
    }
    if (point.wall === null && point.summed !== null) wallGaps += 1
  }
  return { points, grain, max, wallGaps, measured: max !== null }
}

/**
 * The window's days (`per_day.points`, every UTC day, oldest first). A day's
 * wall clock sums the runs that HAD one, so it is only comparable with the
 * summed time when every run that day had one: only then is the day's
 * parallelism stated.
 */
export function executionTimeFromDays(days: readonly ExecutionDayPoint[]): ExecutionTimeModel {
  const points = days.map((day): ExecutionTimePoint => {
    const wall = day.runs > 0 ? finiteOrNull(day.wall_clock_ms) : null
    const summed = day.runs > 0 ? finiteOrNull(day.summed_ms) : null
    const sameRuns = wall !== null && summed !== null && wall > 0 && day.wall_runs === day.runs && day.summed_runs === day.runs
    return {
      key: day.day,
      tick: utcDayLabel(day.day),
      title: day.day,
      wall,
      summed,
      wallReason: wall !== null ? null : day.runs > 0 ? NO_WALL_DAY_REASON : NO_RUN_DAY_REASON,
      summedReason: summed !== null ? null : day.runs > 0 ? 'no run on this day reported a test duration' : NO_RUN_DAY_REASON,
      parallelism: sameRuns ? Math.round(((summed as number) / (wall as number)) * 100) / 100 : null,
      runs: day.runs,
      wallRuns: day.wall_runs,
      runId: null,
    }
  })
  return finish(points, 'day')
}

/** "Oct 14, 10:00 UTC": the run's time, in UTC like every day axis in the kit. */
function runTimeLabel(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return `${at.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' })} UTC`
}

/** One suite's executions, one point per run, OLDEST FIRST (the suite-history order). */
export function executionTimeFromSuiteHistory(history: readonly SuiteHistoryPoint[]): ExecutionTimeModel {
  const points = history.map((point): ExecutionTimePoint => {
    const wall = finiteOrNull(point.wall_ms)
    const summed = finiteOrNull(point.summed_ms)
    const build = point.build_number ? `#${point.build_number}` : null
    const when = runTimeLabel(point.run_created_at)
    return {
      key: point.run_id,
      tick: build ?? utcDayLabel(point.run_created_at),
      title: build ? `${build} · ${when}` : when,
      wall,
      summed,
      wallReason: wall !== null ? null : NO_WALL_RUN_REASON,
      summedReason: summed !== null ? null : NO_SUMMED_RUN_REASON,
      parallelism: finiteOrNull(point.parallelism),
      runs: null,
      wallRuns: null,
      runId: point.run_id,
    }
  })
  return finish(points, 'run')
}

/**
 * The C3 series the frame's summary, table and CSV read, built FROM the drawn
 * model. A gap is `y: null` with its reason, which the table prints as "—".
 * A run axis keys its points by run id and names them in `x_labels`.
 */
export function executionTimeToChartSeries(model: ExecutionTimeModel): SeriesChart {
  const sample = (point: ExecutionTimePoint, key: ExecutionSeriesKey) =>
    model.grain === 'day' ? (key === 'wall' ? point.wallRuns ?? 0 : point.runs ?? 0) : 1
  const line = (key: ExecutionSeriesKey) => ({
    key,
    label: EXECUTION_SERIES_LABEL[key],
    points: model.points.map((point) => {
      const value = key === 'wall' ? point.wall : point.summed
      const reason = key === 'wall' ? point.wallReason : point.summedReason
      return value === null
        ? { x: point.key, y: null, n: sample(point, key), measured: false as const, reason: reason ?? 'not measured' }
        : { x: point.key, y: value, n: sample(point, key) }
    }),
  })
  const chart: SeriesChart = {
    kind: 'series',
    dimensions: [model.grain === 'day' ? 'day' : 'run'],
    x_type: model.grain === 'day' ? 'time' : 'category',
    series: EXECUTION_SERIES.map(line),
  }
  if (model.grain === 'run') chart.x_labels = Object.fromEntries(model.points.map((point) => [point.key, point.title]))
  return chart
}
