/**
 * VIZ-603 "Filter page by this" on the legacy scope (P2): a suite mark sets
 * the page's own suite select (through `PageSuiteTargetContext`, REPLACING,
 * as the select spells the suite), a release mark sets the top bar's release
 * (one pinned project), a suite x release heatmap cell sets both. No
 * `viz_multi_filters`: the multi-filter stores are not the page's filter and
 * are not imported. The project and release stores are the real ones.
 */
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartMark } from '@/components/charts/marks'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import { readSourceFile } from '@/test/readSourceFile'
import { PageSuiteTargetContext, type PageSuiteTarget } from './pageSuiteTarget'
import { CROSS_FILTER_WORDS, crossFilterTargets, filterPhrase, suiteSpelling, useCrossFilter } from './useCrossFilter'

const R1 = '11111111-1111-4111-8111-111111111111'
const R2 = '22222222-2222-4222-8222-222222222222'

const suiteMark = (value: string, label = value): ChartMark => ({ dimension: 'suite', value, label, y: 3, n: 3 })
const releaseMark = (value: string, label = 'v1.2'): ChartMark => ({ dimension: 'release', value, label, y: 3, n: 3 })
/** A heatmap cell: the row's key, "row, column" as its label, the column as its context. */
const cell = (suite: string, column: { dimension: ChartMark['dimension']; value: string }, label = `${suite}, col`): ChartMark => ({
  dimension: 'suite',
  value: suite,
  label,
  y: 80,
  n: 10,
  context: [column],
})

// The page's one announcer and the toast, observed: the hook says and shows the result of the reader's own action.
const assertive = vi.hoisted(() => vi.fn())
vi.mock('@/components/charts/ChartAnnouncer', () => ({ useChartAnnouncer: () => ({ assertive, report: () => {} }) }))
const toast = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: toast }))
// The top bar's release list (the hook reads its cache entry, for names only).
const releases = vi.hoisted(() => ({ items: [] as { id: string; name: string }[] }))
vi.mock('./useReleases', () => ({ useReleases: () => ({ data: { items: releases.items } }) }))

let target: PageSuiteTarget
const set = vi.fn()

beforeEach(() => {
  useProjectStore.setState({ activeProjectId: 'p1' })
  useReleaseStore.getState().clearRelease()
  releases.items = [
    { id: R1, name: '2026.09' },
    { id: R2, name: '2026.10' },
  ]
  set.mockReset()
  target = { selected: '', options: ['Checkout', 'Payments'], set }
  assertive.mockReset()
  toast.mockReset()
})

/** The hook on a page that provides its suite select (`provided`), or on one that does not. */
const hook = (provided = true) => {
  const wrapper = ({ children }: { children: ReactNode }) =>
    provided ? <PageSuiteTargetContext.Provider value={target}>{children}</PageSuiteTargetContext.Provider> : <>{children}</>
  return renderHook(() => useCrossFilter(), { wrapper }).result
}

const said = () => assertive.mock.calls.map(([text]) => text as string)

