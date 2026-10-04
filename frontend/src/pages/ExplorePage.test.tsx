/**
 * VIZ-505 — the Explorer page, with only the network, the flag lookups, the
 * existence probe, the stores and the saved-views service mocked: the plan,
 * the URL codec, the chart pipeline, `LazySection` and the frames are real,
 * so "one discovery plus one request per panel the reader reaches" is read
 * off the requests themselves.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import envelopeFixture from '../../../contracts/viz/fixtures/envelope/valid/filtered.json'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import { VIZ_FLAGS } from '@/config/vizFlags'
import { __resetChartConcurrency } from '@/services/chartApi'
import { useReleaseStore } from '@/store/releaseStore'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

/** The two chart flags, read through the REAL seam (`useCatalogueRollout.ts`). */
const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean | undefined> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => flags.values[key] ?? false,
  useFeatureFlagStatus: (key: string) => flags.values[key],
}))

vi.mock('@/components/reports/catalogue/useEverHadRun', () => ({ useEverHadRun: () => true }))

const project = vi.hoisted(() => ({ id: 'p1' as string }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id }),
}))
const release = vi.hoisted(() => ({ scope: null as string | null }))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => release.scope }))

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
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import ExplorePage, { EXPLORER_OFF_TITLE } from './ExplorePage'

const CHART_DATA_URL = '/api/v1/analytics/chart-data'
const META = envelopeFixture.payload as unknown as EnvelopeMeta
const WEEKS = ['2026-09-07', '2026-09-14', '2026-09-21', '2026-09-28']
const ENVIRONMENTS = ['prod', 'staging']

const suites = (count: number) => Array.from({ length: count }, (_, i) => `suite-${String(i + 1).padStart(2, '0')}`)
let suiteNames = suites(5)

/** `executions` by suite x environment: the suites in rank order on x. */
function discoveryChart(): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['suite', 'environment'],
    x_type: 'category',
    series: ENVIRONMENTS.map((key) => ({
      key,
      label: key,
      points: suiteNames.map((x, i) => ({ x, y: 1000 - i, n: 1000 - i })),
    })),
  }
}

/** One panel: the metric by week, one line per environment; the value grows with the suite's place. */
function panelChart(params: CatalogParams): SeriesChart {
  const place = suiteNames.indexOf(String(params.suite_name ?? suiteNames[0]))
  const rate = params.metric === 'failure_rate' || params.metric === 'pass_rate'
  return {
    kind: 'series',
    dimensions: ['week', 'environment'],
    x_type: 'time',
    series: ENVIRONMENTS.map((key, s) => ({
      key,
      label: key,
      points: WEEKS.map((x, w) => ({ x, y: rate ? 10 + s * 5 + w : (place + 1) * 100 + s * 10 + w, n: 50 })),
    })),
  }
}

const calls = (): CatalogParams[] =>
  get.mock.calls.filter(([url]) => url === CHART_DATA_URL).map(([, config]) => (config as { params: CatalogParams }).params)
const isDiscovery = (params: CatalogParams) => Array.isArray(params.group_by) && ['suite', 'release'].includes(String(params.group_by[0]))
const discoveryCalls = () => calls().filter(isDiscovery)
const panelCalls = () => calls().filter((params) => !isDiscovery(params))

/** The URL the page last wrote, for the assertions that read it. */
const seen = { search: '' }
function LocationProbe() {
  const { search } = useLocation()
  useEffect(() => {
    seen.search = search
  }, [search])
  return null
}

function renderPage(url = '/explore') {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>
          {children}
          <LocationProbe />
        </ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<ExplorePage />, { wrapper })
}

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

const panels = () => [...document.querySelectorAll<HTMLElement>('[data-explore-panel]')]
const scales = () => [...document.querySelectorAll<HTMLElement>('[data-explore-yscale]')]

