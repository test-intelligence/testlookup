/**
 * The drill ladder (VIZ-602 / 603) with only the network, the flag lookups, the
 * existence probe, the project and release scope, and Recharts' drawing
 * mocked: the seam, the scope builder, the chart pipeline, the bar chart and
 * its keyboard cursor, the drill URL, the breadcrumb, the cross-filter and the
 * scope stores are the real ones. Recharts is a stand-in that draws one
 * clickable rectangle per bar and segment and calls `<Bar onClick>` as Recharts
 * does. The rows panel is a stub that records what it was handed (its own
 * behaviour is FK0's, tested there).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import axe from 'axe-core'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import { __resetChartConcurrency } from '@/services/chartApi'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useScopeNoticeStore } from '@/store/scopeNoticeStore'
import { useSuiteStore } from '@/store/suiteStore'
import { DRILL_DROP_WORDS } from '@/hooks/useDrillPath'
import { PageSuiteTargetContext, type PageSuiteTarget } from '@/hooks/pageSuiteTarget'
import type { RowsPanelProps } from './RowsPanel.model'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => flags.values[key] ?? false,
  useFeatureFlagStatus: (key: string) => flags.values[key],
}))

const probe = vi.hoisted(() => ({ enabled: [] as boolean[] }))
vi.mock('./useEverHadRun', () => ({
  useEverHadRun: (enabled: boolean) => {
    probe.enabled.push(enabled)
    return enabled ? true : null
  },
}))

const project = vi.hoisted(() => ({ id: 'p1' }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))
// The cross-filter names a release from the top bar's list: none here (the ladder draws no release).
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: undefined }) }))

const plot = vi.hoisted(() => ({ data: [] as Record<string, unknown>[] }))
vi.mock('recharts', () => ({
  usePlotArea: () => undefined,
  useXAxisScale: () => (value: unknown) => value as number,
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  BarChart: ({ children, data }: { children?: ReactNode; data?: Record<string, unknown>[] }) => {
    plot.data = data ?? []
    return (
      <svg data-chart="bar">
        <g>{children}</g>
      </svg>
    )
  },
  Bar: (props: { dataKey?: string; children?: ReactNode; onClick?: (...args: unknown[]) => void }) => (
    <g data-bar={props.dataKey}>
      {plot.data.map((row, index) => (
        <rect key={index} data-rect={`${props.dataKey}:${String(row.key)}`} onClick={(event) => props.onClick?.({ payload: row }, index, event)} />
      ))}
      {props.children}
    </g>
  ),
  Cell: () => null,
  LabelList: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  ReferenceLine: () => null,
  Tooltip: () => null,
  Legend: () => null,
}))

const panels = vi.hoisted(() => ({ props: [] as unknown[] }))
vi.mock('./RowsPanel', () => {
  const RowsPanel = (props: RowsPanelProps) => {
    panels.props.push(props)
    return props.selectors.length ? (
      <div data-testid="rows-panel" data-title={props.title}>
        <button type="button" onClick={props.onClose}>
          Close rows
        </button>
      </div>
    ) : null
  }
  return { RowsPanel, default: RowsPanel }
})

import FailuresDrill, { DRILL_SECTION_MIN_HEIGHT } from './FailuresDrill'
import { DRILL_NOTICE_REASON, DRILL_TITLE, LADDER_DROP_WORDS, LADDER_TOP_N, ROOT_CRUMB } from './FailuresDrill.model'

const URL = '/api/v1/analytics/chart-data'
const HOSTILE = '<img src=x onerror="window.__xss=1">'

const meta = (suites: string[] = []): EnvelopeMeta => ({
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'p' }],
    releases: [],
    suites,
    window: { from: '2026-07-03', to: '2026-10-01', days: 90, timezone: 'UTC' },
  },
  totals: { matched_runs: 30, total_runs: 30, matched_executions: 900, total_executions: 900 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-10-01T09:00:00Z',
  as_of: '2026-10-01T09:00:00Z',
})

/** L0: executions by suite x status (keys lower-cased, labels as spelled). */
const L0: SeriesChart = {
  kind: 'series',
  dimensions: ['suite', 'status'],
  x_type: 'category',
  x_labels: { payments: 'Payments', [HOSTILE.toLowerCase()]: HOSTILE },
  series: [
    { key: 'passed', label: 'passed', points: [{ x: 'payments', y: 30, n: 30 }, { x: HOSTILE.toLowerCase(), y: 5, n: 5 }] },
    { key: 'failed', label: 'failed', points: [{ x: 'payments', y: 6, n: 6 }, { x: HOSTILE.toLowerCase(), y: 1, n: 1 }] },
  ],
}