describe('crossFilterTargets', () => {
  it('a suite mark is its suite, spelled as drawn when that is the same suite as the key, else the key', () => {
    expect(crossFilterTargets(suiteMark('payments', 'Payments'))).toEqual([{ dimension: 'suite', value: 'Payments', label: 'Payments' }])
    expect(crossFilterTargets(suiteMark('payments', 'payments (nightly)'))).toEqual([
      { dimension: 'suite', value: 'payments', label: 'payments (nightly)' },
    ])
  })

  it('a release mark is its id (or the unattributed sentinel), never its name', () => {
    expect(crossFilterTargets(releaseMark(R1, 'v1.2'))).toEqual([{ dimension: 'release', value: R1, label: 'v1.2' }])
    expect(crossFilterTargets(releaseMark('unattributed', '(unattributed)'))[0]?.value).toBe('unattributed')
  })

  it('a suite x release heatmap cell is BOTH; a suite x day or x environment cell, and a stacked segment, are the suite', () => {
    expect(crossFilterTargets(cell('payments', { dimension: 'release', value: R1 }))).toEqual([
      { dimension: 'suite', value: 'payments', label: 'payments, col' },
      { dimension: 'release', value: R1, label: null },
    ])
    expect(crossFilterTargets(cell('payments', { dimension: 'day', value: '2026-10-01' })).map((t) => t.dimension)).toEqual(['suite'])
    expect(crossFilterTargets(cell('payments', { dimension: 'environment', value: 'ci' })).map((t) => t.dimension)).toEqual(['suite'])
    expect(crossFilterTargets(cell('payments', { dimension: 'status', value: 'failed' })).map((t) => t.dimension)).toEqual(['suite'])
    // A release column that is not an id is not a filter; the suite still is.
    expect(crossFilterTargets(cell('payments', { dimension: 'release', value: 'v1.2' })).map((t) => t.dimension)).toEqual(['suite'])
  })

  it.each([
    ['the no-suite bucket', suiteMark('(none)')],
    ['the Other roll-up', suiteMark('__other__', 'Other')],
    ['a blank suite', suiteMark('   ', '   ')],
    ['an over-long suite', suiteMark('x'.repeat(501))],
    ['a release that is not an id', releaseMark('v1.2')],
    ['a status', { ...suiteMark('failed'), dimension: 'status' } as ChartMark],
    ['a status inside a suite', { ...suiteMark('failed'), dimension: 'status', context: [{ dimension: 'suite', value: 'payments' }] } as ChartMark],
    ['a failure category', { ...suiteMark('assertion'), dimension: 'failure_category' } as ChartMark],
    ['a test', { ...suiteMark('fp-1'), dimension: 'test', context: [{ dimension: 'suite', value: 'payments' }] } as ChartMark],
    ['a class inside a suite', { ...suiteMark('c:Auth'), dimension: 'class', context: [{ dimension: 'suite', value: 'auth' }] } as ChartMark],
    ['an environment', { ...suiteMark('staging'), dimension: 'environment' } as ChartMark],
    ['a project', { ...suiteMark('p2'), dimension: 'project' } as ChartMark],
  ])('%s cannot filter the page', (_name, mark) => {
    expect(crossFilterTargets(mark)).toEqual([])
  })

  it('the select’s own spelling wins over the chart’s; a suite it does not list keeps the chart’s', () => {
    expect(suiteSpelling(['Checkout', 'PayMents'], { dimension: 'suite', value: 'payments', label: null })).toBe('PayMents')
    expect(suiteSpelling(['Checkout'], { dimension: 'suite', value: 'payments', label: null })).toBe('payments')
  })
})

