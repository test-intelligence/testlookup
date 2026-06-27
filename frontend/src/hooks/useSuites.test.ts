/**
 * Tests for the suite-keyed SWR hooks.
 *
 * Regression guard for the `no-non-null-assertion` ratchet: each fetcher used to
 * dereference its id argument with `suiteId!` / `canonicalId!` (a non-null
 * assertion justified only by the conditional `id ? [...] : null` key). The
 * assertions were replaced with the tuple-key destructure the codebase already
 * uses elsewhere (`([, id]: readonly [string, string]) => …`). These tests pin
 * the load-bearing behaviour that makes the destructure sound: the service is
 * called with the id carried by the key, and the fetch is skipped (key is null)
 * when no id is supplied — so the destructured element is never read undefined.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/suitesService', () => ({
  suitesService: {
    get: vi.fn(),
    listSuiteCases: vi.fn(),
    listCanonicalRuns: vi.fn(),
    getCanonicalCase: vi.fn(),
  },
}))

// Each hook keys on the id, so give every test its own SWR cache to avoid
// bleeding a previous test's resolved value across the shared global cache.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

type Mock = ReturnType<typeof vi.fn>

describe('useSuites hooks — tuple-key fetchers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('useSuite fetches with the destructured suite id', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.get as Mock).mockResolvedValue({ id: 's1' })

    const { useSuite } = await import('./useSuites')
    const { result } = renderHook(() => useSuite('s1'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual({ id: 's1' }))
    expect(suitesService.get).toHaveBeenCalledWith('s1')
  })

  it('useSuite skips the fetch when no suite id is supplied', async () => {
    const { suitesService } = await import('@/services/suitesService')

    const { useSuite } = await import('./useSuites')
    const { result } = renderHook(() => useSuite(undefined), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(suitesService.get).not.toHaveBeenCalled()
  })

  it('useSuiteTestCases fetches with the destructured suite id', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.listSuiteCases as Mock).mockResolvedValue([{ id: 'c1' }])

    const { useSuiteTestCases } = await import('./useSuites')
    const { result } = renderHook(() => useSuiteTestCases('s2'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual([{ id: 'c1' }]))
    expect(suitesService.listSuiteCases).toHaveBeenCalledWith('s2')
  })

  it('useSuiteTestCases skips the fetch when no suite id is supplied', async () => {
    const { suitesService } = await import('@/services/suitesService')

    const { useSuiteTestCases } = await import('./useSuites')
    renderHook(() => useSuiteTestCases(undefined), { wrapper })

    await waitFor(() => expect(suitesService.listSuiteCases).not.toHaveBeenCalled())
  })

  it('useCanonicalRuns fetches with the destructured canonical id', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.listCanonicalRuns as Mock).mockResolvedValue([{ id: 'r1' }])

    const { useCanonicalRuns } = await import('./useSuites')
    const { result } = renderHook(() => useCanonicalRuns('k1'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual([{ id: 'r1' }]))
    expect(suitesService.listCanonicalRuns).toHaveBeenCalledWith('k1')
  })

  it('useCanonicalCase fetches with the destructured canonical id', async () => {
    const { suitesService } = await import('@/services/suitesService')
    ;(suitesService.getCanonicalCase as Mock).mockResolvedValue({ id: 'k2' })

    const { useCanonicalCase } = await import('./useSuites')
    const { result } = renderHook(() => useCanonicalCase('k2'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual({ id: 'k2' }))
    expect(suitesService.getCanonicalCase).toHaveBeenCalledWith('k2')
  })

  it('useCanonicalCase skips the fetch when no canonical id is supplied', async () => {
    const { suitesService } = await import('@/services/suitesService')

    const { useCanonicalCase } = await import('./useSuites')
    const { result } = renderHook(() => useCanonicalCase(undefined), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(suitesService.getCanonicalCase).not.toHaveBeenCalled()
  })
})
