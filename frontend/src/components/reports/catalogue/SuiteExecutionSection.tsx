/**
 * Suite › Charts › "Suite execution time" (EXEC-04b): the suite's wall clock
 * and summed test time per run, oldest first, from
 * `/api/v1/analytics/execution/suite-history` (the newest
 * `SUITE_EXECUTION_LIMIT` executions in the page's window), with the window's
 * p50 / p95 of both figures under the plot.
 *
 * The p50 / p95 are the server's, over EVERY execution of the suite in the
 * window, not only the plotted runs, and each says how many runs it is over:
 * a run whose report carried no timestamps has summed time but no wall clock,
 * so the two counts can differ, and neither figure is ever invented from the
 * other.
 *
 * Scope: the suite's own project (the suite page passes it; elsewhere the
 * active one), the page's window clamped to 90 days, and the top-bar release
 * while it belongs to that project.
 */
import { useMemo } from 'react'
import { hasChartData, readyState, type ChartState } from '@/components/charts/chartStateCore'
import ExecutionTimeChartFrame from '@/components/charts/ExecutionTimeChartFrame'
import {
  executionTimeFromSuiteHistory,
  formatMs,
  formatParallelism,
  SUMMED_LABEL,
  WALL_CLOCK_LABEL,
} from '@/components/charts/executionTimeModel'
import { useSuiteExecutionHistory } from '@/hooks/useExecutionMetrics'
import { useReleaseScope } from '@/hooks/useReleaseScope'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import type { SuiteExecutionHistory } from '@/types/executionMetrics'
import { formatNumber } from '@/utils/formatters'
import { clampCatalogueDays } from './catalogueScope'
import { executionState, percentilePair } from './ExecutionTimeSection.model'

export const SUITE_EXECUTION_TITLE = 'Suite execution time'
/** How many of the suite's newest executions the trend plots (the story's "last 30 runs"). */
export const SUITE_EXECUTION_LIMIT = 30
export const SUITE_EXECUTION_HEIGHT = 240
export const SUITE_NO_PROJECT_REASON = "a suite's execution time is read for its own project: pick one project in the top bar"

const NOTE = 'text-xs text-[var(--color-text-secondary)]'

function takeawayOf(data: SuiteExecutionHistory): string | undefined {
  const { wall, summed } = data.stats
  const parts = [
    wall.measured && wall.p50_ms !== null ? `median ${WALL_CLOCK_LABEL.toLowerCase()} ${formatMs(wall.p50_ms)}` : null,
    summed.measured && summed.p50_ms !== null ? `median test time ${formatMs(summed.p50_ms)}` : null,
    data.stats.parallelism_p50 !== null ? `parallelism ${formatParallelism(data.stats.parallelism_p50)}` : null,
  ].filter(Boolean)
  if (parts.length === 0) return undefined
  const sentence = parts.join(' · ')
  return sentence.charAt(0).toUpperCase() + sentence.slice(1)
}

export interface SuiteExecutionSectionProps {
  days: number
  suiteName: string
  /** The suite's own project (the suite page knows it); default: the active project. */
  projectId?: string | null
}

export default function SuiteExecutionSection({ days, suiteName, projectId }: SuiteExecutionSectionProps) {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId
  const project = projectId ?? activeProject
  const release = useReleaseScope()
  // A release belongs to one project: only while the suite's project IS the active one.
  const releaseIds = release && project === activeProject ? [release] : undefined
  const windowDays = clampCatalogueDays(days)
  const read = useSuiteExecutionHistory({
    projectId: project,
    suiteName,
    days: windowDays,
    releaseIds,
    limit: SUITE_EXECUTION_LIMIT,
  })
  const retry = () => void read.mutate()
  const state: ChartState<SuiteExecutionHistory> = project
    ? executionState({ data: read.data, error: read.error, isLoading: read.isLoading, retry })
    : { status: 'not-measured', reason: SUITE_NO_PROJECT_REASON, meta: null }
  const data = hasChartData(state) ? state.data : null
  const model = useMemo(() => (data ? executionTimeFromSuiteHistory(data.points) : null), [data])
  const frameState: ChartState<unknown> = !data
    ? state
    : model && model.measured
      ? readyState(model)
      : { status: 'not-measured', reason: data.reason ?? 'no execution of this suite in the window reported a time', meta: null }

  return (
    <div data-catalogue-section="suite-execution" className="min-w-0">
      <ExecutionTimeChartFrame
        title={SUITE_EXECUTION_TITLE}
        takeaway={data ? takeawayOf(data) : undefined}
        headingLevel={3}
        height={SUITE_EXECUTION_HEIGHT}
        state={frameState}
        model={model}
        scopeLabel={`last ${windowDays} days`}
        footer={
          data ? (
            <div className="flex flex-col gap-0.5">
              <dl data-suite-execution-stats="" className="m-0 flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
                <div className="flex gap-1">
                  <dt className="text-[var(--color-text-secondary)]">{WALL_CLOCK_LABEL} p50 / p95:</dt>
                  <dd data-stat="wall" className="m-0 tabular-nums text-[var(--color-text)]">{percentilePair(data.stats.wall)}</dd>
                </div>
                <div className="flex gap-1">
                  <dt className="text-[var(--color-text-secondary)]">{SUMMED_LABEL} p50 / p95:</dt>
                  <dd data-stat="summed" className="m-0 tabular-nums text-[var(--color-text)]">{percentilePair(data.stats.summed)}</dd>
                </div>
              </dl>
              {data.truncated ? (
                <span data-suite-execution-cut="" className={NOTE}>
                  The newest {formatNumber(data.points.length)} of {formatNumber(data.total)} runs in the window; the p50 / p95 are over all of them.
                </span>
              ) : null}
            </div>
          ) : null
        }
      />
    </div>
  )
}