describe('useCrossFilter', () => {
  it('a suite is offered only on a page that provides its suite select', () => {
    expect(hook(true).current.offers(suiteMark('payments'))).toBe(true)
    expect(hook(false).current.offers(suiteMark('payments'))).toBe(false)
    let outcome: string | undefined
    const bare = hook(false)
    act(() => {
      outcome = bare.current.apply(suiteMark('payments'))
    })
    expect(outcome).toBe('invalid')
    expect(set).not.toHaveBeenCalled()
  })

  it('sets the page’s suite as the select spells it (the chart’s key is lower-cased), REPLACING the selection', () => {
    target = { ...target, selected: 'Checkout' }
    const result = hook()
    act(() => {
      expect(result.current.apply(cell('payments', { dimension: 'day', value: '2026-10-01' }, 'payments, 1 Oct'))).toBe('applied')
    })
    expect(set).toHaveBeenCalledTimes(1)
    expect(set).toHaveBeenCalledWith('Payments')
    const words = CROSS_FILTER_WORDS.applied([filterPhrase('suite', 'Payments')], ['"All suites"'])
    expect(words).toBe('Page filtered by suite "Payments" (clear: "All suites").')
    expect(said()).toEqual([words])
    expect(toast).toHaveBeenCalledWith(words, { id: 'cross-filter' })
  })

  it('a suite already selected (in any case) is not written again, and the reader is told', () => {
    target = { ...target, selected: 'PAYMENTS' }
    const result = hook()
    act(() => {
      expect(result.current.apply(suiteMark('payments', 'Payments'))).toBe('already')
    })
    expect(set).not.toHaveBeenCalled()
    expect(said()).toEqual([CROSS_FILTER_WORDS.already([filterPhrase('suite', 'Payments')])])
    expect(toast).toHaveBeenCalledTimes(1)
  })

  it('a release, in a pinned project: the top bar’s release, named from its list', () => {
    useReleaseStore.getState().setActiveRelease(R1, 'p1')
    const result = hook(false)
    expect(result.current.offers(releaseMark(R2))).toBe(true)
    act(() => {
      expect(result.current.apply(releaseMark(R2, 'drawn name'))).toBe('applied')
    })
    expect(useReleaseStore.getState().activeReleaseId).toBe(R2)
    expect(useReleaseStore.getState().scopedProjectId).toBe('p1')
    expect(said()).toEqual([CROSS_FILTER_WORDS.applied([filterPhrase('release', '2026.10')], ['"All releases"'])])
    act(() => {
      expect(result.current.apply(releaseMark(R2))).toBe('already')
    })
  })

  it('a release selection from another project is replaced, not kept', () => {
    useReleaseStore.getState().setActiveRelease(R1, 'p-other')
    const result = hook()
    act(() => {
      expect(result.current.apply(releaseMark(R1))).toBe('applied')
    })
    expect(useReleaseStore.getState().scopedProjectId).toBe('p1')
  })

  it('a suite x release cell writes BOTH filters, and the words name both', () => {
    const result = hook()
    act(() => {
      expect(result.current.apply(cell('payments', { dimension: 'release', value: 'unattributed' }))).toBe('applied')
    })
    expect(set).toHaveBeenCalledWith('Payments')
    expect(useReleaseStore.getState().activeReleaseId).toBe('unattributed')
    expect(said()).toEqual([
      'Page filtered by suite "Payments" and release "Unattributed" (clear: "All suites", "All releases").',
    ])
  })

  it('All Projects: a release is never offered (it belongs to one project); a suite x release cell writes the suite only', () => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID })
    const result = hook()
    expect(result.current.offers(releaseMark(R1))).toBe(false)
    act(() => {
      expect(result.current.apply(releaseMark(R1))).toBe('invalid')
      expect(result.current.apply(cell('payments', { dimension: 'release', value: R1 }))).toBe('applied')
    })
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(set).toHaveBeenCalledWith('Payments')
  })

  it('status, environment, failure category, test and the rest are never offered, nor written', () => {
    const result = hook()
    for (const dimension of ['status', 'environment', 'failure_category', 'test', 'class', 'day', 'project'] as const) {
      const mark = { ...suiteMark('x'), dimension }
      expect(result.current.offers(mark)).toBe(false)
      act(() => {
        expect(result.current.apply(mark)).toBe('invalid')
      })
    }
    expect(set).not.toHaveBeenCalled()
    expect(assertive).not.toHaveBeenCalled()
    expect(toast).not.toHaveBeenCalled()
  })

  it('a hostile suite name is just text in the words (never markup)', () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    target = { ...target, options: [hostile] }
    const result = hook()
    act(() => {
      result.current.apply(suiteMark(hostile.toLowerCase(), hostile))
    })
    expect(set).toHaveBeenCalledWith(hostile)
  })
})

describe('import guard (P2: the multi-filter runtime is off for good)', () => {
  const importsOf = async (path: string) =>
    [...(await readSourceFile(path)).matchAll(/^import[\s\S]*?from\s+'([^']+)'/gm)].map((m) => m[1])

  it.each(['src/hooks/useCrossFilter.ts', 'src/hooks/pageSuiteTarget.ts'])('%s imports none of the multi-filter stores', async (path) => {
    const imports = await importsOf(path)
    // The pattern found the imports (a guard that reads nothing passes anything).
    expect(imports.length).toBeGreaterThan(0)
    for (const banned of ['multiFiltersFlag', 'suiteStore', 'scopeNoticeStore', 'settledScope']) {
      expect(imports.filter((specifier) => specifier.includes(banned)), banned).toEqual([])
    }
  })

  it('the suite-name rules import nothing', async () => {
    expect(await importsOf('src/lib/suiteName.ts')).toEqual([])
  })
})
