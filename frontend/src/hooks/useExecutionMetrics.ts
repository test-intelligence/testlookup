import useSWR from 'swr'

import { executionMetricsService } from '@/services/executionMetricsService'
import type {
  ExecutionMetrics,
  ExecutionScope,
  RunTiming,
  SuiteExecutionHistory,
} from '@/types/executionMetrics'

/**
 * EXEC-04: execution-metrics hooks. Each asks nothing until its required id is known, so a tab that is not open (or
 * a run / project still loading) makes no request. Keys carry every parameter, so a scope change is a new read.
 */
export function useRunTiming(runId: string | null | undefined, options?: { slowest?: number }) {
  const slowest = options?.slowest
  const { data, error, isLoading, mutate } = useSWR<RunTiming>(
    runId ? ['run-timing', runId, slowest ?? null] : null,
    () => executionMetricsService.runTiming(runId as string, { slowest }),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}

export function useSuiteExecutionHistory(
  scope: ExecutionScope & { suiteName: string | null | undefined; limit?: number },
) {
  const { projectId, suiteName, days, branch, releaseIds, limit } = scope
  const { data, error, isLoading, mutate } = useSWR<SuiteExecutionHistory>(
    projectId && suiteName
      ? ['suite-execution-history', projectId, suiteName, days ?? null, branch ?? null, (releaseIds ?? []).join(','), limit ?? null]
      : null,
    () =>
      executionMetricsService.suiteHistory({
        projectId, suiteName: suiteName as string, days, branch, releaseIds, limit,
      }),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}

export function useExecutionMetrics(scope: ExecutionScope & { top?: number; suiteLimit?: number }) {
  const { projectId, days, branch, releaseIds, suiteNames, top, suiteLimit } = scope
  const { data, error, isLoading, mutate } = useSWR<ExecutionMetrics>(
    projectId
      ? [
          'execution-metrics', projectId, days ?? null, branch ?? null, (releaseIds ?? []).join(','),
          JSON.stringify(suiteNames ?? []), top ?? null, suiteLimit ?? null,
        ]
      : null,
    () => executionMetricsService.projectMetrics({ projectId, days, branch, releaseIds, suiteNames, top, suiteLimit }),
    { revalidateOnFocus: false },
  )
  return { data, error: error as unknown, isLoading, mutate }
}
