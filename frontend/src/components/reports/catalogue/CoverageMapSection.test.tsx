/**
 * The coverage map section (VIZ-502) with only the network, the flag lookups,
 * the existence probe, the project and the canvas engine mocked: the seam,
 * the drill path in the URL, the scope builder, the chart pipeline, the frame,
 * the treemap's keyboard path and the rows panel are the real ones, so "which
 * level is asked for" and "where the reader lands" are read off the requests,
 * the address bar and the focused element.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta, TreeNode, TreeNodeStats } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import {
  CLASS_KEY_SEPARATOR,
  COVERAGE_DRILL_NOTICE_REASON,
  COVERAGE_DROP_WORDS,
  COVERAGE_MAP_CAPTION,
  COVERAGE_MAP_EMPTY_HEIGHT,
  COVERAGE_MAP_TITLE,
  coverageEmptyText,
} from '@/components/charts/coverageMap.model'
import { DRILL_DROP_WORDS } from '@/hooks/useDrillPath'
import { PageSuiteTargetContext, type PageSuiteTarget } from '@/hooks/pageSuiteTarget'
import { __resetChartConcurrency } from '@/services/chartApi'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useScopeNoticeStore } from '@/store/scopeNoticeStore'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

/** The two catalogue flags, read through the REAL seam (`useCatalogueRollout.ts`). */
const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => flags.values[key] ?? false,
  useFeatureFlagStatus: (key: string) => flags.values[key],
}))

vi.mock('./useEverHadRun', () => ({ useEverHadRun: (enabled: boolean) => (enabled ? true : null) }))

const project = vi.hoisted(() => ({ id: 'p1' }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))
// The cross-filter names a release from the top bar's list: none here (the map draws no release).
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: undefined }) }))

