/**
 * The run header's timing rules (EXEC-04b), apart from `RunTimingFigures` so
 * the component file exports only the component: why a wall clock is missing,
 * and the parallelism, stated only when both figures are known.
 */
import type { TestRun } from '@/types/runs'

export type RunTimingInput = Pick<TestRun, 'duration_ms' | 'wall_clock_ms' | 'timing_source'>

/** Why the wall clock is missing, in the words of its tooltip. */
export function wallClockReason(run: Pick<RunTimingInput, 'timing_source'>): string {
  if (run.timing_source === 'derived_sum') return 'The report carried no timestamps, so only the summed test time is known.'
  if (run.timing_source == null) return 'The run has not been timed yet.'
  return 'The report did not say how long the run took.'
}

/** summed ÷ wall, one decimal; `null` unless both are known and positive. */
export function runParallelism(run: Pick<RunTimingInput, 'duration_ms' | 'wall_clock_ms'>): number | null {
  const wall = run.wall_clock_ms
  const summed = run.duration_ms
  if (wall == null || summed == null || !(wall > 0) || !(summed > 0)) return null
  return Math.round((summed / wall) * 10) / 10
}
