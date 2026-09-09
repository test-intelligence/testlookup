import { beforeEach, describe, expect, it, vi } from 'vitest'

const { postData } = vi.hoisted(() => ({ postData: vi.fn() }))

vi.mock('./http', () => ({
  getData: vi.fn(),
  postData,
}))

import { searchService } from './searchService'

describe('searchService.triggerReindex', () => {
  beforeEach(() => postData.mockReset())

  it('sends project scope and full mode as FastAPI query parameters', async () => {
    postData.mockResolvedValue({ task_id: 'task-1', status: 'queued' })

    await searchService.triggerReindex('project-1', true)

    expect(postData).toHaveBeenCalledWith(
      '/api/v1/search/reindex',
      undefined,
      { params: { project_id: 'project-1', full: true } },
    )
  })

  it('keeps the global incremental defaults explicit', async () => {
    postData.mockResolvedValue({ task_id: 'task-2', status: 'queued' })

    await searchService.triggerReindex()

    expect(postData).toHaveBeenCalledWith(
      '/api/v1/search/reindex',
      undefined,
      { params: { full: false } },
    )
  })
})
