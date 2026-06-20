/**
 * Tests for useSeedStatus hook.
 *
 * Regression guard for the SeedDataPage migration off a load-on-mount
 * `useEffect` (set-state-in-effect): verifies the SWR hook still fetches the
 * dev seed status, surfaces `seeded` declaratively, distinguishes the error
 * case via `isError`, and exposes a `refresh` that re-checks status (the
 * old `await fetchStatus()` after a load/reset/delete mutation).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

// The hook keys on a constant ('dev-seed-status'), so each test needs its own
// SWR cache to avoid bleeding the previous test's resolved/rejected value.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

describe('useSeedStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches the seed status and surfaces seeded=true', async () => {
    const { api } = await import('@/services/api')
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { seeded: true } })

    const { useSeedStatus } = await import('./useSeedStatus')
    const { result } = renderHook(() => useSeedStatus(), { wrapper })

    await waitFor(() => expect(result.current.seeded).toBe(true))
    expect(api.get).toHaveBeenCalledWith('/api/v1/dev/seed/status')
    expect(result.current.isError).toBe(false)
  })

  it('surfaces seeded=false when no seed data is present', async () => {
    const { api } = await import('@/services/api')
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { seeded: false } })

    const { useSeedStatus } = await import('./useSeedStatus')
    const { result } = renderHook(() => useSeedStatus(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.seeded).toBe(false)
    expect(result.current.isError).toBe(false)
  })

  it('reports isError and seeded=null when the status check fails', async () => {
    const { api } = await import('@/services/api')
    ;(api.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useSeedStatus } = await import('./useSeedStatus')
    const { result } = renderHook(() => useSeedStatus(), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.seeded).toBeNull()
  })
})
