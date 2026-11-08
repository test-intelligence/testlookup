/**
 * W38 (W31 F-1 / F-2): the erasure-request and SCIM-delete-setting hooks through the REAL SWR and the REAL services
 * (only `@/services/api` is mocked, so the URLs, params and bodies asserted are the wire ones).
 *
 * - `useMyErasureRequest` reads `GET /api/v1/me/erasure-request` once (null = never asked).
 * - `useErasureRequests(status, enabled)` asks NOTHING while disabled, then the queue with `?status=`.
 * - `useScimDeleteAction(enabled)` asks NOTHING while disabled; `setScimDeleteAction` PUTs ONLY `{action}` with no
 *   global toast (the SCIM tab renders a 403 inline).
 */
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/services/api'
import { setScimDeleteAction } from '@/services/ssoService'
import { erasureRequestList, erasureRequestRowFixture, myErasureRequestFixture } from '@/test/subjectRightsMocks'
import { useErasureRequests, useMyErasureRequest, useScimDeleteAction } from './useErasureRequests'

vi.mock('@/services/api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))

function wrapper({ children }: { children: ReactNode }) {
  return <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>{children}</SWRConfig>
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useMyErasureRequest', () => {
  it('reads my newest request once; null when I never asked', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: null })
    const { result } = renderHook(() => useMyErasureRequest(), { wrapper })
    await waitFor(() => expect(result.current.data).toBeNull())
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/api/v1/me/erasure-request', undefined)
  })

  it('hands back the pending request', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: myErasureRequestFixture() })
    const { result } = renderHook(() => useMyErasureRequest(), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('pending'))
  })
})

describe('useErasureRequests', () => {
  it('asks nothing while disabled, then the queue with the status filter', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: erasureRequestList([erasureRequestRowFixture()]) })
    const { result, rerender } = renderHook(({ on }) => useErasureRequests('pending', on), {
      wrapper,
      initialProps: { on: false },
    })
    expect(result.current.data).toBeUndefined()
    expect(api.get).not.toHaveBeenCalled()
    rerender({ on: true })
    await waitFor(() => expect(result.current.data?.total).toBe(1))
    expect(api.get).toHaveBeenCalledWith('/api/v1/users/erasure-requests', { params: { status: 'pending' } })
  })

  it('a different filter is a different read', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: erasureRequestList([]) })
    const { rerender } = renderHook(({ status }) => useErasureRequests(status), {
      wrapper,
      initialProps: { status: 'pending' as 'pending' | 'decided' | 'all' },
    })
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(1))
    rerender({ status: 'decided' })
    await waitFor(() => expect(api.get).toHaveBeenLastCalledWith('/api/v1/users/erasure-requests', { params: { status: 'decided' } }))
  })
})

describe('useScimDeleteAction / setScimDeleteAction', () => {
  it('asks nothing while disabled, then the setting', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { action: 'deactivate', updated_at: null, updated_by_display: null } })
    const { result, rerender } = renderHook(({ on }) => useScimDeleteAction(on), { wrapper, initialProps: { on: false } })
    expect(api.get).not.toHaveBeenCalled()
    rerender({ on: true })
    await waitFor(() => expect(result.current.data?.action).toBe('deactivate'))
    expect(api.get).toHaveBeenCalledWith('/api/v1/settings/scim-delete-action')
  })

  it('PUTs ONLY the action, with no global toast', async () => {
    vi.mocked(api.put).mockResolvedValueOnce({ data: { action: 'erase', updated_at: null, updated_by_display: 'Morgan Admin' } })
    const saved = await setScimDeleteAction('erase')
    expect(api.put).toHaveBeenCalledWith('/api/v1/settings/scim-delete-action', { action: 'erase' }, { suppressToast: true })
    expect(saved.action).toBe('erase')
  })
})
