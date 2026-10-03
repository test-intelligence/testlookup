/**
 * The test scatter section (VIZ-506) with only the network, the flag lookups,
 * the existence probe, the project store and the canvas engine mocked: the
 * seam, the scope builder, the chart pipeline, the frame, the drill URL and
 * the selection list are the real ones. The rows panel is a stub that shows
 * what it was handed (its own behaviour is FK0's and tested there).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EnvelopeMeta, PointsChart, PointsChartPoint } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { RowsPanelProps } from './RowsPanel.model'
import { __resetChartConcurrency } from '@/services/chartApi'

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
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

const engine = vi.hoisted(() => {
  const handlers = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    isDisposed: () => false,
    on: (name: string, handler: (params: unknown) => void) => handlers.set(name, handler),
    off: (name: string) => handlers.delete(name),
  }
  return { instance, handlers, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('@/components/charts/engines/registry', () => ({ loadChartEngine: engine.load }))

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

import ScatterSection, { SCATTER_SECTION_MIN_HEIGHT } from './ScatterSection'
import {
  ALL_PROJECTS_REASON,
  NO_TESTS_MESSAGE,
  SCATTER_HEIGHT,
  SCATTER_NOTHING_HEIGHT,
  SCATTER_TITLE,
  scatterFrameHeight,
  scatterRowsChart,
  scatterRowsSelectors,
} from './ScatterSection.model'

const URL = '/api/v1/analytics/test-scatter'
const HOSTILE = '<img src=x onerror="window.__xss=1">'
const point = (id: string, x: number, y: number, size = 10, label = id): PointsChartPoint => ({ id, label, x, y, size, n: size - 1 })

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'p1', name: 'payments' }],
    releases: [],
    suites: ['Auth'],
    window: { from: '2026-09-01', to: '2026-09-30', days: 30, timezone: 'UTC' },
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
  generated_at: '2026-09-30T09:00:00Z',
  as_of: '2026-09-30T09:00:00Z',
}

const CHART: PointsChart = {
  kind: 'points',
  x: { key: 'p95_duration_ms', label: 'p95 duration (ms)', unit: 'ms', scale: 'log' },
  y: { key: 'failure_rate', label: 'Failure rate (%)', unit: 'percent', scale: 'linear' },
  size: { key: 'executions', label: 'Executions' },
  points: [point('fp-a', 12, 0), point('fp-b', 900, 25, 40, HOSTILE), point('fp-c', 1, 0), point('fp-d', 3000, 5)],
  medians: { x: 456, y: 2.5 },
  excluded: { below_min_executions: 14, no_duration: 2, no_evaluated: 1 },
}

/** The wire body: the C3 keys at the top level, `meta` beside them (`with_meta`). */
let body: Record<string, unknown> | null
const wire = (chart: PointsChart, meta: EnvelopeMeta = META) => ({ ...chart, meta })

function scatterCalls(): CatalogParams[] {
  return get.mock.calls.filter(([url]) => url === URL).map(([, config]) => (config as { params: CatalogParams }).params)
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderSection({
  days = 30,
  placement = 'suite',
  url = '/coverage/suite?name=Auth',
}: { days?: number; placement?: 'suite' | 'project'; url?: string } = {}) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>
          {children}
          <Location />
        </ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<ScatterSection days={days} suiteFilter={['Auth']} placement={placement} />, { wrapper })
}

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

const section = () => document.querySelector('[data-catalogue-section^="scatter-"]') as HTMLElement | null
const searchParams = () => new URLSearchParams(screen.getByTestId('location').textContent ?? '')
const lastPanel = () => panels.props[panels.props.length - 1] as RowsPanelProps

beforeEach(() => {
  flags.values = { viz_chart_data_api: true, viz_advanced_charts: true }
  probe.enabled = []
  project.id = 'p1'
  panels.props = []
  body = wire(CHART)
  get.mockReset()
  get.mockImplementation((url: string) =>
    url === URL && body
      ? Promise.resolve({ data: body, headers: { 'x-request-id': 'req-1' } })
      : Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } })),
  )
  engine.load.mockReset()
  engine.load.mockResolvedValue({ init: engine.init })
  engine.handlers.clear()
  engine.instance.setOption.mockClear()
  engine.instance.dispatchAction.mockClear()
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

