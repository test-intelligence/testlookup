/**
 * Tests for usePerformanceSettings hook.
 *
 * Regression guard for the PerformancePage migration off a load-on-mount
 * `useEffect` (set-state-in-effect): verifies the SWR hook still fetches both
 * the latency budgets and the search config in parallel, surfaces them
 * declaratively, and leaves them `null` (with `isError`) when a fetch fails —
 * the prior `useState<… | null>(null)` + `.catch(() => {})` semantics.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/performanceService', () => ({
  getPerformanceBudgets: vi.fn(),
  getSearchConfig: vi.fn(),
}))

const sampleBudgets = {
  latency_budgets: [
    { operation: 'search', p50_ms: 50, p95_ms: 120, p99_ms: 300, description: 'Full-text search' },
  ],
  throughput_budgets: [{ operation: 'ingest', min_rps: 10, description: 'Report ingestion' }],
  scale_scenarios: [
    {
      name: 'small',
      description: 'Single team',
      projects: 1,
      runs_per_day: 20,
      tests_per_run: 500,
      concurrent_users: 5,
    },
  ],
}

const sampleConfig = {
  index_batch_size: 100,
  incremental_limit: 1000,
  query_timeout_ms: 5000,
  max_results: 50,
  pg_pool_size: 10,
  pg_max_overflow: 20,
  pg_pool_recycle: 1800,
  celery_worker_concurrency: 4,
}

// The hook keys on a constant ('performance-settings'), so each test needs its
// own SWR cache to avoid bleeding the previous test's resolved/rejected value.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

describe('usePerformanceSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches budgets and config in parallel and surfaces them', async () => {
    const { getPerformanceBudgets, getSearchConfig } = await import('@/services/performanceService')
    ;(getPerformanceBudgets as ReturnType<typeof vi.fn>).mockResolvedValue(sampleBudgets)
    ;(getSearchConfig as ReturnType<typeof vi.fn>).mockResolvedValue(sampleConfig)

    const { usePerformanceSettings } = await import('./usePerformanceSettings')
    const { result } = renderHook(() => usePerformanceSettings(), { wrapper })

    await waitFor(() => expect(result.current.budgets).toEqual(sampleBudgets))
    expect(result.current.config).toEqual(sampleConfig)
    expect(getPerformanceBudgets).toHaveBeenCalledTimes(1)
    expect(getSearchConfig).toHaveBeenCalledTimes(1)
    expect(result.current.isError).toBe(false)
  })

  it('leaves budgets and config null and reports isError when a fetch fails', async () => {
    const { getPerformanceBudgets, getSearchConfig } = await import('@/services/performanceService')
    ;(getPerformanceBudgets as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))
    ;(getSearchConfig as ReturnType<typeof vi.fn>).mockResolvedValue(sampleConfig)

    const { usePerformanceSettings } = await import('./usePerformanceSettings')
    const { result } = renderHook(() => usePerformanceSettings(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.budgets).toBeNull()
    expect(result.current.config).toBeNull()
  })
})
