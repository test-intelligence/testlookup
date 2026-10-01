/**
 * The Release gate's "Context" group (VIZ-408, plan 2.2 and 2.5): which
 * releases are compared, what is asked on the wire, the no-release and
 * one-release states, the registry's donut / ranked-bar choice, the captions,
 * and the rules that keep an explanatory chart from implying a verdict.
 *
 * Only the network is mocked (`api.get`, under the real `chartGet` and
 * `useChartData`), plus the release list the top bar already fetches.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Release } from '@/types/releases'
import type { EnvelopeMeta } from '@/lib/viz/contracts'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/services/api', () => ({ api: { get } }))

const releasesState = vi.hoisted(() => ({
  value: { data: undefined as { items: Release[] } | undefined, error: undefined as unknown, mutate: () => {} },
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => releasesState.value }))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: unknown) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Payments' } }),
}))

// jsdom lays nothing out: the charts' containers get a fixed box, the rest is real.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 280 })
        : null,
  }
})

import { __resetChartConcurrency } from '@/services/chartApi'
import GateCatalogue, { ClusterShare } from './GateCatalogue'
import {
  CLUSTERS_TITLE,
  contextNote,
  formatAsOf,
  liveCaption,
  measuredSeriesCount,
  NOT_ATTRIBUTED_NOTE,
  planReleaseComparison,
  readGateRun,
  RELEASES_POPULATION,
  RELEASES_TITLE,
  sideRequestState,
  storedCaption,
  tooFewNote,
  type GateRun,
} from './GateCatalogue.model'
import gateSourceRaw from './GateCatalogue.tsx?raw'
import gateModelRaw from './GateCatalogue.model.ts?raw'

/** The source without its comments (which name what it must not do). */
const gateSource = (gateSourceRaw + gateModelRaw).replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')

const HOSTILE = '<img src=x onerror="window.__xss=1">'

function release(id: string, name: string, overrides: Partial<Release> = {}): Release {
  return {
    id,
    project_id: 'proj-1',
    name,
    version: null,
    description: null,
    status: 'released',
    planned_date: null,
    released_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    phases: [],
    ...overrides,
  }
}

/** Seven releases of proj-1 (newest first by start: r7 … r1) and one of another project. */
const RELEASES: Release[] = [
  release('r1', 'R1', { released_at: '2026-05-01T00:00:00Z' }),
  release('r2', 'R2', { released_at: '2026-06-01T00:00:00Z' }),
  release('r3', 'R3', { planned_date: '2026-07-01' }),
  release('r4', 'R4', { released_at: '2026-07-15T00:00:00Z' }),
  release('r5', 'R5', { released_at: '2026-08-01T00:00:00Z' }),
  release('r6', 'R6', { released_at: '2026-08-15T00:00:00Z', test_run_count: 0 }),
  release('r7', 'R7', { released_at: '2026-09-01T00:00:00Z' }),
  release('x1', 'Other project', { project_id: 'proj-2', released_at: '2026-09-20T00:00:00Z' }),
]

const META: EnvelopeMeta = {
  schema_version: 1,
  scope: {
    projects: [{ id: 'proj-1', name: 'Payments' }],
    releases: [],
    suites: [],
    window: { from: '2026-07-01', to: '2026-09-28', days: 90, timezone: 'UTC' },
  },
  totals: { matched_runs: 4, total_runs: 4, matched_executions: 40, total_executions: 40 },
  pass_rate_basis: 'executions',
  ignored_filters: [],
  truncated: false,
  truncated_total: null,
  measured: true,
  reason: null,
  includes_in_progress: 0,
  partial_day: null,
  generated_at: '2026-09-28T09:30:00Z',
  as_of: '2026-09-28T09:30:00Z',
}

type Point = { x: string; y: number | null; n: number }
function releaseSeries(key: string, label: string, ys: (number | null)[], start = '2026-09-01'): { key: string; label: string; points: Point[] } {
  const base = Date.parse(`${start}T00:00:00Z`)
  return {
    key,
    label,
    points: ys.map((y, i) => ({ x: new Date(base + i * 86_400_000).toISOString().slice(0, 10), y, n: y === null ? 0 : 10 })),
  }
}

