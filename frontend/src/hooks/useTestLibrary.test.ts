/**
 * W13: every test-library read asks only when it can be answered and is
 * wanted — no key without a project (All Projects included), none while
 * disabled — under its pinned key; the timeline pages by the previous page's
 * cursor and stops at a page without one; the owner list IS the project
 * members read (one shared key, one request); the case views are the
 * `test_management` saved views of the case-view kind only. The stub module the
 * page vitests use restates this module's constants: they are pinned equal.
 */
import { act, createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig, unstable_serialize } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { listSavedViews, type SavedView } from '@/services/savedViewsService'
import { testLibraryService } from '@/services/testLibraryService'
import { userManagementService } from '@/services/userManagementService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { testLibraryHookMocks } from '@/test/testLibraryMocks'
import { registerAppMutate, resetAppMutate } from '@/utils/swrCacheMutate'
import {
  CASE_VIEW_KIND,
  CASE_VIEW_PAGE,
  isCaseView,
  refreshLibraryHealth,
  useCaseOwnerOptions,
  useCaseRequirements,
  useCaseViews,
  useEvidenceGapPage,
  useLibraryHealth,
  useTestCaseTimeline,
  useTraceabilityGaps,
} from './useTestLibrary'
import { useProjectMembers } from './useUserManagement'

vi.mock('@/services/testLibraryService', () => ({
  testLibraryService: {
    getHealth: vi.fn(async () => ({ project_id: 'p', total_cases: 3 })),
    getTimeline: vi.fn(),
    listRequirements: vi.fn(async () => ({ items: [], total: 0 })),
    getTraceabilityGaps: vi.fn(async () => ({ gaps: [], gaps_total: 0 })),
    getEvidenceGapPage: vi.fn(async () => ({ items: [], total: 0 })),
  },
}))
vi.mock('@/services/savedViewsService', () => ({ listSavedViews: vi.fn() }))
vi.mock('@/services/userManagementService', () => ({ userManagementService: { listProjectMembers: vi.fn() } }))

const P = '11111111-1111-4111-8111-111111111111'
const CASE = 'c0000000-0000-4000-8000-000000000001'

let cache: Map<string, unknown>
const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(SWRConfig, { value: { provider: () => cache as never, dedupingInterval: 0, shouldRetryOnError: false } }, children)
const keys = () => [...cache.keys()]
const settle = () => new Promise((resolve) => setTimeout(resolve, 20))

beforeEach(() => {
  vi.clearAllMocks()
  cache = new Map()
})
afterEach(() => resetAppMutate())

describe('useTestLibrary: no request without a project or while disabled', () => {
  it('asks nothing in All Projects, without a case, or disabled', async () => {
    renderHook(() => {
      useLibraryHealth(null)
      useLibraryHealth(ALL_PROJECTS_ID)
      useTestCaseTimeline(CASE, [], false)
      useTestCaseTimeline(null, [], true)
      useCaseRequirements(CASE, false)
      useCaseRequirements(null, true)
      useTraceabilityGaps(P, 1, false)
      useTraceabilityGaps(null, 1, true)
      useEvidenceGapPage(P, 'flaky_active', 1, false)
      useEvidenceGapPage(ALL_PROJECTS_ID, 'flaky_active', 1, true)
      useCaseViews(null)
      useCaseViews(ALL_PROJECTS_ID)
      useCaseOwnerOptions(ALL_PROJECTS_ID)
    }, { wrapper })
    await settle()
    for (const fn of Object.values(testLibraryService)) expect(fn).not.toHaveBeenCalled()
    expect(listSavedViews).not.toHaveBeenCalled()
    expect(userManagementService.listProjectMembers).not.toHaveBeenCalled()
  })
})

describe('useTestLibrary keys', () => {
  it('reads the health of one project under its key', async () => {
    const { result } = renderHook(() => useLibraryHealth(P), { wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ project_id: 'p', total_cases: 3 }))
    expect(testLibraryService.getHealth).toHaveBeenCalledWith(P)
    expect(keys()).toContain(unstable_serialize(['tm-library-health', P]))
  })

  it('reads requirements, gaps and evidence-gap pages under their keys, 25 a page', async () => {
    renderHook(() => {
      useCaseRequirements(CASE, true)
      useTraceabilityGaps(P, 2, true)
      useEvidenceGapPage(P, 'no_owner', 3, true)
    }, { wrapper })
    await waitFor(() => expect(testLibraryService.getEvidenceGapPage).toHaveBeenCalled())
    expect(testLibraryService.listRequirements).toHaveBeenCalledWith(CASE)
    expect(testLibraryService.getTraceabilityGaps).toHaveBeenCalledWith(P, 2, 25)
    expect(testLibraryService.getEvidenceGapPage).toHaveBeenCalledWith(P, 'no_owner', 3, 25)
    await waitFor(() => expect(keys()).toEqual(expect.arrayContaining([
      unstable_serialize(['tm-case-requirements', CASE]),
      unstable_serialize(['tm-traceability-gaps', P, 2]),
      unstable_serialize(['tm-evidence-gap-page', P, 'no_owner', 3]),
    ])))
  })

  it('refreshLibraryHealth revalidates every health key and nothing else', async () => {
    const mutate = vi.fn(async () => undefined)
    registerAppMutate(mutate as never)
    await refreshLibraryHealth()
    const [matcher] = mutate.mock.calls[0] as unknown as [(key: unknown) => boolean]
    expect(matcher(['tm-library-health', P])).toBe(true)
    expect(matcher(['tm-cases', P, {}])).toBe(false)
    expect(matcher('tm-library-health')).toBe(false)
  })
})

