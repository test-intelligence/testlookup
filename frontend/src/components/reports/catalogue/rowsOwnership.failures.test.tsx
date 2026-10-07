/**
 * FK3-1 (integrator): the Failures page holds THREE rows hosts reading the one
 * `rows` URL key — the failure groups (`[error_signature]`), the drill ladder
 * (`[suite]`, `[suite, status]`, `[status]`, `[test, status]`) and the
 * project-wide scatter (`[test]`). Mounted together (they shared a page as one
 * composite until UX redesign P3), with the real hook and routing: every "View rows" opens exactly ONE
 * panel, its opener's, and so does every pasted link.
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
  useCatalogueParams: (days: number, _suiteFilter: unknown, extra?: Record<string, unknown>) => ({
    project_id: 'p1',
    days,
    ...(extra ?? {}),
  }),
}))
vi.mock('@/store/projectStore', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/store/projectStore')>()),
  useProjectStore: (selector: (state: { activeProjectId: string }) => unknown) => selector({ activeProjectId: 'p1' }),
}))
vi.mock('@/components/charts/chartCatalogSources', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/components/charts/chartCatalogSources')>()),
  useCatalogChartData: () => ({ status: 'loading' }),
}))
vi.mock('@/hooks/useChartData', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useChartData')>()),
  useChartData: () => ({ status: 'loading' }),
}))

const handlers = vi.hoisted(() => ({ groups: null as MarkActivateHandler | null, ladder: null as MarkActivateHandler | null }))
vi.mock('@/components/charts/FailureGroupsFrame', () => ({
  default: (props: { onMarkActivate?: MarkActivateHandler }) => {
    handlers.groups = props.onMarkActivate ?? null
    return null
  },
}))
vi.mock('@/components/charts/BarChart', () => ({
  default: (props: { onMarkActivate?: MarkActivateHandler }) => {
    handlers.ladder = props.onMarkActivate ?? null
    return null
  },
}))
vi.mock('@/components/charts/failureGroups/FailureGroupPanel', () => ({ default: () => null }))
vi.mock('@/components/charts/failureGroups/SystemicClusters', () => ({ default: () => null }))
vi.mock('@/components/charts/ChartFrame', () => ({ default: () => null }))
vi.mock('@/components/charts/TestScatter', () => ({ default: () => null }))

/** The panels as last rendered, by host (each host asks a different rows chart). */
const panels = vi.hoisted(() => new Map<string, RowsPanelProps>())
vi.mock('./RowsPanel', () => ({
  default: (props: RowsPanelProps) => {
    const host =
      props.chart.groupBy.join(',') === 'error_signature'
        ? 'groups'
        : props.chart.metric === 'failure_rate'
          ? 'scatter'
          : 'ladder'
    panels.set(host, props)
    return null
  },
}))

const { FailureGroupsSection } = await import('./FailureGroupsSection')
const { FailuresDrill } = await import('./FailuresDrill')
const { ScatterSection } = await import('./ScatterSection')

let router: ReturnType<typeof createMemoryRouter>

function mount(entry = '/failures') {
  router = createMemoryRouter(
    [
      {
        path: '/failures',
        element: (
          <>
            <FailureGroupsSection days={30} suiteFilter={null} />
            <FailuresDrill days={30} suiteFilter={null} />
            <ScatterSection days={30} suiteFilter={null} placement="project" />
          </>
        ),
      },
    ],
    { initialEntries: [entry] },
  )
  render(<RouterProvider router={router} />)
}

const open = () => [...panels.entries()].filter(([, p]) => p.selectors.length > 0).map(([host]) => host)
const rowsParam = () => new URLSearchParams(router.state.location.search).getAll('rows')

beforeEach(() => {
  panels.clear()
  handlers.groups = null
  handlers.ladder = null
})

describe('three rows hosts on the Failures page, one rows key', () => {
  it("a failure group's View rows opens the groups' panel only", async () => {
    mount()
    const mark: ChartMark = { dimension: 'error_signature', value: 'timeout #', label: 'Timeout', y: 4, n: null }
    await act(async () => handlers.groups?.(mark, 'rows'))
    expect(rowsParam()).toEqual(['by~failures-groups', 'error_signature~timeout #'])
    expect(open()).toEqual(['groups'])
  })

  it("the ladder's View rows on a suite opens the ladder's panel only", async () => {
    mount()
    const mark: ChartMark = { dimension: 'suite', value: 'payments', label: 'Payments', y: 3, n: 10 }
    await act(async () => handlers.ladder?.(mark, 'rows'))
    expect(rowsParam()[0]).toBe('by~failures-drill')
    expect(open()).toEqual(['ladder'])
    await act(async () => router.navigate(-1))
    expect(open()).toEqual([])
  })

  it.each([
    ['rows=by~failures-groups&rows=error_signature~timeout%20%23', ['groups']],
    ['rows=by~failures-drill&rows=suite~payments', ['ladder']],
    ['rows=by~failures-drill&rows=suite~payments&rows=status~failed', ['ladder']],
    ['rows=by~scatter-project&rows=test~fp-1', ['scatter']],
    // Another host's tag on a shape this host would take: nobody else opens.
    ['rows=by~failures-drill&rows=error_signature~timeout', []],
    ['rows=by~failures-groups&rows=test~fp-1', []],
    ['rows=by~failures-groups&rows=suite~payments', []],
    // Untagged: nobody.
    ['rows=error_signature~timeout', []],
    ['rows=test~fp-1', []],
  ])('a pasted link ?%s opens exactly its owner’s panel', (query, hosts) => {
    mount(`/failures?${query}`)
    expect(open()).toEqual(hosts)
  })
})
