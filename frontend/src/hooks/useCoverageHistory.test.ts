/**
 * W08 TRND-07: the History tab's read and the Fix tagging panel's read ask
 * only when their tab / panel is open AND one project is pinned, and every
 * answer goes through the guard.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({ getCoverageHistory: vi.fn(), getUntaggedTests: vi.fn() }))
vi.mock('@/services/coverageHistoryService', () => ({ UNTAGGED_PAGE_SIZE: 50, coverageHistoryService: service }))

const project = vi.hoisted(() => ({ id: 'proj-1' as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: { activeProjectId: string | null }) => unknown) => selector({ activeProjectId: project.id }),
}))

import { useCoverageHistory, useUntaggedTests } from './useCoverageHistory'

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false } }, children)

const settle = () => new Promise((resolve) => setTimeout(resolve, 20))

describe('useCoverageHistory', () => {
  beforeEach(() => {
    service.getCoverageHistory.mockReset()
    project.id = 'proj-1'
  })

  it('asks nothing while the tab is closed, in All Projects, or with no project', async () => {
    service.getCoverageHistory.mockResolvedValue({ measured: false, reason: null, first_snapshot_day: null, points: [] })
    renderHook(() => useCoverageHistory(30, false), { wrapper })
    project.id = '__ALL__'
    renderHook(() => useCoverageHistory(30, true), { wrapper })
    project.id = null
    renderHook(() => useCoverageHistory(30, true), { wrapper })
    await settle()
    expect(service.getCoverageHistory).not.toHaveBeenCalled()
  })

  it('asks for the pinned project and the window, and parses', async () => {
    service.getCoverageHistory.mockResolvedValue({ window_days: 14, measured: true, reason: null, first_snapshot_day: '2026-10-01', points: [] })
    const { result } = renderHook(() => useCoverageHistory(14, true), { wrapper })
    await waitFor(() => expect(result.current.history).not.toBeNull())
    expect(service.getCoverageHistory).toHaveBeenCalledWith('proj-1', 14)
    expect(result.current.malformed).toBe(false)
  })

  it.each([
    ['garbage', { points: 'x' }],
    ['an empty body', undefined],
  ])('%s is malformed, never a crash', async (_label, answer) => {
    service.getCoverageHistory.mockResolvedValue(answer)
    const { result } = renderHook(() => useCoverageHistory(14, true), { wrapper })
    await waitFor(() => expect(result.current.malformed).toBe(true))
    expect(result.current.history).toBeNull()
  })
})

describe('useUntaggedTests', () => {
  beforeEach(() => service.getUntaggedTests.mockReset())

  it('asks nothing while the panel is closed or without one project', async () => {
    service.getUntaggedTests.mockResolvedValue({ items: [], total: 0 })
    renderHook(() => useUntaggedTests('proj-1', 30, 1, false), { wrapper })
    renderHook(() => useUntaggedTests('__ALL__', 30, 1, true), { wrapper })
    renderHook(() => useUntaggedTests(null, 30, 1, true), { wrapper })
    await settle()
    expect(service.getUntaggedTests).not.toHaveBeenCalled()
  })

  it('asks for the page, 50 a page, and parses', async () => {
    service.getUntaggedTests.mockResolvedValue({ items: [], total: 0, page: 2, size: 50, window_days: 30 })
    const { result } = renderHook(() => useUntaggedTests('proj-1', 30, 2, true), { wrapper })
    await waitFor(() => expect(result.current.list).not.toBeNull())
    expect(service.getUntaggedTests).toHaveBeenCalledWith('proj-1', 30, 2, 50)
  })

  it('a malformed page is malformed, not a crash', async () => {
    service.getUntaggedTests.mockResolvedValue({ items: 5 })
    const { result } = renderHook(() => useUntaggedTests('proj-1', 30, 1, true), { wrapper })
    await waitFor(() => expect(result.current.malformed).toBe(true))
  })
})
