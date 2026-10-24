import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSearchUsage } from './useSearchUsage'

const mockUsage = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: { usage: (...args: unknown[]) => mockUsage(...args) },
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

beforeEach(() => {
  mockUsage.mockReset()
  mockUsage.mockResolvedValue({ days: 1, since: '2026-10-24T00:00:00Z', measured: false, searches: 0, zero_result_searches: 0, retention_days: 30 })
})

describe('useSearchUsage (W12 SRCH-05)', () => {
  it('today (days=1) for the pinned project, or every project the reader can see', async () => {
    const pinned = renderHook(() => useSearchUsage('p1', true), { wrapper })
    await waitFor(() => expect(pinned.result.current.data?.measured).toBe(false))
    expect(mockUsage).toHaveBeenCalledWith('p1', 1)
    renderHook(() => useSearchUsage(null, true), { wrapper })
    await waitFor(() => expect(mockUsage).toHaveBeenCalledWith(null, 1))
  })

  it('disabled (below QA lead): no request', () => {
    renderHook(() => useSearchUsage('p1', false), { wrapper })
    expect(mockUsage).not.toHaveBeenCalled()
  })
})
