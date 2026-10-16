/**
 * TRND-06: the trend-summary hook asks `/analytics/trend-summary` in the
 * page's scope (window, suite, release; no project in All Projects) and hands
 * the page `parseTrendSummary`'s answer: garbage is `null`, never a crash.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({ trendsSummaryFetch: vi.fn() }))
vi.mock('@/services/trendsReportService', () => service)

const scope = vi.hoisted(() => ({ projectId: 'p1' as string | null, release: null as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: { activeProjectId: string | null }) => unknown) => selector({ activeProjectId: scope.projectId }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => scope.release }))

import { useTrendSummary } from './useTrendSummary'

function wrapper() {
  const cache = new Map()
  return ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
}

const VALID = {
  window_days: 14,
  today: '2026-09-18',
  measured: true,
  reason: null,
  days_with_runs: 3,
  empty_days: 11,
  active_day_share: 0.2143,
  executions: { total: 30, passed: 30, failed: 0, broken: 0, skipped: 0, evaluated: 30 },
  pass_rate: 100,
  variance: { stddev_pp: 0, stability: 100 },
  gap: null,
  last_run_day: '2026-09-18',
  previous_run_day: '2026-09-17',
  untagged_share: 0,
  confidence: { score: 45, dimensions: [], verdict: 'MIXED', band: 'Moderate' },
}

describe('useTrendSummary', () => {
  beforeEach(() => {
    scope.projectId = 'p1'
    scope.release = null
    service.trendsSummaryFetch.mockReset()
    service.trendsSummaryFetch.mockResolvedValue(VALID)
  })

  it('asks in the page scope and returns the parsed summary', async () => {
    scope.release = 'r1'
    const { result } = renderHook(() => useTrendSummary(14, 'Checkout'), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.summary?.confidence.score).toBe(45))
    expect(service.trendsSummaryFetch).toHaveBeenCalledWith('p1', 14, 'Checkout', 'r1')
  })

  it('All Projects asks without a project', async () => {
    scope.projectId = '__ALL__'
    renderHook(() => useTrendSummary(7, null), { wrapper: wrapper() })
    await waitFor(() => expect(service.trendsSummaryFetch).toHaveBeenCalled())
    expect(service.trendsSummaryFetch).toHaveBeenCalledWith(null, 7, null, null)
  })

  it('a malformed answer is null, not a crash', async () => {
    service.trendsSummaryFetch.mockResolvedValue({ confidence: 'x' })
    const { result } = renderHook(() => useTrendSummary(14, null), { wrapper: wrapper() })
    await waitFor(() => expect(service.trendsSummaryFetch).toHaveBeenCalled())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.summary).toBeNull()
  })

  it('no project resolved yet: asks nothing', () => {
    scope.projectId = null
    const { result } = renderHook(() => useTrendSummary(14, null), { wrapper: wrapper() })
    expect(service.trendsSummaryFetch).not.toHaveBeenCalled()
    expect(result.current.summary).toBeNull()
  })
})
