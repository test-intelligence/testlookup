/**
 * FAIL-06: the Run Compare picker's reads. A search page asks `GET /runs` for
 * the reader's project, all time, every release, 20 a page; a picker's label
 * is read once (no 5 s poll, unlike `useRun`).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import useSWR, { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

const svc = vi.hoisted(() => ({ searchRuns: vi.fn(), getRun: vi.fn() }))
vi.mock('@/services/runListService', () => ({ runListService: svc }))
vi.mock('swr', async (importOriginal) => {
  const actual = await importOriginal<typeof import('swr')>()
  return { ...actual, default: vi.fn(actual.default) }
})

import { RUN_SEARCH_PAGE_SIZE, useRunSearch, useRunSummary } from './useRunSearch'

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)

const page = { items: [], total: 0, page: 1, size: 20, pages: 0 }

beforeEach(() => {
  svc.searchRuns.mockReset().mockResolvedValue(page)
  svc.getRun.mockReset()
  vi.mocked(useSWR).mockClear()
  useProjectStore.setState({ activeProjectId: 'p1' })
})
afterEach(() => {
  useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
})

describe('useRunSearch — one page of the server\'s search', () => {
  it('asks the project\'s runs for q and the suite, all time, 20 a page', async () => {
    const { result } = renderHook(() => useRunSearch({ q: ' 228 ', suiteName: 'Auth', page: 2, enabled: true }), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual(page))
    expect(RUN_SEARCH_PAGE_SIZE).toBe(20)
    expect(svc.searchRuns).toHaveBeenCalledWith('p1', { q: '228', suite_name: 'Auth', page: 2, size: 20, days: 0 })
  })

  it('in All Projects it asks without a project (every project the reader can see)', async () => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    renderHook(() => useRunSearch({ q: '', page: 1, enabled: true }), { wrapper })
    await waitFor(() => expect(svc.searchRuns).toHaveBeenCalledWith(null, { q: '', suite_name: null, page: 1, size: 20, days: 0 }))
  })

  it('asks nothing while disabled', async () => {
    renderHook(() => useRunSearch({ q: 'x', page: 1, enabled: false }), { wrapper })
    await new Promise((r) => setTimeout(r, 0))
    expect(svc.searchRuns).not.toHaveBeenCalled()
  })
})

describe('useRunSummary — a picker\'s label, read once', () => {
  it('reads the run quietly, and never polls', async () => {
    svc.getRun.mockResolvedValue({ id: 'r1' })
    const { result } = renderHook(() => useRunSummary('r1'), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ id: 'r1' }))
    expect(svc.getRun).toHaveBeenCalledWith('r1', { suppressToast: true })
    const call = vi.mocked(useSWR).mock.calls.find(([key]) => Array.isArray(key) && key[0] === 'run-summary')
    expect(call).toBeDefined()
    const options = (call?.[2] ?? {}) as Record<string, unknown>
    expect(options.refreshInterval).toBeUndefined()
    expect(options.revalidateOnFocus).toBe(false)
  })

  it('no id: no request', async () => {
    renderHook(() => useRunSummary(''), { wrapper })
    await new Promise((r) => setTimeout(r, 0))
    expect(svc.getRun).not.toHaveBeenCalled()
  })
})