function chartPayload(series: ReturnType<typeof releaseSeries>[], meta: EnvelopeMeta = META) {
  return { meta, series: { kind: 'series', dimensions: ['day', 'release'], x_type: 'time', series } }
}

const TWO_RELEASES = chartPayload([
  releaseSeries('r7', 'R7', [90, 92, 94]),
  releaseSeries('r5', 'R5', [80, null, 85], '2026-08-01'),
])

interface Routes {
  run?: unknown | Error
  chart?: unknown | Error
}

let routes: Routes = {}

function response(data: unknown) {
  return Promise.resolve({ data, headers: { 'x-request-id': 'req-1' } })
}

function httpError(status: number) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, headers: {}, data: {} } })
}

beforeEach(() => {
  routes = {
    run: { id: 'run-1', project_id: 'proj-1', release_id: 'r7', release_name: 'R7' },
    chart: TWO_RELEASES,
  }
  releasesState.value = { data: { items: RELEASES }, error: undefined, mutate: () => {} }
  get.mockReset()
  get.mockImplementation((url: string) => {
    const route = url.startsWith('/api/v1/runs/') ? routes.run : url === '/api/v1/analytics/chart-data' ? routes.chart : undefined
    if (route === undefined) return Promise.reject(httpError(404))
    return route instanceof Error ? Promise.reject(route) : response(route)
  })
})

afterEach(() => {
  __resetChartConcurrency()
  vi.useRealTimers()
})

function renderGate(props: Partial<Parameters<typeof GateCatalogue>[0]> = {}) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <GateCatalogue runId="run-1" build="b-42" clusters={[]} {...props} />
    </SWRConfig>,
  )
}

const calls = (prefix: string) => get.mock.calls.filter(([url]) => (url as string).startsWith(prefix))
const chartCalls = () => calls('/api/v1/analytics/chart-data')
const section = (id: string) => document.querySelector(`[data-catalogue-section="${id}"]`) as HTMLElement | null

const clusters = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ cluster_id: `c${i}`, id: `c${i}`, label: `Cluster ${i}`, size: 10 - i }))

// ── Which releases ───────────────────────────────────────────────────────────

describe('planReleaseComparison', () => {
  const run = (releaseId: string | null): GateRun => ({ releaseId, releaseName: releaseId ? 'R-own' : null, projectId: 'proj-1' })

  it("puts the run's release first, then the four most recent others of ITS project, with start dates", () => {
    const plan = planReleaseComparison(run('r3'), RELEASES, 'proj-1')
    // r6 has no runs; x1 is another project's; r3 is the run's own.
    expect(plan.releaseIds).toEqual(['r3', 'r7', 'r5', 'r4', 'r2'])
    expect(plan.attributed).toBe(true)
    // Released, else planned.
    expect(plan.starts).toEqual({ r3: '2026-07-01', r7: '2026-09-01', r5: '2026-08-01', r4: '2026-07-15', r2: '2026-06-01' })
    expect(plan.names.r3).toBe('R3')
  })

  it('compares the five most recent releases when the run has none', () => {
    const plan = planReleaseComparison(run(null), RELEASES, 'proj-1')
    expect(plan.releaseIds).toEqual(['r7', 'r5', 'r4', 'r3', 'r2'])
    expect(plan.attributed).toBe(false)
  })

  it("keeps the run's release when the list does not hold it: its own name, no start date", () => {
    const plan = planReleaseComparison(run('gone'), RELEASES, 'proj-1')
    expect(plan.releaseIds[0]).toBe('gone')
    expect(plan.names.gone).toBe('R-own')
    expect(plan.starts.gone).toBeUndefined()
  })

  it('orders an undated release by its creation date, and ties by name, then id', () => {
    const undated = [
      release('a', 'Same', { created_at: '2026-03-01T00:00:00Z' }),
      release('b', 'Same', { created_at: '2026-03-01T00:00:00Z' }),
      release('c', 'Alpha', { created_at: '2026-03-01T00:00:00Z' }),
      release('d', 'New', { created_at: '2026-04-01T00:00:00Z' }),
    ]
    expect(planReleaseComparison(run(null), undated, 'proj-1').releaseIds).toEqual(['d', 'c', 'a', 'b'])
  })

  it('a release id that names an Object.prototype member is an entry like any other', () => {
    const odd = [
      release('__proto__', 'Proto release', { released_at: '2026-09-10T00:00:00Z' }),
      release('constructor', 'Ctor release', { released_at: '2026-09-09T00:00:00Z' }),
    ]
    const plan = planReleaseComparison(run('__proto__'), odd, 'proj-1')
    expect(plan.releaseIds).toEqual(['__proto__', 'constructor'])
    expect(Object.prototype.hasOwnProperty.call(plan.names, '__proto__')).toBe(true)
    expect(plan.names.__proto__ as unknown).toBe('Proto release')
    expect(plan.names.constructor as unknown).toBe('Ctor release')
    expect(Object.prototype.hasOwnProperty.call(plan.starts, '__proto__')).toBe(true)
    expect(plan.starts.__proto__ as unknown).toBe('2026-09-10')
  })

  it('takes every project when the project is unknown', () => {
    expect(planReleaseComparison(run(null), RELEASES, null).releaseIds[0]).toBe('x1')
  })
})

