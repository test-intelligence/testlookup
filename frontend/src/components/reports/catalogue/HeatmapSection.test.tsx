/**
 * VIZ-501: the heatmap section — the seam (both flags), the lazy mount, the
 * one `/analytics/heatmap` request per kind, the kind selector, the row
 * order, "fit to data", and the requests it must never send (a one-day
 * suite x day, a one-project kind in All Projects).
 *
 * Only the network, the flag lookups, the project store, the existence probe
 * and the canvas engine are mocked: the seam, the scope builder, the chart
 * pipeline (validation included) and the frame are the real ones.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect, type ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnyChartSeries } from '@/lib/viz/contracts'
import { ChartAnnouncerProvider } from '@/components/charts/ChartAnnouncer'
import { CHART_MESSAGES } from '@/components/charts/chartMessages'
import type { CatalogParams } from '@/components/charts/chartCatalogSources'
import type { ChartResponse } from '@/components/charts/chartState'
import {
  HEATMAP_FRAME_HOSTILE_NAME,
  heatmapFrameHostile,
  heatmapFrameStatus,
  heatmapFrameWorstFirst,
} from '@/components/charts/__fixtures__/heatmapFrame'
import { __resetChartConcurrency } from '@/services/chartApi'
import type { HeatmapKind } from './sectionContracts'

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
  const listeners = new Map<string, (params: unknown) => void>()
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    dispatchAction: vi.fn(),
    on: vi.fn((name: string, listener: (params: unknown) => void) => listeners.set(name, listener)),
    off: vi.fn(),
  }
  return { instance, listeners, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('@/components/charts/engines/registry', () => ({ loadChartEngine: engine.load }))

import HeatmapSectionDefault, { HeatmapSection, type HeatmapSectionOwnProps } from './HeatmapSection'
import {
  HEATMAP_FIT_LABEL,
  HEATMAP_GRAIN_NOTE,
  HEATMAP_KIND_LABEL,
  HEATMAP_KIND_SPECS,
  HEATMAP_SORT_LABEL,
  ONE_DAY_HEATMAP_REASON,
} from './HeatmapSection.model'

const HEATMAP_URL = '/api/v1/analytics/heatmap'
const ROWS_URL = '/api/v1/analytics/chart-data/rows'
const CATALOGUE = 'viz_chart_data_api'
const ADVANCED = 'viz_advanced_charts'

let responses: Partial<Record<HeatmapKind, ChartResponse<AnyChartSeries>>>
/** What the rows endpoint counts now (its `total` and reconciliation value). */
const rowsTotal = { value: 0 }

const heatmapCalls = (): CatalogParams[] =>
  get.mock.calls.filter(([url]) => url === HEATMAP_URL).map(([, config]) => (config as { params: CatalogParams }).params)

const rowsCalls = (): CatalogParams[] =>
  get.mock.calls.filter(([url]) => url === ROWS_URL).map(([, config]) => (config as { params: CatalogParams }).params)

/** The URL the router is at, read by a probe inside it (after each render, in an effect). */
const location = { search: '' }
function LocationProbe() {
  const { search } = useLocation()
  useEffect(() => {
    location.search = search
  }, [search])
  return null
}

function renderSection(props: Partial<HeatmapSectionOwnProps> = {}, url = '/trends') {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[url]}>
      <LocationProbe />
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 2000, revalidateOnFocus: false }}>
        <ChartAnnouncerProvider>{children}</ChartAnnouncerProvider>
      </SWRConfig>
    </MemoryRouter>
  )
  return render(<HeatmapSection days={14} suiteFilter={null} kinds={['suite_day']} {...props} />, { wrapper })
}

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })

type Option = {
  yAxis: { data: string[] }
  visualMap: { id: string; min: number; max: number }[]
}
async function lastOption(): Promise<Option> {
  await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
  const calls = engine.instance.setOption.mock.calls
  return calls[calls.length - 1][0] as unknown as Option
}

const section = () => document.querySelector('[data-catalogue-section]') as HTMLElement | null
/** What the section put in the page: a placeholder, a section, a frame (the announcer's own regions excluded). */
const drawnByTheSection = () => [...document.querySelectorAll('[data-lazy-section], [data-catalogue-section], [data-chart-frame]')]
const frameState = () => section()?.querySelector('[data-chart-frame]')?.getAttribute('data-chart-state')

