/**
 * VIZ-306: the report scope in the address bar.
 *
 * Driven through a real data router (`createMemoryRouter`) so PUSH / REPLACE /
 * POP are the real navigation types and history is real history.
 */
import { act, render, waitFor } from '@testing-library/react'
import { Outlet, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'
const R_GONE = '99999999-0000-4000-8000-000000000009'
const PROJECT_A = 'aaaaaaaa-0000-4000-8000-000000000001'
const PROJECT_B = 'bbbbbbbb-0000-4000-8000-000000000002'
/** A project whose release list comes back PAGED: `total` > `items`. */
const PROJECT_PAGED = 'cccccccc-0000-4000-8000-000000000003'
/** On "page 2" of PROJECT_PAGED: not in the first-page items, real by id. */
const R_PAGE2 = '33333333-0000-4000-8000-000000000003'
/** A release whose by-id lookup fails with no answer (network). */
const R_OFFLINE = '44444444-0000-4000-8000-000000000004'

const releasesByProject: Record<string, Array<{ id: string; name: string }>> = {
  [PROJECT_A]: [
    { id: R1, name: '2026.09' },
    { id: R2, name: '2026.10' },
  ],
  [PROJECT_B]: [],
  [PROJECT_PAGED]: [{ id: R1, name: '2026.09' }],
}

vi.mock('@/services/releasesService', () => ({
  releasesService: {
    list: vi.fn(async (projectId: string | null) => ({
      items: releasesByProject[projectId ?? ''] ?? [],
      // PROJECT_PAGED answers with the first page of 60; the others whole.
      total: projectId === PROJECT_PAGED ? 60 : (releasesByProject[projectId ?? ''] ?? []).length,
    })),
    get: vi.fn(),
  },
}))
// By-id release lookups (lib/scopeUrlValidation) go through the shared client.
const apiGet = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get: apiGet } }))
// The authoritative suite list per project. `legacy-nightly` last ran long
// ago: it is NOT among project B's 200 most recent runs (runsService below).
const suitesByProject: Record<string, string[]> = {
  [PROJECT_A]: ['payments', 'cart'],
  [PROJECT_B]: ['payments', 'legacy-nightly'],
}
const suitesList = vi.hoisted(() => vi.fn())
vi.mock('@/services/suitesService', () => ({ suitesService: { list: suitesList } }))
// Project B's 200 most recent runs: all `payments` — the old check read
// these, so it could never see `legacy-nightly`.
vi.mock('@/services/runsService', () => ({
  runsService: {
    list: vi.fn(async (projectId: string | null) => ({
      items:
        projectId === PROJECT_B
          ? Array.from({ length: 200 }, (_, i) => ({ id: `r${i}`, primary_suite_name: 'payments', suite_names: ['payments'] }))
          : [],
      total: projectId === PROJECT_B ? 5000 : 0,
    })),
  },
}))

import { useScopeUrlSyncCore } from './useScopeUrlSync'
import { ReleasePicker } from '@/components/layout/ReleasePicker'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { useSuiteStore } from '@/store/suiteStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { AxiosError } from 'axios'

/** Every report route from VIZ-301, as App.tsx spells it, with a concrete URL. */
const REPORT_ROUTES: Array<[pattern: string, url: string]> = [
  ['overview', '/overview'],
  ['trends', '/trends'],
  ['coverage', '/coverage'],
  ['coverage/suite', '/coverage/suite'],
  ['failures', '/failures'],
  ['defects', '/defects'],
  ['reports/summary', '/reports/summary'],
  ['value-metrics', '/value-metrics'],
  ['intelligence', '/intelligence'],
  ['runs/:runId/intelligence', '/runs/run-1/intelligence'],
  ['release-gate', '/release-gate'],
  ['runs/compare', '/runs/compare'],
  ['flaky-coach', '/flaky-coach'],
]

type MemoryRouter = ReturnType<typeof createMemoryRouter>
/** The router of the current test, so assertions read the REAL location. */
let currentRouter: MemoryRouter | null = null
function loc(): string {
  const l = currentRouter?.state.location
  return l ? `${l.pathname}${l.search}` : ''
}

function Host({ withPicker = false }: { withPicker?: boolean }) {
  useScopeUrlSyncCore()
  return (
    <>
      {withPicker && <ReleasePicker />}
      <Outlet />
    </>
  )
}

function makeRouter(initial: string | string[], withPicker = false) {
  const extra = ['runs/:runId', 'test-management', 'settings']
  return createMemoryRouter(
    [
      {
        path: '/',
        element: <Host withPicker={withPicker} />,
        children: [...REPORT_ROUTES.map(([p]) => p), ...extra].map(path => ({
          path,
          element: <div>{path}</div>,
        })),
      },
    ],
    { initialEntries: Array.isArray(initial) ? initial : [initial] },
  )
}

