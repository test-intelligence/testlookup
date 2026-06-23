/**
 * Tests for useSSOTabData hook.
 *
 * Regression guard for the SSOSettingsPage migration off a tab-keyed
 * load-on-mount `useEffect` (set-state-in-effect): verifies the SWR hook fetches
 * exactly the active tab's slice (and nothing else), surfaces the data
 * declaratively, exposes a failed load as a string for the inline banner, and
 * exposes `refresh` for post-mutation revalidation (the old `loadData()`).
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/ssoService', () => ({
  listSSOConfigs: vi.fn(),
  listSCIMTokens: vi.fn(),
  listIdentityEvents: vi.fn(),
  getIdentitySyncStatus: vi.fn(),
}))

// The hook keys on ['sso-tab', tab], so each test needs its own SWR cache to
// avoid bleeding the previous test's resolved/rejected value.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

describe('useSSOTabData', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches only the config slice for the config tab', async () => {
    const svc = await import('@/services/ssoService')
    ;(svc.listSSOConfigs as ReturnType<typeof vi.fn>).mockResolvedValue([{ id: 'c1' }])

    const { useSSOTabData } = await import('./useSSOTabData')
    const { result } = renderHook(() => useSSOTabData('config'), { wrapper })

    await waitFor(() => expect(result.current.data.configs).toEqual([{ id: 'c1' }]))
    expect(svc.listSSOConfigs).toHaveBeenCalledTimes(1)
    expect(svc.listSCIMTokens).not.toHaveBeenCalled()
    expect(svc.listIdentityEvents).not.toHaveBeenCalled()
    expect(svc.getIdentitySyncStatus).not.toHaveBeenCalled()
    expect(result.current.data.scimTokens).toEqual([])
    expect(result.current.error).toBeNull()
  })

  it('unwraps the events page envelope for the events tab', async () => {
    const svc = await import('@/services/ssoService')
    ;(svc.listIdentityEvents as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 1,
      items: [{ id: 'e1' }],
    })

    const { useSSOTabData } = await import('./useSSOTabData')
    const { result } = renderHook(() => useSSOTabData('events'), { wrapper })

    await waitFor(() => expect(result.current.data.events).toEqual([{ id: 'e1' }]))
    expect(svc.listIdentityEvents).toHaveBeenCalledWith({ days: 30, page_size: 50 })
  })

  it('surfaces a failed load as a string error', async () => {
    const svc = await import('@/services/ssoService')
    ;(svc.getIdentitySyncStatus as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useSSOTabData } = await import('./useSSOTabData')
    const { result } = renderHook(() => useSSOTabData('sync'), { wrapper })

    await waitFor(() => expect(result.current.error).toBe('boom'))
    expect(result.current.isLoading).toBe(false)
    expect(result.current.data.syncStatus).toBeNull()
  })

  it('exposes refresh for post-mutation revalidation', async () => {
    const svc = await import('@/services/ssoService')
    ;(svc.listSCIMTokens as ReturnType<typeof vi.fn>).mockResolvedValue([{ id: 't1' }])

    const { useSSOTabData } = await import('./useSSOTabData')
    const { result } = renderHook(() => useSSOTabData('scim'), { wrapper })

    await waitFor(() => expect(result.current.data.scimTokens).toEqual([{ id: 't1' }]))
    ;(svc.listSCIMTokens as ReturnType<typeof vi.fn>).mockResolvedValue([{ id: 't1' }, { id: 't2' }])
    await result.current.refresh()
    await waitFor(() =>
      expect(result.current.data.scimTokens).toEqual([{ id: 't1' }, { id: 't2' }]),
    )
    expect(svc.listSCIMTokens).toHaveBeenCalledTimes(2)
  })
})
