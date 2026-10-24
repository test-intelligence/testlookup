import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createSavedSearch, deleteSavedSearch, savedSearchesKey, updateSavedSearch, useSavedSearches,
} from './useSavedSearches'

const mockList = vi.fn()
const mockPost = vi.fn()
const mockPatch = vi.fn()
const mockDelete = vi.fn()
vi.mock('@/services/savedViewsService', () => ({ listSavedViews: (...args: unknown[]) => mockList(...args) }))
vi.mock('@/services/http', () => ({
  getData: vi.fn(),
  putData: vi.fn(),
  postData: (...args: unknown[]) => mockPost(...args),
  patchData: (...args: unknown[]) => mockPatch(...args),
  deleteData: (...args: unknown[]) => mockDelete(...args),
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

beforeEach(() => {
  mockList.mockReset()
  mockPost.mockReset()
  mockPatch.mockReset()
  mockDelete.mockReset()
})

describe('useSavedSearches (W12 SRCH-03)', () => {
  it('the saved-views key shape; lists page "search" and keeps the saved searches only', async () => {
    expect(savedSearchesKey('p1')).toEqual(['saved-views', 'p1', 'search'])
    mockList.mockResolvedValue([
      { id: 'a', filters: { kind: 'search', q: 'x', scope: 'all', mode: 'keyword' } },
      { id: 'b', filters: { kind: 'report_view' } },
    ])
    const { result } = renderHook(() => useSavedSearches('p1'), { wrapper })
    await waitFor(() => expect(result.current.views.map((v) => v.id)).toEqual(['a']))
    expect(mockList).toHaveBeenCalledWith('p1', 'search')
  })

  it('All Projects lists without a project', async () => {
    mockList.mockResolvedValue([])
    renderHook(() => useSavedSearches(null), { wrapper })
    await waitFor(() => expect(mockList).toHaveBeenCalledWith(undefined, 'search'))
  })

  it('the writes are the saved-views routes, each rendering its own refusal (no global toast)', async () => {
    const body = { project_id: null, name: 'n', page: 'search' as const, filters: { kind: 'search' }, is_shared: false }
    await createSavedSearch(body)
    expect(mockPost).toHaveBeenCalledWith('/api/v1/saved-views', body, { suppressToast: true })
    await updateSavedSearch('a/b', { name: 'x' })
    expect(mockPatch).toHaveBeenCalledWith('/api/v1/saved-views/a%2Fb', { name: 'x' }, { suppressToast: true })
    await deleteSavedSearch('id-1')
    expect(mockDelete).toHaveBeenCalledWith('/api/v1/saved-views/id-1', { suppressToast: true })
  })
})