// jsdom has no IntersectionObserver (so every LazySection mounts at once); the
// lazy-loading test installs this fake and says which targets are near.
const observers: { fire: (near: boolean) => void }[] = []
class FakeIntersectionObserver {
  targets: Element[] = []
  constructor(public callback: IntersectionObserverCallback) {
    observers.push(this)
  }
  observe(target: Element) {
    this.targets.push(target)
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
  fire(isIntersecting: boolean) {
    this.callback(this.targets.map((target) => ({ target, isIntersecting }) as IntersectionObserverEntry), this as unknown as IntersectionObserver)
  }
}

beforeEach(() => {
  flags.values = { [VIZ_FLAGS.chartDataApi]: true, [VIZ_FLAGS.advancedCharts]: true }
  project.id = 'p1'
  release.scope = null
  suiteNames = suites(5)
  observers.length = 0
  for (const fn of Object.values(service)) fn.mockReset()
  service.listSavedViews.mockResolvedValue([])
  service.createSavedView.mockResolvedValue({})
  window.sessionStorage.clear()
  get.mockReset()
  get.mockImplementation((_url: string, config: { params: CatalogParams }) => {
    const series = isDiscovery(config.params) ? discoveryChart() : panelChart(config.params)
    return Promise.resolve({ data: { meta: META, series }, headers: { 'x-request-id': 'req-1' } })
  })
})

afterEach(() => {
  __resetChartConcurrency()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('ExplorePage (VIZ-505)', () => {
  it('the default view: one discovery, then one request per suite, every panel on the shared rate scale', async () => {
    renderPage()
    await waitFor(() => expect(panels()).toHaveLength(5))
    await waitFor(() => expect(scales()).toHaveLength(5))
    expect(discoveryCalls()).toEqual([{ project_id: 'p1', days: 30, metric: 'executions', group_by: ['suite', 'environment'] }])
    expect(panelCalls()).toHaveLength(5)
    expect(panelCalls()[0]).toEqual({ project_id: 'p1', days: 30, metric: 'failure_rate', group_by: ['week', 'environment'], suite_name: 'suite-01' })
    for (const scale of scales()) {
      expect(scale).toHaveAttribute('data-explore-yscale', 'shared')
      expect(scale).toHaveTextContent('Shared y-scale: 0–100%')
    }
    expect(screen.getByRole('heading', { name: 'suite-03' })).toBeInTheDocument()
    expect(document.querySelector('[data-explore-summary]')).toHaveTextContent(
      'Failure rate per week, one line per environment, one panel per suite, last 30 days.',
    )
  })

  it('15 suites: the 12 with the most executions, and the page says how many there are', async () => {
    suiteNames = suites(15)
    renderPage()
    await waitFor(() => expect(panels()).toHaveLength(12))
    expect(screen.getByText('Showing the 12 suites with the most executions of 15.')).toBeInTheDocument()
    expect(panels().map((p) => p.dataset.explorePanel)).toEqual(suites(12))
    await waitFor(() => expect(panelCalls()).toHaveLength(12))
    expect(discoveryCalls()).toHaveLength(1)
  })

  it('lazy: only the panels near the screen ask for their data', async () => {
    vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
    renderPage()
    await waitFor(() => expect(document.querySelectorAll('[data-lazy-section]')).toHaveLength(5))
    await settle()
    expect(discoveryCalls()).toHaveLength(1)
    expect(panelCalls()).toHaveLength(0)
    act(() => {
      observers[0].fire(true)
      observers[1].fire(true)
      observers[2].fire(false)
    })
    await waitFor(() => expect(panelCalls()).toHaveLength(2))
    await settle()
    expect(panelCalls().map((params) => params.suite_name)).toEqual(['suite-01', 'suite-02'])
    expect(panels()).toHaveLength(2)
  })

  it('the y-scale toggle flips every panel and asks nothing', async () => {
    renderPage()
    await waitFor(() => expect(scales()).toHaveLength(5))
    const before = get.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Own' }))
    await waitFor(() => expect(scales().every((s) => s.dataset.exploreYscale === 'independent')).toBe(true))
    expect(scales()[0]).toHaveTextContent(/^Own y-scale: 0–/)
    expect(seen.search).toContain('y=independent')
    await settle()
    expect(get.mock.calls.length).toBe(before)
  })

  it('a count on a shared scale: every panel draws the largest panel\'s range', async () => {
    renderPage('/explore?metric=executions')
    await waitFor(() => expect(scales()).toHaveLength(5))
    // suite-05 peaks at 513: 0–600 on every panel once all five have reported.
    await waitFor(() => expect(scales().map((s) => s.textContent)).toEqual(Array(5).fill('Shared y-scale: 0–600')))
    // A metric change keeps the panel set: no new discovery.
    expect(discoveryCalls()).toHaveLength(1)
  })

  it('a metric change asks each panel again and never the discovery', async () => {
    renderPage()
    await waitFor(() => expect(panelCalls()).toHaveLength(5))
    fireEvent.change(screen.getByLabelText('Metric'), { target: { value: 'pass_rate' } })
    await waitFor(() => expect(panelCalls().filter((p) => p.metric === 'pass_rate')).toHaveLength(5))
    expect(discoveryCalls()).toHaveLength(1)
    expect(seen.search).toContain('metric=pass_rate')
  })

  it('All Projects: no release facet (said why), no Saved Views, and no project on the wire', async () => {
    project.id = '__ALL__'
    renderPage()
    await waitFor(() => expect(panels()).toHaveLength(5))
    const release = within(screen.getByLabelText('Panels')).getByRole('option', { name: /One per release/ }) as HTMLOptionElement
    expect(release.disabled).toBe(true)
    expect(release.textContent).toContain('pick a project to facet by release')
    expect(screen.queryByRole('button', { name: /Views/ })).toBeNull()
    expect(discoveryCalls()[0]).toEqual({ days: 30, metric: 'executions', group_by: ['suite', 'environment'] })
  })

  it('a release facet from a link in All Projects is reset, with the reason', async () => {
    project.id = '__ALL__'
    renderPage('/explore?facet=release&series=none')
    await waitFor(() => expect(panelCalls()).toHaveLength(1))
    expect(screen.getByText(/Panels "Release" is not available \(releases belong to one project/)).toBeInTheDocument()
    expect(discoveryCalls()).toHaveLength(0)
  })

  it('Saved Views inside a project: Save stores the configuration under filters.explore', async () => {
    release.scope = 'r1'
    renderPage('/explore?metric=pass_rate&x=day&series=branch&facet=release&y=independent&days=14')
    fireEvent.click(await screen.findByRole('button', { name: /Views/ }))
    await screen.findByText(/No saved views for this page yet/)
    fireEvent.change(screen.getByPlaceholderText(/Payments release watch/), { target: { value: 'Pass rate by release' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
    await waitFor(() => expect(service.createSavedView).toHaveBeenCalledTimes(1))
    expect(service.createSavedView.mock.calls[0][0]).toMatchObject({
      project_id: 'p1',
      page: 'explore',
      filters: {
        kind: 'report_view',
        page: 'explore',
        window: 14,
        release_ids: ['r1'],
        explore: { metric: 'pass_rate', x: 'day', series: 'branch', facet: 'release', y: 'independent', days: 14 },
      },
    })
  })

  it('Saved Views: Open applies the configuration, the window and the release', async () => {
    service.listSavedViews.mockResolvedValue([
      {
        id: 'v1',
        user_id: 'me',
        project_id: 'p1',
        name: 'Flaky by branch',
        description: null,
        page: 'explore',
        filters: {
          kind: 'report_view',
          page: 'explore',
          window: 7,
          release_ids: ['r2'],
          explore: { metric: 'flaky_tests', x: 'day', series: 'branch', facet: 'none', y: 'shared', days: 7 },
        },
        is_shared: false,
        is_default: false,
        created_at: '2026-10-01T00:00:00Z',
        updated_at: null,
      },
    ])
    // The real store; its setter is what the top bar's picker calls.
    const setActiveRelease = vi.spyOn(useReleaseStore.getState(), 'setActiveRelease').mockImplementation(() => {})
    renderPage('/explore?metric=executions')
    fireEvent.click(await screen.findByRole('button', { name: /Views/ }))
    fireEvent.click(await screen.findByText('Flaky by branch'))
    await waitFor(() => expect((screen.getByLabelText('Metric') as HTMLSelectElement).value).toBe('flaky_tests'))
    expect(seen.search).toContain('facet=none')
    expect(seen.search).toContain('days=7')
    expect(setActiveRelease).toHaveBeenCalledWith('r2', 'p1')
    await waitFor(() =>
      expect(panelCalls().slice(-1)[0]).toEqual({ project_id: 'p1', days: 7, metric: 'flaky_tests', group_by: ['day', 'branch'] }),
    )
  })

  it('a discovery failure says the data could not be loaded, with Retry', async () => {
    get.mockImplementation(() => Promise.reject(Object.assign(new Error('boom'), { response: { status: 500, data: {} } })))
    renderPage()
    expect(await screen.findByTestId('explore-discovery-unavailable')).toBeInTheDocument()
  })

  it('flags off: the page says the explorer is not enabled, and asks nothing', async () => {
    flags.values = { [VIZ_FLAGS.chartDataApi]: true, [VIZ_FLAGS.advancedCharts]: false }
    renderPage()
    expect(screen.getByText(EXPLORER_OFF_TITLE)).toBeInTheDocument()
    await settle()
    expect(calls()).toHaveLength(0)
  })

  it('flags still loading: a placeholder, not "not enabled"', () => {
    flags.values = {}
    renderPage()
    expect(screen.queryByText(EXPLORER_OFF_TITLE)).toBeNull()
    expect(document.querySelector('[data-explore-pending]')).not.toBeNull()
  })
})
