/**
 * FAIL-08 `useSourceRoots`: one read per project (none in All Projects), the
 * PUT sends the WHOLE list once and becomes the cached answer (no second
 * read), the preview is a one-off POST that touches no cache, and a refused
 * save rejects with the server's error so the section can show its text.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const svc = vi.hoisted(() => ({
  getSourceRoots: vi.fn(),
  putSourceRoots: vi.fn(),
  previewSourceRoots: vi.fn(),
}))
vi.mock('@/services/sourceRootsService', () => svc)

import { useSourceRoots } from './useSourceRoots'

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

const ROOTS = [
  { prefix: 'com.acme.pay', root: 'payments/src/test/java' },
  { prefix: '', root: 'src/test/java' },
]

describe('useSourceRoots', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('reads the project roots once', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 20 })
    const { result } = renderHook(() => useSourceRoots('p1'), { wrapper })
    await waitFor(() => expect(result.current.roots).toEqual(ROOTS))
    expect(result.current.maxRoots).toBe(20)
    expect(svc.getSourceRoots).toHaveBeenCalledTimes(1)
    expect(svc.getSourceRoots).toHaveBeenCalledWith('p1')
  })

  it('asks nothing without a project and reports an empty list', () => {
    const { result } = renderHook(() => useSourceRoots(null), { wrapper })
    expect(result.current.roots).toEqual([])
    expect(result.current.maxRoots).toBe(20)
    expect(svc.getSourceRoots).not.toHaveBeenCalled()
  })

  it('save PUTs the whole list once and caches the answer without a re-read', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    svc.putSourceRoots.mockResolvedValue({ roots: ROOTS, max_roots: 20 })
    const { result } = renderHook(() => useSourceRoots('p1'), { wrapper })
    await waitFor(() => expect(svc.getSourceRoots).toHaveBeenCalledTimes(1))

    await act(async () => {
      await result.current.save(ROOTS)
    })
    expect(svc.putSourceRoots).toHaveBeenCalledTimes(1)
    expect(svc.putSourceRoots).toHaveBeenCalledWith('p1', ROOTS)
    await waitFor(() => expect(result.current.roots).toEqual(ROOTS))
    expect(svc.getSourceRoots).toHaveBeenCalledTimes(1)
  })

  it('a refused save rejects with the server error and keeps the cache', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    const refusal = { response: { status: 422, data: { detail: 'At most 20 source roots.' } } }
    svc.putSourceRoots.mockRejectedValue(refusal)
    const { result } = renderHook(() => useSourceRoots('p1'), { wrapper })
    await waitFor(() => expect(svc.getSourceRoots).toHaveBeenCalledTimes(1))

    await expect(result.current.save(ROOTS)).rejects.toBe(refusal)
    expect(result.current.roots).toEqual([])
  })

  it('preview posts the edited list and writes nothing to the cache', async () => {
    svc.getSourceRoots.mockResolvedValue({ roots: [], max_roots: 20 })
    const answer = { sampled: 15, java_frames: 15, located: 12, matched: 9, lookback_days: 30, examples: [] }
    svc.previewSourceRoots.mockResolvedValue(answer)
    const { result } = renderHook(() => useSourceRoots('p1'), { wrapper })
    await waitFor(() => expect(svc.getSourceRoots).toHaveBeenCalledTimes(1))

    await expect(result.current.preview(ROOTS)).resolves.toEqual(answer)
    expect(svc.previewSourceRoots).toHaveBeenCalledWith('p1', ROOTS)
    expect(result.current.roots).toEqual([])
    expect(svc.putSourceRoots).not.toHaveBeenCalled()
  })
})
