/**
 * What `ExecutionTimeTrend` says besides its lines (EXEC-04b): its axis
 * titles and caption, the gap notice under the plot, and one point's tooltip
 * content — the SAME content the pointer's tooltip, the keyboard readout and
 * the announcer read. A module of its own so the component file exports only
 * the component, and so a test can read the words without drawing.
 */
import { formatNumber } from '@/utils/formatters'
import { NO_VALUE } from './chartText'
import { CHART_VARS } from './tokens'
import { changeRow, tipContent, type TooltipContent } from './tooltip'
import {
  formatMs,
  formatParallelism,
  PARALLELISM_LABEL,
  SUMMED_LABEL,
  WALL_CLOCK_LABEL,
  type ExecutionTimeModel,
} from './executionTimeModel'

/** The summed line's dash: the two lines differ by dash as well as colour. */
export const SUMMED_DASH = '7 3'
export const WALL_COLOR = CHART_VARS.series[0]
export const SUMMED_COLOR = CHART_VARS.series[3]

export const EXECUTION_TREND_Y_AXIS = 'Duration'
export const EXECUTION_TREND_X_AXIS = { day: 'Day (UTC)', run: 'Run (oldest first)' } as const
export const EXECUTION_TREND_CAPTION = {
  day: 'Wall clock and summed test time per UTC day. A day without a measured value is a gap, never 0 ms.',
  run: 'Wall clock and summed test time per run, oldest first. A run whose report carried no timestamps has no wall clock: a gap, never its summed time.',
} as const

/** The sentence under the plot when the wall clock is missing where the summed time is not. */
export function wallGapNotice(model: ExecutionTimeModel): string | null {
  if (model.wallGaps <= 0) return null
  const noun = model.grain === 'day' ? (model.wallGaps === 1 ? 'day has' : 'days have') : model.wallGaps === 1 ? 'run has' : 'runs have'
  return `${formatNumber(model.wallGaps)} ${noun} summed test time but no wall clock: the report carried no timestamps.`
}

/**
 * One point's tooltip content: both figures as reported ("—" with the reason
 * for a gap, never 0 ms), the parallelism when it is known for the same runs,
 * the runs behind a day, and each figure's change against the previous point.
 */
export function executionTimeTipContent(model: ExecutionTimeModel, index: number): TooltipContent {
  const point = model.points[index]
  if (!point) return { rows: [] }
  const before = index > 0 ? model.points[index - 1] : undefined
  const previousLabel = before?.title
  const formatChangeMs = (ms: number) => formatMs(ms)
  const noun = model.grain === 'day' ? 'previous day' : 'previous run'
  return tipContent(point.title, [
    {
      kind: 'value',
      key: 'wall',
      label: WALL_CLOCK_LABEL,
      value: point.wall === null ? NO_VALUE : formatMs(point.wall),
      color: WALL_COLOR,
      mark: 'line',
    },
    {
      kind: 'value',
      key: 'summed',
      label: SUMMED_LABEL,
      value: point.summed === null ? NO_VALUE : formatMs(point.summed),
      color: SUMMED_COLOR,
      mark: 'line',
      dash: SUMMED_DASH,
    },
    point.parallelism !== null && { kind: 'value', key: 'parallelism', label: PARALLELISM_LABEL, value: formatParallelism(point.parallelism) },
    point.runs !== null && {
      kind: 'sample',
      key: 'runs',
      label: 'Runs',
      value:
        point.wallRuns !== null && point.runs > 0
          ? `${formatNumber(point.runs)} (${formatNumber(point.wallRuns)} with a wall clock)`
          : formatNumber(point.runs),
    },
    changeRow({ current: point.wall, previous: before ? before.wall : undefined, previousLabel, formatMagnitude: formatChangeMs, label: `${WALL_CLOCK_LABEL} vs ${noun}` }),
    changeRow({ current: point.summed, previous: before ? before.summed : undefined, previousLabel, formatMagnitude: formatChangeMs, label: `Test time vs ${noun}` }),
    point.wall === null && point.wallReason ? { kind: 'note', key: 'wall-reason', label: WALL_CLOCK_LABEL, value: point.wallReason } : null,
    point.summed === null && point.summedReason && point.summedReason !== point.wallReason
      ? { kind: 'note', key: 'summed-reason', label: SUMMED_LABEL, value: point.summedReason }
      : null,
  ])
}
