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

  // W10 INT-04: a project row's history.
  it('fetches a project row\'s history with the project id', async () => {
    const { getProviderHistory } = await import('@/services/integrationHealthService')
    ;(getProviderHistory as ReturnType<typeof vi.fn>).mockResolvedValue([])

    const { useProviderHistory } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useProviderHistory('github', true, 7, 'p-1'), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getProviderHistory).toHaveBeenCalledTimes(1)
    expect(getProviderHistory).toHaveBeenCalledWith('github', 7, 'p-1')
  })
})

// W10 INT-04: the Integration Health page's read, `?scope=all`.
describe('useIntegrationStatusAll', () => {
  beforeEach(() => vi.clearAllMocks())

  it('asks getAllStatus("all") once when enabled', async () => {
    const { getAllStatus } = await import('@/services/integrationHealthService')
    const PROJECT_ROW = { ...STATUS, provider: 'github', kind: 'project', scope_key: 'p-1', project_id: 'p-1', probed: false }
    ;(getAllStatus as ReturnType<typeof vi.fn>).mockResolvedValue([STATUS, PROJECT_ROW])

    const { useIntegrationStatusAll } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useIntegrationStatusAll(true), { wrapper })

    await waitFor(() => expect(result.current.statuses).toEqual([STATUS, PROJECT_ROW]))
    expect(getAllStatus).toHaveBeenCalledTimes(1)
    expect(getAllStatus).toHaveBeenCalledWith('all')
    expect(result.current.isError).toBe(false)
  })

  it('asks nothing when disabled (below QA lead)', async () => {
    const { getAllStatus } = await import('@/services/integrationHealthService')

    const { useIntegrationStatusAll } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => useIntegrationStatusAll(false), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getAllStatus).not.toHaveBeenCalled()
    expect(result.current.statuses).toEqual([])
  })

  it('keeps its own cache: the deployment-wide read beside it is a separate request', async () => {
    const { getAllStatus } = await import('@/services/integrationHealthService')
    ;(getAllStatus as ReturnType<typeof vi.fn>).mockImplementation(async (scope?: unknown) => (scope === 'all' ? [STATUS, STATUS] : [STATUS]))

    const { useIntegrationStatus, useIntegrationStatusAll } = await import('./useIntegrationHealth')
    const { result } = renderHook(() => ({ global: useIntegrationStatus(), all: useIntegrationStatusAll(true) }), { wrapper })

    await waitFor(() => expect(result.current.all.statuses).toHaveLength(2))
    await waitFor(() => expect(result.current.global.statuses).toHaveLength(1))
    expect(getAllStatus).toHaveBeenCalledTimes(2)
    // The global hook still hands SWR's key array (never the string 'all').
    expect((getAllStatus as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0])).toContainEqual(['integration-health-status'])
  })
})
