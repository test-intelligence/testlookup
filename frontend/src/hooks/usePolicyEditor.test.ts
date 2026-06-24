/**
 * Tests for the policy-editor SWR hooks.
 *
 * Regression guard for the PolicyEditorPage migration off load-on-mount effects
 * (set-state-in-effect): verifies each dataset is surfaced declaratively, that
 * the `enabled` gate (list mode) and the id guard (edit mode) skip the fetch
 * when not applicable, that the single-policy hook re-keys on the id, that
 * `usePolicies` exposes `mutate` for post-deactivate refresh, and that a failed
 * load reports `isError` while keeping the data empty/null.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/policyService', () => ({
  listPolicies: vi.fn(),
  getPolicy: vi.fn(),
}))

// Each hook keys on distinct tuples; give every test its own SWR cache so a
// previous test's resolved/rejected value cannot bleed across.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const POLICY = {
  id: 'p1',
  name: 'Default Gate',
  description: 'desc',
  project_id: null,
  version: 1,
  is_active: true,
  is_draft: false,
  rules: { schema_version: 1, thresholds: {}, dimension_weights: {}, rules: [] },
  created_at: '2026-01-01T00:00:00Z',
}

describe('usePolicies', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches and surfaces the policy list when enabled', async () => {
    const { listPolicies } = await import('@/services/policyService')
    ;(listPolicies as ReturnType<typeof vi.fn>).mockResolvedValue([POLICY])

    const { usePolicies } = await import('./usePolicyEditor')
    const { result } = renderHook(() => usePolicies(true), { wrapper })

    await waitFor(() => expect(result.current.policies).toEqual([POLICY]))
    expect(result.current.isError).toBe(false)
    expect(typeof result.current.mutate).toBe('function')
    expect(listPolicies).toHaveBeenCalledTimes(1)
  })

  it('does not fetch when not in list mode', async () => {
    const { listPolicies } = await import('@/services/policyService')

    const { usePolicies } = await import('./usePolicyEditor')
    const { result } = renderHook(() => usePolicies(false), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(listPolicies).not.toHaveBeenCalled()
    expect(result.current.policies).toEqual([])
  })

  it('reports isError and an empty list when the load fails', async () => {
    const { listPolicies } = await import('@/services/policyService')
    ;(listPolicies as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { usePolicies } = await import('./usePolicyEditor')
    const { result } = renderHook(() => usePolicies(true), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.policies).toEqual([])
  })
})

describe('usePolicy', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches the single policy keyed on its id when enabled', async () => {
    const { getPolicy } = await import('@/services/policyService')
    ;(getPolicy as ReturnType<typeof vi.fn>).mockResolvedValue(POLICY)

    const { usePolicy } = await import('./usePolicyEditor')
    const { result } = renderHook(() => usePolicy('p1', true), { wrapper })

    await waitFor(() => expect(result.current.policy).toEqual(POLICY))
    expect(getPolicy).toHaveBeenCalledWith('p1')
  })

  it('does not fetch without a policy id even when enabled', async () => {
    const { getPolicy } = await import('@/services/policyService')

    const { usePolicy } = await import('./usePolicyEditor')
    const { result } = renderHook(() => usePolicy(undefined, true), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getPolicy).not.toHaveBeenCalled()
    expect(result.current.policy).toBeNull()
  })

  it('does not fetch when disabled (new-policy mode)', async () => {
    const { getPolicy } = await import('@/services/policyService')

    const { usePolicy } = await import('./usePolicyEditor')
    renderHook(() => usePolicy('p1', false), { wrapper })

    await waitFor(() => expect(getPolicy).not.toHaveBeenCalled())
  })

  it('reports isError and a null policy when the load fails', async () => {
    const { getPolicy } = await import('@/services/policyService')
    ;(getPolicy as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('not found'))

    const { usePolicy } = await import('./usePolicyEditor')
    const { result } = renderHook(() => usePolicy('p1', true), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.policy).toBeNull()
  })
})
