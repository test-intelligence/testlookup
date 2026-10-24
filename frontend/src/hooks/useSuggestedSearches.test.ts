import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSuggestedSearches } from './useSuggestedSearches'

const mockSuggested = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: { suggested: (...args: unknown[]) => mockSuggested(...args) },
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

beforeEach(() => {
  mockSuggested.mockReset()
  mockSuggested.mockResolvedValue({ project_id: 'p1', window_days: 7, failing_tests: [], open_defects: [], active_release: null })
})

describe('useSuggestedSearches (W12 SRCH-03)', () => {
  it('asks for ONE pinned project while enabled', async () => {
    const { result } = renderHook(() => useSuggestedSearches('p1', true), { wrapper })
    await waitFor(() => expect(result.current.data?.window_days).toBe(7))
    expect(mockSuggested).toHaveBeenCalledWith('p1')
  })

  it('nothing in All Projects, nothing while the box holds a query', () => {
    renderHook(() => useSuggestedSearches(null, true), { wrapper })
    renderHook(() => useSuggestedSearches('p1', false), { wrapper })
    expect(mockSuggested).not.toHaveBeenCalled()
  })
})
