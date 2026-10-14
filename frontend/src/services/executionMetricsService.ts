/**
 * EXEC-03/04: execution-metrics reads. Every call goes through the shared API client (auth header, array params
 * serialised as `a=1&a=2` for FastAPI).
 */
import { getData } from './http'
import type {
  ExecutionMetrics,
  ExecutionScope,
  RunTiming,
  SuiteExecutionHistory,
} from '@/types/executionMetrics'

function scopeParams(scope: ExecutionScope): Record<string, unknown> {
  const params: Record<string, unknown> = { project_id: scope.projectId }
  if (scope.days != null) params.days = scope.days
  if (scope.branch) params.branch = scope.branch
  if (scope.releaseIds?.length) params.release_id = scope.releaseIds
  return params
}

export const executionMetricsService = {
  runTiming: (runId: string, options?: { slowest?: number; timelineLimit?: number }) =>
    getData<RunTiming>(`/api/v1/runs/${encodeURIComponent(runId)}/timing`, {
      params: {
        ...(options?.slowest != null ? { slowest: options.slowest } : {}),
        ...(options?.timelineLimit != null ? { timeline_limit: options.timelineLimit } : {}),
      },
    }),

  suiteHistory: (scope: ExecutionScope & { suiteName: string; limit?: number }) =>
    getData<SuiteExecutionHistory>('/api/v1/analytics/execution/suite-history', {
      params: {
        ...scopeParams(scope),
        suite_name: scope.suiteName,
        ...(scope.limit != null ? { limit: scope.limit } : {}),
      },
    }),

  projectMetrics: (scope: ExecutionScope & { top?: number; suiteLimit?: number }) =>
    getData<ExecutionMetrics>('/api/v1/analytics/execution-metrics', {
      params: {
        ...scopeParams(scope),
        ...(scope.suiteNames?.length ? { suite_name: scope.suiteNames } : {}),
        ...(scope.top != null ? { top: scope.top } : {}),
        ...(scope.suiteLimit != null ? { suite_limit: scope.suiteLimit } : {}),
      },
    }),
}
