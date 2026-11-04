/**
 * W24 ADM-05: the project-data service.
 *
 * - every request path is written IN FULL (the SPA wire contract);
 * - the import is multipart with `file`, `client_token` and `name` only when one was typed;
 * - the download is an authenticated blob read saved through `downloadBlob`, with the server's file name;
 * - a JSON refusal that arrived as a Blob reads as its sentence, never "Request failed with status code 410";
 * - a 409 `export_running` hands back the running job's id.
 * - W30: the export body only when given; the estimate's params; the three parted calls IN FULL (start, a raw
 *   PUT with `X-Part-Sha256`, complete); `importArchive` — ≤ 50 MB the single POST, > 2 GB refused with no
 *   request, else start → the missing parts ONE in flight → complete, resuming from `upload.received`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { projectDataErrorMessage, projectDataService, runningExportJobId } from './projectDataService'
import {
  ERROR_SENTENCES,
  IMPORT_ARCHIVE_MAX_BYTES,
  IMPORT_PART_MAX_BYTES,
  IMPORT_UPLOAD_MAX_BYTES,
} from '@/types/projectData'
import { downloadBlob } from '@/utils/download'
import {
  errorBlob,
  errorBody,
  estimateFixture,
  exportRunningBody,
  jobFixture,
  jobList,
  partReceiptFixture,
  PROJECT_DATA_PROJECT_ID,
  refusal,
  uploadFixture,
} from '@/test/projectDataMocks'

vi.mock('./api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('@/utils/download', () => ({ downloadBlob: vi.fn() }))
const hashing = vi.hoisted(() => ({ sha256Parts: vi.fn() }))
vi.mock('@/lib/sha256Stream', () => hashing)

const PROJECT = PROJECT_DATA_PROJECT_ID
const JOB = '00000000-0000-4000-8000-00000000d0b2'
const TOKEN = '00000000-0000-4000-8000-00000000d0c3'

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue({ data: jobList([]) })
  vi.mocked(api.post).mockResolvedValue({ data: jobFixture({ status: 'queued' }) })
})

describe('projectDataService paths', () => {
  it('starts an export with no body and no global toast', async () => {
    await projectDataService.startExport(PROJECT)
    expect(api.post).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/export`, undefined, { suppressToast: true })
  })

  it('lists the project exports and my imports with an optional limit', async () => {
    await projectDataService.listExports(PROJECT, 1)
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/data-jobs`, { params: { limit: 1 } })
    await projectDataService.listMyImports()
    expect(api.get).toHaveBeenLastCalledWith('/api/v1/project-imports', { params: undefined })
    await projectDataService.listMyImports(1)
    expect(api.get).toHaveBeenLastCalledWith('/api/v1/project-imports', { params: { limit: 1 } })
  })

  it('reads one job by id', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: jobFixture({ id: JOB }) })
    const job = await projectDataService.getJob(JOB)
    expect(api.get).toHaveBeenCalledWith(`/api/v1/project-data-jobs/${JOB}`, undefined)
    expect(job.id).toBe(JOB)
  })
})

describe('projectDataService.startImport', () => {
  it('posts multipart with the file and the token, and no name when none was typed', async () => {
    const file = new File(['PK'], 'payment-service.zip', { type: 'application/zip' })
    await projectDataService.startImport(file, { clientToken: TOKEN, name: '   ' })
    const [path, form, config] = vi.mocked(api.post).mock.lastCall as [string, FormData, Record<string, unknown>]
    expect(path).toBe('/api/v1/projects/import')
    expect(form).toBeInstanceOf(FormData)
    expect([...form.keys()]).toEqual(['file', 'client_token'])
    expect(form.get('client_token')).toBe(TOKEN)
    expect((form.get('file') as File).name).toBe('payment-service.zip')
    expect(config).toMatchObject({ suppressToast: true, headers: { 'Content-Type': undefined } })
  })

  it('sends a typed name, trimmed', async () => {
    const file = new File(['PK'], 'x.zip')
    await projectDataService.startImport(file, { clientToken: TOKEN, name: '  Payments copy ' })
    const form = vi.mocked(api.post).mock.lastCall?.[1] as FormData
    expect([...form.keys()]).toEqual(['file', 'client_token', 'name'])
    expect(form.get('name')).toBe('Payments copy')
  })
})

describe('projectDataService.downloadExport', () => {
  it('reads a blob through the authed client and saves it under the server name', async () => {
    const blob = new Blob(['PK'], { type: 'application/zip' })
    vi.mocked(api.get).mockResolvedValueOnce({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="payment-service-settings-catalog-20261030-1200.zip"' },
    })
    const name = await projectDataService.downloadExport(JOB)
    expect(api.get).toHaveBeenCalledWith(`/api/v1/project-data-jobs/${JOB}/download`, {
      responseType: 'blob',
      suppressToast: true,
    })
    expect(name).toBe('payment-service-settings-catalog-20261030-1200.zip')
    expect(downloadBlob).toHaveBeenCalledWith(blob, name)
  })

  it('falls back to a job-named file when the response names none', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['PK']), headers: {} })
    expect(await projectDataService.downloadExport(JOB)).toBe(`project-export-${JOB}.zip`)
  })
})

describe('refusals', () => {
  it('reads a JSON refusal out of a Blob as its sentence', async () => {
    const message = await projectDataErrorMessage(refusal(410, errorBlob('export_expired')))
    expect(message).toBe(ERROR_SENTENCES.export_expired)
    expect(message).not.toMatch(/status code/)
  })

  it('reads a plain JSON refusal and falls back for an unstructured one', async () => {
    expect(await projectDataErrorMessage(refusal(413, errorBody('upload_too_large')))).toBe(
      ERROR_SENTENCES.upload_too_large,
    )
    expect(await projectDataErrorMessage(refusal(502, new Blob(['<html>'])), 'Download failed.')).toBe('Download failed.')
  })

  it('hands back the running export id only from a 409 export_running', () => {
    expect(runningExportJobId(refusal(409, exportRunningBody(JOB)))).toBe(JOB)
    expect(runningExportJobId(refusal(409, errorBody('export_not_ready')))).toBeNull()
    expect(runningExportJobId(refusal(404, exportRunningBody(JOB)))).toBeNull()
    expect(runningExportJobId(new Error('boom'))).toBeNull()
  })
})

// ── W30: run history, the estimate and the parted upload ─────────────────────

const MIB = 1024 * 1024
const WHOLE = 'a1'.repeat(32)
const PART_DIGESTS = ['b2'.repeat(32), 'c3'.repeat(32)]

/** A File that REPORTS `size` bytes (its slices are what the test inspects; no 60 MB buffer is allocated). */
function sizedFile(size: number, name = 'payment-service-history.zip'): File {
  const file = new File(['PK'], name, { type: 'application/zip' })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

describe('projectDataService history paths (every path IN FULL)', () => {
  it('sends the export body only when one is given', async () => {
    const body = { include_history: true, history_days: 365, max_runs: 2000 }
    await projectDataService.startExport(PROJECT, body)
    expect(api.post).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/export`, body, { suppressToast: true })
    await projectDataService.startExport(PROJECT)
    expect(api.post).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/export`, undefined, { suppressToast: true })
  })

  it('reads the estimate with the window as params, and no history_days for all history', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: estimateFixture() })
    await projectDataService.getExportEstimate(PROJECT, { includeHistory: true, historyDays: 90 })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/export-estimate`, {
      params: { include_history: true, history_days: 90 },
    })
    await projectDataService.getExportEstimate(PROJECT, { includeHistory: true, historyDays: null })
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/export-estimate`, {
      params: { include_history: true },
    })
  })

  it('starts, sends a part with its digest header, and completes', async () => {
    const start = { client_token: TOKEN, name: null, bytes: 60 * MIB, parts: 2, sha256: WHOLE }
    vi.mocked(api.post).mockResolvedValueOnce({ data: jobFixture({ kind: 'import', status: 'uploading', id: JOB }) })
    await projectDataService.startPartedImport(start)
    expect(api.post).toHaveBeenLastCalledWith('/api/v1/project-imports/uploads', start, { suppressToast: true })

    const blob = new Blob(['part'])
    vi.mocked(api.put).mockResolvedValueOnce({ data: partReceiptFixture({ part: 2, received: [2] }) })
    const receipt = await projectDataService.uploadPart(JOB, 2, blob, PART_DIGESTS[1])
    expect(api.put).toHaveBeenLastCalledWith(`/api/v1/project-imports/uploads/${JOB}/parts/2`, blob, {
      headers: { 'Content-Type': 'application/octet-stream', 'X-Part-Sha256': PART_DIGESTS[1] },
      suppressToast: true,
    })
    expect(receipt.received).toEqual([2])

    await projectDataService.completeUpload(JOB)
    expect(api.post).toHaveBeenLastCalledWith(`/api/v1/project-imports/uploads/${JOB}/complete`, undefined, {
      suppressToast: true,
    })
  })
})