/** A suite's statuses. */
const STATUSES: SeriesChart = {
  kind: 'series',
  dimensions: ['status'],
  x_type: 'category',
  series: [{ key: 'value', label: 'Executions', points: [{ x: 'passed', y: 30, n: 30 }, { x: 'failed', y: 6, n: 6 }] }],
}

/** The leaf: failed executions by test, in one suite. */
const TESTS: SeriesChart = {
  kind: 'series',
  dimensions: ['test'],
  x_type: 'category',
  x_labels: { 'fp-1': 'test_pay', 'fp-2': 'test_refund' },
  series: [{ key: 'value', label: 'Failed', points: [{ x: 'fp-1', y: 4, n: 20 }, { x: 'fp-2', y: 2, n: 9 }] }],
}

/** A status's suites. */
const SUITES_OF_STATUS: SeriesChart = {
  kind: 'series',
  dimensions: ['suite'],
  x_type: 'category',
  x_labels: { payments: 'Payments' },
  series: [{ key: 'value', label: 'Failed', points: [{ x: 'payments', y: 6, n: 36 }, { x: 'cart', y: 2, n: 12 }] }],
}

const wire = (chart: SeriesChart, m: EnvelopeMeta) => ({ ...chart, meta: m })

/** Answers each level's question; `hold` keeps a level's request pending. */
const server = { hold: null as null | ((p: CatalogParams) => boolean) }
function respond(params: CatalogParams) {
  if (server.hold?.(params)) return new Promise(() => {})
  const groupBy = ([] as string[]).concat((params.group_by as string[] | string) ?? [])
  const suites = params.suite_name === undefined ? [] : ([] as string[]).concat(params.suite_name as string).map((s) => s.toLowerCase())
  const body =
    groupBy.join(',') === 'suite,status'
      ? wire(L0, meta(suites))
      : groupBy.join(',') === 'status'
        ? wire(STATUSES, meta(suites))
        : groupBy.join(',') === 'test'
          ? wire(TESTS, meta(suites))
          : wire(SUITES_OF_STATUS, meta(suites))
  return Promise.resolve({ data: body, headers: { 'x-request-id': 'req-1' } })
}

function chartCalls(): CatalogParams[] {
  return get.mock.calls.filter(([url]) => url === URL).map(([, config]) => (config as { params: CatalogParams }).params)
}

/** The most recent chart-data request's parameters. */
function lastCall(): CatalogParams | undefined {
  const calls = chartCalls()
  return calls[calls.length - 1]
}

const nav = { go: null as null | ((delta: number) => void) }
function Location() {
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    nav.go = (delta) => navigate(delta)
  }, [navigate])
  return <output data-testid="location">{location.search}</output>
}

/**
 * The ladder in its page. `suites`: the page's suite select (`PageSuiteTargetContext`, as the
 * Failures page provides it): the page filter is offered only with one.
 */
function renderDrill({
  url = '/failures',
  days = 30,
  suiteFilter = null as string | string[] | null,
  suites = null as PageSuiteTarget | null,
} = {}) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>
          <PageSuiteTargetContext.Provider value={suites}>{children}</PageSuiteTargetContext.Provider>
          <Location />
        </ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<FailuresDrill days={days} suiteFilter={suiteFilter} />, { wrapper })
}

/** The Failures page's suite select, observed: what "Filter page by this" writes. */
const setSuite = vi.fn()
const pageSuites = (selected = ''): PageSuiteTarget => ({ selected, options: ['Checkout', 'Payments'], set: setSuite })

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

const section = () => document.querySelector('[data-catalogue-section="failures-drill"]') as HTMLElement
const drillParams = () => new URLSearchParams(screen.getByTestId('location').textContent ?? '').getAll('drill')
const lastPanel = () => panels.props[panels.props.length - 1] as RowsPanelProps
const surface = () => section().querySelector('[data-chart-cursor]') as HTMLElement
const rect = (id: string) => section().querySelector(`[data-rect="${CSS.escape(id)}"]`) as Element
const breadcrumb = () => within(section().querySelector('[data-drill-breadcrumb]') as HTMLElement).getByRole('navigation', { name: 'Breadcrumb' })

