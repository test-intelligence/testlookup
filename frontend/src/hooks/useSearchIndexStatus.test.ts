import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { REINDEX_POLL_LIMIT_MS, UNKNOWN_INDEX_STATUS, reindexProgress, useSearchIndexStatus } from './useSearchIndexStatus'

const mockGetIndexStatus = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: { getIndexStatus: (...args: unknown[]) => mockGetIndexStatus(...args) },
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

beforeEach(() => { mockGetIndexStatus.mockReset() })

describe('useSearchIndexStatus (W12 SRCH-01)', () => {
  it('one read for the project; a failed read is unknown, never healthy', async () => {
    mockGetIndexStatus.mockRejectedValue(new Error('500'))
    const { result } = renderHook(() => useSearchIndexStatus('p1'), { wrapper })
    await waitFor(() => expect(result.current.indexStatus).toEqual(UNKNOWN_INDEX_STATUS))
    expect(mockGetIndexStatus).toHaveBeenCalledTimes(1)
    expect(mockGetIndexStatus).toHaveBeenCalledWith('p1')
  })

  it('All Projects reads without a project', async () => {
    mockGetIndexStatus.mockResolvedValue({ status: 'healthy', document_count: 3, last_indexed_at: null })
    const { result } = renderHook(() => useSearchIndexStatus(null), { wrapper })
    await waitFor(() => expect(result.current.indexStatus?.document_count).toBe(3))
    expect(mockGetIndexStatus).toHaveBeenCalledWith(undefined)
  })
})

describe('reindexProgress', () => {
  const queuedAt = Date.parse('2026-10-24T14:05:00Z')
  const status = (last: string | null) => ({ status: 'healthy' as const, document_count: 1, last_indexed_at: last })

  it('nothing queued: null', () => {
    expect(reindexProgress(status(null), null, queuedAt)).toBeNull()
  })

  it('pending until the index was written AFTER the queue time', () => {
    expect(reindexProgress(status('2026-10-24T14:00:00Z'), queuedAt, queuedAt + 10_000)).toBe('pending')
    expect(reindexProgress(status('2026-10-24T14:05:00Z'), queuedAt, queuedAt + 10_000)).toBe('pending')
    expect(reindexProgress(status('2026-10-24T14:05:01Z'), queuedAt, queuedAt + 10_000)).toBe('done')
    expect(reindexProgress(null, queuedAt, queuedAt)).toBe('pending')
  })

  it('times out after 10 minutes', () => {
    expect(reindexProgress(status(null), queuedAt, queuedAt + REINDEX_POLL_LIMIT_MS - 1)).toBe('pending')
    expect(reindexProgress(status(null), queuedAt, queuedAt + REINDEX_POLL_LIMIT_MS)).toBe('timeout')
  })
})