// jsdom has no canvas: the engine is mocked at the registry.
const engine = vi.hoisted(() => {
  const listeners = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    isDisposed: () => false,
    on: vi.fn((name: string, handler: (params: unknown) => void) => listeners.set(name, handler)),
    off: vi.fn(),
  }
  return { instance, listeners, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('@/components/charts/engines/registry', () => ({ loadChartEngine: engine.load }))

import CoverageMapSection, {
  ALL_PROJECTS_REASON,
  COVERAGE_MAP_HEIGHT,
  SUITE_RULE_NOTE,
} from './CoverageMapSection'

const URL_MAP = '/api/v1/analytics/coverage-map'
const URL_ROWS = '/api/v1/analytics/chart-data/rows'
const SEP = CLASS_KEY_SEPARATOR
const HOSTILE = '<img src=x onerror="window.__xss=1">'

const META = (over: Partial<EnvelopeMeta> = {}): EnvelopeMeta => ({
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: [],
    window: { from: '2026-09-01', to: '2026-09-30', days: 30, timezone: 'UTC' },
  },
  totals: { matched_runs: 10, total_runs: 10, matched_executions: 400, total_executions: 400 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-30T09:00:00Z',
  as_of: '2026-09-30T09:00:00Z',
  ...({ definitions: { grain: 'execution_row', coverage: 'test_execution' } } as object),
  ...over,
})

const seen = (test_count: number, pass_rate: number | null = 90, executions = test_count * 4): TreeNodeStats => ({
  test_count,
  executions: pass_rate === null ? 0 : executions,
  pass_rate,
  flaky_count: 0,
  flaky_share: 0,
  last_executed_at: '2026-09-29T00:00:00Z',
  staleness_days: 1,
  recency: 'seen',
})
const never = (test_count: number): TreeNodeStats => ({
  test_count,
  executions: 0,
  pass_rate: null,
  flaky_count: 0,
  flaky_share: 0,
  last_executed_at: null,
  staleness_days: null,
  recency: 'never',
})
const node = (id: string, parent: string | null, label: string, stats: TreeNodeStats): TreeNode => ({
  id,
  parent_id: parent,
  label,
  value: stats.test_count,
  measure: stats.pass_rate,
  stats,
})

/** The wire's shape: the C3 keys at the top level with `meta` beside them (`with_meta`). */
const wire = (nodes: TreeNode[], meta: EnvelopeMeta = META()) => ({ kind: 'tree', nodes, meta })

const LEVEL1 = wire([
  node('all', null, 'All suites', seen(13)),
  node('s:payments', 'all', 'Payments', seen(9)),
  node('s:auth', 'all', HOSTILE, seen(3)),
  node('s:legacy', 'all', 'legacy', never(1)),
])
/** What level 1 draws by pass rate: each name, and its value under it (F-03). */
const LEVEL1_DRAWN = ['Payments\n90.0%', `${HOSTILE}\n90.0%`, 'legacy']
const LEVEL2 = wire([
  node('s:payments', null, 'Payments', seen(9)),
  node(`c:payments${SEP}tests/api/test_pay.py`, 's:payments', 'tests/api/test_pay.py', seen(6)),
  node(`c:payments${SEP}__none__`, 's:payments', '(ungrouped)', seen(3)),
])
const LEVEL3 = wire([
  node(`c:payments${SEP}tests/api/test_pay.py`, null, 'tests/api/test_pay.py', seen(6)),
  node('t:fp-1', `c:payments${SEP}tests/api/test_pay.py`, 'test_pay_ok', seen(1)),
  node('t:fp-2', `c:payments${SEP}tests/api/test_pay.py`, 'test_pay_refund', seen(1)),
])

/** Responses by `depth`; a function may hold a request open. */
let responses: Record<string, unknown>
let pending: Record<string, Promise<unknown> | undefined>

function mapCalls(): CatalogParams[] {
  return get.mock.calls.filter(([url]) => url === URL_MAP).map(([, config]) => (config as { params: CatalogParams }).params)
}
function rowsCalls(): CatalogParams[] {
  return get.mock.calls.filter(([url]) => url === URL_ROWS).map(([, config]) => (config as { params: CatalogParams }).params)
}

const where = { search: '' }
function LocationProbe() {
  const { search } = useLocation()
  useEffect(() => {
    where.search = search
  }, [search])
  return null
}

function renderSection({
  entry = '/coverage',
  days = 30,
  suiteFilter = null,
  suites = null,
}: { entry?: string; days?: number; suiteFilter?: string | string[] | null; suites?: PageSuiteTarget | null } = {}) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[entry]}>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>
          {/* The Coverage page's suite select (P2): "Filter page by this" is offered only with it. */}
          <PageSuiteTargetContext.Provider value={suites}>{children}</PageSuiteTargetContext.Provider>
          <LocationProbe />
        </ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<CoverageMapSection days={days} suiteFilter={suiteFilter} />, { wrapper })
}

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

const frame = () => document.querySelector('[data-catalogue-section="coverage-map"] [data-chart-frame]') as HTMLElement
const treemap = () => document.querySelector('[data-chart-keyboard="treemap"]') as HTMLElement | null
const crumbs = () => [...document.querySelectorAll('[data-coverage-breadcrumb] li')].map((li) => li.textContent)
const drillParams = () => new URLSearchParams(where.search).getAll('drill')
const lastOption = () => {
  const calls = engine.instance.setOption.mock.calls
  return calls[calls.length - 1][0] as { series: { data: { name: string; itemStyle: { color: string } }[] }[] }
}

async function drawn() {
  await waitFor(() => expect(treemap()).not.toBeNull())
  await waitFor(() => expect(document.querySelector('[data-chart-type="treemap"]')).toHaveAttribute('data-chart-status', 'ready'))
  return treemap() as HTMLElement
}