describe('projectDataService.importArchive', () => {
  beforeEach(() => {
    hashing.sha256Parts.mockResolvedValue({ sha256: WHOLE, parts: PART_DIGESTS })
  })

  it('sends a file of at most 50 MB through the phase-1 single POST and nothing else', async () => {
    await projectDataService.importArchive(sizedFile(IMPORT_UPLOAD_MAX_BYTES), { clientToken: TOKEN })
    expect(api.post).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.post).mock.lastCall?.[0]).toBe('/api/v1/projects/import')
    expect(api.put).not.toHaveBeenCalled()
    expect(hashing.sha256Parts).not.toHaveBeenCalled()
  })

  it('refuses a file over 2 GB with its sentence before any request', async () => {
    await expect(
      projectDataService.importArchive(sizedFile(IMPORT_ARCHIVE_MAX_BYTES + 1), { clientToken: TOKEN }),
    ).rejects.toThrow(ERROR_SENTENCES.upload_too_large_history)
    expect(api.post).not.toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()
    expect(hashing.sha256Parts).not.toHaveBeenCalled()
  })

  it('uploads a 60 MB file in parts: start → one PUT at a time with each digest → complete', async () => {
    const file = sizedFile(60 * MIB)
    const slice = vi.spyOn(file, 'slice')
    vi.mocked(api.post)
      .mockResolvedValueOnce({ data: jobFixture({ kind: 'import', status: 'uploading', id: JOB }) })
      .mockResolvedValueOnce({ data: jobFixture({ kind: 'import', status: 'queued', id: JOB }) })
    let inFlight = 0
    let maxInFlight = 0
    vi.mocked(api.put).mockImplementation(async (url: string) => {
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      await new Promise((resolve) => setTimeout(resolve, 5))
      inFlight -= 1
      const part = Number(url.split('/').pop())
      return { data: partReceiptFixture({ part, received: part === 1 ? [1] : [1, 2], complete: part === 2 }) }
    })
    const progress = vi.fn()

    const job = await projectDataService.importArchive(file, { clientToken: TOKEN, name: ' Payments history ', onProgress: progress })

    expect(hashing.sha256Parts).toHaveBeenCalledWith(file, expect.objectContaining({ sliceBytes: IMPORT_PART_MAX_BYTES }))
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/v1/project-imports/uploads', {
      client_token: TOKEN, name: 'Payments history', bytes: 60 * MIB, parts: 2, sha256: WHOLE,
    }, { suppressToast: true })
    expect(vi.mocked(api.put).mock.calls.map((c) => [c[0], (c[2] as { headers: Record<string, string> }).headers['X-Part-Sha256']])).toEqual([
      [`/api/v1/project-imports/uploads/${JOB}/parts/1`, PART_DIGESTS[0]],
      [`/api/v1/project-imports/uploads/${JOB}/parts/2`, PART_DIGESTS[1]],
    ])
    expect(maxInFlight).toBe(1)
    expect(slice.mock.calls).toEqual([[0, 32 * MIB], [32 * MIB, 60 * MIB]])
    expect(api.post).toHaveBeenNthCalledWith(2, `/api/v1/project-imports/uploads/${JOB}/complete`, undefined, {
      suppressToast: true,
    })
    expect(job.status).toBe('queued')
    expect(progress.mock.calls.map((c) => c[0])).toEqual([
      { phase: 'uploading', done: 0, total: 2 },
      { phase: 'uploading', done: 1, total: 2 },
      { phase: 'uploading', done: 2, total: 2 },
    ])
  })

  it('resumes: only the parts the server lacks are sent', async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({
        data: jobFixture({ kind: 'import', status: 'uploading', id: JOB, upload: uploadFixture({ received: [1] }) }),
      })
      .mockResolvedValueOnce({ data: jobFixture({ kind: 'import', status: 'queued', id: JOB }) })
    vi.mocked(api.put).mockResolvedValue({ data: partReceiptFixture({ part: 2, received: [1, 2], complete: true }) })
    await projectDataService.importArchive(sizedFile(60 * MIB), { clientToken: TOKEN })
    expect(vi.mocked(api.put).mock.calls.map((c) => c[0])).toEqual([`/api/v1/project-imports/uploads/${JOB}/parts/2`])
    expect(api.post).toHaveBeenCalledTimes(2)
  })

  it('a start that answers a job past uploading resolves it without sending anything', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: jobFixture({ kind: 'import', status: 'succeeded', id: JOB }) })
    const job = await projectDataService.importArchive(sizedFile(60 * MIB), { clientToken: TOKEN })
    expect(job.status).toBe('succeeded')
    expect(api.put).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledTimes(1)
  })
})
