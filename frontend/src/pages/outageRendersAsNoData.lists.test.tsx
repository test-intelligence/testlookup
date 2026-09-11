/**
 * M21: a failed fetch must not render as "you have no data" (list pages).
 *
 * The same regression as outageRendersAsNoData.test.tsx, for the next four
 * pages by impact. Each read only `data`/`isLoading`, so during an outage
 * `data` stayed undefined and the page asserted an empty state it had never
 * measured:
 *
 *   Runs               "No runs in the window. Try a longer window or check your reporter."
 *   Intelligence hub   "No runs in this window" + a look-further-back probe
 *   Releases           "No releases yet" + create CTA
 *   Summary report     "No executions in this window"
 *
 * Each assertion has two halves: the honest panel appears AND the false
 * empty-state copy is gone.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: vi.fn(),
  useRelease: vi.fn(() => ({ data: undefined, isLoading: false, mutate: vi.fn() })),
}))
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: { get: vi.fn(), downloadPdf: vi.fn() },
}))
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const state = {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
    projects: [{ id: 'proj-1', name: 'Project One' }],
  }
  return {
    ...actual,
    useProjectStore: (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state),
  }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

import { useMostRecentRun, useRuns } from '@/hooks/useRuns'
import { useReleases } from '@/hooks/useReleases'
import { summaryReportService } from '@/services/summaryReportService'
import IntelligenceHubPage from './IntelligenceHubPage'
import ReleasesPage from './ReleasesPage'
import RunsPage from './RunsPage'
import SummaryReportPage from './SummaryReportPage'

const NETWORK_DOWN = Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })
const SERVER_500 = Object.assign(new Error('Request failed'), { response: { status: 500, data: {} } })

const failed = (error: unknown) => ({ data: undefined, isLoading: false, error, mutate: vi.fn() })

function renderPage(ui: React.ReactElement) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter>{ui}</MemoryRouter>
    </SWRConfig>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useMostRecentRun).mockReturnValue({ data: undefined } as never)
})

describe('an outage is not an empty list', () => {
  it('Runs', () => {
    vi.mocked(useRuns).mockReturnValue(failed(NETWORK_DOWN) as never)
    renderPage(<RunsPage />)

    expect(screen.getByTestId('runs-data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/No runs in the window/i)).toBeNull()
  })

  it('Intelligence hub, and it does not go looking further back', () => {
    vi.mocked(useRuns).mockReturnValue(failed(SERVER_500) as never)
    renderPage(<IntelligenceHubPage />)

    expect(screen.getByTestId('intelligence-data-unavailable')).toHaveTextContent('HTTP 500')
    expect(screen.queryByText(/No runs in this window/i)).toBeNull()
    expect(vi.mocked(useMostRecentRun)).not.toHaveBeenCalledWith(true)
  })

  it('Releases', () => {
    vi.mocked(useReleases).mockReturnValue(failed(SERVER_500) as never)
    vi.mocked(useRuns).mockReturnValue({ data: { items: [] } } as never)
    renderPage(<ReleasesPage />)

    expect(screen.getByTestId('releases-data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/No releases yet/i)).toBeNull()
  })

  it('Summary report', async () => {
    vi.mocked(summaryReportService.get).mockRejectedValue(SERVER_500)
    renderPage(<SummaryReportPage />)

    expect(await screen.findByTestId('summary-data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/No executions in this window/i)).toBeNull()
  })

  it('a retry on the panel refetches', async () => {
    const state = failed(NETWORK_DOWN)
    vi.mocked(useRuns).mockReturnValue(state as never)
    renderPage(<RunsPage />)

    screen.getByRole('button', { name: /Retry/i }).click()
    await waitFor(() => expect(state.mutate).toHaveBeenCalledTimes(1))
  })
})
