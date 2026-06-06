import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockPost = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: { post: mockPost } }))

import { defaultBuildLabel, reportUploadService } from './reportUploadService'

const ok = { data: { run_id: 'r1', task_id: 't1', total_results: 0 } }

describe('reportUploadService.upload', () => {
  beforeEach(() => {
    mockPost.mockReset()
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
})

describe('defaultBuildLabel', () => {
  it('starts with "upload-" and contains no colons', () => {
    const label = defaultBuildLabel()
    expect(label.startsWith('upload-')).toBe(true)
    expect(label).not.toContain(':')
  })
})
