import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getData, postData, deleteData } = vi.hoisted(() => ({
  getData: vi.fn(),
  postData: vi.fn(),
  deleteData: vi.fn(),
}))

vi.mock('./http', () => ({
  getData,
  postData,
  deleteData,
}))

import { searchService } from './searchService'

describe('searchService.triggerReindex', () => {
  beforeEach(() => postData.mockReset())

  it('sends project scope and full mode as FastAPI query parameters', async () => {
    postData.mockResolvedValue({ task_id: 'task-1', status: 'queued' })

    await searchService.triggerReindex('project-1', true)

    // W12 SRCH-01: Re-index now renders a refusal in-page, so no global toast.
    expect(postData).toHaveBeenCalledWith(
      '/api/v1/search/reindex',
      undefined,
      { params: { project_id: 'project-1', full: true }, suppressToast: true },
    )
  })

  it('keeps the global incremental defaults explicit', async () => {
    postData.mockResolvedValue({ task_id: 'task-2', status: 'queued' })

    await searchService.triggerReindex()

    expect(postData).toHaveBeenCalledWith(
      '/api/v1/search/reindex',
      undefined,
      { params: { full: false }, suppressToast: true },
    )
  })
})

// ── W12: the new reads and the record flag ──────────────────────────────────

describe('searchService (W12)', () => {
  beforeEach(() => {
    getData.mockReset()
    getData.mockResolvedValue({})
    deleteData.mockReset()
    deleteData.mockResolvedValue(undefined)
  })

  it('search passes search_type and record through as query parameters', async () => {
    await searchService.search({ q: 'payment timeout', project_id: 'p1', search_type: 'semantic', page: 1, size: 25, record: true })
    expect(getData).toHaveBeenCalledWith('/api/v1/search', {
      params: { q: 'payment timeout', project_id: 'p1', search_type: 'semantic', page: 1, size: 25, record: true },
    })
  })

  it('search without record sends no record parameter', async () => {
    await searchService.search({ q: 'x', search_type: 'hybrid', page: 2 })
    const [, config] = getData.mock.calls[0]
    expect(config.params).toEqual({ q: 'x', search_type: 'hybrid', page: 2 })
    expect('record' in config.params).toBe(false)
  })

  it('globalSearch passes record through and still joins entity_types', async () => {
    await searchService.globalSearch({ q: 'checkout', entity_types: ['test_case', 'defect'], page: 1, size: 25, record: true })
    expect(getData).toHaveBeenCalledWith('/api/v1/search/global', {
      params: { q: 'checkout', entity_types: 'test_case,defect', page: 1, size: 25, record: true },
    })
  })

  it('globalSearch asks for no global toast only when the caller says so (the typeahead)', async () => {
    await searchService.globalSearch({ q: 'ch', size: 5 }, { suppressToast: true })
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/global', {
      params: { q: 'ch', size: 5, entity_types: undefined }, suppressToast: true,
    })
    // Without the option the config is exactly as before: no suppressToast key at all.
    await searchService.globalSearch({ q: 'ch', size: 5 })
    const [, config] = getData.mock.calls[getData.mock.calls.length - 1]
    expect('suppressToast' in config).toBe(false)
  })

  it('findSimilar encodes the id and asks for the limit', async () => {
    await searchService.findSimilar('tc-1')
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/similar/tc-1', { params: { limit: 5 } })
    await searchService.findSimilar('a/../b?c', 3)
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/similar/a%2F..%2Fb%3Fc', { params: { limit: 3 } })
  })

  it('recent and clearRecent hit the recents route; clearing raises no global toast', async () => {
    await searchService.recent()
    expect(getData).toHaveBeenCalledWith('/api/v1/search/recent')
    await searchService.clearRecent()
    expect(deleteData).toHaveBeenCalledWith('/api/v1/search/recent', { suppressToast: true })
  })

  it('suggested names its project', async () => {
    await searchService.suggested('p1')
    expect(getData).toHaveBeenCalledWith('/api/v1/search/suggested', { params: { project_id: 'p1' } })
  })

  it('usage sends days, and the project only when one is pinned', async () => {
    await searchService.usage('p1')
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/usage', { params: { project_id: 'p1', days: 1 } })
    await searchService.usage(null, 7)
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/usage', { params: { days: 7 } })
  })

  it('getIndexStatus and getEntityCounts are unchanged', async () => {
    await searchService.getIndexStatus('p1')
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/index-status', { params: { project_id: 'p1' } })
    await searchService.getIndexStatus()
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/index-status', { params: {} })
    await searchService.getEntityCounts('p1')
    expect(getData).toHaveBeenLastCalledWith('/api/v1/search/entity-counts', { params: { project_id: 'p1' } })
  })
})
