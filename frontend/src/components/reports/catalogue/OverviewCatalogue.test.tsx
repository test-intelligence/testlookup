/**
 * Overview's catalogue sections (Wave 2.6, VIZ-408, plan 2.2 "Overview").
 *
 * What this pins, each with the reason it matters:
 *   - the donut is the page's OWN day series summed (OD-5): the same totals as
 *     the KPI and the flag-off foot strip, `broken` included, `unknown` as the
 *     residual of `total`;
 *   - the trend places the project's releases on their days;
 *   - "ever had a run" is the PAGE's answer, handed to every frame, never a
 *     probe of the section's own;
 *   - the two server-backed sections ask with the page's scope, clamped to 90
 *     days, and with nothing before the project resolves;
 *   - every section root carries its `data-catalogue-section` and every frame
 *     its title as its heading (B0's specs find them by both).
 *
 * jsdom lays out no plot (a zero-size container), so what is asserted is what
 * the frames are GIVEN — their table views are built from the same series the
 * plots draw.
 */
import { fireEvent, render, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartResponse, ChartState } from '@/components/charts/chartState'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { ChartFetcher, ChartKey } from '@/hooks/useChartData'
import type { TrendPoint } from '@/types/metrics'
import { PROTOTYPE_KEY_NAMES } from '@/components/charts/__fixtures__/protoKeyNames'

// The one server-backed section's request: what it asks (`source` is its SWR
// key's name, `params` the query), and the fetcher it would ask with.
const catalog = vi.hoisted(() => ({
  calls: [] as { source: string; params: CatalogParams | null; everHadData: boolean | null; fetcher: unknown }[],
  state: { status: 'loading' } as ChartState<ChartResponse>,
}))
vi.mock('@/hooks/useChartData', () => ({
  useChartData: (key: readonly unknown[] | null, fetcher: unknown, options: { everHadData: boolean | null }) => {
    catalog.calls.push({
      source: key === null ? 'top-failing' : String(key[0]),
      params: key === null ? null : (key[1] as CatalogParams),
      everHadData: options.everHadData,
      fetcher,
    })
    return catalog.state
  },
}))
// The wire, for the fetcher: the legacy `/analytics/top-failing` answer.
const wire = vi.hoisted(() => ({ calls: [] as { url: string; params: unknown }[], payload: null as unknown }))
vi.mock('@/services/chartApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/chartApi')>()),
  chartGet: async (url: string, options: { params: unknown }) => {
    wire.calls.push({ url, params: options.params })
    return { data: wire.payload, requestId: 'req-1' }
  },
}))

const releasesState = vi.hoisted(() => ({ items: [] as unknown[] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: releasesState.items } }),
}))

const project = vi.hoisted(() => ({ activeProjectId: 'proj-1' as string | null }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: project.activeProjectId }),
}))

vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

// The section must never run a probe of its own: "ever had a run" is the page's.
const useRuns = vi.hoisted(() => vi.fn())
vi.mock('@/hooks/useRuns', () => ({ useRuns }))

import OverviewCatalogue, { type FailureCategoriesRead, type OverviewTrendDay } from './OverviewCatalogue'

const point = (date: string, p: number, f: number, b: number, s: number, total?: number): TrendPoint => ({
  date,
  passed: p,
  failed: f,
  broken: b,
  skipped: s,
  ...(total === undefined ? {} : { total }),
  pass_rate: p + f + b > 0 ? Math.round((p / (p + f + b)) * 1000) / 10 : 0,
})

/** Seven UTC days, three with runs (the middle one only skips). */
const WINDOW: OverviewTrendDay[] = [
  { iso: '2026-08-10', point: null },
  { iso: '2026-08-11', point: point('2026-08-11T00:00:00', 40, 4, 6, 2, 52) },
  { iso: '2026-08-12', point: null },
  { iso: '2026-08-13', point: point('2026-08-13', 0, 0, 0, 5, 5) },
  { iso: '2026-08-14', point: null },
  // 3 executions the statuses do not name: `unknown`.
  { iso: '2026-08-15', point: point('2026-08-15', 45, 3, 2, 1, 54) },
  { iso: '2026-08-16', point: null },
]

/** The page's own failure-categories read, as `useFailureCategories` answered it. */
const CATEGORIES = {
  items: [
    { category: 'assertion', count: 7 },
    { category: 'timeout', count: 3 },
  ],
}
const categoriesRead = (over: Partial<FailureCategoriesRead> = {}): FailureCategoriesRead => ({
  data: CATEGORIES,
  error: undefined,
  isValidating: false,
  retry: () => {},
  ...over,
})

