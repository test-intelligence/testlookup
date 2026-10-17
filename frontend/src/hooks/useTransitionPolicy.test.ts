/**
 * NTF-02: `useTransitionPolicy` asks for one project's policy, never in All
 * Projects (`null`), and hands back only a parsed policy.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const svc = vi.hoisted(() => ({ getTransitionPolicy: vi.fn() }))
vi.mock('@/services/notificationPolicyService', () => ({
  getTransitionPolicy: svc.getTransitionPolicy,
  updateTransitionPolicy: vi.fn(),
}))

import { useTransitionPolicy } from './useTransitionPolicy'

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

const POLICY = {
  project_id: 'p1',
  transitions_enabled: true,
  per_run_events_enabled: false,
  enabled_events: ['test.newly_failing'],
  consecutive_failure_threshold: 2,
  is_default: true,
}

describe('useTransitionPolicy', () => {
  beforeEach(() => {
    svc.getTransitionPolicy.mockReset()
  })

  it('null (All Projects) asks for nothing', async () => {
    const { result } = renderHook(() => useTransitionPolicy(null), { wrapper })
    await new Promise(r => setTimeout(r, 10))
    expect(svc.getTransitionPolicy).not.toHaveBeenCalled()
    expect(result.current.policy).toBeNull()
    expect(result.current.isLoading).toBe(false)
  })

  it('a project asks once for that project and parses the answer', async () => {
    svc.getTransitionPolicy.mockResolvedValue(POLICY)
    const { result } = renderHook(() => useTransitionPolicy('p1'), { wrapper })
    await waitFor(() => expect(result.current.policy).toEqual(POLICY))
    expect(svc.getTransitionPolicy).toHaveBeenCalledTimes(1)
    expect(svc.getTransitionPolicy).toHaveBeenCalledWith('p1')
  })

  it('a malformed answer is policy null, not the raw object', async () => {
    svc.getTransitionPolicy.mockResolvedValue({ enabled_events: 'everything' })
    const { result } = renderHook(() => useTransitionPolicy('p1'), { wrapper })
    await waitFor(() => expect(svc.getTransitionPolicy).toHaveBeenCalled())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.policy).toBeNull()
    expect(result.current.error).toBeUndefined()
  })

  it('a failed read is an error, read once (no retry loop)', async () => {
    svc.getTransitionPolicy.mockRejectedValue(new Error('500'))
    const { result } = renderHook(() => useTransitionPolicy('p1'), { wrapper })
    await waitFor(() => expect(result.current.error).toBeTruthy())
    expect(result.current.policy).toBeNull()
    await new Promise(r => setTimeout(r, 30))
    expect(svc.getTransitionPolicy).toHaveBeenCalledTimes(1)
  })
})