beforeEach(() => {
  flags.values = { [CATALOGUE]: true, [ADVANCED]: true }
  probe.enabled = []
  rowsTotal.value = 0
  project.id = 'p1'
  responses = { suite_day: heatmapFrameWorstFirst, test_run: heatmapFrameStatus, suite_environment: heatmapFrameHostile }
  get.mockReset()
  get.mockImplementation((url: string, config: { params: CatalogParams }) => {
    if (url === ROWS_URL) {
      return Promise.resolve({
        data: {
          items: [],
          total: rowsTotal.value,
          page: 1,
          size: 50,
          pages: 0,
          reconciliation: { mark_field: 'y', measure: 'executions', value: rowsTotal.value },
          meta: null,
        },
        headers: { 'x-request-id': 'req-rows' },
      })
    }
    const payload = responses[config.params.kind as HeatmapKind]
    return payload
      ? Promise.resolve({ data: payload, headers: { 'x-request-id': 'req-1' } })
      : Promise.reject(Object.assign(new Error('no fixture'), { response: { status: 500, data: {} } }))
  })
  engine.load.mockReset()
  engine.load.mockResolvedValue({ init: engine.init })
  engine.instance.setOption.mockClear()
  engine.listeners.clear()
})

afterEach(() => {
  __resetChartConcurrency()
  vi.unstubAllGlobals()
})

describe('HeatmapSection: the seam', () => {
  it.each([
    ['neither flag', {}],
    ['only the catalogue flag', { [CATALOGUE]: true }],
    ['only the advanced flag', { [ADVANCED]: true }],
  ])('%s: renders nothing, asks nothing, loads no engine', async (_name, values) => {
    flags.values = values
    renderSection()
    await settle()
    expect(drawnByTheSection()).toEqual([])
    expect(get).not.toHaveBeenCalled()
    expect(engine.load).not.toHaveBeenCalled()
    expect(probe.enabled).toEqual([])
  })

  it('both flags: one request for the kind, in the page’s scope, and the frame draws it', async () => {
    renderSection({ suiteFilter: 'checkout' })
    await waitFor(() => expect(frameState()).toBe('ready'))
    expect(heatmapCalls()).toEqual([{ kind: 'suite_day', project_id: 'p1', days: 14, suite_name: 'checkout' }])
    expect(within(section() as HTMLElement).getByRole('heading', { name: HEATMAP_KIND_SPECS.suite_day.title })).toBeInTheDocument()
    expect(section()?.querySelector('[data-catalogue-grain]')).toHaveTextContent(HEATMAP_GRAIN_NOTE)
    await lastOption()
  })

  it('the window is clamped to 90 days on the wire', async () => {
    renderSection({ days: 365 })
    await waitFor(() => expect(heatmapCalls()).toHaveLength(1))
    expect(heatmapCalls()[0].days).toBe(90)
  })

  it('names its section and placeholder by the first kind, or by the host’s id', async () => {
    renderSection({ kinds: ['test_run'] })
    await waitFor(() => expect(section()).toHaveAttribute('data-catalogue-section', 'heatmap-test_run'))
  })

  it('no kinds: nothing (the pinned contract says never empty, but a mistake must not throw)', () => {
    renderSection({ kinds: [] })
    expect(drawnByTheSection()).toEqual([])
  })

  it('the default export is the named one (the pinned contract)', () => {
    expect(HeatmapSectionDefault).toBe(HeatmapSection)
  })
})

describe('HeatmapSection: lazy', () => {
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

  beforeEach(() => {
    FakeObserver.instances = []
    vi.stubGlobal('IntersectionObserver', FakeObserver)
  })

  it('asks nothing, probe included, until it is near; then asks once', async () => {
    renderSection({ sectionId: 'trends-heatmap' })
    await settle()
    const placeholder = document.querySelector('[data-lazy-section="trends-heatmap"]') as Element
    expect(placeholder).not.toBeNull()
    expect(get).not.toHaveBeenCalled()
    expect(probe.enabled).toEqual([])
    const observer = FakeObserver.instances.find((o) => o.targets.includes(placeholder)) as FakeObserver
    act(() => observer.callback([{ isIntersecting: true, target: placeholder } as IntersectionObserverEntry], observer as unknown as IntersectionObserver))
    await waitFor(() => expect(heatmapCalls()).toHaveLength(1))
    expect(section()).toHaveAttribute('data-catalogue-section', 'trends-heatmap')
  })
})