describe('small pieces', () => {
  it('reads the run defensively', () => {
    expect(readGateRun({ release_id: 'r1', release_name: 'R1', project_id: 'p' })).toEqual({ releaseId: 'r1', releaseName: 'R1', projectId: 'p' })
    expect(readGateRun({ release_id: '', release_name: 3 })).toEqual({ releaseId: null, releaseName: null, projectId: null })
    expect(readGateRun(null)).toEqual({ releaseId: null, releaseName: null, projectId: null })
  })

  it('formats as_of in UTC, and an unreadable one as text', () => {
    expect(formatAsOf('2026-09-28T09:30:00Z')).toBe('2026-09-28 09:30 UTC')
    expect(formatAsOf('yesterday')).toBe('yesterday')
    expect(liveCaption(null, 'b1')).toMatch(/^Live data\. The verdict above is the stored decision for build b1/)
  })

  it('says why there is no comparison, attributed or not', () => {
    expect(tooFewNote(1, true)).toBe('A release comparison needs at least two releases with runs in the last 90 days; only one has any.')
    expect(tooFewNote(0, false)).toMatch(/^This run is not attributed to a release, and .* none has any\.$/)
  })

  it('maps a failed side request to the frame state it deserves', () => {
    const retry = vi.fn()
    expect(sideRequestState(httpError(500), retry)).toMatchObject({ status: 'error', retry })
    expect(sideRequestState(httpError(403), retry)).toMatchObject({ status: 'forbidden' })
    expect(sideRequestState(Object.assign(new Error('x'), { name: 'CanceledError' }), retry)).toEqual({ status: 'loading' })
  })

  it('counts only series that measured a day', () => {
    const payload = chartPayload([releaseSeries('a', 'A', [null, null]), releaseSeries('b', 'B', [50])])
    expect(measuredSeriesCount(payload as never)).toBe(1)
    expect(measuredSeriesCount({ meta: null, series: { kind: 'matrix' } } as never)).toBe(0)
  })
})

// ── The comparison, on the wire and on screen ───────────────────────────────