function mount(initial: string | string[], withPicker = false) {
  const router = makeRouter(initial, withPicker)
  currentRouter = router
  const view = render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  )
  return { router, view }
}

function search(): URLSearchParams {
  return new URLSearchParams(loc().split('?')[1] ?? '')
}

function resetStores() {
  localStorage.clear()
  useMultiFiltersFlagStore.setState({ enabled: true })
  useProjectStore.setState({ activeProjectId: PROJECT_A })
  useReleaseStore.setState({ activeReleaseId: null, activeReleaseIds: [], scopedProjectId: null })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
  useTimeWindowStore.setState({ days: 30 })
  useScopeNoticeStore.setState({ notices: [] })
  suitesList.mockReset()
  suitesList.mockImplementation(async (projectId: string | null) => {
    const names = suitesByProject[projectId ?? ''] ?? []
    return { items: names.map((name, i) => ({ id: `s${i}`, project_id: projectId, name })), total: names.length }
  })
  apiGet.mockReset()
  apiGet.mockImplementation(async (url: string) => {
    if (url === `/api/v1/releases/${R_PAGE2}`) return { data: { id: R_PAGE2, project_id: PROJECT_PAGED } }
    if (url === `/api/v1/releases/${R_OFFLINE}`) throw new AxiosError('Network Error', 'ERR_NETWORK')
    throw new AxiosError('Not found', 'ERR_BAD_REQUEST', undefined, undefined, { status: 404 } as never)
  })
}

const releaseIds = () => selectReleaseIds(useReleaseStore.getState())

