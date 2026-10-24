import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useRecentSearches, withRecent, withoutRecents } from './useRecentSearches'
import type { RecentSearch, RecentSearches } from '@/types/search'

const mockRecent = vi.fn()
vi.mock('@/services/searchService', () => ({
  searchService: { recent: (...args: unknown[]) => mockRecent(...args) },
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

const entry = (q: string, overrides: Partial<RecentSearch> = {}): RecentSearch => ({
  q, scope: 'all', mode: 'keyword', project_id: 'p1', result_count: 1, counts_exact: true, ts: '2026-10-24T10:00:00Z', ...overrides,
})

beforeEach(() => { mockRecent.mockReset() })

describe('useRecentSearches (W12 SRCH-03)', () => {
  it('reads GET /search/recent once; disabled reads nothing', async () => {
    mockRecent.mockResolvedValue({ items: [entry('a')], max: 20, retention_days: 30 })
    const { result } = renderHook(() => useRecentSearches(true), { wrapper })
    await waitFor(() => expect(result.current.data?.items).toHaveLength(1))
    expect(mockRecent).toHaveBeenCalledTimes(1)

    mockRecent.mockClear()
    renderHook(() => useRecentSearches(false), { wrapper })
    expect(mockRecent).not.toHaveBeenCalled()
  })
})

describe('withRecent / withoutRecents', () => {
  const list: RecentSearches = { items: [entry('checkout'), entry('login'), entry('Checkout', { scope: 'tests' })], max: 3, retention_days: 30 }

  it('prepends, drops the older copy (normalised query + scope + mode + project), caps at max', () => {
    const next = withRecent(list, entry('  CHECKOUT ', { result_count: 9 }))
    expect(next.items.map((i) => [i.q, i.scope])).toEqual([['  CHECKOUT ', 'all'], ['login', 'all'], ['Checkout', 'tests']])
    expect(withRecent(list, entry('new')).items).toHaveLength(3)
    // Another project is another entry.
    expect(withRecent(list, entry('checkout', { project_id: 'p2' })).items).toHaveLength(3)
    expect(withRecent(list, entry('checkout', { project_id: 'p2' })).items[3]).toBeUndefined()
  })

  it('an unread list becomes a list of one; Clear empties it', () => {
    expect(withRecent(undefined, entry('a'))).toEqual({ items: [entry('a')], max: 20, retention_days: 30 })
    expect(withoutRecents(list)).toEqual({ items: [], max: 3, retention_days: 30 })
  })
})
