/**
 * Polling semantics for useInvestigation (AI-1 cockpit).
 *
 * The hook uses SWR's function-form refreshInterval: 2.5 s while the latest
 * status is queued/running/synthesizing, 0 (stopped) once a terminal status
 * (completed/cancelled/failed) lands. Fake timers drive the ticks.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/investigatorService', () => ({
  investigatorService: {
    getInvestigation: vi.fn(),
    listInvestigations: vi.fn(),
  },
}))

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    SWRConfig,
    { value: { provider: () => new Map(), dedupingInterval: 0 } },
    children,
  )
}

const BASE_DETAIL = {
  id: 'inv-1',
  run_id: 'run-1',
  project_id: 'proj-1',
  mode: 'shadow',
  triggered_by: 'manual',
  started_at: '2026-07-15T10:00:00Z',
  completed_at: null,
  cancelled_by: null,
  budget: { max_llm_calls: 20, max_tokens: 100000, max_seconds: 300 },
  spend: { llm_calls: 3, tokens: 12000, cost_usd: 0.02, seconds: 41 },
  hypotheses: [],
  verdict: null,
  prompt_versions: {},
  model: null,
}

describe('useInvestigation polling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // shouldAdvanceTime lets microtask-driven work (SWR's initial mount
    // revalidation + testing-library's waitFor) proceed under real time
    // while the 2.5 s poll ticks are driven deterministically with
    // advanceTimersByTimeAsync.
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not fetch without an investigation id', async () => {
    const { investigatorService } = await import('@/services/investigatorService')
    const { useInvestigation } = await import('./useInvestigation')

    const { result } = renderHook(() => useInvestigation(null), { wrapper })
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })

    expect(result.current.data).toBeUndefined()
    expect(investigatorService.getInvestigation).not.toHaveBeenCalled()
  })

  it('polls every 2.5s while active and stops once the status is terminal', async () => {
    const { investigatorService } = await import('@/services/investigatorService')
    const getInvestigation = investigatorService.getInvestigation as ReturnType<typeof vi.fn>
    getInvestigation
      .mockResolvedValueOnce({ ...BASE_DETAIL, status: 'running' })
      .mockResolvedValueOnce({ ...BASE_DETAIL, status: 'synthesizing' })
      .mockResolvedValue({ ...BASE_DETAIL, status: 'completed', completed_at: '2026-07-15T10:05:00Z' })

    const { useInvestigation } = await import('./useInvestigation')
    const { result } = renderHook(() => useInvestigation('inv-1'), { wrapper })

    // Initial fetch resolves with an ACTIVE status → polling armed.
    await waitFor(() => expect(result.current.data?.status).toBe('running'))
    expect(getInvestigation).toHaveBeenCalledTimes(1)

    // Tick 1 (2.5s): refetch — still active (synthesizing).
    await act(async () => { await vi.advanceTimersByTimeAsync(2600) })
    await waitFor(() => expect(result.current.data?.status).toBe('synthesizing'))
    expect(getInvestigation).toHaveBeenCalledTimes(2)

    // Tick 2: terminal status lands.
    await act(async () => { await vi.advanceTimersByTimeAsync(2600) })
    await waitFor(() => expect(result.current.data?.status).toBe('completed'))
    expect(getInvestigation).toHaveBeenCalledTimes(3)

    // Polling has STOPPED: a long stretch of time produces no further fetches.
    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(getInvestigation).toHaveBeenCalledTimes(3)
  })

  it('never starts polling when the investigation is already terminal', async () => {
    const { investigatorService } = await import('@/services/investigatorService')
    const getInvestigation = investigatorService.getInvestigation as ReturnType<typeof vi.fn>
    getInvestigation.mockResolvedValue({ ...BASE_DETAIL, status: 'failed' })

    const { useInvestigation } = await import('./useInvestigation')
    const { result } = renderHook(() => useInvestigation('inv-1'), { wrapper })

    await waitFor(() => expect(result.current.data?.status).toBe('failed'))
    expect(getInvestigation).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
    expect(getInvestigation).toHaveBeenCalledTimes(1)
  })
})

describe('useInvestigations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the project investigation list and skips without a project', async () => {
    const { investigatorService } = await import('@/services/investigatorService')
    const listInvestigations = investigatorService.listInvestigations as ReturnType<typeof vi.fn>
    listInvestigations.mockResolvedValue({ items: [], total: 0 })

    const { useInvestigations } = await import('./useInvestigation')

    const none = renderHook(() => useInvestigations(null), { wrapper })
    await waitFor(() => expect(none.result.current.isLoading).toBe(false))
    expect(listInvestigations).not.toHaveBeenCalled()

    const some = renderHook(() => useInvestigations('proj-1', 20, 0), { wrapper })
    await waitFor(() => expect(some.result.current.data).toEqual({ items: [], total: 0 }))
    expect(listInvestigations).toHaveBeenCalledWith('proj-1', 20, 0)
  })
})