beforeEach(() => {
  flags.values = { viz_chart_data_api: true, viz_advanced_charts: true }
  project.id = 'p1'
  responses = { '1': LEVEL1, '2': LEVEL2, '3': LEVEL3 }
  pending = {}
  where.search = ''
  useMultiFiltersFlagStore.setState({ enabled: false, resolved: true })
  useScopeNoticeStore.getState().dismiss()
  get.mockReset()
  get.mockImplementation((url: string, config: { params: CatalogParams }) => {
    if (url === URL_MAP) {
      const depth = String(config.params.depth)
      if (pending[depth]) return pending[depth]
      const payload = responses[depth]
      if (payload) return Promise.resolve({ data: payload, headers: { 'x-request-id': 'req-1' } })
    }
    return Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } }))
  })
  engine.load.mockReset()
  engine.load.mockResolvedValue({ init: engine.init })
  engine.instance.setOption.mockClear()
  engine.instance.dispatchAction.mockClear()
  engine.listeners.clear()
})

afterEach(() => {
  __resetChartConcurrency()
})

describe('CoverageMapSection: the flag matrix', () => {
  it.each([
    ['every flag off', {}],
    ['only viz_chart_data_api', { viz_chart_data_api: true }],
    ['only viz_advanced_charts', { viz_advanced_charts: true }],
  ])('%s: renders nothing and asks nothing', async (_name, values) => {
    flags.values = values
    const { container } = renderSection()
    await settle()
    expect(container.querySelector('[data-catalogue-section]')).toBeNull()
    expect(get).not.toHaveBeenCalled()
    expect(engine.load).not.toHaveBeenCalled()
  })

  it('both flags: one level-1 request in the page scope, the frame titled and honestly captioned', async () => {
    renderSection({ days: 30 })
    await drawn()
    expect(mapCalls()).toEqual([{ depth: 1, project_id: 'p1', days: 30 }])
    expect(within(frame()).getByRole('heading', { name: COVERAGE_MAP_TITLE })).toBeInTheDocument()
    expect(within(frame()).getByText(COVERAGE_MAP_CAPTION)).toBeInTheDocument()
    expect(within(frame()).getByText(SUITE_RULE_NOTE)).toBeInTheDocument()
    expect(within(frame()).getByText('Counted per test execution.')).toBeInTheDocument()
    expect(lastOption().series[0].data.map((d) => d.name)).toEqual(LEVEL1_DRAWN)
    expect(crumbs()).toEqual(['All suites'])
    expect(document.querySelector('[data-coverage-breadcrumb] [aria-current="page"]')).toHaveTextContent('All suites')
  })

  it('clamps the window to 90 days on the wire and passes the page suite scope', async () => {
    renderSection({ days: 365, suiteFilter: 'Payments' })
    await drawn()
    expect(mapCalls()).toEqual([{ depth: 1, project_id: 'p1', days: 90, suite_name: 'Payments' }])
  })

  it('All Projects: says the map is per project, and asks nothing', async () => {
    project.id = '__ALL__'
    renderSection()
    await settle()
    expect(within(frame()).getByText(new RegExp(ALL_PROJECTS_REASON))).toBeInTheDocument()
    expect(mapCalls()).toEqual([])
  })
})

