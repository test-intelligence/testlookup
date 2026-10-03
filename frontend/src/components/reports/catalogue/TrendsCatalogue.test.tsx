/**
 * The Trends catalogue sections (Wave 2.6, VIZ-408; the heatmap swapped to
 * `/analytics/heatmap?kind=suite_day` in Wave 3, VIZ-501), with only the
 * network, the flag lookups, the existence probe and the canvas engine
 * mocked: the seam, the scope builder, the chart pipeline and the frames are
 * the real ones, so "which request feeds which view" and "the window is
 * clamped on the wire" are read off the requests themselves.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps, ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnyChartSeries, EnvelopeMeta, SeriesChart } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import { CHART_MESSAGES } from '@/components/charts/chartMessages'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { ChartResponse, ChartState } from '@/components/charts/chartState'
import type DurationChartFrame from '@/components/charts/DurationChartFrame'
import {
  HEATMAP_FRAME_HOSTILE_NAME,
  heatmapFrameHostile,
  heatmapFrameMeta,
  heatmapFrameWorstFirst,
} from '@/components/charts/__fixtures__/heatmapFrame'
import { __resetChartConcurrency } from '@/services/chartApi'
import { ONE_DAY_HEATMAP_REASON } from './HeatmapSection.model'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

/** The two catalogue flags, read through the REAL seam (`useCatalogueRollout.ts`). */
const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => flags.values[key] ?? false,
  useFeatureFlagStatus: (key: string) => flags.values[key],
}))

/** The unfiltered probe: `true` once asked; records whether it was asked. */
const probe = vi.hoisted(() => ({ enabled: [] as boolean[] }))
vi.mock('./useEverHadRun', () => ({
  useEverHadRun: (enabled: boolean) => {
    probe.enabled.push(enabled)
    return enabled ? true : null
  },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: 'p1' }),
}))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => null }))

// jsdom has no canvas: the heatmap's engine is mocked at the registry, as in HeatmapChartFrame.test.tsx.
const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('@/components/charts/engines/registry', () => ({ loadChartEngine: engine.load }))

// The duration frame renders for real; the spy keeps the band the section built.
const durationFrames = vi.hoisted(() => [] as unknown[])
vi.mock('@/components/charts/DurationChartFrame', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/charts/DurationChartFrame')>()
  const Real = actual.default
  return {
    ...actual,
    default: (props: ComponentProps<typeof DurationChartFrame>) => {
      durationFrames.push(props)
      return <Real {...props} />
    },
  }
})

import TrendsCatalogue, {
  DURATION_TITLE,
  durationFrameState,
  grainNote,
  HEATMAP_TITLE,
  ONE_DAY_DURATION_REASON,
  ONE_DAY_SUITES_REASON,
  SERIES_SHAPE_ERROR,
  SUITE_SERIES_TITLE,
} from './TrendsCatalogue'

const CHART_DATA_URL = '/api/v1/analytics/chart-data'
const HEATMAP_URL = '/api/v1/analytics/heatmap'
const CATALOGUE = 'viz_chart_data_api'
const ADVANCED = 'viz_advanced_charts'

const META = { ...heatmapFrameMeta, definitions: { grain: 'execution_row' } } as EnvelopeMeta
const DAYS = heatmapFrameWorstFirst.series.x_keys ?? []

/** The multi-series' day x suite pass rate, in percentage points (chart-data's shape). */
function suiteSeries(names: readonly string[]): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['day', 'suite'],
    x_type: 'time',
    series: names.map((name, s) => ({
      key: name,
      label: name,
      points: DAYS.map((x, i) => ({ x, y: 90 - s * 5 + (i % 3), n: 40 + i })),
    })),
  }
}
/** The day whose p95 the server could not measure. */
const P95_GAP_DAY = DAYS[4]

function durationSeries(metric: 'p50' | 'p95'): SeriesChart {
  return {
    kind: 'series',
    dimensions: ['day'],
    x_type: 'time',
    series: [
      {
        key: metric,
        label: metric,
        points: DAYS.map((x, i) =>
          metric === 'p95' && x === P95_GAP_DAY
            ? { x, y: null, n: 3, measured: false, reason: 'too few executions for a 95th percentile' }
            : { x, y: (metric === 'p50' ? 1_000 : 4_000) + i * 10, n: 40 },
        ),
      },
    ],
  }
}

let responses: Record<string, ChartResponse<AnyChartSeries>>

