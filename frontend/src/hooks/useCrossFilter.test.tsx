/**
 * VIZ-603 "Filter page by this": a suite or release mark is APPENDED to the
 * page's global selection through the existing scope stores (VIZ-303), so the
 * chip it makes can be removed to return to the previous selection. Only with
 * `viz_multi_filters` on (the stores are the page's filter only then); every
 * other dimension is never offered (OD-2: no page-local chips this wave). The
 * stores, the notice store and the flag store are the real ones.
 */
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartMark } from '@/components/charts/marks'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { RELEASE_CAP, selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { DROP_REASONS, useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { SUITE_CAP, useSuiteStore } from '@/store/suiteStore'
import { CROSS_FILTER_WORDS, crossFilterValue, useCrossFilter } from './useCrossFilter'

const R1 = '11111111-1111-4111-8111-111111111111'
const R2 = '22222222-2222-4222-8222-222222222222'

const suiteMark = (value: string, label = value): ChartMark => ({ dimension: 'suite', value, label, y: 3, n: 3 })
const releaseMark = (value: string, label = 'v1.2'): ChartMark => ({ dimension: 'release', value, label, y: 3, n: 3 })

// The page's one announcer, observed: the hook speaks the result of the reader's own action.
const assertive = vi.hoisted(() => vi.fn())
vi.mock('@/components/charts/ChartAnnouncer', () => ({ useChartAnnouncer: () => ({ assertive, report: () => {} }) }))

beforeEach(() => {
  useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
  useProjectStore.setState({ activeProjectId: 'p1' })
  useSuiteStore.getState().clearSuites()
  useReleaseStore.getState().clearRelease()
  useScopeNoticeStore.getState().dismiss()
  assertive.mockReset()
})

const hook = () => renderHook(() => useCrossFilter()).result

describe('crossFilterValue', () => {
  it('a suite is filtered by its display spelling when it is the same suite as the key, else by the key', () => {
    expect(crossFilterValue(suiteMark('payments', 'Payments'))).toEqual({ dimension: 'suite', value: 'Payments' })
    expect(crossFilterValue(suiteMark('payments', 'payments (nightly)'))).toEqual({ dimension: 'suite', value: 'payments' })
  })

  it('a release is filtered by its id (or the unattributed sentinel), never by its name', () => {
    expect(crossFilterValue(releaseMark(R1, 'v1.2'))).toEqual({ dimension: 'release', value: R1 })
    expect(crossFilterValue(releaseMark('unattributed', 'Unattributed'))).toEqual({ dimension: 'release', value: 'unattributed' })
  })

  it.each([
    ['the no-suite bucket', suiteMark('(none)')],
    ['the Other roll-up', suiteMark('__other__', 'Other')],
    ['a blank suite', suiteMark('   ', '   ')],
    ['an over-long suite', suiteMark('x'.repeat(501))],
    ['a release that is not an id', releaseMark('v1.2')],
    ['a status', { ...suiteMark('failed'), dimension: 'status' } as ChartMark],
    ['a failure category', { ...suiteMark('assertion'), dimension: 'failure_category' } as ChartMark],
    ['a test', { ...suiteMark('fp-1'), dimension: 'test' } as ChartMark],
    ['an environment', { ...suiteMark('staging'), dimension: 'environment' } as ChartMark],
  ])('%s cannot filter the page', (_name, mark) => {
    expect(crossFilterValue(mark)).toBeNull()
  })

  it('a hostile suite name is just a name (it is text in a chip, never markup)', () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    expect(crossFilterValue(suiteMark(hostile.toLowerCase(), hostile))).toEqual({ dimension: 'suite', value: hostile })
  })
})

describe('useCrossFilter', () => {
  it('flag off: offered for nothing, and applying writes nothing (M-603b)', () => {
    useMultiFiltersFlagStore.setState({ enabled: false, resolved: true })
    const result = hook()
    expect(result.current.enabled).toBe(false)
    expect(result.current.offers(suiteMark('payments'))).toBe(false)
    let outcome: string | undefined
    act(() => {
      outcome = result.current.apply(suiteMark('payments'))
    })
    expect(outcome).toBe('unavailable')
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
  })

  it('the flag not answered yet reads as off', () => {
    useMultiFiltersFlagStore.setState({ enabled: false, resolved: false })
    expect(hook().current.offers(suiteMark('payments'))).toBe(false)
  })

  it('offered for suite and release marks only', () => {
    const result = hook()
    expect(result.current.offers(suiteMark('payments'))).toBe(true)
    expect(result.current.offers(releaseMark(R1))).toBe(true)
    expect(result.current.offers({ ...suiteMark('failed'), dimension: 'status' })).toBe(false)
    expect(result.current.offers(suiteMark('(none)'))).toBe(false)
  })

  it('APPENDS the suite to the selection, so removing its chip returns to the previous one (M-603a)', () => {
    useSuiteStore.getState().setActiveSuites(['Checkout'], 'p1')
    const result = hook()
    act(() => {
      expect(result.current.apply(suiteMark('payments', 'Payments'))).toBe('added')
    })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['Checkout', 'Payments'])
    expect(useSuiteStore.getState().scopedProjectId).toBe('p1')
    expect(assertive).toHaveBeenCalledWith(CROSS_FILTER_WORDS.added('suite', 'Payments'))
    // The chip's removal is the existing store write; the previous selection is back.
    useSuiteStore.getState().removeSuites(['Payments'])
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['Checkout'])
  })

  it('a suite already selected (in any case) is not added twice', () => {
    useSuiteStore.getState().setActiveSuites(['PAYMENTS'], 'p1')
    const result = hook()
    act(() => {
      expect(result.current.apply(suiteMark('payments', 'Payments'))).toBe('already')
    })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['PAYMENTS'])
    expect(assertive).toHaveBeenCalledWith(CROSS_FILTER_WORDS.already('suite', 'Payments'))
  })

  it('at the suite cap nothing is replaced: the limit is said and the notice names the suite (M-603c)', () => {
    const full = Array.from({ length: SUITE_CAP }, (_, i) => `suite-${i}`)
    useSuiteStore.getState().setActiveSuites(full, 'p1')
    const result = hook()
    act(() => {
      expect(result.current.apply(suiteMark('payments'))).toBe('limit')
    })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(full)
    expect(useScopeNoticeStore.getState().notices).toEqual([
      { dimension: 'suite', values: ['payments'], reason: DROP_REASONS.overCap(SUITE_CAP) },
    ])
    expect(assertive).toHaveBeenCalledWith(CROSS_FILTER_WORDS.limit(SUITE_CAP))
  })

  it('appends a release within the project', () => {
    useReleaseStore.getState().setActiveReleases([R1], 'p1')
    const result = hook()
    act(() => {
      expect(result.current.apply(releaseMark(R2, 'v2.0'))).toBe('added')
    })
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])
    expect(assertive).toHaveBeenCalledWith(CROSS_FILTER_WORDS.added('release', 'v2.0'))
  })

  it('a release selection from another project is not appended to: it starts over in this one', () => {
    useReleaseStore.getState().setActiveReleases([R1], 'p-other')
    const result = hook()
    act(() => {
      result.current.apply(releaseMark(R2))
    })
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R2])
    expect(useReleaseStore.getState().scopedProjectId).toBe('p1')
  })

  it('a release already selected is not added twice; at the release cap the limit is said', () => {
    const full = Array.from({ length: RELEASE_CAP }, (_, i) => `${String(i).padStart(8, '0')}-1111-4111-8111-111111111111`)
    useReleaseStore.getState().setActiveReleases(full, 'p1')
    const result = hook()
    act(() => {
      expect(result.current.apply(releaseMark(full[3]))).toBe('already')
      expect(result.current.apply(releaseMark(R2))).toBe('limit')
    })
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual(full)
    expect(useScopeNoticeStore.getState().notices).toEqual([
      { dimension: 'release', values: [R2], reason: DROP_REASONS.overCap(RELEASE_CAP) },
    ])
  })

  it('All Projects: a release needs one project, and the notice says so', () => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    const result = hook()
    act(() => {
      expect(result.current.apply(releaseMark(R1))).toBe('needs-project')
    })
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([])
    expect(useScopeNoticeStore.getState().notices[0]).toEqual({ dimension: 'release', values: [R1], reason: DROP_REASONS.needsProject })
  })

  it('All Projects: a suite still filters (a suite is a name across projects)', () => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    const result = hook()
    act(() => {
      expect(result.current.apply(suiteMark('payments'))).toBe('added')
    })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['payments'])
    expect(useSuiteStore.getState().scopedProjectId).toBe(ALL_PROJECTS_ID)
  })

  it('a mark that cannot filter is refused without a write', () => {
    const result = hook()
    act(() => {
      expect(result.current.apply({ ...suiteMark('failed'), dimension: 'status' })).toBe('invalid')
    })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
    expect(assertive).not.toHaveBeenCalled()
  })
})