/** Waits until `level` is drawn (its frame ready and its bars in the plot). */
async function level(kind: string) {
  await waitFor(() => expect(section()?.getAttribute('data-drill-level')).toBe(kind))
  await waitFor(() => expect(section().querySelector('[data-chart-frame]')).toHaveAttribute('data-chart-state', 'ready'))
}

beforeEach(() => {
  flags.values = { viz_chart_data_api: true, viz_advanced_charts: true }
  probe.enabled = []
  project.id = 'p1'
  panels.props = []
  server.hold = null
  get.mockReset()
  get.mockImplementation((url: string, config: { params: CatalogParams }) =>
    url === URL ? respond(config.params) : Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } })),
  )
  useMultiFiltersFlagStore.setState({ enabled: false, resolved: true })
  useSuiteStore.getState().clearSuites()
  useScopeNoticeStore.getState().dismiss()
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => {
  __resetChartConcurrency()
})

describe('FailuresDrill placeholder (R2-B F-15)', () => {
  /** An observer that never reports: the section stays its placeholder. */
  class NeverNear {
    observe() {}
    disconnect() {}
    unobserve() {}
    takeRecords() {
      return []
    }
  }
  afterEach(() => vi.unstubAllGlobals())

  it('holds the height the ladder draws at 1280 (372 px measured), not 480: nothing below moves when it mounts', () => {
    vi.stubGlobal('IntersectionObserver', NeverNear)
    const { container } = renderDrill()
    const placeholder = container.querySelector<HTMLElement>('[data-lazy-section="failures-drill"]')
    expect(placeholder?.style.minHeight).toBe('372px')
    expect(DRILL_SECTION_MIN_HEIGHT).toBe(372)
  })
})