function chartDataCalls(): CatalogParams[] {
  return get.mock.calls.filter(([url]) => url === CHART_DATA_URL).map(([, config]) => (config as { params: CatalogParams }).params)
}
function heatmapCalls(): CatalogParams[] {
  return get.mock.calls.filter(([url]) => url === HEATMAP_URL).map(([, config]) => (config as { params: CatalogParams }).params)
}
const metricCalls = (metric: string) => chartDataCalls().filter((params) => params.metric === metric)

function renderCatalogue(props: { days: number; suiteFilter?: string | null }) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>{children}</ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<TrendsCatalogue days={props.days} suiteFilter={props.suiteFilter ?? null} />, { wrapper })
}

/** Let every queued fetch start: a request goes out a few microtasks after the render that keys it. */
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

const section = (id: string) => document.querySelector(`[data-catalogue-section="${id}"]`) as HTMLElement | null

beforeEach(() => {
  flags.values = { [CATALOGUE]: true, [ADVANCED]: true }
  probe.enabled = []
  durationFrames.length = 0
  responses = {
    pass_rate: { meta: META, series: suiteSeries(['checkout', 'search', 'billing']) },
    duration_p50: { meta: META, series: durationSeries('p50') },
    duration_p95: { meta: META, series: durationSeries('p95') },
    suite_day: heatmapFrameWorstFirst,
  }
  get.mockReset()
  get.mockImplementation((url: string, config: { params: CatalogParams }) => {
    const payload = responses[String(url === HEATMAP_URL ? config.params.kind : config.params.metric)]
    return payload
      ? Promise.resolve({ data: payload, headers: { 'x-request-id': 'req-1' } })
      : Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } }))
  })
  engine.load.mockReset()
  engine.load.mockResolvedValue({ init: engine.init })
  engine.instance.setOption.mockClear()
})

afterEach(() => {
  __resetChartConcurrency()
  vi.unstubAllGlobals()
})

