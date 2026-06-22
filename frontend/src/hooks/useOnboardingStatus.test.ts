/**
 * Tests for useOnboardingStatus hook.
 *
 * Regression guard for the OnboardingPage migration off a load-on-mount
 * `useEffect` (set-state-in-effect): verifies the SWR hook fetches onboarding
 * progress for the active project, skips the fetch entirely when no project is
 * resolved (the all-projects view), surfaces `status` declaratively, and
 * toasts on a failed load (the old `.catch`).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'
import type { OnboardingStatus } from '@/services/onboardingService'

vi.mock('@/services/onboardingService', () => ({
  onboardingService: {
    detectProgress: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn() },
}))

// The hook keys on ['onboarding-status', projectId], so each test needs its own
// SWR cache to avoid bleeding the previous test's resolved/rejected value.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const STATUS: OnboardingStatus = {
  project_id: 'p1',
  steps: [],
  completed_count: 1,
  total_count: 5,
  progress_pct: 20,
  is_complete: false,
}

describe('useOnboardingStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches onboarding progress for the active project', async () => {
    const { onboardingService } = await import('@/services/onboardingService')
    ;(onboardingService.detectProgress as ReturnType<typeof vi.fn>).mockResolvedValue(STATUS)

    const { useOnboardingStatus } = await import('./useOnboardingStatus')
    const { result } = renderHook(() => useOnboardingStatus('p1'), { wrapper })

    await waitFor(() => expect(result.current.status).toEqual(STATUS))
    expect(onboardingService.detectProgress).toHaveBeenCalledWith('p1')
  })

  it('skips the fetch entirely when no project is resolved', async () => {
    const { onboardingService } = await import('@/services/onboardingService')

    const { useOnboardingStatus } = await import('./useOnboardingStatus')
    const { result } = renderHook(() => useOnboardingStatus(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.status).toBeNull()
    expect(onboardingService.detectProgress).not.toHaveBeenCalled()
  })

  it('toasts and surfaces a null status when the load fails', async () => {
    const { onboardingService } = await import('@/services/onboardingService')
    ;(onboardingService.detectProgress as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))
    const toast = (await import('react-hot-toast')).default

    const { useOnboardingStatus } = await import('./useOnboardingStatus')
    const { result } = renderHook(() => useOnboardingStatus('p1'), { wrapper })

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Failed to load onboarding status'))
    expect(result.current.status).toBeNull()
    expect(result.current.isLoading).toBe(false)
  })
})
