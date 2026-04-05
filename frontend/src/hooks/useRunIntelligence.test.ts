/**
 * Tests for useRunIntelligence hook.
 *
 * Verifies SWR integration and null runId handling.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/services/runIntelligenceService', () => ({
  runIntelligenceService: {
    get: vi.fn(),
  },
}))

describe('useRunIntelligence', () => {
  it('does not fetch when runId is null', async () => {
    const { runIntelligenceService } = await import('@/services/runIntelligenceService')
    const { useRunIntelligence } = await import('./useRunIntelligence')

    const { result } = renderHook(() => useRunIntelligence(null))

    // SWR with null key → no fetch, intelligence stays undefined
    expect(result.current.intelligence).toBeUndefined()
    expect(runIntelligenceService.get).not.toHaveBeenCalled()
  })

  it('calls service.get with the provided runId', async () => {
    const { runIntelligenceService } = await import('@/services/runIntelligenceService')
    ;(runIntelligenceService.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: 'run-1',
      intelligence_available: true,
    })

    const { useRunIntelligence } = await import('./useRunIntelligence')
    const { result } = renderHook(() => useRunIntelligence('run-1'))

    await waitFor(() => {
      expect(result.current.intelligence).toBeDefined()
    })
    expect(runIntelligenceService.get).toHaveBeenCalledWith('run-1')
  })
})
