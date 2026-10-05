/**
 * FK4-1 (integrator): Suite detail holds two rows hosts that open a panel on
 * the SAME selector shape, a lone `test` — the test x run heatmap's cell and
 * the scatter's point. Both read the one `rows` URL key; the owner entry
 * (`rows=by~<section id>`, `useDrillPath`) decides whose panel it is. Mounted
 * together, exactly as the page does, with real routing and the real hook:
 * only the opener's panel opens, a pasted link opens exactly one, and Back /
 * Forward close and reopen that one.
 */
import { act, render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChartMark, MarkActivateHandler } from '@/components/charts/marks'
import type { RowsPanelProps } from './RowsPanel.model'

vi.mock('./LazySection', () => ({ default: ({ children }: { children: ReactNode }) => <>{children}</> }))
vi.mock('./useEverHadRun', () => ({ useEverHadRun: () => true }))
vi.mock('./catalogueScope', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./catalogueScope')>()),
  useCatalogueParams: (days: number, suiteFilter: unknown, extra?: Record<string, unknown>) => ({
    project_id: 'p1',
    days,
    suite_name: suiteFilter,
    ...(extra ?? {}),
  }),
}))
vi.mock('@/store/projectStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/projectStore')>()),
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: 'p1' }),
}))
// Data stays loading: who opens a panel does not depend on the answer.
vi.mock('@/components/charts/chartCatalogSources', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/charts/chartCatalogSources')>()),
  useCatalogChartData: () => ({ status: 'loading' }),
}))
vi.mock('@/hooks/useChartData', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useChartData')>()),
  useChartData: () => ({ status: 'loading' }),
}))

/** Each chart's activation handler, as its frame received it. */
const handlers = vi.hoisted(() => ({ heatmap: null as MarkActivateHandler | null, scatter: null as MarkActivateHandler | null }))
vi.mock('@/components/charts/HeatmapChartFrame', () => ({
  default: (props: { onMarkActivate?: MarkActivateHandler }) => {
    handlers.heatmap = props.onMarkActivate ?? null
    return null
  },
}))
vi.mock('@/components/charts/ChartFrame', () => ({ default: () => null }))
vi.mock('@/components/charts/TestScatter', () => ({ default: () => null }))

/** The panels as last rendered, by host (the chart spec tells them apart). */
const panels = vi.hoisted(() => new Map<string, RowsPanelProps>())
vi.mock('./RowsPanel', () => ({
  default: (props: RowsPanelProps) => {
    // The heatmap's test rows carry the matrix's top_n; the scatter's do not.
    panels.set(props.chart.topN === 60 ? 'heatmap' : 'scatter', props)
    return null
  },
}))

const { HeatmapSection } = await import('./HeatmapSection')
const { ScatterSection } = await import('./ScatterSection')

let router: ReturnType<typeof createMemoryRouter>

function mount(entry: string) {
  router = createMemoryRouter(
    [
      {
        path: '/coverage/suite',
        element: (
          <>
            <HeatmapSection days={30} suiteFilter="Auth" kinds={['test_run']} />
            <ScatterSection days={30} suiteFilter="Auth" placement="suite" />
          </>
        ),
      },
    ],
    { initialEntries: [entry] },
  )
  render(<RouterProvider router={router} />)
}

/** The hosts whose panel is open. */
const open = () => [...panels.entries()].filter(([, p]) => p.selectors.length > 0).map(([host]) => host)

const testMark = (value: string): ChartMark => ({ dimension: 'test', value, label: `test ${value}`, y: 50, n: 4 })

// The scatter hands its handler to TestScatter, which only mounts with data:
// open its rows the way its handler does, through the hook's own URL write.
const scatterOpens = (value: string) =>
  act(async () => {
    await router.navigate(`/coverage/suite?name=Auth&rows=by~scatter-suite&rows=test~${value}`)
  })

beforeEach(() => {
  panels.clear()
  handlers.heatmap = null
})

describe('two rows hosts on one page (Suite detail), one rows key', () => {
  it("a heatmap cell's View rows opens the heatmap's panel ONLY, though the scatter claims a lone test too", async () => {
    mount('/coverage/suite?name=Auth')
    expect(open()).toEqual([])
    await act(async () => handlers.heatmap?.(testMark('fp-1'), 'rows'))
    expect(new URLSearchParams(router.state.location.search).getAll('rows')).toEqual(['by~heatmap-test_run', 'test~fp-1'])
    expect(open()).toEqual(['heatmap'])
    expect(panels.get('heatmap')?.selectors).toEqual([{ dimension: 'test', value: 'fp-1' }])
  })

  it('Back closes the panel, Forward reopens the same one and only that one', async () => {
    mount('/coverage/suite?name=Auth')
    await act(async () => handlers.heatmap?.(testMark('fp-1'), 'rows'))
    await act(async () => router.navigate(-1))
    expect(open()).toEqual([])
    await act(async () => router.navigate(1))
    expect(open()).toEqual(['heatmap'])
  })

  it("the scatter's selection opens the scatter's panel only", async () => {
    mount('/coverage/suite?name=Auth')
    await scatterOpens('fp-2')
    expect(open()).toEqual(['scatter'])
  })

  it.each([
    ['/coverage/suite?name=Auth&rows=by~heatmap-test_run&rows=test~fp-1', ['heatmap']],
    ['/coverage/suite?name=Auth&rows=by~scatter-suite&rows=test~fp-1', ['scatter']],
    ['/coverage/suite?name=Auth&rows=test~fp-1', []],
    ['/coverage/suite?name=Auth&rows=by~coverage-map&rows=test~fp-1', []],
  ])('a pasted link %s opens exactly its owner’s panel', (entry, hosts) => {
    mount(entry)
    expect(open()).toEqual(hosts)
  })
})
