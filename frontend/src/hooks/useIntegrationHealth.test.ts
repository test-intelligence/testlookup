/**
 * Tests for the integration-health SWR hooks.
 *
 * Regression guard for the IntegrationHealthPage migration off a load-on-mount
 * `useEffect(() => { load() }, [load])` (set-state-in-effect): verifies each
 * dataset is surfaced declaratively, that the per-tab `enabled` gate (and the
 * history provider guard) skips the fetch when its tab is not active, and that a
 * failed load reports `isError` while keeping the data an empty array.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/integrationHealthService', () => ({
  getAllStatus: vi.fn(),
  getHealthTrends: vi.fn(),
  getProviderHistory: vi.fn(),
}))

// Each hook keys on distinct tuples; give every test its own SWR cache so a
// previous test's resolved/rejected value cannot bleed across.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const STATUS = {
  provider: 'jira',
  status: 'healthy',
  last_checked_at: '2026-01-01T00:00:00Z',
  message: null,
  response_ms: 42,
  consecutive_failures: 0,
  last_success_at: '2026-01-01T00:00:00Z',
}

describe('useIntegrationStatus', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches and surfaces statuses', async () => {
    const { getAllStatus } = await import('@/services/integrationHealthService')
    ;(getAllStatus as ReturnType<typeof vi.fn>).mockResolvedValue([STATUS])

    const { useIntegrationStatus } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useIntegrationStatus(), { wrapper })

    await waitFor(() => expect(result.current.statuses).toEqual([STATUS]))
    expect(getAllStatus).toHaveBeenCalledTimes(1)
    expect(result.current.isError).toBe(false)
  })

  it('reports isError and an empty list when the load fails', async () => {
    const { getAllStatus } = await import('@/services/integrationHealthService')
    ;(getAllStatus as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useIntegrationStatus } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useIntegrationStatus(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.statuses).toEqual([])
  })
})

describe('useHealthTrends', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches with the day window when enabled', async () => {
    const { getHealthTrends } = await import('@/services/integrationHealthService')
    ;(getHealthTrends as ReturnType<typeof vi.fn>).mockResolvedValue([])

    const { useHealthTrends } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useHealthTrends(true, 7), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getHealthTrends).toHaveBeenCalledWith(7)
    expect(result.current.trends).toEqual([])
  })

  it('does not fetch when its tab is inactive', async () => {
    const { getHealthTrends } = await import('@/services/integrationHealthService')

    const { useHealthTrends } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useHealthTrends(false), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getHealthTrends).not.toHaveBeenCalled()
    expect(result.current.trends).toEqual([])
  })
})

describe('useProviderHistory', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches history for the selected provider when enabled', async () => {
    const { getProviderHistory } = await import('@/services/integrationHealthService')
    ;(getProviderHistory as ReturnType<typeof vi.fn>).mockResolvedValue([])

    const { useProviderHistory } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useProviderHistory('jira', true, 7), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getProviderHistory).toHaveBeenCalledWith('jira', 7)
  })

  it('does not fetch without a selected provider even when enabled', async () => {
    const { getProviderHistory } = await import('@/services/integrationHealthService')

    const { useProviderHistory } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useProviderHistory('', true), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getProviderHistory).not.toHaveBeenCalled()
    expect(result.current.history).toEqual([])
  })
})
