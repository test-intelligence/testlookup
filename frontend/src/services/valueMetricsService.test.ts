/**
 * HON-04: the Value Metrics export goes through the shared axios instance
 * (whose request interceptor attaches the Bearer token) as a blob, and is
 * saved with `downloadBlob`. The page used to point a bare `<a href download>`
 * at the endpoint; a navigation carries no Authorization header, so the file
 * saved was `{"detail":"Not authenticated"}`.
 */
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { valueMetricsService } from './valueMetricsService'
import { downloadBlob } from '@/utils/download'

vi.mock('./api', () => ({
  api: { get: vi.fn() },
}))

vi.mock('@/utils/download', () => ({
  downloadBlob: vi.fn(),
}))

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

describe('valueMetricsService.downloadExport', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches the export as a blob through the shared axios and saves it under the server filename', async () => {
    const blob = new Blob(['{"report_type":"value_metrics"}'], { type: 'application/json' })
    vi.mocked(api.get).mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename=value-metrics-90d.json' },
    })

    await valueMetricsService.downloadExport('proj-1', 90)

    expect(api.get).toHaveBeenCalledWith('/api/v1/value-metrics/export', {
      params: { project_id: 'proj-1', days: 90 },
      responseType: 'blob',
      suppressToast: true,
    })
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'value-metrics-90d.json')
  })

  it('omits project_id on All projects and falls back to value-metrics-{days}d.json', async () => {
    const blob = new Blob(['{}'])
    vi.mocked(api.get).mockResolvedValue({ data: blob, headers: {} })

    await valueMetricsService.downloadExport(undefined, 7)

    expect(vi.mocked(api.get).mock.calls[0][1]).toMatchObject({ params: { days: 7 } })
    expect(vi.mocked(api.get).mock.calls[0][1]?.params).not.toHaveProperty('project_id')
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'value-metrics-7d.json')
  })

  it('saves nothing when the request fails', async () => {
    vi.mocked(api.get).mockRejectedValue(httpError(403, new Blob(['{"detail":"nope"}'])))

    await expect(valueMetricsService.downloadExport('proj-x', 30)).rejects.toBeInstanceOf(AxiosError)
    expect(downloadBlob).not.toHaveBeenCalled()
  })
})

describe('valueMetricsService.exportErrorMessage', () => {
  it("reads the API's detail out of a blob error body", async () => {
    const error = httpError(403, new Blob([JSON.stringify({ detail: 'You do not have access to this project' })]))
    expect(await valueMetricsService.exportErrorMessage(error)).toBe('You do not have access to this project')
  })

  it('falls back to the axios message when the body is not JSON', async () => {
    const error = httpError(500, new Blob(['<html>bad gateway</html>']))
    expect(await valueMetricsService.exportErrorMessage(error)).toBe('Request failed with status code 500')
  })

  it('names a non-axios failure by its message', async () => {
    expect(await valueMetricsService.exportErrorMessage(new Error('disk full'))).toBe('disk full')
  })
})
