/**
 * K6: "has this project EVER had a run?" — the unfiltered existence probe a
 * catalogue frame needs before it may say "no data yet" instead of "nothing
 * matches the filters".
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const list = vi.fn()
vi.mock('@/services/runsService', () => ({ runsService: { list: (...args: unknown[]) => list(...args) } }))

let activeProjectId: string | null = 'proj-1'
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId }),
}))

let releaseScope: string | null = null
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => releaseScope }))

import { useRuns } from '@/hooks/useRuns'
import { EVER_HAD_RUN_PARAMS, useEverHadRun } from './useEverHadRun'

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000 }}>{children}</SWRConfig>
)

const page = (n: number) => ({ items: Array.from({ length: n }, (_, i) => ({ id: `run-${i}` })), total: n })

describe('useEverHadRun (K6)', () => {
  beforeEach(() => {
    list.mockReset()
    activeProjectId = 'proj-1'
    releaseScope = null
  })

  it('asks nothing and answers null while disabled', () => {
    const { result } = renderHook(() => useEverHadRun(false), { wrapper })
    expect(result.current).toBeNull()
    expect(list).not.toHaveBeenCalled()
  })

  it('is null while the probe is in flight, then true when the project has a run', async () => {
    let release!: (value: unknown) => void
    list.mockImplementation(() => new Promise((resolve) => (release = resolve)))
    const { result } = renderHook(() => useEverHadRun(true), { wrapper })
    expect(result.current).toBeNull()
    await waitFor(() => expect(list).toHaveBeenCalled())
    release(page(1))
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('is false for a project with no run at all', async () => {
    list.mockResolvedValue(page(0))
    const { result } = renderHook(() => useEverHadRun(true), { wrapper })
    await waitFor(() => expect(result.current).toBe(false))
  })

  it('is UNFILTERED: page 1, size 1, and no release even while one is selected', async () => {
    releaseScope = 'R1'
    list.mockResolvedValue(page(1))
    renderHook(() => useEverHadRun(true), { wrapper })
    await waitFor(() => expect(list).toHaveBeenCalled())
    expect(list).toHaveBeenCalledWith('proj-1', { page: 1, size: 1 })
    expect(EVER_HAD_RUN_PARAMS).toEqual({ page: 1, size: 1 })
  })

  it('All Projects: asks across every project (no project id)', async () => {
    activeProjectId = 'all'
    list.mockResolvedValue(page(1))
    renderHook(() => useEverHadRun(true), { wrapper })
    await waitFor(() => expect(list).toHaveBeenCalledWith(null, { page: 1, size: 1 }))
  })

  it('shares ONE request with Overview’s own probe (same params, same SWR key)', async () => {
    list.mockResolvedValue(page(1))
    const { result } = renderHook(
      () => ({
        overview: useRuns({ page: 1, size: 1 }, { ignoreGlobalRelease: true }),
        probe: useEverHadRun(true),
      }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.probe).toBe(true))
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('a failed probe never claims "no data yet": it answers true, so an empty chart says "nothing matches"', async () => {
    list.mockRejectedValue(new Error('500'))
    const { result } = renderHook(() => useEverHadRun(true), { wrapper })
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('latches per project: once a run is seen it stays true and stops polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      list.mockResolvedValue(page(1))
      const { result } = renderHook(() => useEverHadRun(true), { wrapper })
      await waitFor(() => expect(result.current).toBe(true))
      const asked = list.mock.calls.length
      // `useRuns` polls every 15 s; a latched probe has no key, so no poll.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(46_000)
      })
      expect(result.current).toBe(true)
      expect(list.mock.calls.length).toBe(asked)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a project with no run keeps polling, so the first ingest flips it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      list.mockResolvedValueOnce(page(0)).mockResolvedValue(page(1))
      const { result } = renderHook(() => useEverHadRun(true), { wrapper })
      await waitFor(() => expect(result.current).toBe(false))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(16_000)
      })
      await waitFor(() => expect(result.current).toBe(true))
    } finally {
      vi.useRealTimers()
    }
  })

  it('a different project asks again (the latch is per project)', async () => {
    list.mockResolvedValueOnce(page(1)).mockResolvedValueOnce(page(0))
    const { result, rerender } = renderHook(() => useEverHadRun(true), { wrapper })
    await waitFor(() => expect(result.current).toBe(true))
    activeProjectId = 'proj-2'
    rerender()
    await waitFor(() => expect(list).toHaveBeenCalledWith('proj-2', { page: 1, size: 1 }))
    await waitFor(() => expect(result.current).toBe(false))
  })
})
