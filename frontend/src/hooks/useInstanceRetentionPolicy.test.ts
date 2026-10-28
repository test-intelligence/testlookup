/**
 * W22b ACT-06: the two instance-default hooks.
 *
 * Both keys are `null` while closed — the Retention page issues NO instance request until the card is opened /
 * the admin asks to turn it on — and exactly the settings paths once enabled.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { retentionInstanceService } = vi.hoisted(() => ({
  retentionInstanceService: { getInstancePolicy: vi.fn(), previewInstanceEnable: vi.fn() },
}))
vi.mock('@/services/retentionInstanceService', () => ({ retentionInstanceService }))

import { useInstanceEnablePreview, useInstanceRetentionPolicy } from './useInstanceRetentionPolicy'

function harness() {
  const cache = new Map()
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
  return { cache, wrapper }
}

describe('useInstanceRetentionPolicy', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    retentionInstanceService.getInstancePolicy.mockResolvedValue({ enabled: false, runs_days: 180 })
    retentionInstanceService.previewInstanceEnable.mockResolvedValue({ items: [], total: 0 })
  })

  it('asks nothing while the card is closed', () => {
    const { cache, wrapper } = harness()
    const { result } = renderHook(() => useInstanceRetentionPolicy(false), { wrapper })
    expect(retentionInstanceService.getInstancePolicy).not.toHaveBeenCalled()
    expect(result.current.data).toBeUndefined()
    expect([...cache.keys()]).toEqual([])
  })

  it('reads the defaults under the settings key once open', async () => {
    const { cache, wrapper } = harness()
    const { result } = renderHook(() => useInstanceRetentionPolicy(true), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ enabled: false, runs_days: 180 }))
    expect(retentionInstanceService.getInstancePolicy).toHaveBeenCalledTimes(1)
    expect([...cache.keys()]).toEqual(['/api/v1/settings/retention-defaults'])
  })
})

describe('useInstanceEnablePreview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    retentionInstanceService.previewInstanceEnable.mockResolvedValue({ items: [], total: 3 })
  })

  it('asks nothing until the admin asks to turn it on', () => {
    const { cache, wrapper } = harness()
    renderHook(() => useInstanceEnablePreview(false), { wrapper })
    expect(retentionInstanceService.previewInstanceEnable).not.toHaveBeenCalled()
    expect([...cache.keys()]).toEqual([])
  })

  it('reads the preview under its own key once asked', async () => {
    const { cache, wrapper } = harness()
    const { result } = renderHook(() => useInstanceEnablePreview(true), { wrapper })
    await waitFor(() => expect(result.current.data?.total).toBe(3))
    expect([...cache.keys()]).toEqual(['/api/v1/settings/retention-defaults/enable-preview'])
  })
})
