/**
 * Tests for useOwnershipRules hook.
 *
 * Regression guard for the OwnershipEditorPage migration off a load-on-mount
 * `useEffect` (set-state-in-effect): verifies the SWR hook fetches rules for a
 * resolved project, surfaces them declaratively via `rules`, skips the fetch
 * when there is no project (all-projects / none → null projectId), and reports
 * the error path via `isError` while keeping `rules` an empty array.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/ownershipService', () => ({
  listOwnershipRules: vi.fn(),
  getCodeownersCoverage: vi.fn(),
}))

// The hook keys on ['ownership-rules', projectId], so each test needs its own
// SWR cache to avoid bleeding the previous test's resolved/rejected value.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const RULE = {
  id: 'r1',
  project_id: 'p1',
  match_type: 'suite_name' as const,
  match_pattern: 'auth-*',
  service_name: 'auth-service',
  team_name: 'Identity',
  team_contact: null,
  priority: 10,
  is_active: true,
  created_by: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: null,
}

describe('useOwnershipRules', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches and surfaces rules for a resolved project', async () => {
    const { listOwnershipRules } = await import('@/services/ownershipService')
    ;(listOwnershipRules as ReturnType<typeof vi.fn>).mockResolvedValue([RULE])

    const { useOwnershipRules } = await import('./useOwnershipRules')
    const { result } = renderHook(() => useOwnershipRules('p1'), { wrapper })

    await waitFor(() => expect(result.current.rules).toEqual([RULE]))
    expect(listOwnershipRules).toHaveBeenCalledWith('p1')
    expect(result.current.isError).toBe(false)
  })

  it('does not fetch when there is no resolved project', async () => {
    const { listOwnershipRules } = await import('@/services/ownershipService')

    const { useOwnershipRules } = await import('./useOwnershipRules')
    const { result } = renderHook(() => useOwnershipRules(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(listOwnershipRules).not.toHaveBeenCalled()
    expect(result.current.rules).toEqual([])
  })

  it('reports isError and an empty rules list when the load fails', async () => {
    const { listOwnershipRules } = await import('@/services/ownershipService')
    ;(listOwnershipRules as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('boom'))

    const { useOwnershipRules } = await import('./useOwnershipRules')
    const { result } = renderHook(() => useOwnershipRules('p1'), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.rules).toEqual([])
  })
})

const COVERAGE = {
  path_rules: 4,
  codeowners_rules: 3,
  sampled: 20,
  located: 10,
  matched: 7,
  coverage_pct: 70,
  lookback_days: 30,
}

describe('useCodeownersCoverage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches coverage for a resolved project', async () => {
    const { getCodeownersCoverage } = await import('@/services/ownershipService')
    ;(getCodeownersCoverage as ReturnType<typeof vi.fn>).mockResolvedValue(COVERAGE)

    const { useCodeownersCoverage } = await import('./useOwnershipRules')
    const { result } = renderHook(() => useCodeownersCoverage('p1'), { wrapper })

    await waitFor(() => expect(result.current.coverage).toEqual(COVERAGE))
    expect(getCodeownersCoverage).toHaveBeenCalledWith('p1')
  })

  it('does not fetch when there is no resolved project', async () => {
    const { getCodeownersCoverage } = await import('@/services/ownershipService')

    const { useCodeownersCoverage } = await import('./useOwnershipRules')
    const { result } = renderHook(() => useCodeownersCoverage(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(getCodeownersCoverage).not.toHaveBeenCalled()
    expect(result.current.coverage).toBeNull()
  })
})
