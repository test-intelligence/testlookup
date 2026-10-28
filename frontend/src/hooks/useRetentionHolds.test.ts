/**
 * W22a ACT-05: the retention-hold list hook.
 *
 * No project → no request (the key is `null`); the key carries the state, so
 * a change of state is a new question; an answer without `items` (an older
 * server or fixture) reads as no holds; `refreshRetentionHolds` revalidates
 * every state of ONE project and nothing else.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({ listRetentionHolds: vi.fn() }))
vi.mock('@/services/retentionHoldService', () => service)
const { appMutate } = vi.hoisted(() => ({ appMutate: vi.fn() }))
vi.mock('@/utils/swrCacheMutate', () => ({ appMutate }))

import { refreshRetentionHolds, useRetentionHolds } from './useRetentionHolds'
import { holdList, holdRow } from '@/test/retentionHoldMocks'

const PROJECT = '00000000-0000-4000-8000-0000000000a1'
const OTHER = '00000000-0000-4000-8000-0000000000a2'

function wrapper() {
  const cache = new Map()
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
  return Wrapper
}

describe('useRetentionHolds', () => {
  beforeEach(() => {
    service.listRetentionHolds.mockReset()
  })

  it('asks nothing without a project', () => {
    const { result } = renderHook(() => useRetentionHolds(null), { wrapper: wrapper() })
    expect(service.listRetentionHolds).not.toHaveBeenCalled()
    expect(result.current.holds).toEqual([])
    expect(result.current.total).toBeNull()
  })

  it("reads the project's live holds by default, with total and counts", async () => {
    const rows = [holdRow(), holdRow({ entity_type: 'project', entity_id: null })]
    service.listRetentionHolds.mockResolvedValue(holdList(rows, 7))
    const { result } = renderHook(() => useRetentionHolds(PROJECT), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.holds).toHaveLength(2))
    expect(service.listRetentionHolds).toHaveBeenCalledWith(PROJECT, { state: 'live' })
    expect(result.current.total).toBe(7)
    expect(result.current.counts).toEqual({ live: 2, expired: 0, released: 0 })
  })

  it('keys on the state: changing it asks again with the new state', async () => {
    service.listRetentionHolds.mockResolvedValue(holdList([]))
    const { result, rerender } = renderHook(
      ({ state }: { state: 'live' | 'released' }) => useRetentionHolds(PROJECT, state),
      { wrapper: wrapper(), initialProps: { state: 'live' as 'live' | 'released' } },
    )
    await waitFor(() => expect(result.current.total).toBe(0))
    rerender({ state: 'released' })
    await waitFor(() => expect(service.listRetentionHolds).toHaveBeenCalledTimes(2))
    expect(service.listRetentionHolds).toHaveBeenLastCalledWith(PROJECT, { state: 'released' })
  })

  it('reads an answer without items as no holds', async () => {
    service.listRetentionHolds.mockResolvedValue({ total: 0 })
    const { result } = renderHook(() => useRetentionHolds(PROJECT), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.total).toBe(0))
    expect(result.current.holds).toEqual([])
  })

  it('reports a failed read as an error, never as an empty list with a total', async () => {
    service.listRetentionHolds.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useRetentionHolds(PROJECT), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.total).toBeNull()
  })
})

describe('refreshRetentionHolds', () => {
  beforeEach(() => {
    appMutate.mockReset()
  })

  it("revalidates every state of one project's list and nothing else", () => {
    refreshRetentionHolds(PROJECT)
    const matcher = appMutate.mock.calls[0][0]
    const path = `/api/v1/projects/${PROJECT}/retention-holds`
    expect(matcher([path, 'live'])).toBe(true)
    expect(matcher([path, 'expired'])).toBe(true)
    expect(matcher([path, 'all'])).toBe(true)
    expect(matcher([`/api/v1/projects/${OTHER}/retention-holds`, 'live'])).toBe(false)
    expect(matcher(`/api/v1/projects/${PROJECT}/retention-policy`)).toBe(false)
    expect(matcher(path)).toBe(false)
    expect(matcher(null)).toBe(false)
  })

  it('does nothing without a project', async () => {
    await expect(refreshRetentionHolds(null)).resolves.toBeUndefined()
    expect(appMutate).not.toHaveBeenCalled()
  })
})
