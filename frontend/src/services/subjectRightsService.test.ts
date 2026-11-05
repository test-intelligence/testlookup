/**
 * W31 STRAT-12: the subject-rights service.
 *
 * - every request path is written IN FULL (the SPA wire contract reads the literals) and the POSTs suppress the
 *   shared toast (the cards render the refusal inline);
 * - the download is an authenticated blob read saved through `downloadBlob`, with the server's file name;
 * - a JSON refusal that arrived as a Blob reads as its sentence, never "Request failed with status code 410";
 * - a 409 `export_running` hands back the running job's id; the erase POST sends ONLY `confirm_username`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import {
  runningSubjectExportJobId,
  subjectRightsErrorCode,
  subjectRightsErrorMessage,
  subjectRightsService,
} from './subjectRightsService'
import { ERROR_SENTENCES } from '@/types/subjectRights'
import { readSourceFile } from '@/test/readSourceFile'
import { downloadBlob } from '@/utils/download'
import {
  erasurePreviewFixture,
  exportRunningBody,
  SUBJECT_USER_ID,
  subjectErrorBlob,
  subjectErrorBody,
  subjectJobFixture,
  subjectJobList,
  subjectRefusal,
} from '@/test/subjectRightsMocks'

vi.mock('./api', () => ({ api: { get: vi.fn(), post: vi.fn() } }))
vi.mock('@/utils/download', () => ({ downloadBlob: vi.fn() }))

const USER = SUBJECT_USER_ID
const JOB = '00000000-0000-4000-8000-0000000050b2'

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue({ data: subjectJobList([]) })
  vi.mocked(api.post).mockResolvedValue({ data: subjectJobFixture({ status: 'queued' }) })
})

describe('subjectRightsService paths', () => {
  it('starts my own export with no body and no global toast', async () => {
    await subjectRightsService.startMyExport()
    expect(api.post).toHaveBeenCalledWith('/api/v1/me/data-export', undefined, { suppressToast: true })
  })

  it('lists my exports', async () => {
    await subjectRightsService.listMyExports()
    expect(api.get).toHaveBeenCalledWith('/api/v1/me/data-exports', undefined)
  })

  it("starts an admin export of a person and lists that person's jobs", async () => {
    await subjectRightsService.startUserExport(USER)
    expect(api.post).toHaveBeenCalledWith(`/api/v1/users/${USER}/data-export`, undefined, { suppressToast: true })
    await subjectRightsService.listUserJobs(USER)
    expect(api.get).toHaveBeenLastCalledWith(`/api/v1/users/${USER}/data-jobs`, undefined)
  })

  it('reads one job by id', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: subjectJobFixture({ id: JOB }) })
    const job = await subjectRightsService.getJob(JOB)
    expect(api.get).toHaveBeenCalledWith(`/api/v1/subject-data-jobs/${JOB}`, undefined)
    expect(job.id).toBe(JOB)
  })

  it('reads the erasure preview and erases with ONLY the typed username', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: erasurePreviewFixture() })
    const preview = await subjectRightsService.getErasurePreview(USER)
    expect(api.get).toHaveBeenCalledWith(`/api/v1/users/${USER}/erasure-preview`, undefined)
    expect(preview.changes).toHaveLength(6)
    await subjectRightsService.eraseUser(USER, 'quinn.tester')
    expect(api.post).toHaveBeenLastCalledWith(
      `/api/v1/users/${USER}/erasure`,
      { confirm_username: 'quinn.tester' },
      { suppressToast: true },
    )
  })

  it('writes every request path out in full in the source (the SPA wire contract)', async () => {
    const source = await readSourceFile('src/services/subjectRightsService.ts')
    for (const path of [
      "'/api/v1/me/data-export'",
      "'/api/v1/me/data-exports'",
      '`/api/v1/users/${userId}/data-export`',
      '`/api/v1/users/${userId}/data-jobs`',
      '`/api/v1/subject-data-jobs/${jobId}`',
      '`/api/v1/subject-data-jobs/${jobId}/download`',
      '`/api/v1/users/${userId}/erasure-preview`',
      '`/api/v1/users/${userId}/erasure`',
    ]) {
      expect(source).toContain(path)
    }
  })
})

describe('subjectRightsService.downloadExport', () => {
  it("saves the blob under the server's file name, the token never in a URL", async () => {
    const blob = new Blob(['PK'], { type: 'application/zip' })
    vi.mocked(api.get).mockResolvedValueOnce({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="testlookup-personal-data-20261104-0930.zip"' },
    })
    const name = await subjectRightsService.downloadExport(JOB)
    expect(api.get).toHaveBeenCalledWith(`/api/v1/subject-data-jobs/${JOB}/download`, {
      responseType: 'blob',
      suppressToast: true,
    })
    expect(downloadBlob).toHaveBeenCalledWith(blob, 'testlookup-personal-data-20261104-0930.zip')
    expect(name).toBe('testlookup-personal-data-20261104-0930.zip')
  })

  it('falls back to a name with no username in it', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: new Blob(['PK']), headers: {} })
    expect(await subjectRightsService.downloadExport(JOB)).toBe('testlookup-personal-data.zip')
  })
})

describe('subject-rights refusals', () => {
  it('reads a JSON refusal out of a Blob as its sentence and code', async () => {
    const error = subjectRefusal(410, subjectErrorBlob('export_expired'))
    expect(await subjectRightsErrorMessage(error)).toBe(ERROR_SENTENCES.export_expired)
    expect(await subjectRightsErrorCode(error)).toBe('export_expired')
  })

  it('reads a plain JSON refusal as its sentence, and falls back without a body', async () => {
    const error = subjectRefusal(409, subjectErrorBody('self_erasure'))
    expect(await subjectRightsErrorMessage(error)).toBe(ERROR_SENTENCES.self_erasure)
    expect(await subjectRightsErrorMessage(subjectRefusal(500, undefined), 'Try again.')).toBe('Try again.')
    expect(await subjectRightsErrorCode(subjectRefusal(500, undefined))).toBeNull()
  })

  it("hands back the running export's id from a 409 export_running only", () => {
    expect(runningSubjectExportJobId(subjectRefusal(409, exportRunningBody(JOB)))).toBe(JOB)
    expect(runningSubjectExportJobId(subjectRefusal(409, subjectErrorBody('user_erased')))).toBeNull()
    expect(runningSubjectExportJobId(subjectRefusal(403, exportRunningBody(JOB)))).toBeNull()
    expect(runningSubjectExportJobId(new Error('boom'))).toBeNull()
  })
})