afterEach(() => {
  __resetChartConcurrency()
})

/** Renders the section and waits until the scatter is drawn and listening. */
async function drawn(options: Parameters<typeof renderSection>[0] = {}) {
  renderSection(options)
  await waitFor(() => expect(section()?.querySelector('[data-chart-type="scatter"]')).toHaveAttribute('data-chart-status', 'ready'))
  await waitFor(() => expect(engine.handlers.has('brushEnd')).toBe(true))
  return section() as HTMLElement
}

describe('ScatterSection placeholder (R2-B F-15)', () => {
  class NeverNear {
    observe() {}
    disconnect() {}
    unobserve() {}
    takeRecords() {
      return []
    }
  }
  afterEach(() => vi.unstubAllGlobals())

  it('holds the height the scatter draws (543 px measured at 1280 on Failures and Suite detail), not 580', () => {
    vi.stubGlobal('IntersectionObserver', NeverNear)
    renderSection()
    const placeholder = document.querySelector<HTMLElement>('[data-lazy-section="scatter-suite"]')
    expect(placeholder?.style.minHeight).toBe('543px')
    expect(SCATTER_SECTION_MIN_HEIGHT).toBe(543)
  })
})

describe('ScatterSection (VIZ-506)', () => {
  it.each([
    ['both off', {}],
    ['only the catalogue flag', { viz_chart_data_api: true }],
    ['only the advanced flag', { viz_advanced_charts: true }],
  ])('%s: nothing is rendered, nothing is requested, no engine is fetched', async (_name, values) => {
    flags.values = values
    const { container } = renderSection()
    await settle()
    expect(container.querySelector('[data-catalogue-section]')).toBeNull()
    expect(container.querySelector('[data-lazy-section]')).toBeNull()
    expect(get).not.toHaveBeenCalled()
    expect(engine.load).not.toHaveBeenCalled()
    expect(probe.enabled).toEqual([])
  })

  it('both flags on: one request for the page scope, min executions and order stated, the window clamped to 90', async () => {
    const frame = await drawn({ days: 365 })
    expect(scatterCalls()).toEqual([{ min_executions: 5, order: 'failures', project_id: 'p1', days: 90, suite_name: 'Auth' }])
    expect(within(frame).getByRole('heading', { name: SCATTER_TITLE })).toBeInTheDocument()
    expect(engine.load).toHaveBeenCalledWith('scatter')
    await settle()
    expect(scatterCalls()).toHaveLength(1)
  })

  it('states the takeaway and, in the footer, every test left out and the log floor', async () => {
    const frame = await drawn()
    expect(within(frame).getByText('2 of 4 tests slow and flaky (above both medians)')).toBeInTheDocument()
    expect(frame.querySelector('[data-scatter-excluded]')?.textContent).toBe(
      'Not shown: 14 tests with fewer than 5 executions, 2 tests with no duration, 1 test with only skipped or unknown results.',
    )
    expect(frame.querySelector('[data-scatter-floor]')?.textContent).toContain('A p95 under 1 ms is drawn at 1 ms.')
    expect(frame.querySelector('[data-scatter-dense]')).toBeNull()
  })

  it('the table view lists every test from the same series (hostile name as text)', async () => {
    const frame = await drawn()
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    const table = within(frame).getByRole('table', { name: /data table/i })
    expect(within(table).getByRole('rowheader', { name: HOSTILE })).toBeInTheDocument()
    expect(within(table).getAllByRole('rowheader')).toHaveLength(4)
    expect(document.querySelector('img')).toBeNull()
  })

  it('every test excluded: the frame says why instead of drawing an empty plot', async () => {
    body = wire({ ...CHART, points: [], medians: undefined, excluded: { below_min_executions: 3, no_duration: 0, no_evaluated: 0 } })
    renderSection()
    await waitFor(() => expect(section()?.querySelector('[data-scatter-nothing]')).not.toBeNull())
    expect(section()?.querySelector('[data-scatter-nothing]')?.textContent).toBe(
      'No test can be placed on this chart: 3 tests with fewer than 5 executions.',
    )
    expect(section()?.querySelector('[data-chart-type="scatter"]')).toBeNull()
    expect(engine.load).not.toHaveBeenCalled()
    expect(within(section() as HTMLElement).queryByRole('button', { name: 'View as table' })).toBeNull()
    // F-14: said ONCE (the body), not again in the footer; and no plot-sized empty band under it.
    expect(section()?.querySelector('[data-scatter-excluded]')).toBeNull()
    expect(section()?.textContent?.match(/fewer than 5 executions/g)).toHaveLength(1)
    expect((section()?.querySelector('[data-chart-body]') as HTMLElement).style.minHeight).toBe(`${SCATTER_NOTHING_HEIGHT}px`)
  })

  it('nothing in scope at all: the frame says no test ran (never "never had data" on a project that has)', async () => {
    body = wire({ ...CHART, points: [], medians: undefined, excluded: { below_min_executions: 0, no_duration: 0, no_evaluated: 0 } })
    renderSection()
    expect(await within(document.body).findByText(NO_TESTS_MESSAGE)).toBeInTheDocument()
  })

  it('All Projects: the frame says why and nothing is requested', async () => {
    project.id = '__ALL__'
    renderSection()
    expect(await screen.findByText(new RegExp(ALL_PROJECTS_REASON.slice(0, 30)))).toBeInTheDocument()
    await settle()
    expect(get).not.toHaveBeenCalled()
    expect(probe.enabled.every((enabled) => enabled === false)).toBe(true)
  })

  it('a server error is the frame’s error state, not a toast or a crash', async () => {
    body = null
    renderSection()
    expect(await within(document.body).findByRole('button', { name: /retry|try again/i })).toBeInTheDocument()
  })

  it('a dense scatter says so in the footer', async () => {
    const many = Array.from({ length: 2001 }, (_, i) => point(`t${i}`, 2 + i, i % 100))
    body = wire({ ...CHART, points: many, medians: { x: 1002, y: 50 } })
    const frame = await drawn()
    expect(frame.querySelector('[data-scatter-dense]')?.textContent).toContain('Dense: 2,001 tests')
    expect(frame.querySelector('[data-scatter-floor]')).toBeNull()
  })

  describe('selection and rows', () => {
    it('"Select slow and flaky" lists exactly the salient tests under the frame', async () => {
      const frame = await drawn()
      expect(document.querySelector('[data-scatter-selection]')).toBeNull()
      fireEvent.click(within(frame).getByRole('button', { name: 'Select slow and flaky' }))
      const list = document.querySelector('[data-scatter-selection]') as HTMLElement
      // F-08: inside the frame's card (its body), on the card's padding, not under the card.
      expect(list.closest('[data-chart-body]')).not.toBeNull()
      expect(within(list).getAllByRole('rowheader').map((th) => th.textContent)).toEqual([HOSTILE, 'fp-d'])
      expect(list.textContent).toContain('2 tests selected')
    })

    it('a dragged rectangle lists the tests inside it; a cleared one removes the list', async () => {
      await drawn()
      act(() => engine.handlers.get('brushEnd')?.({ areas: [{ brushType: 'rect', coordRange: [[1, 12], [0, 0]] }] }))
      const list = document.querySelector('[data-scatter-selection]') as HTMLElement
      expect(within(list).getAllByRole('rowheader').map((th) => th.textContent).sort()).toEqual(['fp-a', 'fp-c'])
      act(() => engine.handlers.get('brushEnd')?.({ areas: [] }))
      expect(document.querySelector('[data-scatter-selection]')).toBeNull()
    })

    it('"View rows" opens the test’s rows through the URL (Back closes it), with the point’s figures', async () => {
      const frame = await drawn()
      fireEvent.click(within(frame).getByRole('button', { name: 'Select slow and flaky' }))
      fireEvent.click(screen.getByRole('button', { name: `View rows: ${HOSTILE}` }))
      expect(searchParams().getAll('rows')).toEqual(['by~scatter-suite', 'test~fp-b'])
      expect(searchParams().get('name')).toBe('Auth')
      const panel = await screen.findByTestId('rows-panel')
      expect(panel).toHaveAttribute('data-title', HOSTILE)
      const props = lastPanel()
      expect(props.selectors).toEqual([{ dimension: 'test', value: 'fp-b' }])
      expect(props.chart).toEqual({ metric: 'failure_rate', groupBy: ['test'] })
      // The page scope WITHOUT the scatter's own parameters: the rows population is the chart's.
      expect(props.scope).toEqual({ project_id: 'p1', days: 30, suite_name: 'Auth' })
      expect(props.expected).toEqual({ y: 25, n: 39, asOf: META.as_of })
      fireEvent.click(within(panel).getByRole('button', { name: 'Close rows' }))
      await waitFor(() => expect(screen.queryByTestId('rows-panel')).toBeNull())
      expect(searchParams().getAll('rows')).toEqual([])
    })

    it('Enter on the focused point opens its rows (the keyboard path to a drill)', async () => {
      const frame = await drawn()
      const plot = frame.querySelector('[data-chart-keyboard="scatter"]') as HTMLElement
      fireEvent.keyDown(plot, { key: 'End' })
      fireEvent.keyDown(plot, { key: 'Enter' })
      expect(searchParams().getAll('rows')).toEqual(['by~scatter-suite', 'test~fp-d'])
      expect(await screen.findByTestId('rows-panel')).toHaveAttribute('data-title', 'fp-d')
    })

    it('a shared link with a test row reopens the panel; another host’s rows are not this panel’s', async () => {
      await drawn({ url: '/coverage/suite?name=Auth&rows=by~scatter-suite&rows=test~fp-a' })
      expect(await screen.findByTestId('rows-panel')).toHaveAttribute('data-title', 'fp-a')
      expect(lastPanel().expected).toEqual({ y: 0, n: 9, asOf: META.as_of })
    })

    it('the same lone test opened by the test x run heatmap is not this panel (FK4-1)', async () => {
      await drawn({ url: '/coverage/suite?name=Auth&rows=by~heatmap-test_run&rows=test~fp-a' })
      expect(lastPanel().selectors).toEqual([])
      expect(screen.queryByTestId('rows-panel')).toBeNull()
    })

    it('a link to a test the chart no longer shows still opens, titled by its key, with no expectation', async () => {
      await drawn({ url: '/coverage/suite?name=Auth&rows=by~scatter-suite&rows=test~gone' })
      expect(await screen.findByTestId('rows-panel')).toHaveAttribute('data-title', 'gone')
      expect(lastPanel().expected).toBeNull()
    })
  })

  it('project-wide placement drills with a top_n bound', async () => {
    await drawn({ placement: 'project' })
    expect(section()).toHaveAttribute('data-catalogue-section', 'scatter-project')
    expect(lastPanel().chart).toEqual({ metric: 'failure_rate', groupBy: ['test'], topN: 1 })
  })
})

