/**
 * W06 DEF-04 / DEF-03: the Defects page's trend and escape-rate hooks.
 *
 * What they must get right: every part of the scope that changes the request
 * is in the SWR key too (a release in the params but not the key serves one
 * release's numbers under another's name), the suite never reaches the escape
 * rate (it is counted per release), an unset scope part is not sent at all,
 * and `refreshDefectInsights` revalidates both families and nothing else.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({
  getDefectTrend: vi.fn(),
  getDefectEscapeRate: vi.fn(),
  getDefects: vi.fn(),
}))
vi.mock('@/services/analyticsService', () => ({ analyticsService: service }))
const project = vi.hoisted(() => ({ id: 'proj-1' as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: project.id }),
}))
const appMutate = vi.hoisted(() => vi.fn(async () => undefined))
vi.mock('@/utils/swrCacheMutate', () => ({ appMutate }))

import { refreshDefectInsights, useDefectEscapeRate, useDefectTrend } from './useDefectInsights'
import { useDefects } from './useMetrics'
import type { DefectScopeQuery } from '@/types/analytics'

/** One cache per test, and a probe that lists its keys. */
function wrapper() {
  const cache = new Map()
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
  return { Wrapper, cache }
}

function keysOf(cache: Map<string, unknown>, family: string): string[] {
  return [...cache.keys()].filter(k => k.includes(family))
}

