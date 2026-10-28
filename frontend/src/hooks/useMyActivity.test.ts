/**
 * W21d ACT-02: Inbox › Activity's read. It asks NOTHING while disabled (the
 * Inbox's other tabs see no new request); `since` is resolved from `days` at
 * FETCH time; `project_id` is sent only when a project is pinned; a page
 * without `items` reads as an empty list (never a crash); `total` /
 * `projects` come from the server, not from the list's length; the cursor
 * pages.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { listMyActivity, type MyActivityPage } from '@/services/activityService'
import { makeMyActivityItem, makeMyActivityPage } from '@/test/activityLiveMocks'
import { useMyActivity } from './useMyActivity'

vi.mock('@/services/activityService', () => ({
  listMyActivity: vi.fn(),
}))

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } }, children)

const PROJECT = '00000000-0000-4000-8000-000000000321'

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listMyActivity).mockResolvedValue(makeMyActivityPage())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useMyActivity', () => {
  it('asks nothing while disabled', async () => {
    const { result } = renderHook(() => useMyActivity({ view: 'all', days: 7 }, false), { wrapper })
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(listMyActivity).not.toHaveBeenCalled()
    expect(result.current.items).toEqual([])
    expect(result.current.total).toBeNull()
  })

  it('resolves since from days at fetch time and sends no project_id when none is pinned', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ['Date'] })
    const fetchedAt = new Date('2026-10-27T12:00:00Z')
    vi.setSystemTime(fetchedAt)
    renderHook(() => useMyActivity({ view: 'mine', days: 14 }), { wrapper })
    await waitFor(() => expect(listMyActivity).toHaveBeenCalledTimes(1))
    const query = vi.mocked(listMyActivity).mock.calls[0][0]
    expect(query).toEqual({
      view: 'mine',
      since: new Date(fetchedAt.getTime() - 14 * 86_400_000).toISOString(),
    })
    expect(query).not.toHaveProperty('project_id')
    expect(query).not.toHaveProperty('cursor')
  })

  it('sends project_id only when a project is pinned', async () => {
    renderHook(() => useMyActivity({ view: 'involving', days: 7, projectId: PROJECT }), { wrapper })
    await waitFor(() => expect(listMyActivity).toHaveBeenCalledTimes(1))
    expect(vi.mocked(listMyActivity).mock.calls[0][0]).toMatchObject({ view: 'involving', project_id: PROJECT })
  })

  it('reads total and projects from the server, not from the list', async () => {
    vi.mocked(listMyActivity).mockResolvedValue(
      makeMyActivityPage({ items: [makeMyActivityItem({ involvement: 'assigned' })], total: 37, projects: 4 }),
    )
    const { result } = renderHook(() => useMyActivity({ view: 'all', days: 7 }), { wrapper })
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(result.current.total).toBe(37)
    expect(result.current.projects).toBe(4)
    expect(result.current.items[0].involvement).toBe('assigned')
    expect(result.current.hasMore).toBe(false)
  })

  it('reads a page without items as an empty list', async () => {
    vi.mocked(listMyActivity).mockResolvedValue({ total: 0, projects: 0 } as unknown as MyActivityPage)
    const { result } = renderHook(() => useMyActivity({ view: 'all', days: 7 }), { wrapper })
    await waitFor(() => expect(result.current.total).toBe(0))
    expect(result.current.items).toEqual([])
    expect(result.current.projects).toBe(0)
    expect(result.current.error).toBeUndefined()
  })

  it('pages by the server cursor', async () => {
    const first = makeMyActivityItem()
    const second = makeMyActivityItem()
    // Answered by cursor: loading page 2 also revalidates page 1 (revalidateFirstPage).
    vi.mocked(listMyActivity).mockImplementation(async query =>
      query?.cursor === 'cursor-2'
        ? makeMyActivityPage({ items: [second], total: 2, next_cursor: null })
        : makeMyActivityPage({ items: [first], total: 2, next_cursor: 'cursor-2' }),
    )
    const { result } = renderHook(() => useMyActivity({ view: 'all', days: 7 }), { wrapper })
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => {
      void result.current.loadMore()
    })
    await waitFor(() => expect(result.current.items.map(item => item.id)).toEqual([first.id, second.id]))
    expect(vi.mocked(listMyActivity).mock.calls.some(([query]) => query?.cursor === 'cursor-2')).toBe(true)
    expect(result.current.hasMore).toBe(false)
    expect(result.current.total).toBe(2)
  })

  it('surfaces a failed read as an error', async () => {
    vi.mocked(listMyActivity).mockRejectedValue(new Error('Request failed with status code 500'))
    const { result } = renderHook(() => useMyActivity({ view: 'all', days: 7 }), { wrapper })
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error))
    expect(result.current.items).toEqual([])
  })
})