describe('CoverageMapSection: level by level', () => {
  it('Enter on a suite drills: a history push to drill=suite~<KEY>, one depth-2 request, the breadcrumb, focus on the map', async () => {
    renderSection()
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'Enter' })
    await waitFor(() => expect(drillParams()).toEqual(['suite~payments']))
    await waitFor(() => expect(mapCalls()).toContainEqual({ depth: 2, suite: 'payments', project_id: 'p1', days: 30 }))
    await waitFor(() => expect(lastOption().series[0].data.map((d) => d.name)).toEqual(['test_pay.py\n90.0%', '(ungrouped)\n90.0%']))
    expect(crumbs()).toEqual(['All suites', 'Payments'])
    // The root is a link back to level 1 that keeps every other key.
    expect(screen.getByRole('link', { name: 'All suites' })).toHaveAttribute('href', '/coverage')
    await waitFor(() => expect(document.activeElement).toBe(treemap()))
  })

  it('never reads the previous level as the new one: while level 2 loads, the frame is loading', async () => {
    let release: (value: unknown) => void = () => {}
    pending['2'] = new Promise((resolve) => {
      release = resolve
    })
    renderSection()
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'Enter' })
    await waitFor(() => expect(mapCalls()).toHaveLength(2))
    await settle()
    expect(frame()).toHaveAttribute('data-chart-state', 'loading')
    expect(treemap()).toBeNull()
    await act(async () => {
      release({ data: LEVEL2, headers: {} })
    })
    await waitFor(() => expect(frame()).toHaveAttribute('data-chart-state', 'ready'))
  })

  it('a shared link opens the same level: depth 3 asks with both KEYS; the breadcrumb falls back to the key it has', async () => {
    const entry = `/coverage?days=30&drill=${encodeURIComponent('suite~payments')}&drill=${encodeURIComponent(`class~tests/api/test_pay.py`)}`
    renderSection({ entry })
    await drawn()
    expect(mapCalls()).toEqual([{ depth: 3, suite: 'payments', class_key: 'tests/api/test_pay.py', project_id: 'p1', days: 30 }])
    // The suite was never on screen in this visit: its key; the class: the root's label.
    expect(crumbs()).toEqual(['All suites', 'payments', 'tests/api/test_pay.py'])
    expect(screen.getByRole('link', { name: 'payments' }).getAttribute('href')).toBe('/coverage?days=30&drill=suite%7Epayments')
  })

  it('Backspace at the top level is not taken and writes nothing', async () => {
    renderSection({ entry: '/coverage?days=30' })
    const group = await drawn()
    group.focus()
    await settle()
    const before = where.search
    const event = new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true })
    group.dispatchEvent(event)
    await settle()
    expect(event.defaultPrevented).toBe(false)
    expect(where.search).toBe(before)
  })

  it('Backspace goes up one level (a push), and focus lands back on the map', async () => {
    renderSection({ entry: '/coverage?drill=suite~payments' })
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'Backspace' })
    await waitFor(() => expect(drillParams()).toEqual([]))
    await waitFor(() => expect(lastOption().series[0].data.map((d) => d.name)).toEqual(LEVEL1_DRAWN))
    await waitFor(() => expect(document.activeElement).toBe(treemap()))
  })

  it('a breadcrumb link goes up and moves focus to the map once that level is drawn (EPIC AC)', async () => {
    renderSection({ entry: '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py' })
    await drawn()
    fireEvent.click(screen.getByRole('link', { name: 'All suites' }))
    await waitFor(() => expect(drillParams()).toEqual([]))
    await waitFor(() => expect(mapCalls()).toContainEqual({ depth: 1, project_id: 'p1', days: 30 }))
    await waitFor(() => expect(document.activeElement).toBe(treemap()))
    expect(crumbs()).toEqual(['All suites'])
  })

  it('a test opens its executions in the rows panel: one suite in scope, the test as the selector', async () => {
    renderSection({ entry: '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py' })
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'Enter' })
    await waitFor(() => expect(new URLSearchParams(where.search).getAll('rows')).toEqual(['by~coverage-map', 'test~fp-1']))
    await waitFor(() =>
      expect(rowsCalls()).toEqual([
        {
          project_id: 'p1',
          days: 30,
          suite_name: 'payments',
          metric: 'executions',
          group_by: ['test'],
          bucket_test: 'fp-1',
          page: 1,
          size: 50,
        },
      ]),
    )
    expect(screen.getByText('Executions in test_pay_ok')).toBeInTheDocument()
    // The drill path is untouched by opening the panel.
    expect(drillParams()).toEqual(['suite~payments', 'class~tests/api/test_pay.py'])
  })

  it('names a link-opened test panel by the label the level taught it', async () => {
    renderSection({ entry: '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py&rows=by~coverage-map&rows=test~fp-2' })
    await drawn()
    await waitFor(() => expect(screen.getByText('Executions in test_pay_refund')).toBeInTheDocument())
  })

  it('leaves another host\'s rows selection alone (no panel of its own)', async () => {
    renderSection({ entry: '/coverage?rows=by~heatmap-suite_environment&rows=suite~payments&rows=environment~ci' })
    await drawn()
    expect(rowsCalls()).toEqual([])
    expect(screen.queryByText(/^Executions in/)).toBeNull()
  })

  it("a test selection another host owns is not this map's panel, even in the map's own shape (FK4-1)", async () => {
    renderSection({ entry: '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py&rows=by~scatter-suite&rows=test~fp-2' })
    await drawn()
    expect(rowsCalls()).toEqual([])
    expect(screen.queryByText(/^Executions in/)).toBeNull()
  })

  it('says when a link named a level it could not open', async () => {
    renderSection({ entry: '/coverage?drill=nonsense' })
    await drawn()
    expect(within(frame()).getByText(DRILL_DROP_WORDS.invalid)).toBeInTheDocument()
    expect(mapCalls()).toEqual([{ depth: 1, project_id: 'p1', days: 30 }])
  })

  it('opens a rows panel only for EXACTLY one test selector: a test with a status is not its shape (R1B-5)', async () => {
    const base = '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py&rows=by~coverage-map'
    renderSection({ entry: `${base}&rows=test~fp-1&rows=status~failed` })
    await drawn()
    await settle()
    expect(rowsCalls()).toEqual([])
    expect(screen.queryByText(/^Executions in/)).toBeNull()
  })

  it('a status selector alone is not the map\'s panel either (R1B-5)', async () => {
    renderSection({ entry: '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py&rows=by~coverage-map&rows=status~failed' })
    await drawn()
    await settle()
    expect(rowsCalls()).toEqual([])
  })

  it("a link's suite outside the page's suite filter is cut, and said: the map opens above it (R1B-6)", async () => {
    renderSection({ entry: '/coverage?drill=suite~payments&drill=class~tests/api/test_pay.py', suiteFilter: 'Auth' })
    await drawn()
    // Never `suite_name=Auth&suite=payments`: the level the filter does not allow is not asked for.
    expect(mapCalls()).toEqual([{ depth: 1, project_id: 'p1', days: 30, suite_name: 'Auth' }])
    expect(within(frame()).getByText(COVERAGE_DROP_WORDS.outOfScope)).toBeInTheDocument()
    expect(crumbs()).toEqual(['All suites'])
  })

  it('a suite the filter allows (any case) opens as linked, with nothing said', async () => {
    renderSection({ entry: '/coverage?drill=suite~payments', suiteFilter: ['PAYMENTS', 'auth'] })
    await drawn()
    expect(mapCalls()).toEqual([{ depth: 2, suite: 'payments', project_id: 'p1', days: 30, suite_name: ['PAYMENTS', 'auth'] }])
    expect(document.querySelector('[data-drill-dropped]')).toBeNull()
  })

  it('a level the map does not drill by is cut and said, and the next drill starts from what was applied (R1B-6)', async () => {
    renderSection({ entry: '/coverage?drill=class~X' })
    const group = await drawn()
    expect(mapCalls()).toEqual([{ depth: 1, project_id: 'p1', days: 30 }])
    expect(within(frame()).getByText(COVERAGE_DROP_WORDS.unsupported)).toBeInTheDocument()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'Enter' })
    // Not `class~X&suite~payments` (which reads as the top level again): the cut level is replaced.
    await waitFor(() => expect(drillParams()).toEqual(['suite~payments']))
    await waitFor(() => expect(mapCalls()).toContainEqual({ depth: 2, suite: 'payments', project_id: 'p1', days: 30 }))
  })

  it("with viz_multi_filters on, the cut goes to the page's notice (the filter bar), not the frame (R1B-6)", async () => {
    useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
    renderSection({ entry: '/coverage?drill=suite~payments', suiteFilter: 'Auth' })
    await drawn()
    await waitFor(() =>
      expect(useScopeNoticeStore.getState().notices).toEqual([
        { dimension: 'drill', values: [COVERAGE_DROP_WORDS.outOfScope], reason: COVERAGE_DRILL_NOTICE_REASON },
      ]),
    )
    expect(within(frame()).queryByText(COVERAGE_DROP_WORDS.outOfScope)).toBeNull()
  })

  it('with viz_multi_filters on, a link the parser cut is named on the page notice too', async () => {
    useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
    renderSection({ entry: '/coverage?drill=nonsense' })
    await drawn()
    await waitFor(() =>
      expect(useScopeNoticeStore.getState().notices).toEqual([
        { dimension: 'drill', values: [DRILL_DROP_WORDS.invalid], reason: COVERAGE_DRILL_NOTICE_REASON },
      ]),
    )
    expect(within(frame()).queryByText(DRILL_DROP_WORDS.invalid)).toBeNull()
  })
})

