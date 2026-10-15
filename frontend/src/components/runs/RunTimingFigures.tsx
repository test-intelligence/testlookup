/**
 * The run header's timing line (EXEC-04b): "Wall clock 9.0s · Test time
 * (summed) 17.4s · Parallelism 1.9×".
 *
 * A run has two durations and they are not interchangeable:
 *
 *   - **Wall clock** — how long the run took on the clock (EXEC-02,
 *     `wall_clock_ms`). When the report carried no timestamps it is "not
 *     reported": the summed time NEVER stands in for it.
 *   - **Test time (summed)** — every test's duration added up
 *     (`duration_ms`, the figure the run always had). Tests that ran in
 *     parallel are each counted in full, so it can exceed the wall clock.
 *
 * Parallelism (summed ÷ wall) is shown only when both are known. The run's
 * Timing tab (`?tab=timing`) has the breakdown; this line is the header's
 * one-glance version of it.
 */
import { formatDuration } from '@/utils/formatters'
import { runParallelism, wallClockReason, type RunTimingInput } from './runHeaderTiming'

export const WALL_CLOCK = 'Wall clock'
export const TEST_TIME_SUMMED = 'Test time (summed)'
export const PARALLELISM = 'Parallelism'
export const NOT_REPORTED = 'not reported'

export default function RunTimingFigures({ run }: { run: RunTimingInput }) {
  const wall = run.wall_clock_ms ?? null
  const summed = run.duration_ms ?? null
  const parallelism = runParallelism(run)
  return (
    <dl data-run-timing="" className="m-0 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs">
      <div className="flex items-baseline gap-1" title={wall === null ? wallClockReason(run) : 'How long the run took on the clock'}>
        <dt className="text-[var(--color-text-secondary)]">{WALL_CLOCK}</dt>
        <dd data-run-timing-figure="wall" className={wall === null ? 'm-0 text-[var(--color-text-secondary)]' : 'm-0 tabular-nums font-medium text-[var(--color-text)]'}>
          {wall === null ? NOT_REPORTED : formatDuration(wall)}
        </dd>
      </div>
      <div className="flex items-baseline gap-1" title="Every test's duration added up: tests that ran in parallel are each counted in full">
        <dt className="text-[var(--color-text-secondary)]">{TEST_TIME_SUMMED}</dt>
        <dd data-run-timing-figure="summed" className={summed === null ? 'm-0 text-[var(--color-text-secondary)]' : 'm-0 tabular-nums font-medium text-[var(--color-text)]'}>
          {summed === null ? NOT_REPORTED : formatDuration(summed)}
        </dd>
      </div>
      {parallelism !== null ? (
        <div className="flex items-baseline gap-1" title="Test time (summed) ÷ wall clock">
          <dt className="text-[var(--color-text-secondary)]">{PARALLELISM}</dt>
          <dd data-run-timing-figure="parallelism" className="m-0 tabular-nums font-medium text-[var(--color-text)]">
            {parallelism.toFixed(1)}×
          </dd>
        </div>
      ) : null}
    </dl>
  )
}
