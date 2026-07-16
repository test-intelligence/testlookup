/**
 * Polling semantics for useFixAttempts (AI-2 fix-attempts ledger).
 *
 * The hook uses SWR's function-form refreshInterval: 5 s while ANY attempt on
 * the current page is in a non-terminal status (selected/diagnosing/
 * generating/validating), 0 (stopped) once every attempt is terminal. Fake
 * timers drive the ticks.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { FixAttempt, FixAttemptStatus } from '@/types/fixer'

vi.mock('@/services/fixerService', () => ({
  fixerService: {
    getConfig: vi.fn(),
    updateConfig: vi.fn(),
    startRun: vi.fn(),
    listAttempts: vi.fn(),
    getAttempt: vi.fn(),
  },
}))

function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    SWRConfig,
    { value: { provider: () => new Map(), dedupingInterval: 0 } },
    children,
  )
}

function attempt(id: string, status: FixAttemptStatus): FixAttempt {
  return {
    id,
    fixer_run_id: 'fr-1',
    test_fingerprint: `fp-${id}`,
    test_name: `tests/test_checkout.py::test_${id}`,
    status,
    attempt_no: 1,
    patch_summary: null,
    validation: null,
    pr_url: null,
    reason: null,
    created_at: '2026-07-15T10:00:00Z',
    completed_at: null,
  }
}

describe('useFixAttempts polling', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // shouldAdvanceTime lets microtask-driven work (SWR's initial mount
    // revalidation + waitFor) proceed under real time while the 5 s poll
    // ticks are driven deterministically with advanceTimersByTimeAsync.
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not fetch without a project id', async () => {
    const { fixerService } = await import('@/services/fixerService')
    const { useFixAttempts } = await import('./useFixer')

    const { result } = renderHook(() => useFixAttempts(null), { wrapper })
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })

    expect(result.current.data).toBeUndefined()
    expect(fixerService.listAttempts).not.toHaveBeenCalled()
  })

  it('polls every 5s while any attempt is non-terminal and stops when all are terminal', async () => {
    const { fixerService } = await import('@/services/fixerService')
    const listAttempts = fixerService.listAttempts as ReturnType<typeof vi.fn>
    listAttempts
      .mockResolvedValueOnce({ items: [attempt('a1', 'validated'), attempt('a2', 'diagnosing')], total: 2 })
      .mockResolvedValueOnce({ items: [attempt('a1', 'validated'), attempt('a2', 'validating')], total: 2 })
      .mockResolvedValue({ items: [attempt('a1', 'validated'), attempt('a2', 'pr_opened')], total: 2 })

    const { useFixAttempts } = await import('./useFixer')
    const { result } = renderHook(() => useFixAttempts('proj-1', { limit: 25, offset: 0 }), { wrapper })

    // Initial fetch resolves with an in-flight attempt → polling armed.
    await waitFor(() => expect(result.current.data?.items[1]?.status).toBe('diagnosing'))
    expect(listAttempts).toHaveBeenCalledTimes(1)
    expect(listAttempts).toHaveBeenCalledWith('proj-1', { limit: 25, offset: 0 })

    // Tick 1 (5s): refetch — still in flight (validating).
    await act(async () => { await vi.advanceTimersByTimeAsync(5100) })
    await waitFor(() => expect(result.current.data?.items[1]?.status).toBe('validating'))
    expect(listAttempts).toHaveBeenCalledTimes(2)

    // Tick 2: every attempt is now terminal.
    await act(async () => { await vi.advanceTimersByTimeAsync(5100) })
    await waitFor(() => expect(result.current.data?.items[1]?.status).toBe('pr_opened'))
    expect(listAttempts).toHaveBeenCalledTimes(3)

    // Polling has STOPPED: a long stretch of time produces no further fetches.
    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(listAttempts).toHaveBeenCalledTimes(3)
  })

  it('never starts polling when every attempt is already terminal', async () => {
    const { fixerService } = await import('@/services/fixerService')
    const listAttempts = fixerService.listAttempts as ReturnType<typeof vi.fn>
    listAttempts.mockResolvedValue({
      items: [attempt('a1', 'failed_validation'), attempt('a2', 'rejected_globs'), attempt('a3', 'skipped_budget')],
      total: 3,
    })

    const { useFixAttempts } = await import('./useFixer')
    const { result } = renderHook(() => useFixAttempts('proj-1'), { wrapper })

    await waitFor(() => expect(result.current.data?.total).toBe(3))
    expect(listAttempts).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
    expect(listAttempts).toHaveBeenCalledTimes(1)
  })
})

describe('useFixerConfig / useFixAttempt', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the project config and skips without a project', async () => {
    const { fixerService } = await import('@/services/fixerService')
    const getConfig = fixerService.getConfig as ReturnType<typeof vi.fn>
    const { DEFAULT_FIXER_CONFIG } = await import('@/types/fixer')
    getConfig.mockResolvedValue(DEFAULT_FIXER_CONFIG)

    const { useFixerConfig } = await import('./useFixer')

    const none = renderHook(() => useFixerConfig(null), { wrapper })
    await waitFor(() => expect(none.result.current.isLoading).toBe(false))
    expect(getConfig).not.toHaveBeenCalled()

    const some = renderHook(() => useFixerConfig('proj-1'), { wrapper })
    await waitFor(() => expect(some.result.current.data).toEqual(DEFAULT_FIXER_CONFIG))
    expect(getConfig).toHaveBeenCalledWith('proj-1')
  })

  it('fetches a single attempt only when an id is supplied', async () => {
    const { fixerService } = await import('@/services/fixerService')
    const getAttempt = fixerService.getAttempt as ReturnType<typeof vi.fn>
    const detail = { ...attempt('a1', 'validated'), patch: '--- a\n+++ b', runner_log_digest: 'sha256:d1', ledger_run_id: 'ar-9' }
    getAttempt.mockResolvedValue(detail)

    const { useFixAttempt } = await import('./useFixer')

    const none = renderHook(() => useFixAttempt(null), { wrapper })
    await waitFor(() => expect(none.result.current.isLoading).toBe(false))
    expect(getAttempt).not.toHaveBeenCalled()

    const some = renderHook(() => useFixAttempt('a1'), { wrapper })
    await waitFor(() => expect(some.result.current.data).toEqual(detail))
    expect(getAttempt).toHaveBeenCalledWith('a1')
  })
})
