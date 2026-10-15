/**
 * EXEC-04b: the wall clock vs summed model. `null` is "not reported" — a gap
 * with its reason on the line and "—" in the table — and never 0 ms; the
 * summed time never stands in for a wall clock; the parallelism is stated
 * only over the same runs.
 */
import { describe, expect, it } from 'vitest'
import type { ExecutionDayPoint, SuiteHistoryPoint } from '@/types/executionMetrics'
import {
  executionTimeFromDays,
  executionTimeFromSuiteHistory,
  executionTimeToChartSeries,
  formatMs,
  formatParallelism,
  NO_RUN_DAY_REASON,
  NO_WALL_DAY_REASON,
  NO_WALL_RUN_REASON,
  NOT_REPORTED,
} from './executionTimeModel'

const day = (d: string, overrides: Partial<ExecutionDayPoint> = {}): ExecutionDayPoint => ({
  day: d, runs: 2, wall_clock_ms: 18_000, wall_runs: 2, summed_ms: 34_800, summed_runs: 2, ...overrides,
})

const run = (id: string, overrides: Partial<SuiteHistoryPoint> = {}): SuiteHistoryPoint => ({
  run_id: id, build_number: id.toUpperCase(), run_created_at: '2026-10-14T10:05:00+00:00', branch: 'main', run_status: 'PASSED',
  source: 'derived', wall_ms: 9_000, summed_ms: 17_400, parallelism: 1.93, started_at: null, finished_at: null,
  test_count: 12, timed_count: 12, passed: 11, failed: 1, broken: 0, skipped: 0, unknown: 0, ...overrides,
})

describe('executionTimeFromDays', () => {
  it('keeps every day, a day with no run a gap on both lines (never 0 ms)', () => {
    const model = executionTimeFromDays([
      day('2026-10-12'),
      day('2026-10-13', { runs: 0, wall_clock_ms: null, wall_runs: 0, summed_ms: null, summed_runs: 0 }),
      day('2026-10-14'),
    ])
    expect(model.points.map((p) => [p.key, p.tick, p.wall, p.summed])).toEqual([
      ['2026-10-12', 'Oct 12', 18_000, 34_800],
      ['2026-10-13', 'Oct 13', null, null],
      ['2026-10-14', 'Oct 14', 18_000, 34_800],
    ])
    expect(model.points[1].wallReason).toBe(NO_RUN_DAY_REASON)
    expect(model.max).toBe(34_800)
    expect(model.wallGaps).toBe(0)
  })

  it('a day whose runs reported no wall clock: summed time, a wall GAP with the reason, no parallelism', () => {
    const model = executionTimeFromDays([day('2026-10-14', { wall_clock_ms: null, wall_runs: 0 })])
    const [point] = model.points
    expect(point.wall).toBeNull()
    expect(point.summed).toBe(34_800)
    expect(point.wallReason).toBe(NO_WALL_DAY_REASON)
    expect(point.parallelism).toBeNull()
    expect(model.wallGaps).toBe(1)
  })

  it('states a day’s parallelism only when every run that day had both figures', () => {
    expect(executionTimeFromDays([day('2026-10-14')]).points[0].parallelism).toBe(1.93)
    // 3 runs, only 2 with a wall clock: summed / wall would mix different runs.
    expect(executionTimeFromDays([day('2026-10-14', { runs: 3, summed_runs: 3 })]).points[0].parallelism).toBeNull()
  })

  it('a window with nothing measured is not measured', () => {
    const model = executionTimeFromDays([day('2026-10-14', { runs: 0, wall_clock_ms: null, summed_ms: null, wall_runs: 0, summed_runs: 0 })])
    expect(model.measured).toBe(false)
    expect(model.max).toBeNull()
  })
})

describe('executionTimeFromSuiteHistory', () => {
  it('one point per run, oldest first as sent, a derived_sum run a wall gap with the reason', () => {
    const model = executionTimeFromSuiteHistory([run('r1'), run('r2', { source: 'derived_sum', wall_ms: null, parallelism: null })])
    expect(model.grain).toBe('run')
    expect(model.points.map((p) => [p.key, p.tick, p.wall, p.summed])).toEqual([
      ['r1', '#R1', 9_000, 17_400],
      ['r2', '#R2', null, 17_400],
    ])
    expect(model.points[0].title).toBe('#R1 · Oct 14, 10:05 UTC')
    expect(model.points[1].wallReason).toBe(NO_WALL_RUN_REASON)
    expect(model.points[0].parallelism).toBe(1.93)
    expect(model.wallGaps).toBe(1)
  })

  it('a run with no build number is named by its time', () => {
    const model = executionTimeFromSuiteHistory([run('r1', { build_number: null })])
    expect(model.points[0].tick).toBe('Oct 14')
    expect(model.points[0].title).toBe('Oct 14, 10:05 UTC')
  })
})

describe('executionTimeToChartSeries', () => {
  it('the table reads a gap as a null with its reason (printed "—"), never 0', () => {
    const series = executionTimeToChartSeries(executionTimeFromDays([day('2026-10-14', { wall_clock_ms: null, wall_runs: 0 })]))
    expect(series.series.map((s) => s.label)).toEqual(['Wall clock', 'Test time (summed)'])
    const [wall, summed] = series.series
    expect(wall.points[0]).toEqual({ x: '2026-10-14', y: null, n: 0, measured: false, reason: NO_WALL_DAY_REASON })
    expect(summed.points[0]).toEqual({ x: '2026-10-14', y: 34_800, n: 2 })
    expect(series.x_type).toBe('time')
  })

  it('a run axis keys points by run id and names them in x_labels', () => {
    const series = executionTimeToChartSeries(executionTimeFromSuiteHistory([run('r1')]))
    expect(series.x_type).toBe('category')
    expect(series.x_labels).toEqual({ r1: '#R1 · Oct 14, 10:05 UTC' })
  })
})

describe('formatting', () => {
  it('formatMs: whole ms from a float percentile, a tenth under 10 ms, "not reported" for null', () => {
    expect(formatMs(180.25)).toBe('180ms')
    expect(formatMs(0.4)).toBe('0.4ms')
    expect(formatMs(0)).toBe('0ms')
    expect(formatMs(9_000)).toBe('9.0s')
    expect(formatMs(null)).toBe(NOT_REPORTED)
  })

  it('formatParallelism: one decimal and a times sign; "not reported" when unknown', () => {
    expect(formatParallelism(1.93)).toBe('1.9×')
    expect(formatParallelism(null)).toBe(NOT_REPORTED)
    expect(formatParallelism(0)).toBe(NOT_REPORTED)
  })
})