describe('CoverageMapSection: filter the page by a suite (VIZ-603, P2)', () => {
  const setSuite = vi.fn()
  const suites = (selected = ''): PageSuiteTarget => ({ selected, options: ['Checkout', 'Payments'], set: setSuite })
  const intents = () => [...document.querySelectorAll('[data-coverage-readout] [data-mark-intent]')].map((b) => b.getAttribute('data-mark-intent'))
  beforeEach(() => setSuite.mockReset())

  it('no page suite select: a suite node offers its drill only, and Shift+Enter drills', async () => {
    renderSection()
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(intents()).toEqual(['drill'])
    fireEvent.keyDown(group, { key: 'Enter', shiftKey: true })
    await waitFor(() => expect(drillParams()).toEqual(['suite~payments']))
    expect(setSuite).not.toHaveBeenCalled()
  })

  it('with one: a suite node also offers "Filter page by this"; Shift+Enter sets the select as it spells the suite, and does not drill', async () => {
    renderSection({ suites: suites('Checkout') })
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(intents()).toEqual(['drill', 'filter'])
    expect(screen.getByRole('button', { name: 'Filter page by this' })).toBeInTheDocument()
    fireEvent.keyDown(group, { key: 'Enter', shiftKey: true })
    expect(setSuite.mock.calls).toEqual([['Payments']])
    await settle()
    expect(drillParams()).toEqual([])
    expect(mapCalls()).toHaveLength(1)
  })

  it('the readout button does the same; a Shift-click on the node too', async () => {
    renderSection({ suites: suites() })
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.click(screen.getByRole('button', { name: 'Filter page by this' }))
    expect(setSuite.mock.calls).toEqual([['Payments']])
    const click = engine.listeners.get('click') as (params: unknown) => void
    act(() => click({ dataIndex: 1, event: { event: { shiftKey: true } } }))
    expect(setSuite.mock.calls).toEqual([['Payments'], ['Payments']])
    await settle()
    expect(drillParams()).toEqual([])
  })

  it('a class (level 2) or a test (level 3) is never a page filter', async () => {
    renderSection({ entry: `/coverage?drill=${encodeURIComponent('suite~payments')}`, suites: suites() })
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(intents()).toEqual(['drill'])
    fireEvent.keyDown(group, { key: 'Enter', shiftKey: true })
    expect(setSuite).not.toHaveBeenCalled()
  })
})

