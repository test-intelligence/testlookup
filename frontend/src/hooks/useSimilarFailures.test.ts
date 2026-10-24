import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSimilarFailures } from './useSimilarFailures'

const mockFindSimilar = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: { findSimilar: (...args: unknown[]) => mockFindSimilar(...args) },
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

beforeEach(() => {
  mockFindSimilar.mockReset()
  mockFindSimilar.mockResolvedValue({ items: [], total: 0, query: 'x', semantic_status: 'ok' })
})

describe('useSimilarFailures (W12 SRCH-01)', () => {
  it('asks for five neighbours of the result while enabled', async () => {
    const { result } = renderHook(() => useSimilarFailures('tc-1', true), { wrapper })
    await waitFor(() => expect(result.current.data?.semantic_status).toBe('ok'))
    expect(mockFindSimilar).toHaveBeenCalledWith('tc-1', 5)
  })

  it('disabled (the tab is closed): nothing', () => {
    renderHook(() => useSimilarFailures('tc-1', false), { wrapper })
    expect(mockFindSimilar).not.toHaveBeenCalled()
  })

  it('a refusal is the error at once, not retried', async () => {
    mockFindSimilar.mockRejectedValue({ response: { status: 403 } })
    const { result } = renderHook(() => useSimilarFailures('tc-2', true), { wrapper })
    await waitFor(() => expect(result.current.error).toBeDefined())
    expect(mockFindSimilar).toHaveBeenCalledTimes(1)
  })
})
