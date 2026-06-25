/**
 * Tests for the digest-page SWR hooks.
 *
 * Regression guard for the DigestsPage migration off its tab-driven load-on-mount
 * effect (set-state-in-effect): verifies each dataset is surfaced declaratively,
 * that the `enabled` gate (active tab) skips the fetch when the tab is inactive,
 * that saved views re-key on the selected project, that both hooks expose
 * `mutate` for post-mutation refresh, and that a failed load reports `isError`
 * while keeping the data empty.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/digestService', () => ({
  listSubscriptions: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(),
}))

// Each hook keys on distinct tuples; give every test its own SWR cache so a
// previous test's resolved/rejected value cannot bleed across.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const SUB = {
  id: 's1',
  name: 'Weekly QA',
  schedule: 'WEEKLY',
  channel: 'email',
  is_active: true,
  is_paused: false,
  delivery_count: 3,
}

const VIEW = {
  id: 'v1',
  name: 'Failures only',
  is_default: false,
  is_shared: false,
  filters: {},
  created_at: '2026-01-01T00:00:00Z',
}

describe('useDigestSubscriptions', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches and surfaces the subscription list when enabled', async () => {
    const { listSubscriptions } = await import('@/services/digestService')
    ;(listSubscriptions as ReturnType<typeof vi.fn>).mockResolvedValue([SUB])

    const { useDigestSubscriptions } = await import('./useDigestData')
    const { result } = renderHook(() => useDigestSubscriptions(true), { wrapper })

    await waitFor(() => expect(result.current.subscriptions).toEqual([SUB]))
    expect(result.current.isError).toBe(false)
    expect(typeof result.current.mutate).toBe('function')
    expect(listSubscriptions).toHaveBeenCalledTimes(1)
  })

  it('does not fetch when its tab is inactive', async () => {
    const { listSubscriptions } = await import('@/services/digestService')

    const { useDigestSubscriptions } = await import('./useDigestData')
    const { result } = renderHook(() => useDigestSubscriptions(false), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(listSubscriptions).not.toHaveBeenCalled()
    expect(result.current.subscriptions).toEqual([])
  })

  it('reports isError and an empty list when the load fails', async () => {
    const { listSubscriptions } = await import('@/services/digestService')
    ;(listSubscriptions as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useDigestSubscriptions } = await import('./useDigestData')
    const { result } = renderHook(() => useDigestSubscriptions(true), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.subscriptions).toEqual([])
  })
})

describe('useDigestSavedViews', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches the saved views scoped to the selected project when enabled', async () => {
    const { listSavedViews } = await import('@/services/savedViewsService')
    ;(listSavedViews as ReturnType<typeof vi.fn>).mockResolvedValue([VIEW])

    const { useDigestSavedViews } = await import('./useDigestData')
    const { result } = renderHook(() => useDigestSavedViews('p1', true), { wrapper })

    await waitFor(() => expect(result.current.views).toEqual([VIEW]))
    expect(listSavedViews).toHaveBeenCalledWith('p1')
    expect(typeof result.current.mutate).toBe('function')
  })

  it('passes undefined for the all-projects scope', async () => {
    const { listSavedViews } = await import('@/services/savedViewsService')
    ;(listSavedViews as ReturnType<typeof vi.fn>).mockResolvedValue([])

    const { useDigestSavedViews } = await import('./useDigestData')
    renderHook(() => useDigestSavedViews(undefined, true), { wrapper })

    await waitFor(() => expect(listSavedViews).toHaveBeenCalledWith(undefined))
  })

  it('does not fetch when its tab is inactive', async () => {
    const { listSavedViews } = await import('@/services/savedViewsService')

    const { useDigestSavedViews } = await import('./useDigestData')
    const { result } = renderHook(() => useDigestSavedViews('p1', false), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(listSavedViews).not.toHaveBeenCalled()
    expect(result.current.views).toEqual([])
  })

  it('reports isError and an empty list when the load fails', async () => {
    const { listSavedViews } = await import('@/services/savedViewsService')
    ;(listSavedViews as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('nope'))

    const { useDigestSavedViews } = await import('./useDigestData')
    const { result } = renderHook(() => useDigestSavedViews('p1', true), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.views).toEqual([])
  })
})