describe('pass rate by release', () => {
  it('asks once for the run and once for chart-data: pass rate, day x release, 90 days, the five releases', async () => {
    renderGate()
    const frame = await screen.findByRole('heading', { level: 3, name: RELEASES_TITLE })
    expect(section('gate-releases')).toContainElement(frame)
    await waitFor(() => expect(chartCalls()).toHaveLength(1))
    expect(calls('/api/v1/runs/')).toHaveLength(1)
    expect(calls('/api/v1/runs/')[0][0]).toBe('/api/v1/runs/run-1')
    const [, config] = chartCalls()[0] as [string, { params: Record<string, unknown>; suppressToast: boolean }]
    expect(config.params).toEqual({
      metric: 'pass_rate',
      group_by: ['day', 'release'],
      project_id: 'proj-1',
      days: 90,
      release_id: ['r2', 'r3', 'r4', 'r5', 'r7'],
    })
    // Both through chartGet: a failure is the frame's, never a toast.
    for (const [, cfg] of get.mock.calls) expect((cfg as { suppressToast?: boolean }).suppressToast).toBe(true)
  })

  it('reads the run ONCE: no polling while the page stays open (useRun polls every 5 s)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderGate()
    await waitFor(() => expect(chartCalls()).toHaveLength(1))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000)
    })
    expect(calls('/api/v1/runs/')).toHaveLength(1)
    expect(chartCalls()).toHaveLength(1)
    expect(gateSource).not.toMatch(/\buseRun\b/)
  })

  it('draws the releases aligned on their start, with the population, the scope and the dated "stored" caption', async () => {
    renderGate()
    const root = await waitFor(() => {
      const el = section('gate-releases')
      expect(el?.querySelector('[data-chart-frame] svg')).toBeTruthy()
      return el as HTMLElement
    })
    expect(within(root).getByText(RELEASES_POPULATION)).toBeVisible()
    // Inside the frame, in its footer (R2-21: plan 2.5.3 says "in every frame").
    const caption = root.querySelector('[data-chart-frame] [data-chart-footer] [data-gate-caption="live"]')
    expect(caption?.textContent).toBe(
      'Live data as of 2026-09-28 09:30 UTC. The verdict above is the stored decision for build b-42; it is not computed from this chart.',
    )
    expect(root.textContent).toMatch(/Days since release start/i)
    expect(root.querySelector('[data-gate-note="not-attributed"]')).toBeNull()
  })

  it('no release on the run: compares the five most recent releases and says the run is not attributed', async () => {
    routes.run = { id: 'run-1', project_id: 'proj-1', release_id: null }
    renderGate()
    await waitFor(() => expect(chartCalls()).toHaveLength(1))
    expect((chartCalls()[0][1] as { params: { release_id: string[] } }).params.release_id).toEqual(['r2', 'r3', 'r4', 'r5', 'r7'])
    expect(await screen.findByText(NOT_ATTRIBUTED_NOTE)).toBeVisible()
  })

  it('one release: one sentence, no frame, and no chart-data request at all', async () => {
    releasesState.value = { data: { items: [RELEASES[6]] }, error: undefined, mutate: () => {} }
    renderGate()
    const note = await screen.findByText(tooFewNote(1, true))
    expect(section('gate-releases')).toContainElement(note)
    expect(section('gate-releases')?.querySelector('[data-chart-frame]')).toBeNull()
    expect(chartCalls()).toHaveLength(0)
  })

  it('fewer than two releases WITH RUNS in the answer: the sentence, never an empty frame', async () => {
    routes.chart = chartPayload([releaseSeries('r7', 'R7', [90, 91]), releaseSeries('r5', 'R5', [null, null])])
    renderGate()
    const note = await screen.findByText(tooFewNote(1, true))
    expect(section('gate-releases')).toContainElement(note)
    expect(section('gate-releases')?.querySelector('[data-chart-frame]')).toBeNull()
  })

  it('an answer with no series at all is the sentence too (none has any)', async () => {
    routes.chart = chartPayload([])
    renderGate()
    expect(await screen.findByText(tooFewNote(0, true))).toBeVisible()
    expect(section('gate-releases')?.querySelector('[data-chart-frame]')).toBeNull()
  })

  it('a failing chart-data request is the frame error with Retry, and the caption stays', async () => {
    routes.chart = httpError(500)
    renderGate()
    const root = await waitFor(() => {
      const el = section('gate-releases') as HTMLElement
      expect(within(el).getByRole('button', { name: /retry/i })).toBeVisible()
      return el
    })
    expect(root.querySelector('[data-gate-caption="live"]')?.textContent).toMatch(/^Live data\. /)
    routes.chart = TWO_RELEASES
    await act(async () => within(root).getByRole('button', { name: /retry/i }).click())
    await waitFor(() => expect(root.querySelector('[data-chart-frame] svg')).toBeTruthy())
  })

  it('a failing run request is the same frame error, and Retry asks again', async () => {
    routes.run = httpError(500)
    renderGate()
    const root = section('gate-releases') as HTMLElement
    const retry = await within(root).findByRole('button', { name: /retry/i })
    expect(chartCalls()).toHaveLength(0)
    routes.run = { id: 'run-1', project_id: 'proj-1', release_id: 'r7' }
    await act(async () => retry.click())
    await waitFor(() => expect(chartCalls()).toHaveLength(1))
  })

  it('a failing release list is the frame error too', async () => {
    const mutate = vi.fn()
    releasesState.value = { data: undefined, error: httpError(500), mutate }
    renderGate()
    const retry = await within(section('gate-releases') as HTMLElement).findByRole('button', { name: /retry/i })
    await act(async () => retry.click())
    expect(mutate).toHaveBeenCalled()
  })

  it('stays loading (no request) until the release list arrives', async () => {
    releasesState.value = { data: undefined, error: undefined, mutate: () => {} }
    renderGate()
    await waitFor(() => expect(calls('/api/v1/runs/')).toHaveLength(1))
    expect(chartCalls()).toHaveLength(0)
    expect(section('gate-releases')?.querySelector('[data-chart-frame]')).toBeTruthy()
  })

  it('names a release whose id is `__proto__` in the scope sentence, never "[object Object]"', async () => {
    releasesState.value = {
      data: { items: [release('__proto__', 'Proto release', { released_at: '2026-09-01T00:00:00Z' }), RELEASES[6]] },
      error: undefined,
      mutate: () => {},
    }
    routes.run = { id: 'run-1', project_id: 'proj-1', release_id: '__proto__' }
    routes.chart = chartPayload([releaseSeries('__proto__', 'Proto release', [90, 92]), releaseSeries('r7', 'R7', [80, 81])])
    renderGate()
    await waitFor(() => expect(section('gate-releases')?.querySelector('[data-chart-frame] svg')).toBeTruthy())
    const text = (section('gate-releases') as HTMLElement).textContent ?? ''
    expect(text).toContain('releases Proto release, R7')
    expect(text).not.toContain('[object Object]')
  })

  it('a hostile release name is text in the chart, never markup', async () => {
    routes.chart = chartPayload([releaseSeries('r7', HOSTILE, [90, 92]), releaseSeries('r5', 'R5', [80, 81])])
    renderGate()
    await waitFor(() => expect(section('gate-releases')?.querySelector('[data-chart-frame] svg')).toBeTruthy())
    expect(document.querySelector('img')).toBeNull()
    expect((window as { __xss?: unknown }).__xss).toBeUndefined()
  })
})