describe('useDefectTrend', () => {
  beforeEach(() => {
    project.id = 'proj-1'
    service.getDefectTrend.mockReset()
    service.getDefectTrend.mockResolvedValue({ points: [], totals: {}, window_days: 30 })
  })

  it('sends the window, and nothing else, for an unscoped page', async () => {
    const { Wrapper } = wrapper()
    const { result } = renderHook(() => useDefectTrend({ days: 30 }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(service.getDefectTrend).toHaveBeenCalledTimes(1)
    expect(service.getDefectTrend).toHaveBeenCalledWith('proj-1', {
      days: 30, releaseIds: undefined, releaseMatch: undefined, suiteName: null,
    })
  })

  it('sends the release in affects mode and the suite, and keys on every part of them', async () => {
    const { Wrapper, cache } = wrapper()
    const scope: DefectScopeQuery = { days: 7, releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Payments' }
    const { result, rerender } = renderHook((s: DefectScopeQuery) => useDefectTrend(s), {
      wrapper: Wrapper, initialProps: scope,
    })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(service.getDefectTrend).toHaveBeenLastCalledWith('proj-1', {
      days: 7, releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Payments',
    })

    // Each part is its own cache entry: a change in any of them is a new request.
    for (const next of [
      { ...scope, days: 90 },
      { ...scope, releaseIds: ['rel-2'] },
      { ...scope, releaseMatch: 'found_in' as const },
      { ...scope, suiteName: 'Checkout' },
    ]) {
      const before = service.getDefectTrend.mock.calls.length
      rerender(next)
      await waitFor(() => expect(service.getDefectTrend.mock.calls.length).toBe(before + 1))
    }
    expect(keysOf(cache, 'defect-trend')).toHaveLength(5)
    // An array enters the key as a joined string, never as a fresh array (a fresh identity refetches in a loop).
    const before = service.getDefectTrend.mock.calls.length
    rerender({ ...scope, suiteName: 'Checkout', releaseIds: ['rel-2'] })
    rerender({ ...scope, suiteName: 'Checkout', releaseIds: ['rel-2'] })
    await waitFor(() => expect(service.getDefectTrend.mock.calls.length).toBe(before + 1))
    await new Promise(r => setTimeout(r, 20))
    expect(service.getDefectTrend.mock.calls.length).toBe(before + 1)
  })

  it('never sends a release match without a release', async () => {
    const { Wrapper } = wrapper()
    renderHook(() => useDefectTrend({ days: 30, releaseIds: [], releaseMatch: 'affects' }), { wrapper: Wrapper })
    await waitFor(() => expect(service.getDefectTrend).toHaveBeenCalled())
    expect(service.getDefectTrend).toHaveBeenLastCalledWith('proj-1', {
      days: 30, releaseIds: undefined, releaseMatch: undefined, suiteName: null,
    })
  })

  it('fetches All projects without a project, and nothing at all before a project is picked', async () => {
    project.id = '__ALL__'
    const all = wrapper()
    renderHook(() => useDefectTrend({ days: 30 }), { wrapper: all.Wrapper })
    await waitFor(() => expect(service.getDefectTrend).toHaveBeenCalledWith(null, expect.objectContaining({ days: 30 })))

    service.getDefectTrend.mockClear()
    project.id = null
    const none = wrapper()
    renderHook(() => useDefectTrend({ days: 30 }), { wrapper: none.Wrapper })
    await new Promise(r => setTimeout(r, 20))
    expect(service.getDefectTrend).not.toHaveBeenCalled()
  })
})

describe('useDefectEscapeRate', () => {
  beforeEach(() => {
    project.id = 'proj-1'
    service.getDefectEscapeRate.mockReset()
    service.getDefectEscapeRate.mockResolvedValue({ rate: null, measured: false })
  })

  it('sends the window and the release, never a suite or a release match, and keys on both', async () => {
    const { Wrapper, cache } = wrapper()
    const { result, rerender } = renderHook((s: DefectScopeQuery) => useDefectEscapeRate(s), {
      wrapper: Wrapper,
      initialProps: { days: 30, releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Payments' },
    })
    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(service.getDefectEscapeRate).toHaveBeenCalledWith('proj-1', { days: 30, releaseIds: ['rel-1'] })

    // A suite change is not a new escape-rate request (it does not narrow it).
    rerender({ days: 30, releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Checkout' })
    await new Promise(r => setTimeout(r, 20))
    expect(service.getDefectEscapeRate).toHaveBeenCalledTimes(1)

    rerender({ days: 90, releaseIds: ['rel-1'] })
    await waitFor(() => expect(service.getDefectEscapeRate).toHaveBeenCalledTimes(2))
    rerender({ days: 90 })
    await waitFor(() => expect(service.getDefectEscapeRate).toHaveBeenCalledTimes(3))
    expect(service.getDefectEscapeRate).toHaveBeenLastCalledWith('proj-1', { days: 90, releaseIds: undefined })
    expect(keysOf(cache, 'defect-escape-rate')).toHaveLength(3)
  })

  it('defaults to the server\'s 30-day window', async () => {
    const { Wrapper } = wrapper()
    renderHook(() => useDefectEscapeRate({}), { wrapper: Wrapper })
    await waitFor(() => expect(service.getDefectEscapeRate).toHaveBeenCalledWith('proj-1', { days: 30, releaseIds: undefined }))
  })
})

describe('refreshDefectInsights', () => {
  it('revalidates every trend and escape-rate entry, and no other family', async () => {
    appMutate.mockClear()
    await refreshDefectInsights()
    expect(appMutate).toHaveBeenCalledTimes(1)
    const matcher = (appMutate.mock.calls[0] as unknown[])[0] as (key: unknown) => boolean
    expect(matcher(['defect-trend', 'proj-1', 30, null, null, null])).toBe(true)
    expect(matcher(['defect-escape-rate', 'proj-1', 30, null])).toBe(true)
    expect(matcher(['analytics-defects', 'proj-1', undefined])).toBe(false)
    expect(matcher('defect-trend')).toBe(false)
  })
})

// The list hook takes the SAME scope (`useMetrics.useDefects`, W06 §1.8). Its
// own test file (`useDefects.test.tsx`) pins the unscoped call shape; this pins
// what the scope adds, and that it adds nothing when unset.
describe('useDefects with the page\'s scope', () => {
  beforeEach(() => {
    project.id = 'proj-1'
    service.getDefects.mockReset()
    service.getDefects.mockResolvedValue({ items: [], total: 0, page: 1, size: 100, pages: 1 })
  })

  it('unscoped: exactly the legacy params, no release or suite key at all', async () => {
    const { Wrapper } = wrapper()
    renderHook(() => useDefects(undefined, { releaseIds: undefined, releaseMatch: undefined, suiteName: null }), { wrapper: Wrapper })
    await waitFor(() => expect(service.getDefects).toHaveBeenCalledTimes(1))
    const params = (service.getDefects.mock.calls[0] as unknown[])[1] as Record<string, unknown>
    expect(Object.keys(params).sort()).toEqual(['page', 'resolution_status', 'size'])
  })

  it('"Only this release": release_id and release_match=affects; the suite as suite_name', async () => {
    const { Wrapper } = wrapper()
    renderHook(() => useDefects(undefined, { releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Payments' }), { wrapper: Wrapper })
    await waitFor(() => expect(service.getDefects).toHaveBeenCalledTimes(1))
    expect(service.getDefects).toHaveBeenCalledWith('proj-1', {
      size: 100, resolution_status: undefined, page: 1, release_id: 'rel-1', release_match: 'affects', suite_name: 'Payments',
    })
  })

  it('never sends a release match without a release, and keys on the scope', async () => {
    const { Wrapper, cache } = wrapper()
    const { rerender } = renderHook((scope: DefectScopeQuery) => useDefects(undefined, scope), {
      wrapper: Wrapper, initialProps: { releaseIds: [], releaseMatch: 'affects', suiteName: null },
    })
    await waitFor(() => expect(service.getDefects).toHaveBeenCalledTimes(1))
    expect(Object.keys((service.getDefects.mock.calls[0] as unknown[])[1] as object)).not.toContain('release_match')
    rerender({ releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: null })
    await waitFor(() => expect(service.getDefects).toHaveBeenCalledTimes(2))
    rerender({ releaseIds: ['rel-1'], releaseMatch: 'affects', suiteName: 'Payments' })
    await waitFor(() => expect(service.getDefects).toHaveBeenCalledTimes(3))
    expect(keysOf(cache, 'analytics-defects')).toHaveLength(3)
  })
})
