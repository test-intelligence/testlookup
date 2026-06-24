/**
 * Tests for the audit-dashboard SWR hooks.
 *
 * Regression guard for the AuditDashboardPage migration off tab-keyed
 * load-on-mount effects (set-state-in-effect): verifies each dataset is surfaced
 * declaratively, that the per-tab `enabled` gate (and the observability project
 * guard) skips the fetch when its tab is not active, that events re-key on the
 * filter params, and that a failed load reports `isError` while keeping the data
 * empty.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/auditDashboardService', () => ({
  listCategories: vi.fn(),
  listAuditEvents: vi.fn(),
  getProjectObservability: vi.fn(),
}))

// Each hook keys on distinct tuples; give every test its own SWR cache so a
// previous test's resolved/rejected value cannot bleed across.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const CATEGORY = { key: 'access', description: 'Access events' }
const EVENT = {
  source: 'access',
  action: 'login',
  actor_name: 'alice',
  actor_id: 'u1',
  project_id: null,
  detail: null,
  created_at: '2026-01-01T00:00:00Z',
}
const OBSERVABILITY = {
  project_id: 'p1',
  period_days: 7,
  total_runs: 3,
  total_tests: 9,
  avg_pass_rate: 90,
  failed_runs: 1,
  ai_analyses_count: 2,
  release_decisions_count: 1,
  audit_events_count: 4,
}

describe('useAuditCategories', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches and surfaces categories', async () => {
    const { listCategories } = await import('@/services/auditDashboardService')
    ;(listCategories as ReturnType<typeof vi.fn>).mockResolvedValue([CATEGORY])

    const { useAuditCategories } = await import('./useAuditDashboard')
    const { result } = renderHook(() => useAuditCategories(), { wrapper })

    await waitFor(() => expect(result.current.categories).toEqual([CATEGORY]))
    expect(result.current.isError).toBe(false)
  })

  it('reports isError and an empty list when the load fails', async () => {
    const { listCategories } = await import('@/services/auditDashboardService')
    ;(listCategories as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useAuditCategories } = await import('./useAuditDashboard')
    const { result } = renderHook(() => useAuditCategories(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.categories).toEqual([])
  })
})

describe('useAuditEvents', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches events with the active filters when enabled', async () => {
    const { listAuditEvents } = await import('@/services/auditDashboardService')
    ;(listAuditEvents as ReturnType<typeof vi.fn>).mockResolvedValue({ total: 1, items: [EVENT] })

    const { useAuditEvents } = await import('./useAuditDashboard')
    const { result } = renderHook(
      () => useAuditEvents({ projectId: 'p1', category: 'access', days: 30 }, true),
      { wrapper },
    )

    await waitFor(() => expect(result.current.events).toEqual([EVENT]))
    expect(result.current.total).toBe(1)
    expect(listAuditEvents).toHaveBeenCalledWith({
      project_id: 'p1',
      category: 'access',
      days: 30,
      page_size: 100,
    })
  })

  it('omits empty project and category filters', async () => {
    const { listAuditEvents } = await import('@/services/auditDashboardService')
    ;(listAuditEvents as ReturnType<typeof vi.fn>).mockResolvedValue({ total: 0, items: [] })

    const { useAuditEvents } = await import('./useAuditDashboard')
    renderHook(() => useAuditEvents({ category: '', days: 7 }, true), { wrapper })

    await waitFor(() => expect(listAuditEvents).toHaveBeenCalledTimes(1))
    expect(listAuditEvents).toHaveBeenCalledWith({
      project_id: undefined,
      category: undefined,
      days: 7,
      page_size: 100,
    })
  })

  it('does not fetch when its tab is inactive', async () => {
    const { listAuditEvents } = await import('@/services/auditDashboardService')

    const { useAuditEvents } = await import('./useAuditDashboard')
    const { result } = renderHook(
      () => useAuditEvents({ category: '', days: 7 }, false),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(listAuditEvents).not.toHaveBeenCalled()
    expect(result.current.events).toEqual([])
    expect(result.current.total).toBe(0)
  })
})

describe('useProjectObservability', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches observability for the selected project when enabled', async () => {
    const { getProjectObservability } = await import('@/services/auditDashboardService')
    ;(getProjectObservability as ReturnType<typeof vi.fn>).mockResolvedValue(OBSERVABILITY)

    const { useProjectObservability } = await import('./useAuditDashboard')
    const { result } = renderHook(() => useProjectObservability('p1', true), { wrapper })

    await waitFor(() => expect(result.current.observability).toEqual(OBSERVABILITY))
    expect(getProjectObservability).toHaveBeenCalledWith('p1', 7)
  })

  it('does not fetch without a selected project even when enabled', async () => {
    const { getProjectObservability } = await import('@/services/auditDashboardService')

    const { useProjectObservability } = await import('./useAuditDashboard')
    const { result } = renderHook(() => useProjectObservability(undefined, true), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getProjectObservability).not.toHaveBeenCalled()
    expect(result.current.observability).toBeNull()
  })

  it('does not fetch when its tab is inactive', async () => {
    const { getProjectObservability } = await import('@/services/auditDashboardService')

    const { useProjectObservability } = await import('./useAuditDashboard')
    renderHook(() => useProjectObservability('p1', false), { wrapper })

    await waitFor(() => expect(getProjectObservability).not.toHaveBeenCalled())
  })
})
