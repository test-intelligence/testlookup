/**
 * Regression: an outage must not render as "you have no data".
 *
 * Overview, Coverage, Trends and Defects each destructured only `data` and
 * `isLoading` from SWR and never `error`. Every one of those fetches is a
 * plain axios call that rejects on failure, so during a backend outage
 * `isLoading` went false, `data` stayed undefined, and each page fell through
 * to its empty state — asserting as fact something it had never measured:
 *
 *   Overview  "No test runs yet … widening the time window will not help"
 *   Coverage  "No coverage data yet — Upload test results…"
 *   Defects   verdict PENDING → "queue empty"
 *   Trends    every band and recommendation computed over an empty series
 *
 * Each assertion below has two halves on purpose: the honest panel appears
 * AND the false empty-state copy is gone. Asserting only the first would pass
 * against a page that rendered both.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OverviewPage from './OverviewPage'
import CoveragePage from './CoveragePage'
import TrendsPage from './TrendsPage'
import DefectsPage from './DefectsPage'

vi.mock('@/hooks/useMetrics', () => {
  const d = () => ({ data: undefined, isLoading: false })
  return {
    useDashboardSummary:  vi.fn(d),
    useTrendData:         vi.fn(d),
    useFlakyTests:        vi.fn(d),
    useFailureCategories: vi.fn(d),
    useTopFailing:        vi.fn(d),
    useCoverage:          vi.fn(d),
    useDefects:           vi.fn(d),
    useSuiteDetail:       vi.fn(d),
    useAiSummary:         vi.fn(d),
  }
})
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/hooks/useValueMetrics', () => ({
  useValueMetricsKpi: () => ({ metrics: undefined }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] } }),
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
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

/** What axios produces when the request never reached the backend. */
const NETWORK_DOWN = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })
/** What axios produces when the backend answered, badly. */
const SERVER_500 = Object.assign(new Error('Request failed'), {
  response: { status: 500, data: {} },
})

const failed = (error: unknown) => ({ data: undefined, isLoading: false, error, mutate: vi.fn() })

function renderPage(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('an outage renders as an outage, not as an empty dataset', () => {
  beforeEach(() => vi.clearAllMocks())

  it('Overview does not claim "No test runs yet" when the summary fetch failed', async () => {
    const { useDashboardSummary } = await import('@/hooks/useMetrics')
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue(failed(NETWORK_DOWN))

    renderPage(<OverviewPage />)

    expect(screen.getByTestId('overview-data-unavailable')).toBeInTheDocument()
    expect(screen.getByText(/can't reach the server/i)).toBeInTheDocument()
    // The specific sentence that sent operators to re-ingest data they had.
    expect(screen.queryByText(/No test runs yet/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/widening the time window will not help/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('overview-empty-window')).not.toBeInTheDocument()
  })

  it('Coverage does not claim "No coverage data yet" when the coverage fetch failed', async () => {
    const { useCoverage } = await import('@/hooks/useMetrics')
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue(failed(SERVER_500))

    renderPage(<CoveragePage />)

    expect(screen.getByTestId('coverage-data-unavailable')).toBeInTheDocument()
    // A 5xx is reported as a server failure, and the status is shown.
    expect(screen.getByText(/server failed to answer/i)).toBeInTheDocument()
    expect(screen.getByText('HTTP 500')).toBeInTheDocument()
    expect(screen.queryByText(/No coverage data yet/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Upload test results/i)).not.toBeInTheDocument()
  })

  it('Trends renders the outage panel rather than bands computed over no data', async () => {
    const { useTrendData } = await import('@/hooks/useMetrics')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue(failed(NETWORK_DOWN))

    renderPage(<TrendsPage />)

    expect(screen.getByTestId('trends-data-unavailable')).toBeInTheDocument()
    expect(screen.getByText(/nothing below is a statement about your test results/i))
      .toBeInTheDocument()
  })

  it('Defects does not report "queue empty" when the defect fetch failed', async () => {
    const { useDefects } = await import('@/hooks/useMetrics')
    ;(useDefects as ReturnType<typeof vi.fn>).mockReturnValue(failed(NETWORK_DOWN))

    renderPage(<DefectsPage />)

    expect(screen.getByTestId('defects-data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/queue empty/i)).not.toBeInTheDocument()
  })

  it('still renders the ordinary empty state when the fetch SUCCEEDED and was empty', async () => {
    const { useCoverage } = await import('@/hooks/useMetrics')
    // No error: an genuinely empty payload must keep its empty-state copy.
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { summary: {}, suites: [] },
      isLoading: false,
      error: undefined,
      mutate: vi.fn(),
    })

    renderPage(<CoveragePage />)

    expect(screen.getByText(/No coverage data yet/i)).toBeInTheDocument()
    expect(screen.queryByTestId('coverage-data-unavailable')).not.toBeInTheDocument()
  })
})