describe('useScopeUrlSync', () => {
  beforeEach(resetStores)
  afterEach(() => {
    currentRouter = null
  })

  it('flag off: reads nothing and writes nothing', async () => {
    useMultiFiltersFlagStore.setState({ enabled: false })
    mount(`/trends?release=${R1}&release=${R2}&suites=payments&window=14`)
    await act(async () => { await new Promise(r => setTimeout(r, 30)) })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
    expect(useTimeWindowStore.getState().days).toBe(30)
    expect(loc()).toBe(`/trends?release=${R1}&release=${R2}&suites=payments&window=14`)
  })

  it('the URL wins over the persisted store on load', async () => {
    useReleaseStore.getState().setActiveReleases([R2], PROJECT_A)
    useSuiteStore.getState().setActiveSuites(['persisted'], PROJECT_A)
    useTimeWindowStore.setState({ days: 7 })
    mount(`/trends?release=${R1}&release=${R2}&suites=payments&suites=cart&window=14`)
    await waitFor(() => expect(useSuiteStore.getState().activeSuiteNames).toEqual(['payments', 'cart']))
    expect(releaseIds()).toEqual([R1, R2])
    expect(useTimeWindowStore.getState().days).toBe(14)
  })

  it('with no params, the store is written out (replace) once releases are verified', async () => {
    useReleaseStore.getState().setActiveReleases([R2, R1], PROJECT_A)
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
    mount('/coverage?tab=x')
    await waitFor(() => expect(search().getAll('release')).toEqual([R1, R2]))
    expect(search().getAll('suites')).toEqual(['payments'])
    expect(search().get('window')).toBe('30')
    expect(search().get('tab')).toBe('x')
  })

  describe('round trip on every report route', () => {
    for (const [pattern, url] of REPORT_ROUTES) {
      it(`${pattern}: store → URL → fresh session → same scope`, async () => {
        // Session 1: set the scope, let the sync write it.
        useReleaseStore.getState().setActiveReleases([R2, R1], PROJECT_A)
        useSuiteStore.getState().setActiveSuites(['payments', 'cart'], PROJECT_A)
        useTimeWindowStore.setState({ days: 14 })
        const first = mount(url)
        await waitFor(() => expect(search().getAll('release')).toEqual([R1, R2]))
        await waitFor(() => expect(search().getAll('suites')).toEqual(['cart', 'payments']))
        expect(search().get('window')).toBe('14')
        const shared = loc()
        expect(shared.startsWith(url)).toBe(true)
        first.view.unmount()

        // Session 2: nothing persisted; the pasted link alone restores it.
        resetStores()
        mount(shared)
        await waitFor(() => expect(releaseIds()).toEqual([R1, R2]))
        await waitFor(() => expect(useSuiteStore.getState().activeSuiteNames).toEqual(['cart', 'payments']))
        expect(useTimeWindowStore.getState().days).toBe(14)
        expect(loc()).toBe(shared)
      })
    }
  })

  it('history: filter changes REPLACE, Back/Forward restore each page\'s filters', async () => {
    const { router } = mount('/trends')
    await waitFor(() => expect(search().get('window')).toBe('30'))

    act(() => { useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A) })
    await waitFor(() => expect(search().getAll('release')).toEqual([R1, R2]))
    const trendsUrl = loc()

    await act(async () => { await router.navigate('/coverage') })
    // A link that does not carry the filter does not drop it.
    await waitFor(() => expect(search().getAll('release')).toEqual([R1, R2]))
    expect(loc().startsWith('/coverage?')).toBe(true)

    act(() => { useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A) })
    await waitFor(() => expect(search().getAll('suites')).toEqual(['payments']))
    act(() => { useSuiteStore.getState().setActiveSuites(['payments', 'cart'], PROJECT_A) })
    await waitFor(() => expect(search().getAll('suites')).toEqual(['cart', 'payments']))
    const coverageUrl = loc()

    // ONE Back lands on Trends: the two suite refinements added no entries.
    await act(async () => { await router.navigate(-1) })
    await waitFor(() => expect(loc()).toBe(trendsUrl))
    await waitFor(() => expect(useSuiteStore.getState().activeSuiteNames).toEqual([]))
    expect(releaseIds()).toEqual([R1, R2])

    // Forward restores Coverage's suites.
    await act(async () => { await router.navigate(1) })
    await waitFor(() => expect(loc()).toBe(coverageUrl))
    await waitFor(() => expect(useSuiteStore.getState().activeSuiteNames).toEqual(['cart', 'payments']))
  })

  it('drops an unknown release and an over-cap tail, names them, applies the rest', async () => {
    const qs = new URLSearchParams()
    qs.append('release', R1)
    qs.append('release', R_GONE)
    qs.append('release', 'garbage')
    mount(`/trends?${qs}`)
    await waitFor(() => expect(releaseIds()).toEqual([R1]))
    await waitFor(() => expect(search().getAll('release')).toEqual([R1]))
    const notices = useScopeNoticeStore.getState().notices
    expect(notices.flatMap(n => n.values)).toEqual(expect.arrayContaining([R_GONE, 'garbage']))
    expect(notices.every(n => n.dimension === 'release')).toBe(true)
  })

  it('a project change drops releases and suites the new project lacks, naming them', async () => {
    useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT_A)
    useSuiteStore.getState().setActiveSuites(['payments', 'cart'], PROJECT_A)
    mount('/trends')
    await waitFor(() => expect(search().getAll('release')).toEqual([R1, R2]))

    act(() => { useProjectStore.setState({ activeProjectId: PROJECT_B }) })
    await waitFor(() => expect(releaseIds()).toEqual([]))
    await waitFor(() => expect(useSuiteStore.getState().activeSuiteNames).toEqual(['payments']))
    const notices = useScopeNoticeStore.getState().notices
    expect(notices.find(n => n.dimension === 'release')?.values).toEqual(['2026.09', '2026.10'])
    expect(notices.find(n => n.dimension === 'suite')?.values).toEqual(['cart'])
    await waitFor(() => expect(search().has('release')).toBe(false))
  })

  it('a project change KEEPS a valid suite older than the 200 most recent runs (authoritative suite list)', async () => {
    useSuiteStore.getState().setActiveSuites(['legacy-nightly', 'ghost'], PROJECT_A)
    mount('/trends')
    await waitFor(() => expect(search().getAll('suites')).toEqual(['ghost', 'legacy-nightly']))

    act(() => { useProjectStore.setState({ activeProjectId: PROJECT_B }) })
    await waitFor(() => expect(useSuiteStore.getState().scopedProjectId).toBe(PROJECT_B))
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['legacy-nightly'])
    expect(useScopeNoticeStore.getState().notices.find(n => n.dimension === 'suite')?.values).toEqual(['ghost'])
    expect(suitesList).toHaveBeenCalledWith(PROJECT_B)
  })

  it('a project change drops NO suite when the suite list cannot be read', async () => {
    useSuiteStore.getState().setActiveSuites(['legacy-nightly', 'ghost'], PROJECT_A)
    mount('/trends')
    await waitFor(() => expect(search().getAll('suites')).toEqual(['ghost', 'legacy-nightly']))
    suitesList.mockRejectedValue(new AxiosError('Network Error', 'ERR_NETWORK'))

    act(() => { useProjectStore.setState({ activeProjectId: PROJECT_B }) })
    await waitFor(() => expect(suitesList).toHaveBeenCalledWith(PROJECT_B))
    await act(async () => { await new Promise(r => setTimeout(r, 50)) })
    expect(useSuiteStore.getState().activeSuiteNames).toEqual(['legacy-nightly', 'ghost'])
    expect(useScopeNoticeStore.getState().notices).toEqual([])
  })

  describe('release validation reads past the first page', () => {
    beforeEach(() => {
      useProjectStore.setState({ activeProjectId: PROJECT_PAGED })
    })

    it('an id on page 2 is KEPT and published (looked up by id)', async () => {
      mount(`/trends?release=${R1}&release=${R_PAGE2}`)
      await waitFor(() => expect(search().getAll('release').sort()).toEqual([R1, R_PAGE2].sort()))
      expect(releaseIds().sort()).toEqual([R1, R_PAGE2].sort())
      expect(useScopeNoticeStore.getState().notices).toEqual([])
      expect(apiGet).toHaveBeenCalledWith(`/api/v1/releases/${R_PAGE2}`, { suppressToast: true })
      // R1 was on the first page: not looked up.
      expect(apiGet).not.toHaveBeenCalledWith(`/api/v1/releases/${R1}`, expect.anything())
    })

    it('a genuinely unknown id is dropped and NAMED; the rest apply', async () => {
      mount(`/trends?release=${R_PAGE2}&release=${R_GONE}`)
      await waitFor(() => expect(releaseIds()).toEqual([R_PAGE2]))
      await waitFor(() => expect(search().getAll('release')).toEqual([R_PAGE2]))
      const notice = useScopeNoticeStore.getState().notices.find(n => n.dimension === 'release')
      expect(notice?.values).toEqual([R_GONE])
    })

    it('a failed lookup drops NOTHING and raises no notice', async () => {
      mount(`/trends?release=${R1}&release=${R_OFFLINE}`)
      await waitFor(() => expect(apiGet).toHaveBeenCalledWith(`/api/v1/releases/${R_OFFLINE}`, { suppressToast: true }))
      await act(async () => { await new Promise(r => setTimeout(r, 50)) })
      expect(releaseIds().sort()).toEqual([R1, R_OFFLINE].sort())
      expect(useScopeNoticeStore.getState().notices).toEqual([])
      // Unverified, so not re-published — the link's own values stay put.
      expect(search().getAll('release')).toEqual([R1, R_OFFLINE])
    })
  })

  it('All Projects: the release key is neither read nor stripped', async () => {
    useProjectStore.setState({ activeProjectId: 'all' as never })
    mount(`/trends?release=${R1}&suites=payments`)
    await waitFor(() => expect(useSuiteStore.getState().activeSuiteNames).toEqual(['payments']))
    await waitFor(() => expect(search().get('window')).toBe('30'))
    expect(search().getAll('release')).toEqual([R1])
    expect(releaseIds()).toEqual([])
  })

  describe('page-local ?suite / ?days are never read or written', () => {
    for (const url of [
      '/runs/compare?suite=legacy&left=a&right=b',
      '/runs/run-1?suite=legacy',
      '/test-management?suite=legacy&tab=Suites',
      '/intelligence?suite=legacy',
      '/coverage/suite?name=payments&days=7',
    ]) {
      it(url, async () => {
        useSuiteStore.getState().setActiveSuites(['payments'], PROJECT_A)
        useTimeWindowStore.setState({ days: 30 })
        mount(url)
        await act(async () => { await new Promise(r => setTimeout(r, 40)) })
        const before = new URLSearchParams(url.split('?')[1])
        for (const key of ['suite', 'days', 'left', 'right', 'tab', 'name']) {
          expect(search().getAll(key), key).toEqual(before.getAll(key))
        }
        expect(useSuiteStore.getState().activeSuiteNames).toEqual(['payments'])
        expect(useTimeWindowStore.getState().days).toBe(30)
      })
    }
  })

  it('replaces ReleasePicker\'s write-back: a repeated ?release survives the picker', async () => {
    mount(`/trends?release=${R1}&release=${R2}`, true)
    await waitFor(() => expect(releaseIds()).toEqual([R1, R2]))
    // Let the release list load, which is when the legacy loop would publish.
    await act(async () => { await new Promise(r => setTimeout(r, 60)) })
    expect(search().getAll('release')).toEqual([R1, R2])
    expect(releaseIds()).toEqual([R1, R2])

    // Now change the selection so its FIRST id differs from the URL's first
    // value. The legacy loop would `set('release', <first id>)`, collapsing
    // the repeated key to one value — and the sync would then adopt that,
    // silently shrinking the user's selection to a single release.
    act(() => { useReleaseStore.getState().setActiveReleases([R2, R1], PROJECT_A) })
    await act(async () => { await new Promise(r => setTimeout(r, 60)) })
    expect(search().getAll('release')).toEqual([R1, R2])
    expect([...releaseIds()].sort()).toEqual([R1, R2])
  })
})
