/**
 * W09 FLK-02: `useQuarantinePolicy` asks for one project's policy only when
 * enabled (the Policy and Quarantined tabs), hands back only a parsed policy,
 * and shares ONE cache entry, which a save can fill without asking again.
 */
import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const svc = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
vi.mock('@/services/quarantinePolicyService', async (importActual) => {
  const actual = await importActual<typeof import('@/services/quarantinePolicyService')>()
  return { ...actual, quarantinePolicyService: svc }
})

import { useQuarantinePolicy } from './useQuarantinePolicy'

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

const POLICY = {
  project_id: 'p1',
  sla_days: 14,
  auto_create_defect: false,
  auto_promote: false,
  promote_after_passes: 20,
  detection_flip_rate_threshold: 0.2,
  detection_min_runs: 10,
  max_active_quarantined: 8,
  is_default: true,
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 10))

describe('useQuarantinePolicy', () => {
  beforeEach(() => {
    svc.get.mockReset()
  })

  it.each([
    ['disabled (Detected, Proposed, History)', 'p1', false],
    ['no project', null, true],
    ['no project, disabled', undefined, false],
  ])('%s asks for nothing', async (_label, projectId, enabled) => {
    const { result } = renderHook(() => useQuarantinePolicy(projectId as string | null, enabled as boolean), { wrapper })
    await settle()
    expect(svc.get).not.toHaveBeenCalled()
    expect(result.current.policy).toBeNull()
    expect(result.current.isLoading).toBe(false)
  })

  it('enabled with a project asks once for it and parses the answer', async () => {
    svc.get.mockResolvedValue(POLICY)
    const { result } = renderHook(() => useQuarantinePolicy('p1', true), { wrapper })
    await waitFor(() => expect(result.current.policy).toEqual(POLICY))
    expect(svc.get).toHaveBeenCalledTimes(1)
    expect(svc.get).toHaveBeenCalledWith('p1')
  })

  it('a malformed answer is policy null, not the raw object', async () => {
    svc.get.mockResolvedValue({ sla_days: 'two weeks' })
    const { result } = renderHook(() => useQuarantinePolicy('p1', true), { wrapper })
    await waitFor(() => expect(svc.get).toHaveBeenCalled())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.policy).toBeNull()
    expect(result.current.error).toBeUndefined()
  })

  it('a failed read is an error, once (no retry loop)', async () => {
    svc.get.mockRejectedValue(new Error('503'))
    const { result } = renderHook(() => useQuarantinePolicy('p1', true), { wrapper })
    await waitFor(() => expect(result.current.error).toBeInstanceOf(Error))
    await settle()
    expect(svc.get).toHaveBeenCalledTimes(1)
    expect(result.current.policy).toBeNull()
  })

  it('a saved answer fills the shared entry without asking again; no value revalidates', async () => {
    svc.get.mockResolvedValue(POLICY)
    const { result } = renderHook(
      () => ({ card: useQuarantinePolicy('p1', true), quarantined: useQuarantinePolicy('p1', true) }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.card.policy).toEqual(POLICY))
    expect(svc.get).toHaveBeenCalledTimes(1)
    const saved = { ...POLICY, max_active_quarantined: 1, is_default: false }
    await act(async () => {
      await result.current.card.mutate(saved)
    })
    expect(result.current.quarantined.policy).toEqual(saved)
    expect(svc.get).toHaveBeenCalledTimes(1)
    await act(async () => {
      await result.current.card.mutate()
    })
    expect(svc.get).toHaveBeenCalledTimes(2)
  })
})