// ── Cluster share ───────────────────────────────────────────────────────────

describe('failure cluster share', () => {
  it('a donut at five clusters', async () => {
    renderGate({ clusters: clusters(5) })
    const root = section('gate-clusters') as HTMLElement
    expect(within(root).getByRole('heading', { level: 3, name: CLUSTERS_TITLE })).toBeVisible()
    expect(root.querySelector('[data-chart-choice]')).toHaveAttribute('data-chart-choice', 'donut')
  })

  it('a ranked bar at six, and no pie offered', async () => {
    renderGate({ clusters: clusters(6) })
    const choice = section('gate-clusters')?.querySelector('[data-chart-choice]')
    expect(choice).toHaveAttribute('data-chart-choice', 'ranked-bar')
    expect(choice).toHaveAttribute('data-chart-offers-pie', 'false')
  })

  it('carries the "stored" caption: its data is the decision, nothing recomputed', () => {
    renderGate({ clusters: clusters(3) })
    expect(section('gate-clusters')?.querySelector('[data-gate-caption="stored"]')?.textContent).toBe(storedCaption('b-42'))
    expect(storedCaption('b-42')).toMatch(/Nothing here is recomputed/)
  })

  // R2-21: the dated caption sat under the frame's border, outside the frame.
  // It is in the frame's own footer now, for the donut and the ranked bar both.
  it.each([
    ['donut', 3],
    ['ranked-bar', 7],
  ] as const)('the "stored" caption is inside the %s frame\'s footer, once', (choice, n) => {
    renderGate({ clusters: clusters(n) })
    const root = section('gate-clusters') as HTMLElement
    expect(root.querySelector('[data-chart-choice]')).toHaveAttribute('data-chart-choice', choice)
    expect(root.querySelectorAll('[data-gate-caption="stored"]')).toHaveLength(1)
    const caption = root.querySelector('[data-chart-frame] [data-chart-footer] [data-gate-caption="stored"]')
    expect(caption?.textContent).toBe(storedCaption('b-42'))
  })

  it('is left out when no stored cluster has a size: no empty frame', () => {
    renderGate({ clusters: [{ cluster_id: 'c', label: 'x', size: 0 }] })
    expect(section('gate-clusters')).toBeNull()
  })

  it('a hostile cluster label is text', () => {
    render(<ClusterShare clusters={[{ cluster_id: 'c', label: HOSTILE, size: 3 }, { cluster_id: 'd', label: 'D', size: 1 }]} build="b" />)
    expect(document.querySelector('img')).toBeNull()
  })
})

