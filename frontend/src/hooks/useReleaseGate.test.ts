/**
 * REL-01: the release detail's gate reads. The real `releasesService` runs
 * over a mocked HTTP layer, so the 404 -> `null` mapping (never evaluated) and
 * the SWR keys the page revalidates after an evaluation are both pinned.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getData = vi.fn()
vi.mock('@/services/http', () => ({
  getData: (...args: unknown[]) => getData(...args),
  postData: vi.fn(),
  putData: vi.fn(),
  deleteData: vi.fn(),
}))

import { usePhaseGates, useReleaseBaseline, useReleaseGate } from './useReleaseGate'

// Inferred `Map<any, any>`: SWR's provider wants its own Cache type.
let cache = new Map()
const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)

const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { response: { status } })

beforeEach(() => {
  cache = new Map()
  getData.mockReset()
})

describe('useReleaseGate', () => {
  it('reads the recorded verdict from GET /releases/{id}/gate under the key the page revalidates', async () => {
    const gate = { release_id: 'rel-1', verdict: 'GO', blocking_reasons: [] }
    getData.mockResolvedValue(gate)
    const { result } = renderHook(() => useReleaseGate('rel-1'), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual(gate))
    expect(getData).toHaveBeenCalledWith('/api/v1/releases/rel-1/gate')
    expect([...cache.keys()].some((k: string) => k.includes('release-gate') && k.includes('rel-1'))).toBe(true)
  })

  it('a never-evaluated release (the 404) is null, not an error', async () => {
    getData.mockRejectedValue(httpError(404))
    const { result } = renderHook(() => useReleaseGate('rel-1'), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data).toBeNull()
    expect(result.current.error).toBeUndefined()
  })

  it('any other failure stays an error (an outage is not "never evaluated")', async () => {
    getData.mockRejectedValue(httpError(500))
    const { result } = renderHook(() => useReleaseGate('rel-1'), { wrapper })
    await waitFor(() => expect(result.current.error).toBeDefined())
    expect(result.current.data).toBeUndefined()
  })

  it('asks nothing without a release id', () => {
    renderHook(() => useReleaseGate(null), { wrapper })
    expect(getData).not.toHaveBeenCalled()
  })
})

describe('useReleaseBaseline and usePhaseGates', () => {
  it('read the baseline comparison and the live phase gates from their own routes', async () => {
    getData.mockImplementation((url: string) => Promise.resolve(
      url.endsWith('/gate/baseline')
        ? { comparable: false, reason: 'this release has no baseline' }
        : { release_id: 'rel-1', status: 'NO_PHASES', phases: [] },
    ))
    const { result } = renderHook(
      () => ({ baseline: useReleaseBaseline('rel-1'), phases: usePhaseGates('rel-1') }),
      { wrapper },
    )
    await waitFor(() => expect(result.current.baseline.data).toEqual({ comparable: false, reason: 'this release has no baseline' }))
    await waitFor(() => expect(result.current.phases.data).toMatchObject({ status: 'NO_PHASES' }))
    expect(getData).toHaveBeenCalledWith('/api/v1/releases/rel-1/gate/baseline')
    expect(getData).toHaveBeenCalledWith('/api/v1/releases/rel-1/phases/gate')
    const keys: string[] = [...cache.keys()]
    expect(keys.some(k => k.includes('release-gate-baseline'))).toBe(true)
    expect(keys.some(k => k.includes('release-phase-gates'))).toBe(true)
  })

  it('ask nothing without a release id', () => {
    renderHook(() => ({ baseline: useReleaseBaseline(null), phases: usePhaseGates(null) }), { wrapper })
    expect(getData).not.toHaveBeenCalled()
  })
})
