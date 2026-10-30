/**
 * W24 ADM-05: the project-data service.
 *
 * - every request path is written IN FULL (the SPA wire contract);
 * - the import is multipart with `file`, `client_token` and `name` only when one was typed;
 * - the download is an authenticated blob read saved through `downloadBlob`, with the server's file name;
 * - a JSON refusal that arrived as a Blob reads as its sentence, never "Request failed with status code 410";
 * - a 409 `export_running` hands back the running job's id.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import { projectDataErrorMessage, projectDataService, runningExportJobId } from './projectDataService'
import { ERROR_SENTENCES } from '@/types/projectData'
import { downloadBlob } from '@/utils/download'
import {
  errorBlob,
  errorBody,
  exportRunningBody,
  jobFixture,
  jobList,
  PROJECT_DATA_PROJECT_ID,
  refusal,
} from '@/test/projectDataMocks'

vi.mock('./api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@/utils/download', () => ({ downloadBlob: vi.fn() }))

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