function renderSections(overrides: Partial<Parameters<typeof OverviewCatalogue>[0]> = {}) {
  return render(
    <OverviewCatalogue
      days={7}
      window={WINDOW}
      trendsLoading={false}
      categories={categoriesRead()}
      suiteFilter={null}
      filtersApplied={false}
      everHadRun={true}
      {...overrides}
    />,
  )
}

const section = (id: string) => document.querySelector(`[data-catalogue-section="${id}"]`) as HTMLElement

/** A frame's table view, opened: `Status -> Count` from the data table. */
function donutCounts(frame: HTMLElement): Record<string, string> {
  fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
  const table = within(frame).getByRole('table', { name: /data table/i })
  const out: Record<string, string> = {}
  for (const header of within(table).getAllByRole('rowheader')) {
    const cells = (header.parentElement as HTMLElement).querySelectorAll('td')
    out[header.textContent ?? ''] = cells[0]?.textContent ?? ''
  }
  return out
}

beforeEach(() => {
  wire.calls = []
  wire.payload = null
  catalog.calls = []
  catalog.state = { status: 'loading' }
  releasesState.items = []
  project.activeProjectId = 'proj-1'
  useRuns.mockReset()
})

describe('OverviewCatalogue — the DOM contract B0 relies on', () => {
  it('marks each section and keeps each frame title as its heading', () => {
    renderSections()
    const expected: [string, string][] = [
      ['overview-trend', 'Pass rate trend'],
      ['overview-donut', 'Status breakdown'],
      ['overview-top-failing', 'Top failing tests'],
      ['overview-categories', 'Failure categories'],
    ]
    for (const [id, title] of expected) {
      expect(section(id)).not.toBeNull()
      expect(within(section(id)).getByRole('heading', { level: 3, name: title })).toBeInTheDocument()
    }
  })
})

/** Every section id in the DOM, in order. */
const sectionIds = () =>
  Array.from(document.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))