describe('CoverageMapSection: colour, truncation, failure', () => {
  it('re-colours by the chosen measure without a new request', async () => {
    renderSection()
    await drawn()
    expect(within(frame()).getByRole('list', { name: 'Pass rate: colour key' })).toBeInTheDocument()
    const options = engine.instance.setOption.mock.calls.length
    fireEvent.change(screen.getByRole('combobox', { name: /Colour by/ }), { target: { value: 'staleness' } })
    // A new option for the same nodes (jsdom resolves no colours, so the legend says which measure).
    await waitFor(() => expect(engine.instance.setOption.mock.calls.length).toBeGreaterThan(options))
    // The same nodes, each with its new value under the name (the never-run suite has none: its pattern says it).
    expect(lastOption().series[0].data.map((d) => d.name)).toEqual(['Payments\n1 day', `${HOSTILE}\n1 day`, 'legacy'])
    expect(within(frame()).getByRole('list', { name: 'Days since last run: colour key' })).toBeInTheDocument()
    // Unknown values are ignored, not applied.
    fireEvent.change(screen.getByRole('combobox', { name: /Colour by/ }), { target: { value: 'constructor' } })
    expect(within(frame()).getByRole('list', { name: 'Days since last run: colour key' })).toBeInTheDocument()
    expect(mapCalls()).toHaveLength(1)
  })

  it('states the Other fold itself (never "Showing top N of M", which would count the root)', async () => {
    responses['1'] = wire(
      [
        node('all', null, 'All suites', seen(820)),
        node('s:a', 'all', 'a', seen(10)),
        node('s:b', 'all', 'b', seen(8)),
        node('other:all', 'all', 'Other (810)', { ...seen(802), pass_rate: null, executions: 900 }),
      ],
      META({ truncated: true, truncated_total: 812 }),
    )
    renderSection()
    await drawn()
    expect(within(frame()).getByText('Showing the 2 largest of 812 suites; the rest are combined in Other (810).')).toBeInTheDocument()
    expect(within(frame()).queryByText(/Showing top/)).toBeNull()
  })

  it('an error keeps the breadcrumb and offers Retry', async () => {
    responses['2'] = undefined as never
    renderSection({ entry: '/coverage?drill=suite~payments' })
    await waitFor(() => expect(frame()).toHaveAttribute('data-chart-state', 'error'))
    expect(crumbs()).toEqual(['All suites', 'payments'])
    expect(within(frame()).getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('an empty level is the frame\'s empty state, not an empty canvas', async () => {
    responses['1'] = wire([])
    renderSection()
    await waitFor(() => expect(frame().getAttribute('data-chart-state')).toMatch(/filtered-empty|never-had-data/))
    expect(treemap()).toBeNull()
  })

  it('an empty level says what it has none of, in one sentence, in a short band (F-14)', async () => {
    responses['2'] = wire([])
    renderSection({ entry: '/coverage?drill=suite~payments' })
    await waitFor(() => expect(frame()).toHaveAttribute('data-chart-state', 'filtered-empty'))
    const body = frame().querySelector('[data-chart-body]') as HTMLElement
    expect(body).toHaveTextContent(coverageEmptyText({ depth: 2, suite: 'payments', classKey: null }))
    // Not the filters' sentence over a footer that counts the scope's executions, and no "Clear filters".
    expect(within(frame()).queryByText(/No data matches/)).toBeNull()
    expect(within(frame()).queryByRole('button', { name: /Clear filters/ })).toBeNull()
    expect(body.style.minHeight).toBe(`${COVERAGE_MAP_EMPTY_HEIGHT}px`)
    expect(COVERAGE_MAP_EMPTY_HEIGHT).toBeLessThan(COVERAGE_MAP_HEIGHT / 2)
  })

  it('a drawn level keeps the full canvas height', async () => {
    renderSection()
    await drawn()
    expect((frame().querySelector('[data-chart-body]') as HTMLElement).style.minHeight).toBe(`${COVERAGE_MAP_HEIGHT}px`)
  })

  it('hostile labels stay text in the focused row, the table and the breadcrumb', async () => {
    renderSection()
    const group = await drawn()
    group.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(document.querySelector('[data-coverage-focused]')?.textContent).toBe(HOSTILE)
    fireEvent.click(within(frame()).getByRole('button', { name: /View as table/ }))
    expect(within(frame()).getAllByText(HOSTILE).length).toBeGreaterThan(0)
    expect(document.querySelector('img')).toBeNull()
  })
})