describe('HeatmapSection: kinds', () => {
  it('one kind: no selector', async () => {
    renderSection()
    await waitFor(() => expect(section()).not.toBeNull())
    expect(screen.queryByRole('group', { name: HEATMAP_KIND_LABEL })).toBeNull()
  })

  it('several kinds: a radio group in the host’s order, the first chosen; choosing asks for that kind', async () => {
    renderSection({ kinds: ['suite_environment', 'suite_release'] })
    const group = await screen.findByRole('group', { name: HEATMAP_KIND_LABEL })
    const radios = within(group).getAllByRole('radio')
    expect(radios.map((radio) => radio.closest('label')?.textContent)).toEqual(['Environment', 'Release'])
    expect(radios[0]).toBeChecked()
    await waitFor(() => expect(heatmapCalls()).toEqual([{ kind: 'suite_environment', project_id: 'p1', days: 14 }]))

    responses.suite_release = { ...heatmapFrameHostile, series: { ...heatmapFrameHostile.series } }
    fireEvent.click(radios[1])
    expect(radios[1]).toBeChecked()
    await waitFor(() => expect(heatmapCalls().map((params) => params.kind)).toEqual(['suite_environment', 'suite_release']))
    expect(await within(section() as HTMLElement).findByRole('heading', { name: HEATMAP_KIND_SPECS.suite_release.title })).toBeInTheDocument()
    expect(section()).toHaveAttribute('data-heatmap-kind', 'suite_release')
  })

  it('a host title wins over the kind’s', async () => {
    renderSection({ title: 'Pass rate per suite and day' })
    expect(await screen.findByRole('heading', { name: 'Pass rate per suite and day' })).toBeInTheDocument()
  })
})

describe('HeatmapSection: requests it must not send', () => {
  it('a one-day suite x day is not a trend: says why, asks nothing', async () => {
    renderSection({ days: 1 })
    expect(await screen.findByText(new RegExp(ONE_DAY_HEATMAP_REASON.slice(0, 40)))).toBeInTheDocument()
    await settle()
    expect(get).not.toHaveBeenCalled()
    expect(probe.enabled.every((enabled) => !enabled)).toBe(true)
  })

  it('a one-day test x run is fine (its columns are runs)', async () => {
    renderSection({ days: 1, kinds: ['test_run'] })
    await waitFor(() => expect(heatmapCalls()).toEqual([{ kind: 'test_run', project_id: 'p1', days: 1 }]))
  })

  it.each([['test_run'], ['suite_release']] as const)('All Projects: %s needs one project, says so, asks nothing', async (kind) => {
    project.id = '__ALL__'
    renderSection({ kinds: [kind] })
    expect(await screen.findByText(new RegExp(String(HEATMAP_KIND_SPECS[kind].allProjectsReason).slice(0, 30)))).toBeInTheDocument()
    await settle()
    expect(get).not.toHaveBeenCalled()
  })

  it('All Projects: suite x day asks with no project', async () => {
    project.id = '__ALL__'
    renderSection()
    await waitFor(() => expect(heatmapCalls()).toEqual([{ kind: 'suite_day', days: 14 }]))
  })

  it('a server error is the frame’s error, with no draw', async () => {
    delete responses.suite_day
    renderSection()
    await waitFor(() => expect(frameState()).toBe('error'))
    expect(engine.load).not.toHaveBeenCalled()
  })
})