describe('OverviewCatalogue — `rows` picks which rows render (UX redesign P3)', () => {
  it('no `rows`: both rows, exactly as before the prop existed', () => {
    renderSections()
    expect(sectionIds()).toEqual(['overview-trend', 'overview-donut', 'overview-top-failing', 'overview-categories'])
    expect(catalog.calls.map((c) => c.source)).toEqual(['overview-top-failing'])
  })

  it('`all` is the default: the same sections as no prop', () => {
    renderSections({ rows: 'all' })
    expect(sectionIds()).toEqual(['overview-trend', 'overview-donut', 'overview-top-failing', 'overview-categories'])
  })

  it('`headline`: the trend and the donut only, and the top-failing request is never made', () => {
    renderSections({ rows: 'headline' })
    expect(sectionIds()).toEqual(['overview-trend', 'overview-donut'])
    expect(document.querySelector('[data-lazy-section]')).toBeNull()
    // The one server-backed section is not rendered, so nothing asks.
    expect(catalog.calls).toEqual([])
  })

  it('`breakdown`: top failing and failure categories only, top failing asking as before', () => {
    renderSections({ rows: 'breakdown', suiteFilter: 'checkout-api' })
    expect(sectionIds()).toEqual(['overview-top-failing', 'overview-categories'])
    expect(catalog.calls.map((c) => [c.source, c.params])).toEqual([
      ['overview-top-failing', { project_id: 'proj-1', days: 7, suite_name: 'checkout-api' }],
    ])
    for (const [id, title] of [
      ['overview-top-failing', 'Top failing tests'],
      ['overview-categories', 'Failure categories'],
    ] as const) {
      expect(within(section(id)).getByRole('heading', { level: 3, name: title })).toBeInTheDocument()
    }
  })

  it('a row is drawn the same whichever value includes it (the donut’s counts under `headline`)', () => {
    renderSections({ rows: 'headline' })
    const counts = donutCounts(section('overview-donut'))
    expect([counts.Passed, counts.Failed, counts.Broken, counts.Skipped, counts.Unknown]).toEqual(['85', '7', '8', '8', '3'])
  })

  it('far from the reader, `breakdown` holds the two lazy placeholders at the row’s height and asks nothing', () => {
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
    try {
      renderSections({ rows: 'breakdown' })
      const placeholders = Array.from(document.querySelectorAll<HTMLElement>('[data-lazy-section]'))
      expect(placeholders.map((el) => [el.getAttribute('data-lazy-section'), el.style.minHeight])).toEqual([
        ['overview-top-failing', '390px'],
        ['overview-categories', '390px'],
      ])
      expect(catalog.calls).toEqual([])
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('OverviewCatalogue — the status donut is the page’s own day series (OD-5)', () => {
  it('sums every status over the days with runs, broken included', () => {
    renderSections()
    const counts = donutCounts(section('overview-donut'))
    // 40+0+45 passed, 4+0+3 failed, 6+0+2 broken, 2+5+1 skipped.
    expect(counts.Passed).toBe('85')
    expect(counts.Failed).toBe('7')
    expect(counts.Broken).toBe('8')
    expect(counts.Skipped).toBe('8')
  })

  it('draws the executions no status names as `unknown`: the residual of the total', () => {
    renderSections()
    const frame = section('overview-donut')
    expect(donutCounts(frame).Unknown).toBe('3')
    // The ring's total is every execution of the window: 52 + 5 + 54.
    expect(frame.querySelector('[data-donut-table-total]')?.textContent).toBe('Total 111 executions')
  })

  it('leaves `unknown` out, not zero, when a day carries no total', () => {
    renderSections({
      window: [
        { iso: '2026-08-15', point: point('2026-08-15', 45, 3, 2, 1) },
        { iso: '2026-08-16', point: point('2026-08-16', 10, 0, 0, 0, 10) },
      ],
      days: 2,
    })
    const counts = donutCounts(section('overview-donut'))
    expect(counts.Unknown).toBeUndefined()
    expect(counts.Passed).toBe('55')
  })

  it('shows the window as empty (not "no data matches your filters") when no filter is set', () => {
    renderSections({ window: [{ iso: '2026-08-16', point: null }], days: 1 })
    expect(within(section('overview-donut')).getByText('No executions in the last 24 hours.')).toBeInTheDocument()
    expect(within(section('overview-trend')).getByText('No executions in the last 24 hours.')).toBeInTheDocument()
  })

  it('keeps the filter words when a suite or release filter narrowed an empty window', () => {
    renderSections({ window: [{ iso: '2026-08-16', point: null }], days: 1, filtersApplied: true })
    expect(within(section('overview-donut')).getByText('No data matches the current filters')).toBeInTheDocument()
  })

  it('holds a skeleton, never a verdict, while the page’s trend is loading', () => {
    renderSections({ trendsLoading: true })
    expect(section('overview-donut').querySelector('[data-chart-state="loading"], [aria-busy="true"]')).not.toBeNull()
    expect(within(section('overview-donut')).queryByRole('button', { name: 'View as table' })).toBeNull()
  })
})

describe('OverviewCatalogue — "ever had a run" is the page’s answer', () => {
  it('tells a project that never ran to ingest, in the headline frames', () => {
    renderSections({ window: [{ iso: '2026-08-16', point: null }], days: 1, everHadRun: false })
    for (const id of ['overview-trend', 'overview-donut']) {
      expect(within(section(id)).getByText('No runs have been ingested for this project yet')).toBeInTheDocument()
    }
  })

  it('waits (a skeleton, never "no data yet") while the page’s probe has not answered for an empty window', () => {
    renderSections({ window: [{ iso: '2026-08-16', point: null }], days: 1, everHadRun: null })
    for (const id of ['overview-trend', 'overview-donut']) {
      expect(within(section(id)).queryByText('No runs have been ingested for this project yet')).toBeNull()
      expect(within(section(id)).queryByText('No executions in the last 24 hours.')).toBeNull()
    }
  })

  it('says "not measured", never 0 %, when the window’s runs only skipped', () => {
    renderSections({ window: [{ iso: '2026-08-16', point: point('2026-08-16', 0, 0, 0, 5, 5) }], days: 1 })
    expect(within(section('overview-trend')).getByText('Not measured')).toBeInTheDocument()
  })

  it('hands the page’s value to the server-backed section, and runs no probe of its own', () => {
    renderSections({ everHadRun: false })
    expect(catalog.calls.map((c) => c.everHadData)).toEqual([false])
    catalog.calls = []
    renderSections({ everHadRun: null })
    expect(catalog.calls.map((c) => c.everHadData)).toEqual([null])
    expect(useRuns).not.toHaveBeenCalled()
  })

  it('reads the page’s answer for an empty categories payload too: "never ran", or a skeleton while unknown', () => {
    const empty = categoriesRead({ data: { items: [] } })
    const { unmount } = renderSections({ categories: empty, everHadRun: false })
    expect(within(section('overview-categories')).getByText('No runs have been ingested for this project yet')).toBeInTheDocument()
    unmount()
    renderSections({ categories: empty, everHadRun: null })
    expect(section('overview-categories').querySelector('[data-chart-state="loading"]')).not.toBeNull()
  })
})

describe('OverviewCatalogue — the pass-rate trend', () => {
  it('places a release on its day, and counts one outside the window instead of dropping it', () => {
    releasesState.items = [
      { id: 'r1', name: 'v2.4.0', released_at: '2026-08-13T15:00:00Z', planned_date: null },
      // Not released: its planned day is the marker.
      { id: 'r2', name: 'v2.5.0', released_at: null, planned_date: '2026-08-15' },
      { id: 'r3', name: 'v1.0.0', released_at: '2026-01-02T00:00:00Z', planned_date: null },
    ]
    renderSections()
    const frame = section('overview-trend')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    const markers = within(frame).getByRole('table', { name: /Release markers/i })
    const onDay = (day: string) =>
      (within(markers).getByRole('rowheader', { name: day }).parentElement as HTMLElement).querySelector('td')?.textContent
    expect(onDay('2026-08-13')).toBe('v2.4.0')
    expect(onDay('2026-08-15')).toBe('v2.5.0')
    expect(markers.textContent).not.toMatch(/v1\.0\.0/)
    expect(within(frame).getAllByText(/1 release.*outside/i).length).toBeGreaterThan(0)
  })

  it('draws no markers in All Projects: one project’s release is not a boundary in another’s trend', () => {
    project.activeProjectId = '__ALL__'
    releasesState.items = [{ id: 'r1', name: 'v2.4.0', released_at: '2026-08-13T15:00:00Z', planned_date: null }]
    renderSections()
    const frame = section('overview-trend')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    expect(frame.textContent).not.toMatch(/v2\.4\.0/)
  })

  it('draws the trend, with no markers, while the project’s releases have not answered', () => {
    releasesState.items = undefined as unknown as unknown[]
    renderSections()
    const frame = section('overview-trend')
    expect(frame.querySelector('[data-chart-state]')?.getAttribute('data-chart-state')).toBe('ready')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    expect(within(frame).getByRole('table', { name: /Release markers/i })).toHaveTextContent('No releases fall inside this window')
  })

  it('is one bucket per window day: a day with no runs is a gap, not a missing day', () => {
    renderSections()
    const frame = section('overview-trend')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    const table = within(frame).getByRole('table', { name: /data table/i })
    expect(within(table).getAllByRole('rowheader')).toHaveLength(WINDOW.length)
  })
})

describe('OverviewCatalogue — failure categories are the page’s own read', () => {
  it('asks nothing of its own: the only chart request is top failing', () => {
    renderSections({ suiteFilter: 'checkout-api' })
    expect(catalog.calls.map((c) => c.source)).toEqual(['overview-top-failing'])
  })

  it('draws the page’s payload, one slice per category', () => {
    renderSections()
    const frame = section('overview-categories')
    expect(frame.querySelector('[data-chart-state]')?.getAttribute('data-chart-state')).toBe('ready')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    expect(within(frame).getByRole('rowheader', { name: 'assertion' })).toBeInTheDocument()
    expect(within(frame).getByRole('rowheader', { name: 'timeout' })).toBeInTheDocument()
  })

  it('a failed page read is the frame’s error, and its Retry asks the page’s read again', () => {
    const retry = vi.fn()
    renderSections({
      categories: categoriesRead({ data: undefined, error: { response: { status: 500 } }, retry }),
    })
    const frame = section('overview-categories')
    expect(frame.querySelector('[data-chart-state]')?.getAttribute('data-chart-state')).toBe('error')
    fireEvent.click(within(frame).getByRole('button', { name: /retry/i }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('a payload the contract refuses is an error frame, never a drawing', () => {
    renderSections({ categories: categoriesRead({ data: { items: [{ category: 'x', count: 1 }], meta: { bogus: true } } }) })
    expect(section('overview-categories').querySelector('[data-chart-state]')?.getAttribute('data-chart-state')).toBe('error')
  })

  it('a refused payload from a read that ALSO failed shows the read’s own error, not the payload’s', () => {
    const refused = { items: [{ category: 'x', count: 1 }], meta: { bogus: true } }
    renderSections({ categories: categoriesRead({ data: refused, error: { response: { status: 500 } } }) })
    const failed = section('overview-categories')
    expect(failed.querySelector('[data-chart-state]')?.getAttribute('data-chart-state')).toBe('error')
    const kind = failed.querySelector('[data-chart-error-kind]')?.getAttribute('data-chart-error-kind')
    document.body.innerHTML = ''
    // The same refused payload with no read error (`null`, as SWR reports none): the payload's error.
    renderSections({ categories: categoriesRead({ data: refused, error: null }) })
    const payloadKind = section('overview-categories').querySelector('[data-chart-error-kind]')?.getAttribute('data-chart-error-kind')
    expect(payloadKind).toBeTruthy()
    expect(kind).toBeTruthy()
    expect(kind).not.toBe(payloadKind)
  })

  it('a loading page read is a skeleton', () => {
    renderSections({ categories: categoriesRead({ data: undefined }) })
    expect(section('overview-categories').querySelector('[data-chart-state]')?.getAttribute('data-chart-state')).toBe('loading')
  })
})

describe('OverviewCatalogue — top failing, the one server-backed section', () => {
  it('asks top-failing with the page’s project and window', () => {
    renderSections({ suiteFilter: 'checkout-api' })
    expect(catalog.calls).toHaveLength(1)
    expect(catalog.calls[0].params).toEqual({ project_id: 'proj-1', days: 7, suite_name: 'checkout-api' })
  })

  it('never asks for more than 90 days, whatever window reaches it', () => {
    renderSections({ days: 365 })
    expect(catalog.calls.map((c) => c.params?.days)).toEqual([90])
  })

  it('asks nothing before the project resolves', () => {
    project.activeProjectId = null
    renderSections()
    expect(catalog.calls.map((c) => c.params)).toEqual([null])
  })

  it('draw a ranked bar per failing test, labelled by name', () => {
    catalog.state = {
      status: 'ready',
      meta: null,
      revalidating: false,
      data: {
        meta: null,
        series: {
          kind: 'series',
          dimensions: ['test'],
          x_type: 'category',
          series: [{ key: 'failures', label: 'Failures', points: [{ x: 'fp-1', y: 9, n: 9 }] }],
          x_labels: { 'fp-1': 'test_checkout <b>' },
        },
      },
    }
    renderSections()
    const frame = section('overview-top-failing')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    // A hostile name is text.
    expect(within(frame).getByRole('rowheader', { name: 'test_checkout <b>' })).toBeInTheDocument()
  })

  /** What the section's fetcher makes of a `/analytics/top-failing` answer: bar key -> drawn label. */
  async function drawnLabels(items: { name: string; suite: string | null; fails: number }[]): Promise<[string, string][]> {
    renderSections()
    wire.payload = {
      items: items.map((item, i) => ({
        test_name: item.name,
        suite_name: item.suite,
        test_fingerprint: `fp-${i}`,
        fail_count: item.fails,
      })),
    }
    const fetcher = catalog.calls[0].fetcher as ChartFetcher<ChartKey>
    const { data } = await fetcher(['overview-top-failing', catalog.calls[0].params], { signal: new AbortController().signal })
    const series = (data as ChartResponse).series
    if (series.kind !== 'series') throw new Error('not a category series')
    return series.series[0].points.map((p) => [String(p.x), (series.x_labels ?? {})[String(p.x)]])
  }

  it('asks the legacy top-failing endpoint with the section’s params', async () => {
    await drawnLabels([{ name: 'a', suite: 's', fails: 1 }])
    expect(wire.calls).toEqual([{ url: '/api/v1/analytics/top-failing', params: catalog.calls[0].params }])
  })

  it('names the suite only for two tests that share a name (R2-2); a unique name stays as it is', async () => {
    expect(
      await drawnLabels([
        { name: 'card declined shows reason', suite: 'Payments', fails: 14 },
        { name: 'login times out', suite: 'Auth', fails: 11 },
        { name: 'login times out', suite: 'Checkout', fails: 9 },
      ]),
    ).toEqual([
      ['fp-0', 'card declined shows reason'],
      ['fp-1', 'login times out (Auth)'],
      ['fp-2', 'login times out (Checkout)'],
    ])
  })

  it('a twin without a suite keeps its bare name (nothing invented); its twin still says its suite', async () => {
    expect(
      await drawnLabels([
        { name: 'flaky', suite: 'Auth', fails: 3 },
        { name: 'flaky', suite: null, fails: 2 },
      ]),
    ).toEqual([
      ['fp-0', 'flaky (Auth)'],
      ['fp-1', 'flaky'],
    ])
  })

  it('hostile and prototype-member names: twins are told apart by suite, and drawn as text', async () => {
    const hostile = '<img src=x onerror="window.__xss=1">'
    const items = [
      { name: hostile, suite: 'Search', fails: 8 },
      { name: hostile, suite: '<b>Auth</b>', fails: 7 },
      ...PROTOTYPE_KEY_NAMES.flatMap((name, i) => [
        { name, suite: 'A', fails: 6 - i },
        { name, suite: 'B', fails: 6 - i },
      ]),
      // A prototype-member name used ONCE is not a twin of anything.
      { name: 'valueOf', suite: 'C', fails: 1 },
    ]
    const labels = new Map(await drawnLabels(items))
    expect(labels.get('fp-0')).toBe(`${hostile} (Search)`)
    expect(labels.get('fp-1')).toBe(`${hostile} (<b>Auth</b>)`)
    PROTOTYPE_KEY_NAMES.forEach((name, i) => {
      expect(labels.get(`fp-${2 + i * 2}`)).toBe(`${name} (A)`)
      expect(labels.get(`fp-${3 + i * 2}`)).toBe(`${name} (B)`)
    })
    expect(labels.get(`fp-${items.length - 1}`)).toBe('valueOf')
  })

  /** The section's fetcher on a raw `/analytics/top-failing` answer: bar key -> drawn label. */
  async function drawnFromRaw(payload: unknown): Promise<[string, string][]> {
    renderSections()
    wire.payload = payload
    const fetcher = catalog.calls[0].fetcher as ChartFetcher<ChartKey>
    const { data } = await fetcher(['overview-top-failing', catalog.calls[0].params], { signal: new AbortController().signal })
    const series = (data as ChartResponse).series
    if (series.kind !== 'series') throw new Error('not a category series')
    return series.series[0].points.map((p) => [String(p.x), (series.x_labels ?? {})[String(p.x)]])
  }

  it('an answer with no item list is handed to the adapter as it came (no bars), never a crash', async () => {
    expect(await drawnFromRaw(null)).toEqual([])
    expect(await drawnFromRaw({ items: 'not a list' })).toEqual([])
    expect(await drawnFromRaw(['not', 'a', 'record'])).toEqual([])
  })

  it('entries that are not records are neither counted as names nor drawn; a nameless test stays nameless', async () => {
    expect(
      await drawnFromRaw({
        items: [
          null,
          'login times out',
          ['login times out'],
          { test_name: 'login times out', suite_name: 'Auth', test_fingerprint: 'fp-a', fail_count: 4 },
          { suite_name: 'Auth', test_fingerprint: 'fp-n', fail_count: 2 },
        ],
      }),
    ).toEqual([
      // The string entry is not a second "login times out": the name is used once.
      ['fp-a', 'login times out'],
      ['fp-n', ''],
    ])
  })

  it('a twin whose suite is blank or not text keeps its bare name', async () => {
    expect(
      await drawnFromRaw({
        items: [
          { test_name: 'flaky', suite_name: '   ', test_fingerprint: 'fp-0', fail_count: 3 },
          { test_name: 'flaky', suite_name: 42, test_fingerprint: 'fp-1', fail_count: 2 },
          { test_name: 'flaky', suite_name: ' Auth ', test_fingerprint: 'fp-2', fail_count: 1 },
        ],
      }),
    ).toEqual([
      ['fp-0', 'flaky'],
      ['fp-1', 'flaky'],
      ['fp-2', 'flaky (Auth)'],
    ])
  })

  it('draws the twins as two bars with two different labels', async () => {
    await drawnLabels([
      { name: 'login times out', suite: 'Auth', fails: 11 },
      { name: 'login times out', suite: 'Checkout', fails: 9 },
    ])
    const { data } = await (catalog.calls[0].fetcher as ChartFetcher<ChartKey>)(['overview-top-failing', null], {
      signal: new AbortController().signal,
    })
    catalog.state = { status: 'ready', meta: null, revalidating: false, data: data as ChartResponse }
    document.body.innerHTML = ''
    renderSections()
    const frame = section('overview-top-failing')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    expect(within(frame).getByRole('rowheader', { name: 'login times out (Auth)' })).toBeInTheDocument()
    expect(within(frame).getByRole('rowheader', { name: 'login times out (Checkout)' })).toBeInTheDocument()
  })
})
