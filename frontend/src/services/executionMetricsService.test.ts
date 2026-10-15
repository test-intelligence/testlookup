/**
 * EXEC-06: the execution-metrics CSV downloads go through the shared axios instance (whose request interceptor
 * attaches the Bearer token) as a blob and are saved with `downloadBlob`. A bare `<a href download>` at the endpoint
 * carries no Authorization header, so the file saved would be `{"detail":"Not authenticated"}` (HON-04).
 */
import { AxiosError, AxiosHeaders } from 'axios'
import toast from 'react-hot-toast'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { csvExportErrorMessage, executionMetricsService } from './executionMetricsService'
import { downloadBlob } from '@/utils/download'

vi.mock('./api', () => ({
  api: { get: vi.fn() },
}))

vi.mock('@/utils/download', () => ({
  downloadBlob: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn() },
}))

const RUN = '33333333-3333-4333-8333-333333333333'
const PROJECT = '11111111-1111-4111-8111-111111111111'

function httpError(status: number, data: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, null, {
    status,
    statusText: '',
    headers: {},
    config,
    data,
  })
}

describe('executionMetricsService.downloadRunTimingCsv', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches the run file as a blob through the shared axios and saves it under the server filename', async () => {
    const blob = new Blob(['﻿test_name,suite_name\r\n'], { type: 'text/csv' })
    vi.mocked(api.get).mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="run-1234-timing.csv"' },
    })

    await expect(executionMetricsService.downloadRunTimingCsv(RUN)).resolves.toBe(true)

    expect(api.get).toHaveBeenCalledWith(`/api/v1/runs/${RUN}/timing.csv`, {
      params: {},
      responseType: 'blob',
      suppressToast: true,
    })
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'run-1234-timing.csv')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('encodes the run id into the path and falls back to run-<id>-timing.csv', async () => {
    const blob = new Blob([''])
    vi.mocked(api.get).mockResolvedValue({ data: blob, headers: {} })

    await executionMetricsService.downloadRunTimingCsv('a/b?c')

    expect(vi.mocked(api.get).mock.calls[0][0]).toBe('/api/v1/runs/a%2Fb%3Fc/timing.csv')
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'run-a/b?c-timing.csv')
  })

  it("saves nothing on a failure and toasts the API's own words from the blob body", async () => {
    vi.mocked(api.get).mockRejectedValue(httpError(404, new Blob(['{"detail":"Test run not found"}'])))

    await expect(executionMetricsService.downloadRunTimingCsv(RUN)).resolves.toBe(false)

    expect(downloadBlob).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith('Export failed: Test run not found')
  })
})

describe('executionMetricsService.downloadExecutionMetricsCsv', () => {
  beforeEach(() => vi.clearAllMocks())

  it('sends the section scope with the REST names and saves the server file', async () => {
    const blob = new Blob(['﻿suite_name\r\n'])
    vi.mocked(api.get).mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="execution-metrics-11111111-7d.csv"' },
    })

    await expect(
      executionMetricsService.downloadExecutionMetricsCsv({
        projectId: PROJECT,
        days: 7,
        branch: 'main',
        releaseIds: ['r1', 'r2'],
        suiteNames: ['checkout'],
      }),
    ).resolves.toBe(true)

    expect(api.get).toHaveBeenCalledWith('/api/v1/analytics/execution-metrics.csv', {
      params: { project_id: PROJECT, days: 7, branch: 'main', release_id: ['r1', 'r2'], suite_name: ['checkout'] },
      responseType: 'blob',
      suppressToast: true,
    })
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'execution-metrics-11111111-7d.csv')
  })

  it('sends no unused filter and falls back to execution-metrics-<days>d.csv', async () => {
    const blob = new Blob([''])
    vi.mocked(api.get).mockResolvedValue({ data: blob, headers: {} })

    await executionMetricsService.downloadExecutionMetricsCsv({ projectId: PROJECT })

    expect(vi.mocked(api.get).mock.calls[0][1]?.params).toEqual({ project_id: PROJECT })
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'execution-metrics-30d.csv')
  })

  it('makes no request without a project (All projects)', async () => {
    await expect(executionMetricsService.downloadExecutionMetricsCsv({ projectId: null, days: 30 })).resolves.toBe(
      false,
    )

    expect(api.get).not.toHaveBeenCalled()
    expect(downloadBlob).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith('Select a project to export its execution time.')
  })

  it('toasts a contract 422 by its detail and saves nothing', async () => {
    vi.mocked(api.get).mockRejectedValue(
      httpError(422, new Blob([JSON.stringify({ code: 'window_days_range', detail: 'days must be 1-365' })])),
    )

    await expect(executionMetricsService.downloadExecutionMetricsCsv({ projectId: PROJECT, days: 400 })).resolves.toBe(
      false,
    )
    expect(downloadBlob).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith('Export failed: days must be 1-365')
  })
})

describe('csvExportErrorMessage', () => {
  it('falls back to the axios message when the body is not JSON', async () => {
    expect(await csvExportErrorMessage(httpError(502, new Blob(['<html>bad gateway</html>'])))).toBe(
      'Request failed with status code 502',
    )
  })

  it('names a non-axios failure by its message', async () => {
    expect(await csvExportErrorMessage(new Error('disk full'))).toBe('disk full')
  })
})