describe('FailuresDrill (VIZ-602 / 603)', () => {
  it.each([
    ['both off', {}],
    ['only the catalogue flag', { viz_chart_data_api: true }],
    ['only the advanced flag', { viz_advanced_charts: true }],
  ])('%s: nothing is rendered and nothing is requested', async (_name, values) => {
    flags.values = values
    const { container } = renderDrill({ url: '/failures?drill=suite~payments' })
    await settle()
    expect(container.querySelector('[data-catalogue-section]')).toBeNull()
    expect(container.querySelector('[data-lazy-section]')).toBeNull()
    expect(get).not.toHaveBeenCalled()
    expect(probe.enabled).toEqual([])
  })

  it('L0 "Results by suite": one request, executions by suite x status, the window clamped to 90', async () => {
    renderDrill({ days: 365 })
    await level('suites')
    expect(chartCalls()).toEqual([{ metric: 'executions', group_by: ['suite', 'status'], project_id: 'p1', days: 90 }])
    expect(within(section()).getByRole('heading', { name: DRILL_TITLE })).toBeInTheDocument()
    expect(within(breadcrumb()).getByText(ROOT_CRUMB)).toHaveAttribute('aria-current', 'page')
    // F-20: the ladder keeps room for its last value tick ("1,000" whole at 375 px).
    expect(section().querySelector('[data-bar-chart]')).toHaveAttribute('data-bar-fit-end', 'true')
  })

  it('the AC: activating the "payments" failed segment re-groups to failed tests in payments, with the breadcrumb', async () => {
    renderDrill()
    await level('suites')
    fireEvent.click(rect('failed:payments'))
    expect(drillParams()).toEqual(['suite~payments', 'status~failed'])
    await level('tests')
    expect(lastCall()).toEqual({ metric: 'failed', group_by: ['test'], top_n: LADDER_TOP_N, project_id: 'p1', days: 30, suite_name: 'payments' })
    expect(within(section()).getByRole('heading', { name: 'Failed tests in Payments' })).toBeInTheDocument()
    const crumbs = within(breadcrumb()).getAllByRole('listitem').map((li) => li.textContent)
    expect(crumbs).toEqual([ROOT_CRUMB, 'Payments', 'failed'])
    // The label was learned from the level the reader came from: no read just for a name.
    expect(chartCalls()).toHaveLength(2)
    // Focus moved to the new chart.
    await waitFor(() => expect(document.activeElement).toBe(surface()))
  })

  it('F-10: a shared link to the leaf names the suite as the bars do ("Payments"), never by its key', async () => {
    renderDrill({ url: '/failures?drill=suite~payments&drill=status~failed' })
    await level('tests')
    await waitFor(() => expect(within(section()).getByRole('heading', { name: 'Failed tests in Payments' })).toBeInTheDocument())
    expect(within(breadcrumb()).getAllByRole('listitem').map((li) => li.textContent)).toEqual([ROOT_CRUMB, 'Payments', 'failed'])
    // The KEY stays in the URL and the level's request; the name comes from the root level's own read
    // (the same request Back would make, so Back to the root then reads nothing new).
    expect(drillParams()).toEqual(['suite~payments', 'status~failed'])
    expect(chartCalls()).toContainEqual({ metric: 'failed', group_by: ['test'], top_n: LADDER_TOP_N, project_id: 'p1', days: 30, suite_name: 'payments' })
    expect(chartCalls()).toContainEqual({ metric: 'executions', group_by: ['suite', 'status'], project_id: 'p1', days: 30 })
    expect(chartCalls()).toHaveLength(2)
    // The rows panel's title too.
    const chart = surface()
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.click(within(section().querySelector('[data-mark-actions]') as HTMLElement).getByRole('button', { name: 'View rows' }))
    await waitFor(() => expect(lastPanel().title).toBe('test_pay, failed'))
  })

  it('F-10: a suite the root level does not name keeps its key (nothing invented), and asks for it once', async () => {
    renderDrill({ url: '/failures?drill=suite~cart' })
    await level('statuses')
    await settle()
    expect(within(section()).getByRole('heading', { name: 'Results in cart by status' })).toBeInTheDocument()
    expect(chartCalls().filter((p) => (p.group_by as string[]).join(',') === 'suite,status')).toHaveLength(1)
  })

  it('at the leaf a test offers "View rows" only; it opens the panel on [test, status], reconciling with the bar', async () => {
    renderDrill({ url: '/failures?drill=suite~payments&drill=status~failed' })
    await level('tests')
    const chart = surface()
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    const actions = section().querySelector('[data-mark-actions]') as HTMLElement
    expect(within(actions).getAllByRole('button').map((b) => b.textContent)).toEqual(['View rows'])
    fireEvent.keyDown(chart, { key: 'Enter' })
    expect(new URLSearchParams(screen.getByTestId('location').textContent ?? '').getAll('rows')).toEqual(['by~failures-drill', 'test~fp-1', 'status~failed'])
    await waitFor(() => expect(screen.getByTestId('rows-panel')).toBeInTheDocument())
    expect(lastPanel()).toMatchObject({
      selectors: [
        { dimension: 'test', value: 'fp-1' },
        { dimension: 'status', value: 'failed' },
      ],
      chart: { metric: 'failed', groupBy: ['test', 'status'] },
      scope: { project_id: 'p1', days: 30, suite_name: 'payments' },
      title: 'test_pay, failed',
      expected: { y: 4, n: 20, asOf: '2026-10-01T09:00:00Z' },
    })
    // The drill path survives opening the panel (M-602d: the rows keep the drill's scope).
    expect(drillParams()).toEqual(['suite~payments', 'status~failed'])
  })

  it('"View rows" at L0 opens a segment’s rows in the L0 chart’s own terms', async () => {
    renderDrill()
    await level('suites')
    const chart = surface()
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.click(within(section().querySelector('[data-mark-actions]') as HTMLElement).getByRole('button', { name: 'View rows' }))
    await waitFor(() => expect(screen.getByTestId('rows-panel')).toBeInTheDocument())
    expect(lastPanel()).toMatchObject({
      selectors: [{ dimension: 'suite', value: 'payments' }],
      chart: { metric: 'executions', groupBy: ['suite', 'status'] },
      scope: { project_id: 'p1', days: 30 },
      title: 'Payments',
      expected: { y: 36, n: 36 },
    })
    expect(lastPanel().scope).not.toHaveProperty('suite_name')
    fireEvent.click(screen.getByRole('button', { name: 'Close rows' }))
    await waitFor(() => expect(screen.queryByTestId('rows-panel')).toBeNull())
  })

  it('keyboard only: Enter on a bar drills into its suite, then Enter on a status reaches the leaf', async () => {
    renderDrill()
    await level('suites')
    let chart = surface()
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.keyDown(chart, { key: 'Enter' })
    expect(drillParams()).toEqual(['suite~payments'])
    await level('statuses')
    expect(lastCall()).toEqual({ metric: 'executions', group_by: ['status'], project_id: 'p1', days: 30, suite_name: 'payments' })
    expect(within(section()).getByRole('heading', { name: 'Results in Payments by status' })).toBeInTheDocument()
    await waitFor(() => expect(document.activeElement).toBe(surface()))
    chart = surface()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.keyDown(chart, { key: 'Enter' })
    expect(drillParams()).toEqual(['suite~payments', 'status~failed'])
    await level('tests')
  })

  it('a drill is a history PUSH: Back returns to the level above (M-602a), Forward goes down again', async () => {
    renderDrill()
    await level('suites')
    fireEvent.click(rect('failed:payments'))
    await level('tests')
    act(() => nav.go?.(-1))
    expect(drillParams()).toEqual([])
    await level('suites')
    act(() => nav.go?.(1))
    expect(drillParams()).toEqual(['suite~payments', 'status~failed'])
    await level('tests')
  })

  it('the breadcrumb root clears the drill and moves focus to the chart (M-602b)', async () => {
    renderDrill({ url: '/failures?tab=x&drill=suite~payments&drill=status~failed' })
    await level('tests')
    fireEvent.click(within(breadcrumb()).getByRole('link', { name: ROOT_CRUMB }))
    expect(drillParams()).toEqual([])
    // Other keys stay as they were.
    expect(screen.getByTestId('location').textContent).toBe('?tab=x')
    await level('suites')
    await waitFor(() => expect(document.activeElement).toBe(surface()))
  })

  it('a middle crumb goes up one level', async () => {
    renderDrill({ url: '/failures?drill=suite~payments&drill=status~failed' })
    await level('tests')
    // The crumb reads as the bars do (F-10), once the root level's read has named the suite.
    fireEvent.click(await within(breadcrumb()).findByRole('link', { name: 'Payments' }))
    expect(drillParams()).toEqual(['suite~payments'])
    await level('statuses')
  })

  it('a status-first path: failed results by suite, then a suite reaches the same leaf', async () => {
    renderDrill({ url: '/failures?drill=status~failed' })
    await level('status-suites')
    expect(lastCall()).toEqual({ metric: 'failed', group_by: ['suite'], project_id: 'p1', days: 30 })
    fireEvent.click(rect('value:cart'))
    expect(drillParams()).toEqual(['status~failed', 'suite~cart'])
    await level('tests')
    expect(lastCall()).toMatchObject({ metric: 'failed', group_by: ['test'], suite_name: 'cart' })
  })

  it('while the next level loads, the previous level’s bars are not read as this level’s', async () => {
    renderDrill()
    await level('suites')
    server.hold = (p) => p.group_by !== undefined && String(p.group_by) === 'status'
    const chart = surface()
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    fireEvent.keyDown(chart, { key: 'Enter' })
    await waitFor(() => expect(section().getAttribute('data-drill-level')).toBe('statuses'))
    await settle()
    expect(section().querySelector('[data-chart-frame]')).toHaveAttribute('data-chart-state', 'loading')
    expect(section().querySelector('[data-rect]')).toBeNull()
    expect(drillParams()).toEqual(['suite~payments'])
  })

  it('a hostile suite name is text in the bars’ readout, the breadcrumb and the title', async () => {
    renderDrill()
    await level('suites')
    fireEvent.click(rect(`passed:${HOSTILE.toLowerCase()}`))
    await level('tests')
    expect(within(breadcrumb()).getByText(HOSTILE)).toBeInTheDocument()
    expect(within(section()).getByRole('heading', { name: `Passed tests in ${HOSTILE}` })).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('a shared link opens its level directly, and a broken one opens the valid part and says why', async () => {
    renderDrill({ url: '/failures?drill=suite~payments&drill=nonsense' })
    await level('statuses')
    expect(section().querySelector('[data-drill-notice]')?.textContent).toBe(DRILL_DROP_WORDS.invalid)
  })

  it('a suite outside the page’s suite filter is cut and said; a drill from there replaces it', async () => {
    renderDrill({ url: '/failures?drill=status~failed&drill=suite~cart', suiteFilter: ['Payments'] })
    await level('status-suites')
    expect(section().querySelector('[data-drill-notice]')?.textContent).toBe(LADDER_DROP_WORDS.outOfScope)
    expect(lastCall()).toEqual({ metric: 'failed', group_by: ['suite'], project_id: 'p1', days: 30, suite_name: 'Payments' })
    fireEvent.click(rect('value:payments'))
    expect(drillParams()).toEqual(['status~failed', 'suite~payments'])
    await level('tests')
  })

  it('with the page filter on, a dropped level goes to the page’s notice instead', async () => {
    useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
    renderDrill({ url: '/failures?drill=suite~payments&drill=nonsense' })
    await level('statuses')
    expect(section().querySelector('[data-drill-notice]')).toBeNull()
    expect(useScopeNoticeStore.getState().notices).toEqual([{ dimension: 'drill', values: [DRILL_DROP_WORDS.invalid], reason: DRILL_NOTICE_REASON }])
  })

  it('open rows of another section’s shape (the scatter’s [test]) are not this section’s panel', async () => {
    renderDrill({ url: '/failures?rows=by~scatter-project&rows=test~fp-1' })
    await level('suites')
    expect(lastPanel().selectors).toEqual([])
    expect(screen.queryByTestId('rows-panel')).toBeNull()
  })

  it('a selection in this level’s shape that another host owns opens nothing here (FK4-1)', async () => {
    renderDrill({ url: '/failures?rows=by~failures-groups&rows=suite~payments' })
    await level('suites')
    expect(lastPanel().selectors).toEqual([])
  })

  it('this section’s own pasted selection reopens its panel', async () => {
    renderDrill({ url: '/failures?rows=by~failures-drill&rows=suite~payments' })
    await level('suites')
    expect(lastPanel().selectors).toEqual([{ dimension: 'suite', value: 'payments' }])
  })

  describe('603: filter the page by a suite (P2: the page’s own suite select)', () => {
    beforeEach(() => setSuite.mockReset())

    it('no page suite select: the action is absent, and a Shift-click drills (M-603b)', async () => {
      renderDrill()
      await level('suites')
      const chart = surface()
      chart.focus()
      fireEvent.keyDown(chart, { key: 'ArrowDown' })
      expect(within(section()).queryByRole('button', { name: 'Filter page by this' })).toBeNull()
      fireEvent.click(rect('failed:payments'), { shiftKey: true })
      expect(drillParams()).toEqual(['suite~payments', 'status~failed'])
      expect(setSuite).not.toHaveBeenCalled()
    })

    it('with one: Shift-click REPLACES the page’s suite with this one, as the select spells it; the drill does not move', async () => {
      renderDrill({ suites: pageSuites('Checkout') })
      await level('suites')
      fireEvent.click(rect('failed:payments'), { shiftKey: true })
      expect(setSuite.mock.calls).toEqual([['Payments']])
      expect(drillParams()).toEqual([])
      // Never the multi-filter store: it is not the page's filter.
      expect(useSuiteStore.getState().activeSuiteNames).toEqual([])
    })

    it('the suite already selected is not written again', async () => {
      renderDrill({ suites: pageSuites('Payments') })
      await level('suites')
      fireEvent.click(rect('failed:payments'), { shiftKey: true })
      expect(setSuite).not.toHaveBeenCalled()
      expect(drillParams()).toEqual([])
    })

    it('with one: the readout offers "Filter page by this", and Shift+Enter does the same', async () => {
      renderDrill({ suites: pageSuites() })
      await level('suites')
      const chart = surface()
      chart.focus()
      fireEvent.keyDown(chart, { key: 'ArrowDown' })
      const actions = section().querySelector('[data-mark-actions]') as HTMLElement
      expect(within(actions).getAllByRole('button').map((b) => b.textContent)).toEqual([
        'Drill into Payments',
        'View rows',
        'Filter page by this',
      ])
      fireEvent.keyDown(chart, { key: 'Enter', shiftKey: true })
      expect(setSuite.mock.calls).toEqual([['Payments']])
    })
  })

  it('axe finds nothing on the section with the readout and its buttons open', async () => {
    renderDrill({ url: '/failures?drill=status~failed', suites: pageSuites() })
    await level('status-suites')
    const chart = surface()
    chart.focus()
    fireEvent.keyDown(chart, { key: 'ArrowDown' })
    // jsdom has no layout: contrast is checked in the browser (the e2e axe runs).
    const results = await axe.run(section(), { rules: { 'color-contrast': { enabled: false } } })
    expect(results.violations).toEqual([])
  })
})
