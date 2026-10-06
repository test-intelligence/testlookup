/**
 * P1 (2026-10-04): the report pages' own Views menu saves and applies the
 * LEGACY scope — the top-bar release, the window, the page's suite filter and
 * the page's extras — with the real release, window and project stores.
 */
import type { ReactNode } from 'react'
import { act, render, renderHook, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SavedView } from '@/services/savedViewsService'
import { useProjectStore } from '@/store/projectStore'
import { useReleaseStore } from '@/store/releaseStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'

const service = vi.hoisted(() => ({
  listSavedViews: vi.fn(),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => service)
vi.mock('@/store/authStore', () => ({
  useAuthStore: (select: (s: { user: { id: string } }) => unknown) => select({ user: { id: 'me' } }),
}))
const toast = vi.hoisted(() => Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import SavedViewsMenu from './SavedViewsMenu'
import { useReportViewsMenu, type ReportViewsMenuInput } from './useReportViewsMenu'
import { readReportView, reportViewFilters } from './savedViewsModel'

const WINDOWS = [1, 7, 14, 30, 90] as const

const view = (filters: Record<string, unknown>, over: Partial<SavedView> = {}): SavedView => ({
  id: 'v1',
  user_id: 'me',
  project_id: 'p1',
  name: 'Payments watch',
  description: null,
  page: 'trends',
  filters: { kind: 'report_view', page: 'trends', ...filters },
  is_shared: false,
  is_default: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: null,
  ...over,
})

function routerAt(url: string) {
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
    </MemoryRouter>
  )
}

function renderMenuHook(input: Partial<ReportViewsMenuInput> = {}, url = '/trends') {
  const setSuite = vi.fn()
  const props: ReportViewsMenuInput = {
    route: '/trends',
    windowDays: 14,
    windowOptions: WINDOWS,
    suite: { names: ['Payments'], set: setSuite },
    release: true,
    ...input,
  }
  const hook = renderHook(() => useReportViewsMenu(props), { wrapper: routerAt(url) })
  return { ...hook, setSuite }
}

/** Open a view through the hook's onApply, as the menu does. */
function apply(menu: ReturnType<typeof useReportViewsMenu>, saved: SavedView) {
  if (!menu) throw new Error('no menu')
  act(() => menu.onApply(readReportView(saved, menu.current.windowDays), saved))
}

beforeEach(() => {
  for (const fn of Object.values(service)) fn.mockReset()
  toast.mockReset()
  window.sessionStorage.clear()
  useProjectStore.setState({ activeProjectId: 'p1' })
  useReleaseStore.setState({ activeReleaseId: 'r1', scopedProjectId: 'p1' })
  useTimeWindowStore.setState({ days: 14 })
})

describe('useReportViewsMenu', () => {
  it('saves the legacy scope: the top-bar release, the window and the page suite', () => {
    const extraFilters = { summary: { mode: 'latest' } }
    const { result } = renderMenuHook({ extraFilters })
    expect(result.current).toMatchObject({
      page: 'trends',
      projectId: 'p1',
      current: { releaseIds: ['r1'], suiteNames: ['Payments'], windowDays: 14 },
      extraFilters,
    })
    const menu = result.current
    if (!menu) throw new Error('no menu')
    expect(reportViewFilters('trends', menu.current)).toEqual({
      kind: 'report_view', page: 'trends', window: 14, release_ids: ['r1'], release_id: 'r1', suites: ['Payments'],
    })
  })

  it('has no menu in All Projects, nor on a route with no saved-views page', () => {
    useProjectStore.setState({ activeProjectId: 'all' })
    expect(renderMenuHook().result.current).toBeNull()
    useProjectStore.setState({ activeProjectId: 'p1' })
    expect(renderMenuHook({ route: '/value-metrics' }).result.current).toBeNull()
  })

  it('opening a view sets the release, the window (snapped to the page) and the suite', () => {
    const { result, setSuite } = renderMenuHook({ windowOptions: [1, 7, 30, 90] })
    apply(result.current, view({ release_ids: ['r2'], release_id: 'r2', suites: ['Checkout'], window: 14 }))
    expect(useReleaseStore.getState()).toMatchObject({ activeReleaseId: 'r2', scopedProjectId: 'p1' })
    // 14 is not a Summary window: the nearest one it offers.
    expect(useTimeWindowStore.getState().days).toBe(7)
    expect(setSuite).toHaveBeenCalledWith('Checkout')
    expect(toast).not.toHaveBeenCalled()
  })

  it('a view with no release or suite clears them: a view is the whole scope', () => {
    const { result, setSuite } = renderMenuHook()
    apply(result.current, view({ window: 30 }))
    expect(useReleaseStore.getState()).toMatchObject({ activeReleaseId: null, scopedProjectId: null })
    expect(useTimeWindowStore.getState().days).toBe(30)
    expect(setSuite).toHaveBeenCalledWith('')
  })

  it('a multi-select view opens with its first release and suite, and says what it left out', () => {
    const { result, setSuite } = renderMenuHook()
    apply(result.current, view({ release_ids: ['r3', 'r4'], suites: ['Cart', 'Payments', 'Search'], window: 7 }))
    expect(useReleaseStore.getState().activeReleaseId).toBe('r3')
    expect(setSuite).toHaveBeenCalledTimes(1)
    expect(setSuite).toHaveBeenCalledWith('Cart')
    expect(toast).toHaveBeenCalledTimes(1)
    expect(toast.mock.calls[0][0]).toBe(
      'This page filters by one at a time, so "Payments watch" opened with the first. Left out: 1 other release, 2 other suites.',
    )
  })

  it('Defects: the release and the window are left alone; the tab is the page extra', () => {
    const applyExtra = vi.fn()
    const extraFilters = { defects: { tab: 'OPEN' } }
    const { result } = renderMenuHook({
      route: '/defects',
      windowDays: 14,
      windowOptions: null,
      suite: undefined,
      release: false,
      extraFilters,
      applyExtra,
    })
    expect(result.current).toMatchObject({ page: 'defects', current: { releaseIds: [], suiteNames: [] }, linked: false })
    const saved = view({ release_ids: ['r2'], release_id: 'r2', window: 90, defects: { tab: 'CLOSED' } }, { page: 'defects' })
    apply(result.current, saved)
    expect(useReleaseStore.getState()).toMatchObject({ activeReleaseId: 'r1', scopedProjectId: 'p1' })
    expect(useTimeWindowStore.getState().days).toBe(14)
    expect(applyExtra).toHaveBeenCalledWith(saved)
  })

  it('linked: a release in the URL, or one already in effect for this project', () => {
    expect(renderMenuHook().result.current?.linked).toBe(true)
    useReleaseStore.setState({ activeReleaseId: 'r1', scopedProjectId: 'other-project' })
    expect(renderMenuHook().result.current?.linked).toBe(false)
    expect(renderMenuHook({}, '/trends?release=r9').result.current?.linked).toBe(true)
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    expect(renderMenuHook().result.current?.linked).toBe(false)
  })
})

describe('useReportViewsMenu with the menu: my default view', () => {
  function Page({ setSuite }: { setSuite: (name: string) => void }) {
    const menu = useReportViewsMenu({
      route: '/trends',
      windowDays: useTimeWindowStore((s) => s.days),
      windowOptions: WINDOWS,
      suite: { names: [], set: setSuite },
      release: true,
    })
    return menu ? <SavedViewsMenu {...menu} variant="ghost" /> : null
  }
  const myDefault = view({ release_ids: ['r2'], release_id: 'r2', suites: ['Checkout'], window: 30 }, { is_default: true })

  it('opens on the first visit when no release is in effect', async () => {
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    service.listSavedViews.mockResolvedValue([myDefault])
    const setSuite = vi.fn()
    render(<Page setSuite={setSuite} />, { wrapper: routerAt('/trends') })
    await waitFor(() => expect(setSuite).toHaveBeenCalledWith('Checkout'))
    expect(useReleaseStore.getState().activeReleaseId).toBe('r2')
    expect(useTimeWindowStore.getState().days).toBe(30)
  })

  it('never over a release already in effect, nor over ?release= in the URL', async () => {
    service.listSavedViews.mockResolvedValue([myDefault])
    const setSuite = vi.fn()
    const first = render(<Page setSuite={setSuite} />, { wrapper: routerAt('/trends') })
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(1))
    first.unmount()

    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    render(<Page setSuite={setSuite} />, { wrapper: routerAt('/trends?release=r9') })
    await waitFor(() => expect(service.listSavedViews).toHaveBeenCalledTimes(2))
    // Let the default-view effect run, if it were going to.
    await act(async () => {})
    expect(setSuite).not.toHaveBeenCalled()
    expect(useReleaseStore.getState().activeReleaseId).toBeNull()
    expect(useTimeWindowStore.getState().days).toBe(14)
  })
})
