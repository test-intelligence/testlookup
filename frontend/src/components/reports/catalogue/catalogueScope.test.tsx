/**
 * K3: the scope every catalogue request is built from.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

let activeProjectId: string | null = 'proj-1'
let releaseScope: string | readonly string[] | null = null

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => releaseScope }))

import {
  CATALOGUE_MAX_WINDOW_DAYS,
  ROW_GRAIN_MAX_WINDOW_DAYS,
  catalogueParams,
  clampCatalogueDays,
  useCatalogueParams,
} from './catalogueScope'

const base = { projectId: 'proj-1', allProjects: false, days: 30 }

describe('clampCatalogueDays', () => {
  it('no report page may ask for more than 90 days', () => {
    expect(CATALOGUE_MAX_WINDOW_DAYS).toBe(90)
    // Owner decision 2026-09-29, confirmed by the BE re-measure: the row-grain
    // lever stays at 90 for this wave.
    expect(ROW_GRAIN_MAX_WINDOW_DAYS).toBe(90)
    expect(clampCatalogueDays(365)).toBe(90)
    expect(clampCatalogueDays(91)).toBe(90)
    expect(clampCatalogueDays(90)).toBe(90)
    expect(clampCatalogueDays(Infinity)).toBe(90)
  })

  it('keeps an allowed window as it is, and never goes below one whole day', () => {
    expect(clampCatalogueDays(1)).toBe(1)
    expect(clampCatalogueDays(14)).toBe(14)
    expect(clampCatalogueDays(0)).toBe(1)
    expect(clampCatalogueDays(-7)).toBe(1)
    expect(clampCatalogueDays(7.9)).toBe(7)
    expect(clampCatalogueDays(Number.NaN)).toBe(1)
  })

  it('takes a lower cap for a row-grain chart', () => {
    expect(clampCatalogueDays(90, 30)).toBe(30)
    expect(clampCatalogueDays(14, 30)).toBe(14)
  })
})

describe('catalogueParams (K3)', () => {
  it('clamps days on the wire: ?window=365 can never reach a request', () => {
    expect(catalogueParams({ ...base, days: 365 })?.days).toBe(90)
  })

  it('an extra `days` cannot bypass the clamp', () => {
    expect(catalogueParams({ ...base, days: 30, extra: { days: 365 } })?.days).toBe(30)
  })

  it('sends the project for a single project', () => {
    expect(catalogueParams(base)).toEqual({ project_id: 'proj-1', days: 30 })
  })

  it('is null until the project resolves: nothing is asked on a guess', () => {
    expect(catalogueParams({ ...base, projectId: null })).toBeNull()
    expect(catalogueParams({ ...base, projectId: '' })).toBeNull()
  })

  it('never sends project_id in All Projects mode, even from extra', () => {
    const params = catalogueParams({ ...base, projectId: 'all', allProjects: true, extra: { project_id: 'proj-9' } })
    expect(params).not.toBeNull()
    expect(params).not.toHaveProperty('project_id')
  })

  it('sends no release in All Projects mode: a release belongs to one project', () => {
    const params = catalogueParams({ ...base, projectId: null, allProjects: true, releaseIds: 'R1' })
    expect(params).not.toHaveProperty('release_id')
  })

  it('one value stays a scalar: byte-identical to the legacy single-release request', () => {
    const params = catalogueParams({ ...base, releaseIds: ['R1'], suiteNames: 'payments' })
    expect(params?.release_id).toBe('R1')
    expect(params?.suite_name).toBe('payments')
  })

  it('several values go out as a sorted list (the serializer repeats the key)', () => {
    const params = catalogueParams({ ...base, releaseIds: ['R2', 'R1'], suiteNames: ['cart', 'auth', 'cart'] })
    expect(params?.release_id).toEqual(['R1', 'R2'])
    expect(params?.suite_name).toEqual(['auth', 'cart'])
  })

  it('omits empty keys entirely: absent, never `release_id=` or null', () => {
    for (const empty of [null, undefined, '', [] as string[], ['']]) {
      const params = catalogueParams({ ...base, releaseIds: empty, suiteNames: empty })
      expect(params).not.toHaveProperty('release_id')
      expect(params).not.toHaveProperty('suite_name')
    }
  })

  it('passes the chart-specific parameters through, and the scope wins over them', () => {
    const params = catalogueParams({
      ...base,
      releaseIds: 'R1',
      extra: { metric: 'pass_rate', group_by: ['day', 'suite'], top_n: 7, release_id: 'R-other', suite_name: 'x' },
    })
    expect(params).toEqual({
      metric: 'pass_rate',
      group_by: ['day', 'suite'],
      top_n: 7,
      project_id: 'proj-1',
      days: 30,
      release_id: 'R1',
    })
  })

  it('applies a lower cap for a row-grain chart', () => {
    expect(catalogueParams({ ...base, days: 90, maxDays: 30 })?.days).toBe(30)
  })
})

describe('useCatalogueParams (K3)', () => {
  beforeEach(() => {
    activeProjectId = 'proj-1'
    releaseScope = null
  })

  it('reads the active project and the release scope', () => {
    releaseScope = 'R1'
    const { result } = renderHook(() => useCatalogueParams(30, 'payments'))
    expect(result.current).toEqual({ project_id: 'proj-1', days: 30, release_id: 'R1', suite_name: 'payments' })
  })

  it('is null with no project yet', () => {
    activeProjectId = null
    const { result } = renderHook(() => useCatalogueParams(30, null))
    expect(result.current).toBeNull()
  })

  it('All Projects: no project_id', () => {
    activeProjectId = 'all'
    const { result } = renderHook(() => useCatalogueParams(7, null))
    expect(result.current).toEqual({ days: 7 })
  })

  it('keeps its identity across renders while the scope is unchanged', () => {
    const { result, rerender } = renderHook(({ days }) => useCatalogueParams(days, ['b', 'a'], { metric: 'pass_rate' }), {
      initialProps: { days: 30 },
    })
    const first = result.current
    rerender({ days: 30 })
    expect(result.current).toBe(first)
    rerender({ days: 90 })
    expect(result.current).not.toBe(first)
    expect(result.current?.days).toBe(90)
  })
})
