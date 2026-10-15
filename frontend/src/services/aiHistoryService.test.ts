/**
 * W03 AIH-02/03/04/06: the AI history service sends the REST parameter names (`kind`, `limit`, `from`, `to`,
 * `suite_name`, `baseline_run_id`, `version_id`, `baseline_version_id`), never an option that was not given, and no
 * global toast for reads its components render themselves. The export goes through the shared (authed) client as a
 * blob saved with `downloadBlob` (HON-04); a 409 from the review gate toasts the API's own message.
 */
import { AxiosError, AxiosHeaders } from 'axios'
import toast from 'react-hot-toast'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { aiHistoryExportErrorMessage, aiHistoryExportFilename, aiHistoryService } from './aiHistoryService'
import { downloadBlob } from '@/utils/download'

vi.mock('./api', () => ({ api: { get: vi.fn() } }))
vi.mock('@/utils/download', () => ({ downloadBlob: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn() } }))

const RUN = '33333333-3333-4333-8333-333333333333'

function httpError(status: number, data: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, null, {
    status, statusText: '', headers: {}, config, data,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue({ data: {} })
})

describe('aiHistoryService reads', () => {
  it('lists a kind with limit 100 by default', async () => {
    await aiHistoryService.listVersions(RUN)
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/ai-history`, {
      params: { kind: 'analysis_digest', limit: 100 },
      suppressToast: true,
    })
    await aiHistoryService.listVersions(RUN, { kind: 'run_summary', limit: 5 })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/ai-history`, {
      params: { kind: 'run_summary', limit: 5 },
      suppressToast: true,
    })
  })

  it('gets one version by id, path-encoded', async () => {
    await aiHistoryService.getVersion(RUN, 'abc/../x')
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/ai-history/abc%2F..%2Fx`, { suppressToast: true })
  })

  it('diffs with `from` and `to` (to omitted = the server default, latest)', async () => {
    await aiHistoryService.diffVersions(RUN, { from: 'v1-id', to: 'live' })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/ai-history/diff`, {
      params: { from: 'v1-id', to: 'live' },
      suppressToast: true,
    })
    await aiHistoryService.diffVersions(RUN, { from: 'v1-id' })
    expect(vi.mocked(api.get).mock.lastCall?.[1]).toEqual({ params: { from: 'v1-id' }, suppressToast: true })
  })

  it('asks the server for the previous run, sending suite_name only when given', async () => {
    await aiHistoryService.getPreviousRun(RUN)
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/previous`, { params: {}, suppressToast: true })
    await aiHistoryService.getPreviousRun(RUN, { suiteName: 'Auth' })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/previous`, { params: { suite_name: 'Auth' }, suppressToast: true })
  })

  it('compares with the REST names, sending nothing for an option not given', async () => {
    await aiHistoryService.compare(RUN)
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/ai-compare`, { params: {}, suppressToast: true })
    await aiHistoryService.compare(RUN, { baselineRunId: 'b', suiteName: 'Auth', versionId: 'v', baselineVersionId: 'bv' })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/runs/${RUN}/ai-compare`, {
      params: { baseline_run_id: 'b', suite_name: 'Auth', version_id: 'v', baseline_version_id: 'bv' },
      suppressToast: true,
    })
    await aiHistoryService.compare(RUN, { baselineRunId: null, suiteName: '' })
    expect(vi.mocked(api.get).mock.lastCall?.[1]).toEqual({ params: {}, suppressToast: true })
  })
})

describe('aiHistoryService.downloadExport', () => {
  it('fetches the file as a blob through the shared client and saves it under the server name', async () => {
    const blob = new Blob(['{}'], { type: 'application/json' })
    vi.mocked(api.get).mockResolvedValue({ data: blob, headers: { 'content-disposition': 'attachment; filename="run-240-ai-history.json"' } })
    await expect(aiHistoryService.downloadExport(RUN, '240')).resolves.toBe(true)
    expect(api.get).toHaveBeenCalledWith(`/api/v1/runs/${RUN}/ai-history/export`, { responseType: 'blob', suppressToast: true })
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'run-240-ai-history.json')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('falls back to a safe run-<build>-ai-history.json name', async () => {
    const blob = new Blob(['{}'])
    vi.mocked(api.get).mockResolvedValue({ data: blob, headers: {} })
    await aiHistoryService.downloadExport(RUN, 'rel/1.2 "x"')
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'run-rel_1.2__x_-ai-history.json')
    expect(aiHistoryExportFilename(RUN, null)).toBe('run-33333333-ai-history.json')
  })

  it('a 409 (awaiting human review) toasts the API\'s message and saves nothing', async () => {
    const body = new Blob([JSON.stringify({
      detail: { code: 'report_pending_review', message: 'This AI-generated report has not been accepted by a human reviewer, so it cannot be distributed.' },
    })])
    vi.mocked(api.get).mockRejectedValue(httpError(409, body))
    await expect(aiHistoryService.downloadExport(RUN, '240')).resolves.toBe(false)
    expect(downloadBlob).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith('This AI-generated report has not been accepted by a human reviewer, so it cannot be distributed.')
  })

  it('reads a string detail, and falls back when the body says nothing', async () => {
    await expect(aiHistoryExportErrorMessage(httpError(503, new Blob([JSON.stringify({ detail: 'ai_history_unavailable' })])))).resolves.toBe('ai_history_unavailable')
    await expect(aiHistoryExportErrorMessage(httpError(500, new Blob(['not json'])))).resolves.toBe('AI history export failed')
    await expect(aiHistoryExportErrorMessage(new Error('boom'))).resolves.toBe('boom')
  })
})
