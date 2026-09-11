/**
 * M22: how many /runs requests the Overview page makes.
 *
 * The page asks three questions of /runs: the windowed list, the newest run
 * ignoring the window, and "has this project ever had a run" ignoring the
 * release filter too. Each was its own 15 s poll, so an open tab made three
 * requests per cycle even though, whenever the window has a run, the first
 * answer already implies the other two.
 *
 * Real `useRuns` and real SWR here; only `runsService.list` is faked, so the
 * count below is the count the backend would see. Each case also checks what
 * the reader sees, because "fewer requests" is trivially satisfied by a page
 * that stopped asking a question it still needs answered.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useMetrics', () => {
  const d = () => ({ data: undefined, isLoading: false })
  return {
    useDashboardSummary: vi.fn(() => ({
      data: { total_executions_7d: { value: summaryTotal.value } },
      isLoading: false,
    })),
    useTrendData: vi.fn(d),
    useFlakyTests: vi.fn(d),
    useFailureCategories: vi.fn(d),
    useTopFailing: vi.fn(d),
    useCoverage: vi.fn(d),
    useDefects: vi.fn(d),
    useSuiteDetail: vi.fn(d),
    useAiSummary: vi.fn(d),
  }
})
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/hooks/useValueMetrics', () => ({
  useValueMetricsKpi: () => ({ metrics: undefined }),
}))
vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    instances: [], widgetIds: [], addInstance: vi.fn(), removeInstance: vi.fn(),
    save: vi.fn(), reset: vi.fn(), isDirty: false, savedViews: [],
    activeViewId: null, setActiveView: vi.fn(), deleteView: vi.fn(),
    updateInstance: vi.fn(), moveInstance: vi.fn(),
  }),
}))
vi.mock('@/hooks/useIntegrationsConfig', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useIntegrationsConfig')>()
  return { ...actual, useIntegrationsConfig: () => ({ config: undefined, error: undefined, isLoading: false }) }
})
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } }
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state),
  }
})
vi.mock('@/services/runsService', () => ({
  runsService: { list: vi.fn() },
}))

import { runsService } from '@/services/runsService'
import { useReleaseStore } from '@/store/releaseStore'
import OverviewPage from './OverviewPage'

const summaryTotal = vi.hoisted(() => ({ value: 0 }))
const list = vi.mocked(runsService.list)

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString()
const run = (id: string, createdAt: string) => ({
  id, created_at: createdAt, status: 'completed', suite_name: 'api', total_tests: 1,
})

/** `windowed` answers the size-100 request; `lifetime` the size-1 probes. */
function serve({ windowed, lifetime }: { windowed: unknown[]; lifetime: unknown[] }) {
  list.mockImplementation(async (_projectId, params) => {
    const items = (params as { size?: number } | undefined)?.size === 1 ? lifetime : windowed
    return { items, total: items.length } as never
  })
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

/** Let every fetch the page is going to make land, then count. */
async function settle() {
  await waitFor(() => expect(list).toHaveBeenCalled())
  await new Promise((resolve) => setTimeout(resolve, 150))
}

const sizes = () => list.mock.calls.map(([, params]) => (params as { size?: number }).size)

describe('Overview /runs requests (M22)', () => {
  beforeEach(() => {
    localStorage.clear()
    useReleaseStore.setState({ activeReleaseId: null, scopedProjectId: null })
    list.mockReset()
  })

  it('makes ONE /runs request when the window has runs', async () => {
    summaryTotal.value = 12
    serve({ windowed: [run('r1', daysAgo(1))], lifetime: [run('r1', daysAgo(1))] })

    renderPage()
    await settle()

    expect(sizes()).toEqual([100])
    expect(screen.queryByTestId('overview-empty-window')).toBeNull()
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
  })

  it('still finds the newest run when the window is empty', async () => {
    summaryTotal.value = 0
    serve({ windowed: [], lifetime: [run('old', daysAgo(20))] })

    renderPage()
    await settle()

    // The windowed list, then one probe: with no release selected the two
    // probes have identical keys and SWR serves them from one request.
    expect(sizes()).toEqual([100, 1])
    const banner = await screen.findByTestId('overview-empty-window')
    expect(banner).toHaveTextContent(/20 days ago/)
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
  })

  it('reads the newest run from the windowed list when the summary says 0', async () => {
    // The summary can be 0 while the window has runs (a suite filter, or an
    // aggregate that has not caught up). The page must not claim "no runs".
    summaryTotal.value = 0
    serve({ windowed: [run('r2', daysAgo(2))], lifetime: [run('r2', daysAgo(2))] })

    renderPage()
    await settle()

    expect(sizes()).toEqual([100])
    const banner = await screen.findByTestId('overview-empty-window')
    expect(banner).toHaveTextContent(/2 days ago/)
    expect(banner).not.toHaveTextContent(/No test runs yet/)
  })

  it('still welcomes a project that has never had a run', async () => {
    summaryTotal.value = 0
    serve({ windowed: [], lifetime: [] })

    renderPage()
    await settle()

    expect(await screen.findByTestId('overview-empty-window')).toHaveTextContent(/No test runs yet/)
    expect(await screen.findByText(/Welcome to TestLookup/i)).toBeInTheDocument()
  })
})
