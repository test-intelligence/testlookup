/**
 * Tests for useSuiteTrend hook.
 *
 * Regression guard for the SuiteDetailPage migration off a
 * `(suiteName, activeProjectId, days)`-keyed load effect (set-state-in-effect):
 * verifies the SWR hook fetches the per-day trend for the active project, maps
 * the "all projects" view to a `null` project id (as the old effect did), skips
 * the fetch when there is no suite name, and surfaces `[]` on error — the prior
 * `useState<SuiteTrendPoint[]>([])` + `.catch(() => setTrendPoints([]))`
 * semantics.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

vi.mock('@/services/testManagementService', () => ({
  testManagementService: { getSuiteTrend: vi.fn() },
}))

const samplePoints = [
  {
    date: '2026-06-01',
    run_count: 3,
    total_tests: 120,
    passed_count: 110,
    failed_count: 8,
    skipped_count: 2,
    broken_count: 0,
  },
]

// Each test needs its own SWR cache so a previous test's resolved/rejected value
// for an overlapping key doesn't bleed across.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

async function getMock() {
  const { testManagementService } = await import('@/services/testManagementService')
  return testManagementService.getSuiteTrend as ReturnType<typeof vi.fn>
}

describe('useSuiteTrend', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID, activeProject: null })
  })

  it('fetches the trend for the active project and surfaces the points', async () => {
    useProjectStore.setState({ activeProjectId: 'proj-1' })
    const getSuiteTrend = await getMock()
    getSuiteTrend.mockResolvedValue({ suite_name: 'smoke', days: 30, points: samplePoints })

    const { useSuiteTrend } = await import('./useSuiteTrend')
    const { result } = renderHook(() => useSuiteTrend('smoke', 30), { wrapper })

    await waitFor(() => expect(result.current.points).toEqual(samplePoints))
    expect(getSuiteTrend).toHaveBeenCalledWith('smoke', 'proj-1', 30)
    expect(result.current.isLoading).toBe(false)
  })

  it('maps the all-projects view to a null project id', async () => {
    const getSuiteTrend = await getMock()
    getSuiteTrend.mockResolvedValue({ suite_name: 'smoke', days: 7, points: [] })

    const { useSuiteTrend } = await import('./useSuiteTrend')
    renderHook(() => useSuiteTrend('smoke', 7), { wrapper })

    await waitFor(() => expect(getSuiteTrend).toHaveBeenCalledWith('smoke', null, 7))
  })

  it('does not fetch and returns an empty list when there is no suite name', async () => {
    const getSuiteTrend = await getMock()

    const { useSuiteTrend } = await import('./useSuiteTrend')
    const { result } = renderHook(() => useSuiteTrend(null, 30), { wrapper })

    expect(result.current.points).toEqual([])
    expect(getSuiteTrend).not.toHaveBeenCalled()
  })

  it('surfaces an empty list when the fetch fails', async () => {
    const getSuiteTrend = await getMock()
    getSuiteTrend.mockRejectedValue(new Error('boom'))

    const { useSuiteTrend } = await import('./useSuiteTrend')
    const { result } = renderHook(() => useSuiteTrend('smoke', 30), { wrapper })

    await waitFor(() => expect(getSuiteTrend).toHaveBeenCalled())
    expect(result.current.points).toEqual([])
  })
})
