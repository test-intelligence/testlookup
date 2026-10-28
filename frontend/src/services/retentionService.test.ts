/**
 * W22b ACT-03: the criteria-deletion service's export calls.
 *
 * - `execute` sends `export` ONLY when an archive was chosen (without it the body is byte-for-byte today's).
 * - `collect` POSTs through the shared (authed) client as a blob with no global toast, and hands back the archive
 *   plus both digests and the server's file name; there is no presigned URL.
 * - `deletionArchiveErrorMessage` reads a JSON refusal that arrived as a Blob (the request asked for a blob).
 * - `acknowledge` posts `{manifest_sha256}` to the job's acknowledge path.
 */
import { AxiosError, AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { criteriaDeletionService, deletionArchiveErrorMessage } from './retentionService'
import { EXPORT_ERROR_SENTENCES } from '@/types/retentionExport'
import { MOCK_ARCHIVE_SHA256, MOCK_MANIFEST_SHA256 } from '@/test/retentionExportMocks'

vi.mock('./api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))

const PROJECT = '00000000-0000-4000-8000-00000000e0a1'
const JOB = '00000000-0000-4000-8000-00000000e0b2'

function httpError(status: number, data: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, null, {
    status, statusText: '', headers: {}, config, data,
  })
}

function jsonBlob(body: unknown): Blob {
  return new Blob([JSON.stringify(body)], { type: 'application/json' })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.post).mockResolvedValue({ data: {} })
  vi.mocked(api.get).mockResolvedValue({ data: { jobs: [], total: 0 } })
})

describe('criteriaDeletionService.execute', () => {
  it('sends exactly today’s body when no archive was chosen', async () => {
    await criteriaDeletionService.execute(PROJECT, JOB, 'Payments sentinel')
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenLastCalledWith(
      `/api/v1/projects/${PROJECT}/deletion/execute`,
      { job_id: JOB, confirmation_name: 'Payments sentinel' },
      undefined,
    )
    const body = vi.mocked(api.post).mock.lastCall?.[1] as Record<string, unknown>
    expect(Object.keys(body)).toEqual(['job_id', 'confirmation_name'])
  })

  it('adds `export` when an archive was chosen', async () => {
    await criteriaDeletionService.execute(PROJECT, JOB, 'Payments sentinel', { tier: 'evidence', target: 'collect' })
    expect(api.post).toHaveBeenLastCalledWith(
      `/api/v1/projects/${PROJECT}/deletion/execute`,
      { job_id: JOB, confirmation_name: 'Payments sentinel', export: { tier: 'evidence', target: 'collect' } },
      undefined,
    )
  })
})

describe('criteriaDeletionService.jobs', () => {
  it('reads the project’s jobs answer with its total', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: { jobs: [], total: 7 } })
    await expect(criteriaDeletionService.jobs(PROJECT)).resolves.toEqual({ jobs: [], total: 7 })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/deletion/jobs`, undefined)
  })
})

describe('criteriaDeletionService.collect', () => {
  it('posts as a blob without a toast and returns the archive, both digests and the file name', async () => {
    const archive = new Blob(['PK\u0003\u0004 sentinel'], { type: 'application/zip' })
    vi.mocked(api.post).mockResolvedValueOnce({
      data: archive,
      headers: {
        'x-manifest-sha256': MOCK_MANIFEST_SHA256,
        'x-archive-sha256': MOCK_ARCHIVE_SHA256,
        'content-disposition': 'attachment; filename="testlookup-deletion-archive-e0b2abcd-20261028.zip"',
      },
    })
    const result = await criteriaDeletionService.collect(PROJECT, JOB)
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(api.post).toHaveBeenLastCalledWith(
      `/api/v1/projects/${PROJECT}/deletion/jobs/${JOB}/collect`,
      null,
      { responseType: 'blob', suppressToast: true },
    )
    expect(result).toEqual({
      blob: archive,
      manifestSha256: MOCK_MANIFEST_SHA256,
      archiveSha256: MOCK_ARCHIVE_SHA256,
      filename: 'testlookup-deletion-archive-e0b2abcd-20261028.zip',
    })
  })

  it('reports a missing header or file name as null (the caller builds the name; no digest is invented)', async () => {
    const archive = new Blob(['PK'])
    vi.mocked(api.post).mockResolvedValueOnce({ data: archive, headers: {} })
    await expect(criteriaDeletionService.collect(PROJECT, JOB)).resolves.toEqual({
      blob: archive, manifestSha256: null, archiveSha256: null, filename: null,
    })
  })

  it('lets a refusal reach the caller (nothing swallowed)', async () => {
    const refusal = httpError(409, jsonBlob({ detail: { code: 'not_awaiting', message: EXPORT_ERROR_SENTENCES.not_awaiting } }))
    vi.mocked(api.post).mockRejectedValueOnce(refusal)
    await expect(criteriaDeletionService.collect(PROJECT, JOB)).rejects.toBe(refusal)
  })
})

describe('deletionArchiveErrorMessage', () => {
  it('reads a {code, message} refusal that arrived as a Blob', async () => {
    const error = httpError(409, jsonBlob({ detail: { code: 'collect_expired', message: EXPORT_ERROR_SENTENCES.collect_expired } }))
    await expect(deletionArchiveErrorMessage(error)).resolves.toBe(EXPORT_ERROR_SENTENCES.collect_expired)
  })

  it('reads a plain-string detail that arrived as a Blob', async () => {
    const error = httpError(403, jsonBlob({ detail: 'Requires at least ADMIN role' }))
    await expect(deletionArchiveErrorMessage(error)).resolves.toBe('Requires at least ADMIN role')
  })

  it('reads an already-parsed JSON body too', async () => {
    const error = httpError(502, { detail: { code: 'archive_unreadable', message: EXPORT_ERROR_SENTENCES.archive_unreadable } })
    await expect(deletionArchiveErrorMessage(error)).resolves.toBe(EXPORT_ERROR_SENTENCES.archive_unreadable)
  })

  it('falls back to one sentence for an unreadable body or a non-HTTP failure', async () => {
    const garbled = httpError(500, new Blob(['<html>gateway sentinel</html>']))
    await expect(deletionArchiveErrorMessage(garbled)).resolves.toBe('The archive could not be downloaded.')
    await expect(deletionArchiveErrorMessage(new Error('Network down sentinel'))).resolves.toBe('Network down sentinel')
    await expect(deletionArchiveErrorMessage('odd')).resolves.toBe('The archive could not be downloaded.')
  })
})

describe('criteriaDeletionService.acknowledge', () => {
  it('posts the manifest digest to the job’s acknowledge path and returns the job', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: { id: JOB, status: 'queued' } })
    await expect(criteriaDeletionService.acknowledge(PROJECT, JOB, MOCK_MANIFEST_SHA256)).resolves.toEqual({
      id: JOB,
      status: 'queued',
    })
    expect(api.post).toHaveBeenLastCalledWith(
      `/api/v1/projects/${PROJECT}/deletion/jobs/${JOB}/acknowledge`,
      { manifest_sha256: MOCK_MANIFEST_SHA256 },
      undefined,
    )
  })
})