describe('helpers', () => {
  it('scatterFrameHeight: the plot height while there is a plot (or none yet), a sentence-sized body when nothing is placed', () => {
    expect(scatterFrameHeight(null)).toBe(SCATTER_HEIGHT)
    expect(scatterFrameHeight(CHART)).toBe(SCATTER_HEIGHT)
    expect(scatterFrameHeight({ ...CHART, points: [] })).toBe(SCATTER_NOTHING_HEIGHT)
    expect(SCATTER_NOTHING_HEIGHT).toBeLessThan(SCATTER_HEIGHT / 2)
  })

  it('scatterRowsSelectors keeps a single test selector only', () => {
    expect(scatterRowsSelectors([{ dimension: 'test', value: 'a' }])).toEqual([{ dimension: 'test', value: 'a' }])
    expect(scatterRowsSelectors([{ dimension: 'test', value: 'a' }, { dimension: 'day', value: '2026-09-01' }])).toEqual([])
    expect(scatterRowsSelectors([{ dimension: 'suite', value: 'a' }])).toEqual([])
    expect(scatterRowsSelectors([])).toEqual([])
  })
  it('scatterRowsChart bounds a project-wide test drill by top_n only', () => {
    expect(scatterRowsChart('suite')).toEqual({ metric: 'failure_rate', groupBy: ['test'] })
    expect(scatterRowsChart('project').topN).toBe(1)
  })
})