describe('TrendsCatalogue (VIZ-408, Trends)', () => {
  it('Customise (VIZ-604): a side panel changes the metric and the lines; the request and title follow; Reset restores', async () => {
    const KEY = 'testlookup.chartConfig.trends.suite-series'
    window.localStorage.removeItem(KEY)
    responses.failures = { meta: META, series: suiteSeries(['checkout', 'search']) }
    renderCatalogue({ days: 14 })
    await waitFor(() => expect(metricCalls('pass_rate')).toHaveLength(1))
    fireEvent.click(within(section('trends-multi-series') as HTMLElement).getByRole('button', { name: 'Customise' }))
    // A side panel beside the chart, not a modal.
    const panel = screen.getByRole('complementary', { name: /^Customise / })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    fireEvent.change(within(panel).getByLabelText('Metric'), { target: { value: 'failures' } })
    fireEvent.change(within(panel).getByLabelText(/^Lines/), { target: { value: '5' } })
    await waitFor(() =>
      expect(metricCalls('failures').slice(-1)[0]).toEqual({ metric: 'failures', group_by: ['day', 'suite'], top_n: 5, project_id: 'p1', days: 14 }),
    )
    await waitFor(() =>
      expect(within(section('trends-multi-series') as HTMLElement).getByRole('heading', { name: 'Failures by suite' })).toBeInTheDocument(),
    )
    expect(JSON.parse(window.localStorage.getItem(KEY) ?? '{}')).toMatchObject({ metric: 'failures', topN: 5 })
    // The guard rails are listed with their reasons, never silently missing.
    expect(within(panel).getByText(/A pie shows the parts of one whole/)).toBeInTheDocument()

    fireEvent.click(within(panel).getByRole('button', { name: 'Reset to default' }))
    await waitFor(() =>
      expect(within(section('trends-multi-series') as HTMLElement).getByRole('heading', { name: SUITE_SERIES_TITLE })).toBeInTheDocument(),
    )
    expect(window.localStorage.getItem(KEY)).toBeNull()
  })

  it('the heatmap asks /analytics/heatmap for suite x day; the suite series feeds the multi-series alone', async () => {
    renderCatalogue({ days: 14 })
    await waitFor(() => expect(within(section('trends-heatmap') as HTMLElement).getByText(/Rows: lowest pass rate first/)).toBeInTheDocument())
    expect(heatmapCalls()).toEqual([{ kind: 'suite_day', project_id: 'p1', days: 14 }])
    expect(metricCalls('pass_rate')).toEqual([
      { metric: 'pass_rate', group_by: ['day', 'suite'], top_n: 7, project_id: 'p1', days: 14 },
    ])
    // The heatmap's footer reads the SERVER's row cut (by failures); the multi-series has its lines.
    expect(within(section('trends-heatmap') as HTMLElement).getByText(/Top 7 of 12 suites by failures/)).toBeInTheDocument()
    expect(within(section('trends-heatmap') as HTMLElement).getByRole('heading', { name: HEATMAP_TITLE })).toBeInTheDocument()
    expect(within(section('trends-multi-series') as HTMLElement).getByRole('heading', { name: SUITE_SERIES_TITLE })).toBeInTheDocument()
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    await settle()
    expect(metricCalls('pass_rate')).toHaveLength(1)
    expect(heatmapCalls()).toHaveLength(1)
    // Trends offers one kind: no kind selector.
    expect(section('trends-heatmap')?.querySelector('[data-heatmap-kinds]')).toBeNull()
  })

  it('the heatmap draws the endpoint’s matrix worst first, in 0..1 on the canvas', async () => {
    renderCatalogue({ days: 14 })
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    const calls = engine.instance.setOption.mock.calls
    const option = calls[calls.length - 1][0] as {
      yAxis: { data: string[] }
      series: { id: string; data: [number, number, number | string][] }[]
    }
    expect(option.yAxis.data[0]).toBe('legacy-import')
    const cells = option.series.find((s) => s.id === 'cells')?.data ?? []
    const values = cells.map((cell) => cell[2]).filter((v): v is number => typeof v === 'number')
    expect(Math.max(...values)).toBeLessThanOrEqual(1)
  })

  it('a heatmap that fails leaves the other sections drawn', async () => {
    delete responses.suite_day
    renderCatalogue({ days: 14 })
    await waitFor(() => expect(section('trends-heatmap')?.querySelector('[data-chart-state="error"]')).not.toBeNull())
    // Drawn (the shared META says the suites were truncated), not an error.
    await waitFor(() => expect(section('trends-multi-series')?.querySelector('[data-chart-state="truncated"]')).not.toBeNull())
    expect(section('trends-duration')?.querySelector('[data-chart-state="error"]')).toBeNull()
  })

  it('asks the duration percentiles once each, by day, in the same scope', async () => {
    renderCatalogue({ days: 30, suiteFilter: 'checkout' })
    await waitFor(() => expect(metricCalls('duration_p95')).toHaveLength(1))
    expect(metricCalls('duration_p50')).toEqual([
      { metric: 'duration_p50', group_by: 'day', project_id: 'p1', days: 30, suite_name: 'checkout' },
    ])
    expect(metricCalls('duration_p95')).toEqual([
      { metric: 'duration_p95', group_by: 'day', project_id: 'p1', days: 30, suite_name: 'checkout' },
    ])
    expect(metricCalls('pass_rate')[0]).toMatchObject({ days: 30, suite_name: 'checkout' })
  })

  it('needs BOTH flags for the heatmap: with only the catalogue flag, no heatmap and no engine', async () => {
    flags.values = { [CATALOGUE]: true, [ADVANCED]: false }
    renderCatalogue({ days: 14 })
    await waitFor(() => expect(metricCalls('pass_rate')).toHaveLength(1))
    await screen.findByRole('heading', { name: SUITE_SERIES_TITLE })
    expect(section('trends-heatmap')).toBeNull()
    expect(screen.queryByRole('heading', { name: HEATMAP_TITLE })).toBeNull()
    expect(document.querySelector('[data-lazy-section="trends-heatmap"]')).toBeNull()
    await settle()
    expect(heatmapCalls()).toEqual([])
    expect(engine.load).not.toHaveBeenCalled()
  })

  it('the advanced flag alone draws no heatmap either', async () => {
    flags.values = { [CATALOGUE]: false, [ADVANCED]: true }
    renderCatalogue({ days: 14 })
    await settle()
    expect(section('trends-heatmap')).toBeNull()
    expect(document.querySelector('[data-lazy-section="trends-heatmap"]')).toBeNull()
    expect(heatmapCalls()).toEqual([])
    expect(engine.load).not.toHaveBeenCalled()
  })

  it('clamps the window on the wire: 365 days never reaches a request (the heatmap keeps 90)', async () => {
    renderCatalogue({ days: 365 })
    await waitFor(() => expect(chartDataCalls()).toHaveLength(3))
    await waitFor(() => expect(heatmapCalls()).toHaveLength(1))
    for (const params of [...chartDataCalls(), ...heatmapCalls()]) expect(params.days).toBe(90)
  })

  it('draws a p95 the server did not measure as a gap, never 0 ms', async () => {
    renderCatalogue({ days: 14 })
    await waitFor(() => {
      const last = durationFrames[durationFrames.length - 1] as { band: { points: { x: string }[] } | null }
      expect(last.band?.points).toHaveLength(DAYS.length)
    })
    const { band } = durationFrames[durationFrames.length - 1] as { band: { points: { x: string; p50: number | null; p95: number | null; high: number | null }[] } }
    const gap = band.points.find((point) => point.x === P95_GAP_DAY)
    expect(gap?.p95).toBeNull()
    expect(gap?.high).toBeNull()
    expect(gap?.p50).toBe(1_040)
    // Every other day is measured.
    expect(band.points.filter((point) => point.p95 === null)).toHaveLength(1)
    expect(within(section('trends-duration') as HTMLElement).getByRole('heading', { name: DURATION_TITLE })).toBeInTheDocument()
  })

  it('a one-day window is not a trend: every frame says why, and nothing is asked', async () => {
    renderCatalogue({ days: 1 })
    expect(await within(section('trends-multi-series') as HTMLElement).findByText(new RegExp(ONE_DAY_SUITES_REASON.slice(0, 40)))).toBeInTheDocument()
    expect(within(section('trends-duration') as HTMLElement).getByText(new RegExp(ONE_DAY_DURATION_REASON.slice(0, 40)))).toBeInTheDocument()
    expect(within(section('trends-heatmap') as HTMLElement).getByText(new RegExp(ONE_DAY_HEATMAP_REASON.slice(0, 40)))).toBeInTheDocument()
    await settle()
    expect(chartDataCalls()).toEqual([])
    expect(heatmapCalls()).toEqual([])
    // Nothing to ask, so no existence probe either.
    expect(probe.enabled.every((enabled) => !enabled)).toBe(true)
  })

  it('a 7-day window draws: the four frames ask for 7 days', async () => {
    renderCatalogue({ days: 7 })
    await waitFor(() => expect(chartDataCalls()).toHaveLength(3))
    await waitFor(() => expect(heatmapCalls()).toHaveLength(1))
    for (const params of [...chartDataCalls(), ...heatmapCalls()]) expect(params.days).toBe(7)
    await screen.findByRole('heading', { name: SUITE_SERIES_TITLE })
    expect(screen.queryByText(new RegExp(ONE_DAY_SUITES_REASON.slice(0, 40)))).toBeNull()
  })

  it('states the grain each frame was counted in', async () => {
    renderCatalogue({ days: 14 })
    await waitFor(() => expect(section('trends-heatmap')?.querySelector('[data-catalogue-grain]')).not.toBeNull())
    for (const id of ['trends-multi-series', 'trends-duration', 'trends-heatmap']) {
      await waitFor(() => expect(section(id)?.querySelector('[data-catalogue-grain]')).toHaveTextContent('Counted per test execution.'))
    }
  })

  it('shows a payload of the wrong shape as an error in the frame, not a blank chart', async () => {
    responses.pass_rate = { meta: META, series: { kind: 'matrix', value_type: 'rate', x_labels: ['a'], y_labels: ['b'], cells: [{ x: 0, y: 0, value: 50, n: 1 }] } }
    renderCatalogue({ days: 14 })
    expect(await within(section('trends-multi-series') as HTMLElement).findByText(SERIES_SHAPE_ERROR)).toBeInTheDocument()
  })

  it('renders a hostile suite name as text', async () => {
    responses.pass_rate = { meta: META, series: suiteSeries(['payments', HEATMAP_FRAME_HOSTILE_NAME]) }
    responses.suite_day = heatmapFrameHostile
    renderCatalogue({ days: 7 })
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    await screen.findByRole('heading', { name: SUITE_SERIES_TITLE })
    const heatmap = section('trends-heatmap') as HTMLElement
    await waitFor(() => expect(heatmap.querySelector('[data-chart-state="ready"]')).not.toBeNull())
    fireEvent.click(within(heatmap).getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(within(heatmap).getByRole('rowheader', { name: HEATMAP_FRAME_HOSTILE_NAME })).toBeInTheDocument()
    expect(document.querySelector('[data-trends-catalogue] img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
    const calls = engine.instance.setOption.mock.calls
    const option = calls[calls.length - 1][0] as { yAxis: { data: string[] } }
    // The axis prints the name cut in the middle: still text, never markup.
    expect(option.yAxis.data[0].startsWith('<img src')).toBe(true)
  })
})

describe('TrendsCatalogue lazy mounting', () => {
  /** An IntersectionObserver the test drives: nothing is near until `reveal`. */
  class FakeObserver {
    static instances: FakeObserver[] = []
    targets: Element[] = []
    constructor(readonly callback: IntersectionObserverCallback) {
      FakeObserver.instances.push(this)
    }
    observe(target: Element) {
      this.targets.push(target)
    }
    unobserve() {}
    disconnect() {
      this.targets = []
    }
    takeRecords() {
      return []
    }
  }

  function reveal(label: string) {
    const observer = FakeObserver.instances.find((o) => o.targets.some((t) => t.getAttribute('data-lazy-section') === label))
    if (!observer) throw new Error(`no observer on ${label}`)
    const target = observer.targets.find((t) => t.getAttribute('data-lazy-section') === label) as Element
    act(() => observer.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], observer as unknown as IntersectionObserver))
  }

  beforeEach(() => {
    FakeObserver.instances = []
    vi.stubGlobal('IntersectionObserver', FakeObserver)
  })

  it('asks nothing, probe included, until a section is near; each section asks when it is', async () => {
    renderCatalogue({ days: 14 })
    await settle()
    expect(document.querySelectorAll('[data-lazy-section]')).toHaveLength(4)
    expect(document.querySelector('[data-catalogue-section]')).toBeNull()
    expect(chartDataCalls()).toEqual([])
    expect(probe.enabled.every((enabled) => !enabled)).toBe(true)

    reveal('trends-multi-series')
    await waitFor(() => expect(metricCalls('pass_rate')).toHaveLength(1))
    expect(probe.enabled[probe.enabled.length - 1]).toBe(true)
    await settle()
    expect(metricCalls('duration_p50')).toEqual([])

    reveal('trends-duration')
    await waitFor(() => expect(metricCalls('duration_p95')).toHaveLength(1))

    // VIZ-605: with fewer than two suites or releases chosen, the comparison
    // says so and asks nothing.
    const before = chartDataCalls().length
    reveal('trends-compare')
    await screen.findByText(/pick two or more suites or releases/)
    await settle()
    expect(chartDataCalls()).toHaveLength(before)

    // The heatmap has its own request: none until it is near, one when it is.
    expect(heatmapCalls()).toEqual([])
    reveal('trends-heatmap')
    await waitFor(() => expect(heatmapCalls()).toHaveLength(1))
    await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
    await settle()
    expect(metricCalls('pass_rate')).toHaveLength(1)
  })

  it('a reader who reaches the heatmap first asks only the heatmap', async () => {
    renderCatalogue({ days: 14 })
    reveal('trends-heatmap')
    await waitFor(() => expect(heatmapCalls()).toHaveLength(1))
    await settle()
    expect(chartDataCalls()).toEqual([])
    reveal('trends-multi-series')
    await screen.findByRole('heading', { name: SUITE_SERIES_TITLE })
    await settle()
    expect(metricCalls('pass_rate')).toHaveLength(1)
    expect(heatmapCalls()).toHaveLength(1)
  })
})

describe('durationFrameState', () => {
  const ready = (series: SeriesChart): ChartState<ChartResponse> => ({ status: 'ready', data: { meta: META, series }, meta: META, revalidating: false })
  const error = (retry: () => void): ChartState<ChartResponse> => ({
    status: 'error',
    error: { kind: 'server', message: 'boom', requestId: 'r', status: 500 },
    retry,
  })

  it('an error on either side is the frame error, and Retry asks both', () => {
    const again50 = vi.fn()
    const again95 = vi.fn()
    const state = durationFrameState(error(again50), error(again95))
    expect(state.status).toBe('error')
    if (state.status === 'error') state.retry?.()
    expect(again50).toHaveBeenCalledTimes(1)
    expect(again95).toHaveBeenCalledTimes(1)
    expect(durationFrameState(ready(durationSeries('p50')), error(again95)).status).toBe('error')
  })

  it('forbidden, then loading, win over data', () => {
    expect(durationFrameState({ status: 'forbidden', requestId: null }, ready(durationSeries('p95'))).status).toBe('forbidden')
    expect(durationFrameState({ status: 'loading' }, ready(durationSeries('p95'))).status).toBe('loading')
  })

  it('draws when either side has data; with neither, the p50 speaks', () => {
    const empty: ChartState<ChartResponse> = { status: 'filtered-empty', meta: META }
    expect(durationFrameState(empty, ready(durationSeries('p95'))).status).toBe('ready')
    expect(durationFrameState(empty, { status: 'never-had-data' })).toBe(empty)
  })
})

describe('grainNote', () => {
  it('names the two documented grains and nothing else', () => {
    expect(grainNote({ ...META, definitions: { grain: 'run_aggregate' } } as EnvelopeMeta)).toBe('Counted per run, from run totals.')
    expect(grainNote(META)).toBe('Counted per test execution.')
    expect(grainNote(heatmapFrameMeta)).toBeNull()
    expect(grainNote({ ...META, definitions: 'x' } as unknown as EnvelopeMeta)).toBeNull()
    expect(grainNote(null)).toBeNull()
  })
})