describe('HeatmapSection: row order and fit', () => {
  it('rows worst first by default; the select re-orders them and says so', async () => {
    renderSection()
    expect((await lastOption()).yAxis.data[0]).toBe('legacy-import')
    const select = screen.getByRole('combobox', { name: HEATMAP_SORT_LABEL })
    expect(select).toHaveValue('worst')
    engine.instance.setOption.mockClear()
    fireEvent.change(select, { target: { value: 'name' } })
    expect((await lastOption()).yAxis.data.slice(0, 2)).toEqual(['admin', 'auth'])
    expect(section()?.querySelector('[data-heatmap-rows]')?.textContent).toMatch(/^Rows: by name\./)
    // Through the page's one announcer, never a live region of the section's own.
    await waitFor(() => expect(document.querySelector('[data-chart-announcer="assertive"]')).toHaveTextContent('Rows: by name.'))
    expect(section()?.querySelector('[aria-live]')).toBeNull()
    // No new request: a permutation of the same rows.
    expect(heatmapCalls()).toHaveLength(1)
  })

  it('fit to data: a pressed toggle that narrows the ramp to the measured range', async () => {
    renderSection({ kinds: ['suite_environment'] })
    let ramp = (await lastOption()).visualMap.find((v) => v.id === 'ramp')
    expect([ramp?.min, ramp?.max]).toEqual([0, 1])
    const toggle = screen.getByRole('button', { name: HEATMAP_FIT_LABEL })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    engine.instance.setOption.mockClear()
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    ramp = (await lastOption()).visualMap.find((v) => v.id === 'ramp')
    expect(ramp?.min).toBeGreaterThan(0.5)
    expect(ramp?.max).toBeLessThan(1)
    expect(section()?.querySelector('[data-heatmap-rows]')?.textContent).toContain('Colour scale fitted to the data')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
  })

  it('a status kind has no colour scale to fit: no toggle', async () => {
    renderSection({ kinds: ['test_run'] })
    await waitFor(() => expect(frameState()).toBe('ready'))
    expect(screen.queryByRole('button', { name: HEATMAP_FIT_LABEL })).toBeNull()
    expect(screen.getByRole('combobox', { name: HEATMAP_SORT_LABEL })).toBeInTheDocument()
  })

  it('hostile names reach the table as text', async () => {
    renderSection({ kinds: ['suite_environment'] })
    await waitFor(() => expect(frameState()).toBe('ready'))
    fireEvent.click(screen.getByRole('button', { name: CHART_MESSAGES.viewTable }))
    expect(screen.getByRole('rowheader', { name: HEATMAP_FRAME_HOSTILE_NAME })).toBeInTheDocument()
    expect(section()?.querySelector('img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })
})

describe('HeatmapSection: View rows on a cell (VIZ-602 seam)', () => {
  async function clickCell(x: number, y: number) {
    await lastOption()
    await waitFor(() => expect(engine.listeners.get('click')).toBeDefined())
    act(() => engine.listeners.get('click')?.({ value: [x, y, 0.5], event: { event: {} } }))
  }

  it('a click opens the cell’s executions: selectors in the URL, the chart’s scope on the request', async () => {
    renderSection({ suiteFilter: 'checkout' })
    // Drawn worst first: row 0 is legacy-import; column 0 is 2026-09-01.
    await clickCell(0, 0)
    await waitFor(() => expect(rowsCalls()).toHaveLength(1))
    expect(rowsCalls()[0]).toEqual({
      project_id: 'p1',
      days: 14,
      suite_name: 'checkout',
      metric: 'executions',
      // The rows endpoint requires the time dimension first.
      group_by: ['day', 'suite'],
      bucket_suite: 'legacy-import',
      bucket_day: '2026-09-01',
      page: 1,
      size: 50,
    })
    expect(new URLSearchParams(location.search).getAll('rows')).toEqual([
      'by~heatmap-suite_day',
      'suite~legacy-import',
      'day~2026-09-01',
    ])
    expect(await screen.findByRole('heading', { name: 'Executions in legacy-import, 2026-09-01' })).toBeInTheDocument()
  })

  it('closing the panel clears the URL and the panel', async () => {
    renderSection()
    await clickCell(0, 0)
    fireEvent.click(await screen.findByRole('button', { name: 'Close executions' }))
    await waitFor(() => expect(new URLSearchParams(location.search).getAll('rows')).toEqual([]))
    expect(screen.queryByRole('heading', { name: /Executions in/ })).toBeNull()
  })

  it('a test x run cell lists the TEST’s executions (a run is not a selectable dimension), under a top_n', async () => {
    renderSection({ kinds: ['test_run'] })
    await clickCell(0, 0)
    await waitFor(() => expect(rowsCalls()).toHaveLength(1))
    expect(rowsCalls()[0]).toMatchObject({ metric: 'executions', group_by: ['test'], top_n: 60, bucket_test: 'fp-0001' })
    expect(rowsCalls()[0]).not.toHaveProperty('bucket_day')
  })

  it('a test x run cell’s panel is titled by the TEST and expects no count: it lists every run of the test (R1B-2)', async () => {
    // The test ran 30 times in the window; the cell (one run) counted 1.
    rowsTotal.value = 30
    renderSection({ kinds: ['test_run'] })
    await clickCell(0, 0)
    await waitFor(() => expect(rowsCalls()).toHaveLength(1))
    expect(await screen.findByRole('heading', { name: 'Executions in test_checkout_total_rounds_half_up' })).toBeInTheDocument()
    await settle()
    expect(screen.queryByRole('heading', { name: /Build 1200/ })).toBeNull()
    expect(screen.queryByText(/Results have changed/)).toBeNull()
    expect(screen.queryByText(/the chart counted/)).toBeNull()
  })

  it('a kind switch: the previous kind’s matrix is not drawn (nor clickable) while the new one loads (R1B-3)', async () => {
    renderSection({ kinds: ['suite_environment', 'suite_release'] })
    await waitFor(() => expect(frameState()).toBe('ready'))
    await lastOption()
    // The environment chart's click is bound (so clearing below cannot race its binding).
    await waitFor(() => expect(engine.listeners.get('click')).toBeDefined())
    // The release answer is held back until the test lets it go.
    let release: (value: unknown) => void = () => {}
    const pending = new Promise((resolve) => {
      release = resolve
    })
    const fallback = get.getMockImplementation() as (url: string, config: { params: CatalogParams }) => Promise<unknown>
    get.mockImplementation((url: string, config: { params: CatalogParams }) =>
      url === HEATMAP_URL && config.params.kind === 'suite_release'
        ? pending.then(() => ({ data: heatmapFrameHostile, headers: { 'x-request-id': 'req-release' } }))
        : fallback(url, config),
    )
    engine.listeners.clear()
    engine.instance.setOption.mockClear()
    fireEvent.click(within(screen.getByRole('group', { name: HEATMAP_KIND_LABEL })).getAllByRole('radio')[1])
    await waitFor(() => expect(heatmapCalls().map((params) => params.kind)).toEqual(['suite_environment', 'suite_release']))
    await settle()
    // Loading, not the environment matrix under release words: no chart, no cell to click.
    expect(frameState()).toBe('loading')
    expect(engine.instance.setOption).not.toHaveBeenCalled()
    // No chart bound a click since the switch: nothing on screen can be activated.
    expect(engine.listeners.get('click')).toBeUndefined()
    await settle()
    expect(rowsCalls()).toEqual([])
    expect(new URLSearchParams(location.search).getAll('rows')).toEqual([])
    // The release answer arrives: drawn, and its cells select by release.
    await act(async () => {
      release(undefined)
      await pending
    })
    await waitFor(() => expect(frameState()).toBe('ready'))
    await waitFor(() => expect(engine.listeners.get('click')).toBeDefined())
    act(() => engine.listeners.get('click')?.({ value: [0, 0, 0.5], event: { event: {} } }))
    await waitFor(() => expect(rowsCalls()).toHaveLength(1))
    expect(rowsCalls()[0]).toHaveProperty('bucket_release')
    expect(rowsCalls()[0]).not.toHaveProperty('bucket_environment')
  })

  it('a rows selection another host owns (or an untagged one) opens nothing here (FK4-1)', async () => {
    for (const url of [
      '/trends?rows=by~trends-other&rows=suite~payments&rows=day~2026-09-01',
      '/trends?rows=suite~payments&rows=day~2026-09-01',
    ]) {
      const view = renderSection({}, url)
      await lastOption()
      await settle()
      expect(rowsCalls(), url).toEqual([])
      expect(screen.queryByRole('heading', { name: /Executions in/ })).toBeNull()
      view.unmount()
    }
  })

  it('its own pasted selection reopens the panel, titled from the drawn matrix, with no expectation', async () => {
    renderSection({}, '/trends?rows=by~heatmap-suite_day&rows=suite~legacy-import&rows=day~2026-09-01')
    await waitFor(() => expect(rowsCalls()).toHaveLength(1))
    expect(rowsCalls()[0]).toMatchObject({ bucket_suite: 'legacy-import', bucket_day: '2026-09-01' })
    expect(await screen.findByRole('heading', { name: 'Executions in legacy-import, 2026-09-01' })).toBeInTheDocument()
  })

  it('a test x run selection is the heatmap’s only under its own owner; its title is the test (FK4-1)', async () => {
    const view = renderSection({ kinds: ['test_run'] }, '/s?rows=by~scatter-suite&rows=test~fp-0001')
    await lastOption()
    await settle()
    expect(rowsCalls()).toEqual([])
    view.unmount()
    renderSection({ kinds: ['test_run'] }, '/s?rows=by~heatmap-test_run&rows=test~fp-0001')
    await waitFor(() => expect(rowsCalls()).toHaveLength(1))
    expect(rowsCalls()[0]).toMatchObject({ group_by: ['test'], top_n: 60, bucket_test: 'fp-0001' })
  })

  it('a selection of another kind’s shape under this owner is not asked with the wrong group_by', async () => {
    renderSection({}, '/trends?rows=by~heatmap-suite_day&rows=suite~payments&rows=environment~ci')
    await lastOption()
    await settle()
    expect(rowsCalls()).toEqual([])
  })

  it('a cell nobody ran opens nothing', async () => {
    renderSection()
    // admin did not run on 2026-09-04 (a null cell, n 0): drawn row of admin, column 3.
    const option = await lastOption()
    const admin = option.yAxis.data.indexOf('admin')
    await clickCell(3, admin)
    await settle()
    expect(rowsCalls()).toEqual([])
  })
})
