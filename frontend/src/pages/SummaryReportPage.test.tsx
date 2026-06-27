/**
 * SummaryReportPage tests — pin the contract for the consolidated
 * per-project Summary Report:
 *
 *   - "Pick a single project" empty state when ALL_PROJECTS is active.
 *   - "No executions in this window" empty state when totals are zero.
 *   - KPI tiles + per-suite table render when the API returns data.
 *   - Window chip + mode toggle re-fetch the data.
 *   - Export PDF button hits the service and triggers a download.
 *
 * Data path: ``summaryReportService.get`` (mocked) feeds
 * ``useSummaryReport``; the page reads from ``useProjectStore`` for
 * scope.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SummaryReportPage from './SummaryReportPage'
import type { SummaryReport, SummaryReportMode } from '@/types/summaryReport'
import { DEFAULT_TIME_WINDOW_DAYS, useTimeWindowStore } from '@/store/timeWindowStore'

const mockGet = vi.fn()
const mockDownloadPdf = vi.fn()

vi.mock('@/services/summaryReportService', () => ({
  summaryReportService: {
    get: (...args: unknown[]) => mockGet(...args),
    downloadPdf: (...args: unknown[]) => mockDownloadPdf(...args),
  },
}))

// Default project mock: a single active project (so the all-projects gate
// doesn't fire). Individual tests override this via vi.mocked(...).mockReturnValueOnce.
const mockProjectStore = vi.fn((selector: (s: {
  activeProjectId: string | null
  activeProject: null | { id: string; name: string }
}) => unknown) => selector({
  activeProjectId: 'p1',
  activeProject: { id: 'p1', name: 'GoogleProject' },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: unknown) => mockProjectStore(selector as never),
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

function makeReport(overrides: Partial<SummaryReport> = {}): SummaryReport {
  return {
    project_id: 'p1',
    project_name: 'GoogleProject',
    mode: 'window',
    window_days: 7,
    generated_at: '2026-05-16T00:00:00+00:00',
    period_start: '2026-05-09T00:00:00+00:00',
    period_end: '2026-05-16T00:00:00+00:00',
    totals: {
      total_test_cases: 200,
      passed: 180,
      failed: 15,
      skipped: 3,
      broken: 2,
      evaluated: 197,
      pass_rate_pct: 90.0,
      fail_rate_pct: 7.5,
      skip_rate_pct: 1.5,
      broken_rate_pct: 1.0,
      weighted_pass_rate_pct: 91.4,
    },
    run_count: 4,
    runs_per_day: 0.57,
    avg_duration_ms: 12_345,
    latest_run_at: '2026-05-15T00:00:00+00:00',
    flaky_test_count: 3,
    flaky_rate_pct: 1.5,
    suites: [
      {
        suite_name: 'checkout-api',
        total: 120, passed: 102, failed: 13, skipped: 3, broken: 2,
        pass_rate_pct: 85.0, weighted_pass_rate_pct: 87.2,
        last_run_at: '2026-05-15T00:00:00+00:00',
      },
      {
        suite_name: 'auth-api',
        total: 80, passed: 78, failed: 2, skipped: 0, broken: 0,
        pass_rate_pct: 97.5, weighted_pass_rate_pct: 97.5,
        last_run_at: '2026-05-15T00:00:00+00:00',
      },
    ],
    top_failing_tests: [
      { suite_name: 'checkout-api', class_name: 'CheckoutTests', test_name: 'test_pay', failures: 8 },
    ],
    ...overrides,
  }
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/reports/summary']}>
        <SummaryReportPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('SummaryReportPage', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockDownloadPdf.mockReset()
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: 'p1',
      activeProject: { id: 'p1', name: 'GoogleProject' },
    }))
    // Clean state so each test starts from documented defaults. The
    // window is now in the global Zustand store (key
    // ``testlookup-time-window``); aggregation mode is still page-local.
    try { localStorage.removeItem('testlookup-time-window') } catch { /* ignore */ }
    try { localStorage.removeItem('summary-report.mode') } catch { /* ignore */ }
    // Reset the in-memory store too — its constructor reads from
    // localStorage once at module load, so clearing the key isn't enough
    // between tests.
    useTimeWindowStore.setState({ days: DEFAULT_TIME_WINDOW_DAYS })
  })

  it('defaults to the shared window + latest-per-suite aggregation on first visit', async () => {
    // Regression for 2026-05-18 bug report: fresh users were landing on
    // the window-mode view and seeing Suite totals scaled by run count
    // (5 runs × 100 tests = 500), which reads as duplicate rows. Latest
    // mode shows one snapshot per suite — what users actually expect on
    // "show me where things stand right now". The window itself comes
    // from the shared store default (``DEFAULT_TIME_WINDOW_DAYS``, 7d as
    // of the v2 migration), not the old hardcoded 24h.
    mockGet.mockResolvedValue(makeReport({ mode: 'latest' }))

    renderPage()

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({
          days: DEFAULT_TIME_WINDOW_DAYS,
          mode: 'latest' as SummaryReportMode,
        }),
      )
    })
  })

  it('restores the last-picked window from the shared store on mount', async () => {
    useTimeWindowStore.setState({ days: 30 })
    try { localStorage.setItem('summary-report.mode', 'latest') } catch { /* ignore */ }
    mockGet.mockResolvedValue(makeReport({ mode: 'latest', window_days: 30 }))

    renderPage()

    await waitFor(() => {
      // First call after mount uses the persisted values, not the defaults.
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ days: 30, mode: 'latest' as SummaryReportMode }),
      )
    })
  })

  it('persists the user picked window so the next visit restores it', async () => {
    mockGet.mockResolvedValue(makeReport())

    renderPage()
    await waitFor(() => expect(mockGet).toHaveBeenCalled())

    fireEvent.click(screen.getByText('7d'))
    await waitFor(() => {
      expect(useTimeWindowStore.getState().days).toBe(7)
    })

    fireEvent.click(screen.getByText(/Latest run per suite/i))
    await waitFor(() => {
      expect(localStorage.getItem('summary-report.mode')).toBe('latest')
    })
  })

  it('snaps a non-supported global window to the nearest allowed option', async () => {
    // 14d isn't in Summary Report's option set [1,7,30,90]; the nearest
    // supported value is 7. The page should not refuse to render — it
    // should silently fall back to the closest.
    useTimeWindowStore.setState({ days: 14 })
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    await waitFor(() => {
      // Page's allowed set = [1,7,30,90]; nearest to 14 is 7.
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ days: 7 }),
      )
    })
  })

  it('shows the all-projects empty state when no specific project is active', () => {
    mockProjectStore.mockImplementation((selector) => selector({
      activeProjectId: '__ALL__',
      activeProject: null,
    }))

    renderPage()

    expect(screen.getByText(/Pick a single project/i)).toBeInTheDocument()
    // The KPI grid and per-suite table must NOT render in this state — pin
    // those instead of asserting on the fetcher, which the SWR hook may
    // still fire (the backend short-circuits to an empty envelope).
    expect(screen.queryByText('Total tests')).not.toBeInTheDocument()
    expect(screen.queryByText('Per-suite breakdown')).not.toBeInTheDocument()
  })

  it('shows the no-data empty state when totals are zero', async () => {
    mockGet.mockResolvedValue(makeReport({
      totals: {
        total_test_cases: 0, passed: 0, failed: 0, skipped: 0, broken: 0,
        evaluated: 0, pass_rate_pct: 0, fail_rate_pct: 0,
        skip_rate_pct: 0, broken_rate_pct: 0, weighted_pass_rate_pct: 0,
      },
      suites: [],
      top_failing_tests: [],
      run_count: 0,
    }))

    renderPage()

    expect(await screen.findByText(/No executions in this window/i)).toBeInTheDocument()
  })

  it('renders the no-data empty state (no crash) when the envelope has null totals', async () => {
    // The backend can short-circuit to an empty envelope where ``totals``
    // is absent entirely (not just zeroed) — the same shape the
    // all-projects gate alludes to. The page guards ``totals == null``
    // before dereferencing the KPI fields, so a missing-totals payload
    // must fall through to the empty state rather than throwing on a
    // ``totals.total_test_cases`` read. Regression for the guard that
    // narrows ``totals`` in place of the old non-null assertions.
    mockGet.mockResolvedValue(makeReport({
      totals: null as unknown as SummaryReport['totals'],
      suites: [],
      top_failing_tests: [],
      run_count: 0,
    }))

    renderPage()

    expect(await screen.findByText(/No executions in this window/i)).toBeInTheDocument()
    // KPI grid must not render — proves we never hit the dereference path.
    expect(screen.queryByText('Total tests')).not.toBeInTheDocument()
  })

  it('renders KPI tiles, per-suite table, and top failing rows on happy path', async () => {
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    // Headline KPI labels render.
    expect(await screen.findByText('Total tests')).toBeInTheDocument()
    // "Pass %" appears twice (KPI label + table header) — both are real and
    // both must be present, so assert on the multiple match instead of
    // single-element retrieval.
    expect(screen.getAllByText('Pass %').length).toBeGreaterThanOrEqual(2)
    // Per-suite row labels appear in the suite table; ``checkout-api``
    // also appears in the "Top failing tests" Suite column so use
    // getAllByText. Two matches = one per section.
    expect(screen.getAllByText('checkout-api').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('auth-api')).toBeInTheDocument()
    // Top failing row:
    expect(screen.getByText('test_pay')).toBeInTheDocument()
  })

  it('renders each suite name as a link to /coverage/suite?name=...&days=...', async () => {
    // Window was bumped to 30d in localStorage previously; we want a
    // deterministic value so the href assertion is stable.
    useTimeWindowStore.setState({ days: 7 })
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    const link = await screen.findByRole('link', { name: 'auth-api' })
    expect(link).toHaveAttribute(
      'href',
      '/coverage/suite?name=auth-api&days=7',
    )
  })

  it('re-fetches when the user picks a different window or aggregation mode', async () => {
    mockGet.mockResolvedValue(makeReport())

    renderPage()

    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    const initialCalls = mockGet.mock.calls.length

    fireEvent.click(screen.getByText('30d'))

    await waitFor(() => {
      // Default mode is now ``latest`` (post-2026-05-18 bug fix); we
      // assert window-change preserves whatever mode is currently
      // active rather than re-asserting on the default.
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ days: 30, mode: 'latest' as SummaryReportMode }),
      )
      expect(mockGet.mock.calls.length).toBeGreaterThan(initialCalls)
    })

    const beforeMode = mockGet.mock.calls.length
    // Switch FROM the default 'latest' TO 'window' to exercise the toggle.
    fireEvent.click(screen.getByText(/All runs in window/i))

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'window' as SummaryReportMode }),
      )
      expect(mockGet.mock.calls.length).toBeGreaterThan(beforeMode)
    })
  })

  it('triggers a PDF download when the export button is clicked', async () => {
    mockGet.mockResolvedValue(makeReport())
    mockDownloadPdf.mockResolvedValue(new Blob(['%PDF-1.4'], { type: 'application/pdf' }))

    // Stub the URL / DOM bits the page touches to drive the browser download.
    const origCreateUrl = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()

    renderPage()

    // Wait for the export button to enable (it depends on hasData).
    const button = await screen.findByRole('button', { name: /Export PDF/i })
    await waitFor(() => expect(button).not.toBeDisabled())

    fireEvent.click(button)

    await waitFor(() => {
      // Default window comes from the shared store
      // (``DEFAULT_TIME_WINDOW_DAYS``, 7d as of the v2 migration) and the
      // default aggregation mode is ``latest`` post-2026-05-18 (bug fix:
      // ``window`` mode produces run-count-scaled totals that read as
      // duplicates). Pin both so a future bump of either default doesn't
      // silently regress.
      expect(mockDownloadPdf).toHaveBeenCalledWith({
        project_id: 'p1',
        days: DEFAULT_TIME_WINDOW_DAYS,
        mode: 'latest',
      })
    })

    URL.createObjectURL = origCreateUrl
    URL.revokeObjectURL = origRevoke
  })
})
