import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockPost = vi.hoisted(() => vi.fn())
const mockGet = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: { post: mockPost, get: mockGet } }))

import { defaultBuildLabel, newUploadIdempotencyKey, reportUploadService } from './reportUploadService'

const ok = { data: { run_id: 'r1', task_id: 't1', total_results: 0 } }

describe('reportUploadService.upload', () => {
  beforeEach(() => {
    mockPost.mockReset()
    mockGet.mockReset()
    mockPost.mockResolvedValue(ok)
  })

  it('posts multipart FormData with required fields and nulls Content-Type', async () => {
    const file = new File(['<testsuite/>'], 'junit.xml', { type: 'text/xml' })
    const res = await reportUploadService.upload({
      projectId: 'p1', file, format: 'junit', buildNumber: 'b-1', branch: 'main',
    })

    expect(res.run_id).toBe('r1')
    expect(mockPost).toHaveBeenCalledTimes(1)
    const [url, body, config] = mockPost.mock.calls[0]
    expect(url).toBe('/api/v1/ingest/file')
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('project_id')).toBe('p1')
    expect((body as FormData).get('build_number')).toBe('b-1')
    expect((body as FormData).get('format')).toBe('junit')
    expect((body as FormData).get('branch')).toBe('main')
    expect(((body as FormData).get('file') as File).name).toBe('junit.xml')
    // Must null the JSON default so the browser sets the multipart boundary.
    expect(config.headers['Content-Type']).toBeUndefined()
    expect(config.headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(config.suppressToast).toBe(true)
  })

  it('sends run_ai (default true; false when opted out)', async () => {
    const file = new File(['{}'], 'a.json')
    await reportUploadService.upload({ projectId: 'p1', file })
    expect((mockPost.mock.calls[0][1] as FormData).get('run_ai')).toBe('true')

    mockPost.mockClear()
    await reportUploadService.upload({ projectId: 'p1', file, runAi: false })
    expect((mockPost.mock.calls[0][1] as FormData).get('run_ai')).toBe('false')
  })

  it('defaults the build label and omits empty optional fields', async () => {
    const file = new File(['{}'], 'a.json')
    await reportUploadService.upload({ projectId: 'p1', file })
    const body = mockPost.mock.calls[0][1] as FormData
    expect(String(body.get('build_number'))).toMatch(/^upload-/)
    expect(body.get('format')).toBe('auto')
    expect(body.get('branch')).toBeNull()
    expect(body.get('commit_hash')).toBeNull()
    expect(body.get('release_name')).toBeNull()
  })

  it('reports upload progress as a 0-100 percent', async () => {
    const onProgress = vi.fn()
    mockPost.mockImplementation((_u: string, _b: FormData, cfg: { onUploadProgress: (e: { loaded: number; total: number }) => void }) => {
      cfg.onUploadProgress({ loaded: 50, total: 100 })
      return Promise.resolve(ok)
    })
    await reportUploadService.upload({ projectId: 'p1', file: new File(['{}'], 'a.json'), onProgress })
    expect(onProgress).toHaveBeenCalledWith(50)
  })

  it('preserves an explicit logical upload key', async () => {
    await reportUploadService.upload({ projectId: 'p1', file: new File(['{}'], 'a.json'), idempotencyKey: 'source:job-42' })
    expect(mockPost.mock.calls[0][2].headers['Idempotency-Key']).toBe('source:job-42')
  })

  it('recovers an ambiguous503 receipt and polls only the configured API by its original ID', async () => {
    const task = '11111111-1111-4111-8111-111111111111', run = '22222222-2222-4222-8222-222222222222'
    mockPost.mockRejectedValue({ response: { status: 503, headers: {
      'X-Upload-Task-ID': task, 'X-Upload-Run-ID': run, Location: 'https://other.invalid/unrelated',
    } } })
    mockGet.mockResolvedValue({ data: { task_id: task, run_id: run, state: 'pending' } })
    const receipt = await reportUploadService.upload({ projectId: 'p1', file: new File(['{}'], 'a.json') })
    expect(receipt).toEqual({ task_id: task, run_id: run, total_results: 0, publication_confirmed: false })
    await reportUploadService.getStatus(receipt.task_id)
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(mockGet).toHaveBeenCalledWith(`/api/v1/ingest/uploads/${task}`, { suppressToast: true })
  })

  it('leaves generic503 failures with their caller and does not replay', async () => {
    const error = { response: { status: 503, headers: { 'Retry-After': '1' } } }
    mockPost.mockRejectedValue(error)
    await expect(reportUploadService.upload({ projectId: 'p1', file: new File(['{}'], 'a.json') })).rejects.toBe(error)
    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('refuses an incomplete receipt without treating it as successful acceptance', async () => {
    mockPost.mockRejectedValue({ response: { status: 503, headers: { 'x-upload-task-id': '11111111-1111-4111-8111-111111111111' } } })
    await expect(reportUploadService.upload({ projectId: 'p1', file: new File(['{}'], 'a.json') })).rejects.toThrow('receipt is incomplete')
    expect(mockPost).toHaveBeenCalledTimes(1)
    expect(mockGet).not.toHaveBeenCalled()
  })
})

describe('defaultBuildLabel', () => {
  it('starts with "upload-" and contains no colons', () => {
    const label = defaultBuildLabel()
    expect(label.startsWith('upload-')).toBe(true)
    expect(label).not.toContain(':')
  })

  it('generates independent UUID keys without requiring secure-context randomUUID', () => {
    const first = newUploadIdempotencyKey(), second = newUploadIdempotencyKey()
    expect(first).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(second).not.toBe(first)
  })
})