// ── The group and the verdict rules (plan 2.5) ─────────────────────────────

describe('the Context group', () => {
  it('is a labelled section with the "explains, does not decide" note', () => {
    renderGate()
    const group = screen.getByRole('region', { name: 'Context' })
    expect(group).toHaveAttribute('data-catalogue-section', 'gate-context')
    expect(within(group).getByText(contextNote('b-42'))).toBeVisible()
  })

  // R2-9: side by side at xl, the release frame (530 px, the chart that needs
  // the width) got half the column next to a 270 px donut and a 260 px hole.
  it('is ONE column at every width: the release comparison full width, the cluster share on its own row below', () => {
    const { unmount } = renderGate({ clusters: clusters(3) })
    const grid = document.querySelector('[data-gate-context-grid]') as HTMLElement
    const classes = grid.className.split(/\s+/)
    expect(classes).toContain('grid-cols-1')
    expect(classes.filter((c) => /grid-cols-/.test(c) && c !== 'grid-cols-1')).toEqual([])
    const rows = Array.from(grid.children)
    expect(rows).toHaveLength(2)
    expect(rows[1].getAttribute('data-catalogue-section')).toBe('gate-clusters')
    unmount()
    renderGate()
    expect((document.querySelector('[data-gate-context-grid]') as HTMLElement).className).not.toMatch(/grid-cols-[2-9]/)
  })

  // R2-8: an h3 like every other gate section, and so are its two frames: no h2 in
  // the group, so nothing after it falls under "Context" in the outline (the
  // e2e helpers read a frame's title from its h2 / h3).
  it('is headed by an h3, its two frames h3 too, and holds no h2', async () => {
    renderGate({ clusters: clusters(3) })
    const group = screen.getByRole('region', { name: 'Context' })
    expect(within(group).getByRole('heading', { name: 'Context' }).tagName).toBe('H3')
    expect(within(group).getByRole('heading', { level: 3, name: CLUSTERS_TITLE })).toBeVisible()
    expect(await within(group).findByRole('heading', { level: 3, name: RELEASES_TITLE })).toBeVisible()
    expect(within(group).queryAllByRole('heading', { level: 2 })).toEqual([])
  })

  it('draws no decision colour: no target line, no status token, no verdict tint', async () => {
    renderGate({ clusters: clusters(3) })
    await waitFor(() => expect(section('gate-releases')?.querySelector('[data-chart-frame] svg')).toBeTruthy())
    const html = (section('gate-context') as HTMLElement).outerHTML
    expect(html).not.toMatch(/--status-/)
    expect(html).not.toMatch(/rate-target|data-rate-target/i)
    expect(screen.queryByText(/target/i)).toBeNull()
    // The source cannot reach for one either.
    expect(gateSource).not.toMatch(/rateTarget|ReferenceLine|--status-|recommendation=|risk_score|riskScore/)
  })
})
