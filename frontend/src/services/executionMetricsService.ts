/**
 * EXEC-03/04: execution-metrics reads. Every call goes through the shared API client (auth header, array params
 * serialised as `a=1&a=2` for FastAPI).
 *
 * EXEC-06: the two CSV downloads (`downloadRunTimingCsv`, `downloadExecutionMetricsCsv`) also go through the shared
 * client, as a blob, and are saved with `downloadBlob`. Never a bare `<a href>` at the endpoint: a navigation carries
 * no Authorization header, so the file saved would be the API's 401 body (HON-04).
 */
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'

import { api } from './api'
import { extractErrorMessage } from './apiErrors'
import { getData } from './http'
import { downloadBlob } from '@/utils/download'
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

/** `attachment; filename="x.csv"` (plain, quoted or RFC 5987) → `x.csv`; else `fallback`. */
function filenameFromDisposition(header: unknown, fallback: string): string {
  if (typeof header !== 'string' || !header) return fallback
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim())
    } catch {
      return fallback
    }
  }
  const named = header.match(/filename="([^"]+)"/i) ?? header.match(/filename=([^;]+)/i)
  return named?.[1]?.trim() || fallback
}

/** The words for a failed download: the API's `detail`, read out of the blob error body. */
export async function csvExportErrorMessage(error: unknown): Promise<string> {
  if (!isAxiosError(error)) return error instanceof Error ? error.message : 'Export failed'
  const body: unknown = error.response?.data
  let detail: unknown
  if (body instanceof Blob) {
    try {
      detail = (JSON.parse(await body.text()) as { detail?: unknown }).detail
    } catch {
      detail = undefined
    }
  } else if (body && typeof body === 'object') {
    detail = (body as { detail?: unknown }).detail
  }
  return extractErrorMessage(detail, error.message)
}

/**
 * Fetch `url` as a blob through the shared client and save it under the server's filename (else `fallback`).
 * Resolves `true` when the file was handed to the browser. A failure is toasted here with the API's own words
 * (the global toast is suppressed: it cannot read a blob's `detail`) and resolves `false`, so an "Export CSV"
 * button needs no error handling of its own.
 */
async function downloadCsv(url: string, params: Record<string, unknown>, fallback: string): Promise<boolean> {
  try {
    const response = await api.get<Blob>(url, { params, responseType: 'blob', suppressToast: true })
    downloadBlob(response.data, filenameFromDisposition(response.headers?.['content-disposition'], fallback))
    return true
  } catch (error) {
    toast.error(`Export failed: ${await csvExportErrorMessage(error)}`)
    return false
  }
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

  /**
   * EXEC-06: every test of the run with its timing, as `run-<build>-timing.csv` (`GET /runs/{id}/timing.csv`).
   * Resolves `false` (after a toast) when the download failed.
   */
  downloadRunTimingCsv: (runId: string): Promise<boolean> =>
    downloadCsv(`/api/v1/runs/${encodeURIComponent(runId)}/timing.csv`, {}, `run-${runId.slice(0, 8)}-timing.csv`),

  /**
   * EXEC-06: the per-suite table of Trends › Execution time, as CSV (`GET /analytics/execution-metrics.csv`), for
   * the same scope the section reads. Without a project there is nothing to export: a toast says so, no request.
   */
  downloadExecutionMetricsCsv: async (scope: ExecutionScope & { suiteLimit?: number }): Promise<boolean> => {
    if (!scope.projectId) {
      toast.error('Select a project to export its execution time.')
      return false
    }
    return downloadCsv(
      '/api/v1/analytics/execution-metrics.csv',
      {
        ...scopeParams(scope),
        ...(scope.suiteNames?.length ? { suite_name: scope.suiteNames } : {}),
        ...(scope.suiteLimit != null ? { suite_limit: scope.suiteLimit } : {}),
      },
      `execution-metrics-${scope.days ?? 30}d.csv`,
    )
  },
}