const entry = (id: string) => ({ id, kind: 'comment', occurred_at: '2026-10-20T10:00:00Z', title: 'Comment' })

describe('useTestCaseTimeline', () => {
  it('pages by the previous page cursor and stops at a page without one', async () => {
    vi.mocked(testLibraryService.getTimeline)
      .mockResolvedValueOnce({ case_id: CASE, kinds: ['comment'], items: [entry('a')], next_cursor: 'c1', automation_linked: false })
      .mockResolvedValueOnce({ case_id: CASE, kinds: ['comment'], items: [entry('b')], next_cursor: null, automation_linked: false })
    const { result } = renderHook(() => useTestCaseTimeline(CASE, ['comment', 'version'], true), { wrapper })
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(testLibraryService.getTimeline).toHaveBeenLastCalledWith(CASE, { kinds: ['comment', 'version'], cursor: null })
    expect(result.current.hasMore).toBe(true)
    expect(result.current.automationLinked).toBe(false)

    act(() => { result.current.loadMore() })
    await waitFor(() => expect(result.current.items.map((e) => e.id)).toEqual(['a', 'b']))
    expect(testLibraryService.getTimeline).toHaveBeenLastCalledWith(CASE, { kinds: ['comment', 'version'], cursor: 'c1' })
    expect(result.current.hasMore).toBe(false)
    expect(testLibraryService.getTimeline).toHaveBeenCalledTimes(2)
  })

  it('All sends no kinds', async () => {
    vi.mocked(testLibraryService.getTimeline).mockResolvedValue({ case_id: CASE, kinds: [], items: [], next_cursor: null })
    renderHook(() => useTestCaseTimeline(CASE, [], true), { wrapper })
    await waitFor(() => expect(testLibraryService.getTimeline).toHaveBeenCalledWith(CASE, { kinds: undefined, cursor: null }))
  })
})

describe('useCaseOwnerOptions and useCaseViews', () => {
  it('the owner list is the members read itself: one key, one request, Me first, then by name', async () => {
    vi.mocked(userManagementService.listProjectMembers).mockResolvedValue([
      { user_id: 'u-2', full_name: 'Priya Shah', username: 'priya' },
      { user_id: 'u-1', full_name: null, username: 'alex' },
    ] as never)
    const { result } = renderHook(() => ({ owners: useCaseOwnerOptions(P), members: useProjectMembers(P) }), { wrapper })
    await waitFor(() => expect(result.current.owners.options).toHaveLength(3))
    expect(result.current.owners.options).toEqual([
      { value: 'me', label: 'Me' }, { value: 'u-1', label: 'alex' }, { value: 'u-2', label: 'Priya Shah' },
    ])
    expect(userManagementService.listProjectMembers).toHaveBeenCalledTimes(1)
    expect(keys()).toContain(`/api/v1/projects/${P}/members`)
  })

  it('lists the test_management saved views of the case-view kind only', async () => {
    const view = (id: string, filters: Record<string, unknown>) => ({ id, name: id, filters }) as unknown as SavedView
    vi.mocked(listSavedViews).mockResolvedValue([
      view('mine', { kind: 'test_case_view', statuses: ['draft'] }),
      view('layout', { page: 'test_management', instances: [] }),
      view('report', { kind: 'report_view' }),
    ])
    const { result } = renderHook(() => useCaseViews(P), { wrapper })
    await waitFor(() => expect(result.current.views.map((v) => v.id)).toEqual(['mine']))
    expect(listSavedViews).toHaveBeenCalledWith(P, 'test_management')
    expect(keys()).toContain(unstable_serialize(['saved-views', P, 'test_management']))
  })
})

describe('the page vitests stub module', () => {
  it('restates this module constants and answers idle shapes', () => {
    const mocks = testLibraryHookMocks()
    expect(mocks.CASE_VIEW_PAGE).toBe(CASE_VIEW_PAGE)
    expect(mocks.CASE_VIEW_KIND).toBe(CASE_VIEW_KIND)
    const view = { filters: { kind: 'test_case_view' } } as unknown as SavedView
    const other = { filters: { kind: 'search' } } as unknown as SavedView
    expect([mocks.isCaseView(view), mocks.isCaseView(other)]).toEqual([isCaseView(view), isCaseView(other)])
    expect(mocks.useLibraryHealth()).toMatchObject({ data: undefined, error: undefined, isLoading: false })
    expect(mocks.useTestCaseTimeline()).toMatchObject({ items: [], hasMore: false, isLoading: false })
    expect(mocks.useCaseOwnerOptions().options).toEqual([{ value: 'me', label: 'Me' }])
    expect(mocks.useCaseViews().views).toEqual([])
    const hookNames = Object.keys(mocks).filter((name) => name.startsWith('use') || name.startsWith('refresh')).sort()
    expect(hookNames).toEqual([
      'refreshLibraryHealth', 'useCaseOwnerOptions', 'useCaseRequirements', 'useCaseViews', 'useEvidenceGapPage',
      'useLibraryHealth', 'useTestCaseTimeline', 'useTraceabilityGaps',
    ])
  })
})
