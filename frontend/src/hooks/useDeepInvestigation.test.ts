/**
 * Tests for usePipelineStatus hook.
 *
 * Regression guard for the DeepInvestigationPage migration off a load-on-mount
 * `useEffect` (set-state-in-effect): verifies the SWR hook fetches the WF-1
 * pipeline status keyed on the run id, skips the fetch entirely when there is
 * no run (the old `if (!runId) { setPipelineStatus(null); return }` guard),
 * and surfaces `undefined` data (rendered as null on the page) on a failed
 * load without retrying.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/deepInvestigationService', () => ({
  deepInvestigationService: {
    getPipelineStatus: vi.fn(),
  },
}))

// The hook keys on the run id, so each test gets its own SWR cache to avoid
// bleeding a previous test's resolved/rejected value across keys.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const STATUS = {
  pipeline_run_id: 'p1',
  workflow_type: 'deep',
  status: 'running',
  started_at: null,
  completed_at: null,
  error: null,
  stage_summary: { completed: 1, failed: 0, skipped: 0, pending: 3 },
}

describe('usePipelineStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the pipeline status for the given run', async () => {
    const { deepInvestigationService } = await import('@/services/deepInvestigationService')
    ;(deepInvestigationService.getPipelineStatus as ReturnType<typeof vi.fn>).mockResolvedValue(STATUS)

    const { usePipelineStatus } = await import('./useDeepInvestigation')
    const { result } = renderHook(() => usePipelineStatus('run-1', 'deep'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual(STATUS))
    expect(deepInvestigationService.getPipelineStatus).toHaveBeenCalledWith('run-1', 'deep')
  })

  it('does not fetch when there is no run', async () => {
    const { deepInvestigationService } = await import('@/services/deepInvestigationService')

    const { usePipelineStatus } = await import('./useDeepInvestigation')
    const { result } = renderHook(() => usePipelineStatus(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toBeUndefined()
    expect(deepInvestigationService.getPipelineStatus).not.toHaveBeenCalled()
  })

  it('surfaces an error with no data when the status load fails', async () => {
    const { deepInvestigationService } = await import('@/services/deepInvestigationService')
    ;(deepInvestigationService.getPipelineStatus as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { usePipelineStatus } = await import('./useDeepInvestigation')
    const { result } = renderHook(() => usePipelineStatus('run-1'), { wrapper })

    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error))
    expect(result.current.data).toBeUndefined()
  })
})
