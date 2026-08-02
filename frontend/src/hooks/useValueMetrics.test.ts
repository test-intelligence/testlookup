/**
 * Tests for useValueMetrics hook.
 *
 * Regression guard for the ValueMetricsPage migration off a load-on-mount
 * `useEffect` (set-state-in-effect): verifies the SWR hook still fetches with
 * the project + window key and surfaces the metrics declaratively. Extended
 * for US-12.1: the key/fetch now carry the `months` window for the
 * hours-saved monthly series (contract default 6).
 */
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn() } }))

vi.mock('@/services/valueMetricsService', () => ({
  valueMetricsService: {
    get: vi.fn(),
    getMethodology: vi.fn(),
  },
}))

const sampleMetrics = {
  period_days: 30,
  project_id: 'proj-1',
  triage_time_saved_minutes: 120,
  triage_time_saved_hours: 2,
  defects_auto_grouped: 3,
  tests_grouped: 9,
  duplicate_tickets_avoided: 1,
  defects_promoted: 0,
  flaky_tests_identified: 2,
  quarantine_recommended: 1,
  risky_releases_blocked: 0,
  releases_conditional: 1,
  release_overrides: 0,
  intelligence_reports_generated: 4,
  // US-12.1 hours-saved model keys (pinned contract)
  available: true,
  insufficient_data_reason: null,
  headline: { hours_saved_30d: 12.5, fte_equivalent_30d: 0.3 },
  monthly: [],
  assumptions: {
    triage_minutes_per_failure: 15,
    blocked_run_wait_minutes: 30,
    defect_filing_minutes: 10,
  },
  assumptions_source: 'default',
  methodology_version: 1,
}

describe('useValueMetrics', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches with the provided projectId, days and default months', async () => {
    const { valueMetricsService } = await import('@/services/valueMetricsService')
    ;(valueMetricsService.get as ReturnType<typeof vi.fn>).mockResolvedValue(sampleMetrics)

    const { useValueMetrics } = await import('./useValueMetrics')
    const { result } = renderHook(() => useValueMetrics('proj-1', 90))

    await waitFor(() => expect(result.current.metrics).toEqual(sampleMetrics))
    expect(valueMetricsService.get).toHaveBeenCalledWith('proj-1', 90, 6)
    expect(result.current.isError).toBe(false)
  })

  it('fetches without a project filter in all-projects mode', async () => {
    const { valueMetricsService } = await import('@/services/valueMetricsService')
    ;(valueMetricsService.get as ReturnType<typeof vi.fn>).mockResolvedValue(sampleMetrics)

    const { useValueMetrics } = await import('./useValueMetrics')
    const { result } = renderHook(() => useValueMetrics(undefined, 7))

    await waitFor(() => expect(result.current.metrics).toEqual(sampleMetrics))
    expect(valueMetricsService.get).toHaveBeenCalledWith(undefined, 7, 6)
  })

  it('honours an explicit months window in the fetch and key', async () => {
    const { valueMetricsService } = await import('@/services/valueMetricsService')
    ;(valueMetricsService.get as ReturnType<typeof vi.fn>).mockResolvedValue(sampleMetrics)

    const { useValueMetrics } = await import('./useValueMetrics')
    const { result } = renderHook(() => useValueMetrics('proj-1', 30, 12))

    await waitFor(() => expect(result.current.metrics).toEqual(sampleMetrics))
    expect(valueMetricsService.get).toHaveBeenCalledWith('proj-1', 30, 12)
  })

  it('surfaces an error and toasts when the fetch fails', async () => {
    const toast = (await import('react-hot-toast')).default
    const { valueMetricsService } = await import('@/services/valueMetricsService')
    ;(valueMetricsService.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useValueMetrics } = await import('./useValueMetrics')
    const { result } = renderHook(() => useValueMetrics('proj-err', 30))

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.metrics).toBeUndefined()
    expect(toast.error).toHaveBeenCalledWith('Failed to load value metrics')
  })
})
